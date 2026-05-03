"""UHDI -> HGLDD 1.0 translator. Output matches `firtool --emit-hgldd`."""
from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from uhdi_common.backend import Backend, register
from uhdi_common.context import BaseContext, ConversionError
from uhdi_common.refs import loc_file_path


class HGLDDConversionError(ConversionError):
    pass


@dataclass
class _FileInfo:
    """Combined HGL/HDL flat path list (HDL files after `hdl_start`).

    Dedup across both segments. `index` mirrors `ordered` for O(1) add."""
    ordered: List[str] = field(default_factory=list)
    hdl_start: Optional[int] = None
    index: Dict[str, int] = field(default_factory=dict)

    def add_source(self, path: str) -> int:
        if (existing := self.index.get(path)) is not None:
            return existing
        if self.hdl_start is not None:
            pos = self.hdl_start
            self.ordered.insert(pos, path)
            for k, v in list(self.index.items()):
                if v >= pos:
                    self.index[k] = v + 1
            self.hdl_start += 1
            self.index[path] = pos
            return pos
        self.ordered.append(path)
        pos = len(self.ordered) - 1
        self.index[path] = pos
        return pos

    def add_hdl(self, path: str) -> int:
        if (existing := self.index.get(path)) is not None:
            return existing
        if self.hdl_start is None:
            self.hdl_start = len(self.ordered)
        self.ordered.append(path)
        pos = len(self.ordered) - 1
        self.index[path] = pos
        return pos


@dataclass
class _Context(BaseContext):
    files: _FileInfo = field(default_factory=_FileInfo)
    aggregated_leaves: set = field(default_factory=set)


def _loc_to_hgldd(loc, repr_key, ctx):
    """Convert uhdi Location to HGLDD `{file: 1-indexed, begin/end_line/column}`."""
    if not loc:
        return None
    raw_path = loc_file_path(loc, repr_key, ctx)
    if raw_path is None:
        return None
    repr_info = ctx.representations.get(repr_key, {})
    add = ctx.files.add_hdl if repr_info.get("kind") == "hdl" else ctx.files.add_source
    out = {"file": add(raw_path) + 1}  # HGLDD is 1-indexed.
    if "beginLine" in loc:
        out["begin_line"] = loc["beginLine"]
        out["end_line"] = loc.get("endLine", loc["beginLine"])
    if "beginColumn" in loc:
        out["begin_column"] = loc["beginColumn"]
        out["end_column"] = loc.get("endColumn", loc["beginColumn"])
    return out


def _type_description(type_ref, ctx):
    """Build HGLDD type descriptor for given type_ref."""
    # 1-bit elides packed_range to match native HGLDD's clock/reset convention.
    descriptor = ctx.types.get(type_ref)
    if descriptor is None:
        return {"type_name": "logic"}
    kind = descriptor.get("kind")
    if kind in ("uint", "sint"):
        width = int(descriptor.get("width", 1))
        return {"type_name": "logic"} if width <= 1 else \
               {"type_name": "logic", "packed_range": [width - 1, 0]}
    if kind == "struct":
        return {"type_name": type_ref}
    if kind == "vector":
        elem = dict(_type_description(descriptor.get("elementRef", ""), ctx))
        if (size := int(descriptor.get("size", 0))) > 0:
            elem.setdefault("unpacked_range", [size - 1, 0])
        return elem
    return {"type_name": "logic"}


def _opnode_to_hgldd(opnode, ctx, seen=None):
    """Convert opnode to HGLDD format."""
    return {"opcode": opnode.get("opcode", ""),
            "operands": [_expression_to_hgldd(o, ctx, seen)
                         for o in opnode.get("operands", [])]}


def _expression_to_hgldd(operand, ctx, seen=None):
    """Convert expression operand to HGLDD format."""
    if not isinstance(operand, dict):
        return {}
    if "sigName" in operand:
        return {"sig_name": operand["sigName"]}
    if "constant" in operand:
        n = int(operand["constant"])
        if (w := int(operand.get("width", 0))) > 0:
            return {"bit_vector": format(n & ((1 << w) - 1), f"0{w}b")}
        return {"integer_num": n}
    if "bitVector" in operand:
        return {"bit_vector": operand["bitVector"]}
    if "varRef" in operand:
        target = ctx.variables.get(operand["varRef"], {})
        name = (target.get("representations", {})
                .get(ctx.simulation_repr, {}).get("name"))
        return {"sig_name": name} if name else {}
    if "exprRef" in operand:
        ref = operand["exprRef"]
        seen = set() if seen is None else seen
        if ref in seen:
            raise HGLDDConversionError(
                f"cycle in expression graph at exprRef {ref!r}")
        expr = ctx.expressions.get(ref)
        return _opnode_to_hgldd(expr, ctx, seen | {ref}) if expr is not None else {}
    if "opcode" in operand:
        return _opnode_to_hgldd(operand, ctx, seen)
    return {}


def _topo_sorted_struct_ids(ctx):
    """Topologically sort struct IDs (inner structs before outer, no forward refs).

    Raises on cyclic struct refs."""
    visiting: set = set()
    visited: set = set()
    order: List[str] = []

    def visit(tid):
        if tid in visited:
            return
        if tid in visiting:
            raise HGLDDConversionError(
                f"cycle in type pool: '{tid}' references itself transitively")
        d = ctx.types.get(tid)
        if d is None:
            return
        kind = d.get("kind")
        if kind == "struct":
            visiting.add(tid)
            for m in d.get("members") or []:
                if (t := m.get("typeRef")) is not None:
                    visit(t)
            visiting.discard(tid)
            visited.add(tid)
            order.append(tid)
        elif kind == "vector":
            visiting.add(tid)
            if (e := d.get("elementRef")) is not None:
                visit(e)
            visiting.discard(tid)
            visited.add(tid)

    for tid, d in ctx.types.items():
        if d.get("kind") == "struct":
            visit(tid)
    return order


def _struct_objects(ctx):
    """Build struct-type objects (one per unique struct)."""
    # Pick a deterministic representative loc per struct (lowest scope-id +
    # earliest line) so multi-scope structs get a stable hgl_loc.
    struct_candidates: Dict[str, List[Tuple[Any, str]]] = {}
    scope_var_loc: Dict[Any, Dict[str, Any]] = {}
    for var in ctx.variables.values():
        type_ref = var.get("typeRef")
        hgl = (var.get("representations", {}).get(ctx.authoring_repr, {})
               or {})
        loc = _loc_to_hgldd(hgl.get("location"), ctx.authoring_repr, ctx)
        if (type_ref and loc and
                (ctx.types.get(type_ref) or {}).get("kind") == "struct"):
            struct_candidates.setdefault(type_ref, []).append(
                (loc, var.get("ownerScopeRef", "")))
        if (scope := var.get("ownerScopeRef", "")) and (n := hgl.get("name")) and loc:
            scope_var_loc.setdefault((scope, n), loc)

    def _struct_loc_key(item):
        loc, scope = item
        return (str(scope),
                int(loc.get("begin_line", 0) or 0),
                int(loc.get("begin_column", 0) or 0))

    struct_info: Dict[str, Any] = {
        tid: min(cands, key=_struct_loc_key)
        for tid, cands in struct_candidates.items()
    }

    out = []
    for tid in _topo_sorted_struct_ids(ctx):
        d = ctx.types.get(tid) or {}
        if d.get("kind") != "struct":  # pragma: no cover
            continue
        struct_loc, owner = struct_info.get(tid, (None, ""))
        port_vars = []
        for m in d.get("members") or []:
            name = m.get("name", "")
            pv = {"var_name": name, **_type_description(m.get("typeRef", ""), ctx)}
            # Native EmitHGLDD picks backing-signal line for unflipped
            # (output-like) members, bundle-decl line for flipped.
            backing = (None if m.get("flipped", False)
                       else scope_var_loc.get((owner, name)))
            if (mloc := backing or struct_loc):
                pv["hgl_loc"] = mloc
            port_vars.append(pv)
        obj = {"kind": "struct", "obj_name": tid, "port_vars": port_vars}
        if struct_loc:
            obj["hgl_loc"] = struct_loc
        out.append(obj)
    return out


def _first_vector_element_sig(hdl_value, ctx):
    """For Vec `'{buf_0, buf_1, ...}`, return `buf_0`.

    HGLDD names port_vars after the first flat element."""
    if not isinstance(hdl_value, dict):
        return ""
    expr = ctx.expressions.get(hdl_value.get("exprRef") or "") or {}
    operands = expr.get("operands") or []
    if operands and isinstance(operands[0], dict) and "sigName" in operands[0]:
        return operands[0]["sigName"]
    return ""


def _variable_to_port_var(var_id, var, ctx):
    """Convert variable to HGLDD port_var entry."""
    reprs = var.get("representations", {}) or {}
    hgl = reprs.get(ctx.authoring_repr, {}) or {}
    hdl = reprs.get(ctx.simulation_repr, {}) or {}

    # Output ports DCE'd to a backing signal: native HGLDD doesn't emit them.
    if (var.get("bindKind") == "port"
            and var.get("direction") == "output" and not hdl):
        return None

    # Skip flat ports already represented as leaves of an aggregate's
    # value tree (native emits only the aggregate).
    hdl_value = hdl.get("value") if isinstance(hdl, dict) else None
    if (var.get("bindKind") == "port"
            and isinstance(hdl_value, dict)
            and "sigName" in hdl_value
            and hdl_value["sigName"] in ctx.aggregated_leaves):
        return None

    # Vec: name after `buf_0` for Tywaves VCD path-lookup.
    var_name = hgl.get("name") or var_id
    if (ctx.types.get(var.get("typeRef", "")) or {}).get("kind") == "vector":
        if first := _first_vector_element_sig(hdl_value, ctx):
            var_name = first
    out = {"var_name": var_name}

    out.update(_type_description(var.get("typeRef", ""), ctx))

    if isinstance(hdl_value, dict):
        if "sigName" in hdl_value:
            out["value"] = {"sig_name": hdl_value["sigName"]}
        elif "exprRef" in hdl_value:
            if rendered := _expression_to_hgldd(hdl_value, ctx):
                out["value"] = rendered
        elif "constant" in hdl_value:
            width = int((ctx.types.get(var.get("typeRef", "")) or {})
                        .get("width", 0))
            n = int(hdl_value["constant"])
            out["value"] = ({"bit_vector": format(n & ((1 << width) - 1),
                                                   f"0{width}b")}
                            if width > 0 else {"integer_num": n})
        elif "bitVector" in hdl_value:
            out["value"] = {"bit_vector": hdl_value["bitVector"]}

    if loc := _loc_to_hgldd(hgl.get("location"), ctx.authoring_repr, ctx):
        out["hgl_loc"] = loc
    if loc := _loc_to_hgldd(hdl.get("location"), ctx.simulation_repr, ctx):
        out["hdl_loc"] = loc
    return out


def _collect_aggregated_leaves(ctx) -> set:
    """Collect sigNames reachable from composite (exprRef-rooted) variable value trees.

    These leaves are already represented via the parent."""
    leaves: set = set()
    seen: set = set()

    def walk(node):
        if isinstance(node, dict):
            if "sigName" in node:
                leaves.add(node["sigName"])
            if (ref := node.get("exprRef")) and ref not in seen:
                seen.add(ref)
                if (expr := ctx.expressions.get(ref)) is not None:
                    walk(expr)
            for k, v in node.items():
                if k in ("sigName", "exprRef"):
                    continue
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for var in ctx.variables.values():
        hdl = (var.get("representations", {}) or {}).get(
            ctx.simulation_repr, {})
        value = hdl.get("value") if isinstance(hdl, dict) else None
        # Only composite (exprRef-rooted) values seed -- a bare sigName
        # at the root means this var IS the leaf, not a parent.
        if isinstance(value, dict) and "exprRef" in value:
            walk(value)

    return leaves


def _ordered_scope_vars(scope, scope_id, ctx):
    """Iterate scope vars: variableRefs first, then ownerScopeRef vars."""
    seen = set()
    for vid in scope.get("variableRefs") or []:
        if (v := ctx.variables.get(vid)) is not None:
            seen.add(vid)
            yield vid, v
    for vid, v in ctx.variables.items():
        if vid not in seen and v.get("ownerScopeRef") == scope_id:
            yield vid, v


def _instance_child(inst, ctx):
    """Build instance child entry."""
    scope_ref = inst.get("scopeRef")
    target = ctx.scopes.get(scope_ref, {})
    target_hdl = target.get("representations", {}).get(ctx.simulation_repr, {})
    child = {"name": inst.get("as") or scope_ref, "obj_name": scope_ref,
             "module_name": (target_hdl.get("name")
                             or target.get("name") or scope_ref)}
    inst_reprs = inst.get("representations", {}) or {}
    for repr_key, out_key in ((ctx.authoring_repr, "hgl_loc"),
                              (ctx.simulation_repr, "hdl_loc")):
        loc_src = (inst_reprs.get(repr_key, {}) or {}).get("location")
        if loc := _loc_to_hgldd(loc_src, repr_key, ctx):
            child[out_key] = loc
    return child


def _scope_object(scope_id, scope, ctx):
    """Build scope object (module/extmodule)."""
    reprs = scope.get("representations", {}) or {}
    hgl = reprs.get(ctx.authoring_repr, {}) or {}
    hdl = reprs.get(ctx.simulation_repr, {}) or {}
    out = {
        "kind": "module",
        "obj_name": hgl.get("name") or scope.get("name") or scope_id,
        "module_name": hdl.get("name") or scope.get("name") or scope_id,
    }
    if scope.get("kind") == "extmodule":
        out["isExtModule"] = 1
    if loc := _loc_to_hgldd(hgl.get("location"), ctx.authoring_repr, ctx):
        out["hgl_loc"] = loc
    if loc := _loc_to_hgldd(hdl.get("location"), ctx.simulation_repr, ctx):
        out["hdl_loc"] = loc

    # Dedupe by var_name keeping first occurrence (pool may carry
    # duplicate records with different audit fields; native coalesces).
    seen_names: set = set()
    port_vars: List[Dict[str, Any]] = []
    for vid, v in _ordered_scope_vars(scope, scope_id, ctx):
        pv = _variable_to_port_var(vid, v, ctx)
        if pv is None:
            continue
        name = pv.get("var_name")
        if name in seen_names:
            continue
        seen_names.add(name)
        port_vars.append(pv)
    out["port_vars"] = port_vars

    children = [_instance_child(inst, ctx)
                for inst in scope.get("instantiates") or []]

    # Inline children: collisions get `_<N>` suffix (mirrors firtool's
    # LowerToHW uniquifier).
    used = {pv.get("var_name") for pv in out["port_vars"]}
    suffix: Dict[str, int] = {}

    def _uniquify(name):
        if name not in used:
            used.add(name); return name
        n = suffix.get(name, 0)
        while True:
            cand = f"{name}_{n}"
            n += 1
            if cand not in used:
                suffix[name] = n; used.add(cand); return cand

    for other_id, other in ctx.scopes.items():
        if other.get("kind") != "inline" or other.get("containerScopeRef") != scope_id:
            continue
        inline_hgl = (other.get("representations", {})
                      .get(ctx.authoring_repr, {}) or {})
        inline = {"name": (inline_hgl.get("name") or other.get("name") or other_id)}
        if loc := _loc_to_hgldd(inline_hgl.get("location"),
                                ctx.authoring_repr, ctx):
            inline["hgl_loc"] = loc
        inline_pvs = []
        for vid, v in _ordered_scope_vars(other, other_id, ctx):
            pv = _variable_to_port_var(vid, v, ctx)
            if pv is None:
                continue
            pv["var_name"] = _uniquify(pv.get("var_name", ""))
            inline_pvs.append(pv)
        inline["port_vars"] = inline_pvs
        inline["children"] = []
        children.append(inline)

    out["children"] = children
    return out


def convert(uhdi):
    """Translate uhdi document into HGLDD."""
    try:
        ctx = _Context.from_uhdi(uhdi)
    except ConversionError as e:
        raise HGLDDConversionError(str(e)) from None

    ctx.aggregated_leaves = _collect_aggregated_leaves(ctx)
    for sid in uhdi.get("top", []):
        if sid not in ctx.scopes:
            raise HGLDDConversionError(f"top references unknown scope '{sid}'")

    # Structs first so type_name lookups resolve.
    objects = list(_struct_objects(ctx))
    objects.extend(_scope_object(sid, s, ctx)
                   for sid, s in ctx.scopes.items()
                   if s.get("kind") in ("module", "extmodule"))

    # No HDL files: point past end (else 1 would tag first source as HDL).
    hdl_start = (ctx.files.hdl_start
                 if ctx.files.hdl_start is not None
                 else len(ctx.files.ordered))
    return {"HGLDD": {"version": "1.0",
                      "file_info": list(ctx.files.ordered),
                      "hdl_file_index": hdl_start + 1},
            "objects": objects}


@register
class HGLDDBackend(Backend):
    name = "hgldd"
    description = (
        "uhdi -> HGLDD 1.0 (the symbol table consumed by Tywaves, "
        "Surfer, Verdi).  Drop-in replacement for "
        "`firtool --emit-hgldd`.")
    binary_output = False
    output_extension = "dd"

    def convert(self,
                uhdi: Dict[str, Any],
                output: Optional[pathlib.Path] = None
                ) -> Dict[str, Any]:
        del output
        return convert(uhdi)
