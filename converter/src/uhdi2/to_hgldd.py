"""UHDI 2.0 -> HGLDD 1.0. One pass over modules -> variables, no id parsing,
no intermediate parent maps -- the v2 shape is already module-nested.

Reuses v1's pure, ctx.types-only helpers (`_type_description`,
`_topo_sorted_struct_ids`) and the pure `_FileInfo` dataclass from
`uhdi_to_hgldd.convert` unchanged, via a minimal `_TypesCtx` shim.

`hgl_loc`/`hdl_loc` reconstruction mirrors v1's `_loc_to_hgldd` exactly
(including its empty-HDL-path substitution and the source/hdl leading-`/`
asymmetry): see `_loc_to_hgldd2`.

Struct/vector "representative location" selection reproduces v1's
`_struct_objects` picks (`min` by `(str(owner_module), beginLine,
beginColumn)` for a struct's own `hgl_loc`; first-in-document-order for a
Vec[Bundle]'s `hgl_loc`) using v2 root-variable locations in place of v1's
per-level synthetic Variable records -- see `_location_candidate_types`
for why the walk stops at a vector boundary (no synthetic Variable ever
existed for a per-index vector-of-struct element in the v1 pool, so no v2
root variable should manufacture a location for it either).

Per-member `hgl_loc` inside a struct object always falls back to the
struct's own representative location (never a per-field "backing" lookup):
empirically, v1's `scope_var_loc` name-based backing mechanism, when it
resolves at all, resolves to the same location a synthetic subfield always
carried (which is always its root's location, `Location`'s "leaf never
differs from root" invariant per the upgrade report) -- see
`test/test_uhdi2.py` for the corpus-wide check this simplification relies
on.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Tuple

from uhdi_common.context import ConversionError
from uhdi_to_hgldd.convert import (_FileInfo, _topo_sorted_struct_ids,
                                   _type_description)


class HGLDD2ConversionError(ConversionError):
    pass


@dataclass
class _TypesCtx:
    """Shim satisfying the `ctx.types` contract the reused v1 helpers need."""
    types: Dict[str, Any]


_HDL_LANGUAGE_EXTENSIONS = {"SystemVerilog": ".sv", "Verilog": ".v"}


# ---- location / value plumbing --------------------------------------------


def _loc_to_hgldd2(loc: Optional[Dict[str, Any]], files_pool: List[str],
                   is_hdl: bool, hdl_fallback: Optional[str],
                   files: _FileInfo) -> Optional[Dict[str, Any]]:
    """v2 `Location` (unflattened: `file`/`beginLine`/`beginColumn`/
    `endLine`/`endColumn`) -> HGLDD `hgl_loc`/`hdl_loc`. Mirrors v1's
    `_loc_to_hgldd`: an empty resolved HDL path falls back to the
    document's own hdl_path (firtool's --emit-uhdi can leave
    `verilog.files` blank); only non-HDL (source) paths get their leading
    `/` stripped."""
    if not loc or "file" not in loc:
        return None
    try:
        idx = int(loc["file"])
    except (TypeError, ValueError):
        return None
    if not (0 <= idx < len(files_pool)):
        return None
    raw_path = str(files_pool[idx])
    if is_hdl and not raw_path and hdl_fallback:
        raw_path = hdl_fallback
    if not raw_path:
        return None
    if not is_hdl:
        raw_path = raw_path.lstrip("/")
    add = files.add_hdl if is_hdl else files.add_source
    out: Dict[str, Any] = {"file": add(raw_path) + 1}
    if "beginLine" in loc:
        out["begin_line"] = loc["beginLine"]
        out["end_line"] = loc.get("endLine", loc["beginLine"])
    if "beginColumn" in loc:
        out["begin_column"] = loc["beginColumn"]
        out["end_column"] = loc.get("endColumn", loc["beginColumn"])
    return out


def _module_source_lang_type_info(source: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Module.source keeps its own `typeName` untouched by the type
    source-name amendment (that only concerns Variable.source)."""
    source = source or {}
    type_name = source.get("typeName")
    return {"type_name": type_name} if type_name else None


def _variable_source_lang_type_info(var_source: Dict[str, Any], type_ref: str,
                                    ctx: "_TypesCtx") -> Optional[Dict[str, Any]]:
    """Reconstructs a root variable's `source_lang_type_info`. Two paths:
    the variable kept its own raw `typeName` (the type source-name
    conflict fallback -- see upgrade.py), or it's rebuilt from
    `binding[types[typeRef].source.name]` (or just the bare name, with no
    binding)."""
    if type_name := var_source.get("typeName"):
        return {"type_name": type_name}
    tsrc = (ctx.types.get(type_ref) or {}).get("source") or {}
    name = tsrc.get("name")
    if not name:
        return None
    binding = var_source.get("binding")
    out: Dict[str, Any] = {"type_name": f"{binding}[{name}]" if binding else name}
    if params := tsrc.get("params"):
        out["params"] = params
    return out


def _root_variable_source_lang_type_info(var_source: Dict[str, Any], type_ref: str,
                                         ctx: "_TypesCtx") -> Optional[Dict[str, Any]]:
    """As `_variable_source_lang_type_info`, but for a root (top-level
    module) variable specifically: v1 never gives one to a variable that
    had no `sourceLangType` at all in the first place (e.g. an
    emitter-added duplicate port that wasn't deduplicated because its
    sigName happens not to be reachable through any aggregate's own value
    tree -- see `nested_bundle`'s "io_out"). `binding`/`typeName` are only
    ever set on `Variable.source` when a typeName existed (see
    upgrade.py); their absence is the signal to suppress reconstruction
    here. This gate does not apply to struct-member reconstruction
    (`_struct_objects`), where a `source_lang_type_info` is attempted
    only for a struct that itself has a resolvable location/binding to
    begin with (an already-scoped case, per `_pick_struct_winner`) --
    NOTE this still can't distinguish "had a bare, unbracketed typeName"
    from "had none" (not observed in the corpus; see upgrade report)."""
    if not (var_source.get("binding") or var_source.get("typeName")):
        return None
    return _variable_source_lang_type_info(var_source, type_ref, ctx)


def _scalar_value(binding: Any, type_ref: str, ctx: "_TypesCtx") -> Dict[str, Any]:
    if isinstance(binding, str):
        return {"sig_name": binding}
    if isinstance(binding, dict) and "signal" in binding:
        return {"sig_name": binding["signal"]}
    if isinstance(binding, dict) and "constant" in binding:
        width = int((ctx.types.get(type_ref) or {}).get("width", 0))
        n = int(binding["constant"])
        return ({"bit_vector": format(n & ((1 << width) - 1), f"0{width}b")}
                if width > 0 else {"integer_num": n})
    if isinstance(binding, dict) and "bitVector" in binding:
        return {"bit_vector": binding["bitVector"]}
    return {}


def _value_at_path(bind_map: Dict[str, Any], path: str, type_ref: str,
                   ctx: "_TypesCtx") -> Dict[str, Any]:
    type_def = ctx.types.get(type_ref) or {}
    if type_def.get("kind") in ("struct", "vector"):
        return _aggregate_value(bind_map, type_ref, path, ctx)
    leaf = bind_map.get(path)
    return {} if leaf is None else _scalar_value(leaf, type_ref, ctx)


def _aggregate_value(bind_map: Dict[str, Any], type_ref: str, prefix: str,
                     ctx: "_TypesCtx") -> Dict[str, Any]:
    type_def = ctx.types.get(type_ref) or {}
    kind = type_def.get("kind")
    operands: List[Dict[str, Any]] = []
    if kind == "struct":
        for m in type_def.get("members") or []:
            path = f"{prefix}.{m['name']}" if prefix else m["name"]
            operands.append(_value_at_path(bind_map, path, m["typeRef"], ctx))
    elif kind == "vector":
        elem_ref = type_def.get("elementRef", "")
        for i in range(int(type_def.get("size", 0))):
            path = f"{prefix}.{i}" if prefix else str(i)
            operands.append(_value_at_path(bind_map, path, elem_ref, ctx))
    return {"opcode": "'{", "operands": operands}


def _variable_value(var: Dict[str, Any], ctx: "_TypesCtx") -> Optional[Dict[str, Any]]:
    bind = (var.get("bind") or {}).get("verilog")
    if bind is None:
        return None
    type_ref = var.get("typeRef", "")
    if (ctx.types.get(type_ref) or {}).get("kind") in ("struct", "vector"):
        return _aggregate_value(bind, type_ref, "", ctx) if isinstance(bind, dict) else None
    return _scalar_value(bind, type_ref, ctx) or None


def _variable_hdl_loc(var: Dict[str, Any], target_files: List[str], hdl_fallback: Optional[str],
                      files: _FileInfo) -> Optional[Dict[str, Any]]:
    """A variable's own verilog-side location: either the sibling `loc`
    the upgrader attaches to an aggregate's `bind` container, or the
    `loc` embedded in a scalar binding's `{"signal", "loc"}` form."""
    bind_container = var.get("bind") or {}
    loc = bind_container.get("loc")
    if loc is None:
        verilog_bind = bind_container.get("verilog")
        if isinstance(verilog_bind, dict):
            loc = verilog_bind.get("loc")
    return _loc_to_hgldd2(loc, target_files, True, hdl_fallback, files)


def _first_leaf_sig(value: Optional[Dict[str, Any]]) -> str:
    """Vec naming: HGLDD names a vector port_var after its first flat
    element sig_name."""
    while value and value.get("operands"):
        first = value["operands"][0]
        if "sig_name" in first:
            return first["sig_name"]
        value = first
    return ""


# ---- enum index -------------------------------------------------------


def _populate_enum_index(doc_v2: Dict[str, Any], ctx: "_TypesCtx"
                         ) -> Tuple[Dict[str, int], Dict[str, Dict[str, Any]]]:
    """Mirrors v1's `_populate_enum_index`, walking v2 modules/variables
    instead of v1 scopes/variables."""
    enum_id_by_type: Dict[str, int] = {}
    enum_defs_by_module: Dict[str, Dict[str, Any]] = {}
    next_eid = 0

    def register(type_ref: str, defs: Dict[str, Any]) -> None:
        nonlocal next_eid
        if not type_ref:
            return
        td = ctx.types.get(type_ref) or {}
        if td.get("kind") != "enum":
            return
        eid = enum_id_by_type.get(type_ref)
        if eid is None:
            eid = next_eid
            enum_id_by_type[type_ref] = eid
            next_eid += 1
        defs[str(eid)] = dict(td.get("variants") or {})

    for mid, mod in doc_v2["modules"].items():
        defs: Dict[str, Any] = {}
        for var in (mod.get("variables") or {}).values():
            stack = [var.get("typeRef", "")]
            seen: set = set()
            while stack:
                tref = stack.pop()
                if not tref or tref in seen:
                    continue
                seen.add(tref)
                register(tref, defs)
                cur = ctx.types.get(tref) or {}
                if cur.get("kind") == "struct":
                    stack.extend(m.get("typeRef", "") for m in cur.get("members") or [])
                elif cur.get("kind") == "vector":
                    stack.append(cur.get("elementRef", ""))
        if defs:
            enum_defs_by_module[mid] = defs
    return enum_id_by_type, enum_defs_by_module


# ---- struct/vector representative-location selection -------------------


def _location_candidate_types(type_ref: str, ctx: "_TypesCtx",
                              seen: Optional[set] = None) -> Iterator[str]:
    """Every type id that, in v1, would have had its OWN pool Variable
    (the root itself, or a `bindKind: synthetic` subfield) -- i.e. every
    struct-member level, recursively. A vector's `elementRef` is NOT
    walked into: v1 never emits a synthetic Variable for a per-index
    vector-of-struct element (its leaves are reached only through the
    flat exprRef/memberRefs chain), so no v2 root variable should
    manufacture a location for that level either -- only the vector's
    own type id (for the Vec[Bundle] `vec_loc` lookup) is yielded."""
    seen = seen if seen is not None else set()
    if not type_ref or type_ref in seen:
        return
    seen.add(type_ref)
    yield type_ref
    td = ctx.types.get(type_ref) or {}
    if td.get("kind") == "struct":
        for m in td.get("members") or []:
            yield from _location_candidate_types(m.get("typeRef", ""), ctx, seen)


def _collect_location_candidates(
    doc_v2: Dict[str, Any], ctx: "_TypesCtx"
) -> Tuple[Dict[str, List[Tuple[Dict[str, Any], str, Optional[str]]]],
           Dict[str, Tuple[Dict[str, Any], str]]]:
    """`struct_candidates[struct_id] = [(loc, module_id, root_binding), ...]`
    (every candidate; the winner is `min` by `(str(module_id), beginLine,
    beginColumn)`, mirroring v1). `vec_candidates[vector_id] = (loc,
    module_id)` (first found, mirroring v1's first-match `vec_loc`
    search -- module dict order, then variable dict order, then
    `_location_candidate_types` order, all of which reproduce v1's
    document order)."""
    struct_candidates: Dict[str, List[Tuple[Dict[str, Any], str, Optional[str]]]] = {}
    vec_candidates: Dict[str, Tuple[Dict[str, Any], str]] = {}
    for mid, mod in doc_v2["modules"].items():
        for var in (mod.get("variables") or {}).values():
            root_loc = (var.get("source") or {}).get("loc")
            if not root_loc:
                continue
            root_binding = (var.get("source") or {}).get("binding")
            for tref in _location_candidate_types(var.get("typeRef", ""), ctx):
                td = ctx.types.get(tref) or {}
                kind = td.get("kind")
                if kind == "struct":
                    struct_candidates.setdefault(tref, []).append(
                        (root_loc, mid, root_binding))
                elif kind == "vector" and tref not in vec_candidates:
                    vec_candidates[tref] = (root_loc, mid)
    return struct_candidates, vec_candidates


def _pick_struct_winner(
    candidates: List[Tuple[Dict[str, Any], str, Optional[str]]]
) -> Tuple[Dict[str, Any], str, Optional[str]]:
    def key(item: Tuple[Dict[str, Any], str, Optional[str]]) -> Tuple[str, int, int]:
        loc, mid, _ = item
        return (str(mid), int(loc.get("beginLine", 0) or 0), int(loc.get("beginColumn", 0) or 0))
    return min(candidates, key=key)


# ---- struct objects ---------------------------------------------------


def _struct_objects(doc_v2: Dict[str, Any], ctx: "_TypesCtx",
                    enum_id_by_type: Dict[str, int],
                    files: _FileInfo) -> List[Dict[str, Any]]:
    src_files = doc_v2.get("source", {}).get("files", [])
    struct_candidates, vec_candidates = _collect_location_candidates(doc_v2, ctx)

    struct_loc: Dict[str, Optional[Dict[str, Any]]] = {}
    struct_binding: Dict[str, Optional[str]] = {}
    for tid, cands in struct_candidates.items():
        loc, _mid, binding = _pick_struct_winner(cands)
        struct_loc[tid] = _loc_to_hgldd2(loc, src_files, False, None, files)
        struct_binding[tid] = binding

    vec_by_elem: Dict[str, List[Tuple[str, int, Optional[Dict[str, Any]]]]] = {}
    for vtid, vd in ctx.types.items():
        if vd.get("kind") != "vector":
            continue
        eref = vd.get("elementRef", "")
        if not eref or (ctx.types.get(eref) or {}).get("kind") != "struct":
            continue
        size = int(vd.get("size", 0))
        if size < 1:
            continue
        vec_loc = None
        if (cand := vec_candidates.get(vtid)) is not None:
            vec_loc = _loc_to_hgldd2(cand[0], src_files, False, None, files)
        vec_by_elem.setdefault(eref, []).append((vtid, size, vec_loc))

    out = []
    for tid in _topo_sorted_struct_ids(ctx):
        d = ctx.types.get(tid) or {}
        if d.get("kind") != "struct":
            continue
        loc = struct_loc.get(tid)
        binding = struct_binding.get(tid)
        port_vars = []
        for m in d.get("members") or []:
            pv = {"var_name": m.get("name", ""), **_type_description(m.get("typeRef", ""), ctx)}
            if loc:
                pv["hgl_loc"] = loc
            if (eid := enum_id_by_type.get(m.get("typeRef", ""))) is not None:
                pv["enum_def_ref"] = eid
            if binding and (slti := _variable_source_lang_type_info(
                    {"binding": binding}, m.get("typeRef", ""), ctx)):
                pv["source_lang_type_info"] = slti
            port_vars.append(pv)
        obj: Dict[str, Any] = {"kind": "struct", "obj_name": tid, "port_vars": port_vars}
        if loc:
            obj["hgl_loc"] = loc
        out.append(obj)

        for vtid, size, vec_loc in vec_by_elem.get(tid, []):
            elem_pvs = []
            for m in d.get("members") or []:
                epv = {"var_name": m.get("name", ""),
                      **_type_description(m.get("typeRef", ""), ctx)}
                if l := (vec_loc or loc):
                    epv["hgl_loc"] = l
                if (eid := enum_id_by_type.get(m.get("typeRef", ""))) is not None:
                    epv["enum_def_ref"] = eid
                elem_pvs.append(epv)
            base = {"kind": "struct", "obj_name": vtid, "port_vars": elem_pvs}
            if vec_loc:
                base["hgl_loc"] = vec_loc
            out.append(base)
            for i in range(size - 1):
                elem_obj = {"kind": "struct", "obj_name": f"{vtid}_{i}", "port_vars": elem_pvs}
                if vec_loc:
                    elem_obj["hgl_loc"] = vec_loc
                out.append(elem_obj)
    return out


# ---- module / variable objects -----------------------------------------


def _variable_port_var(name: str, var: Dict[str, Any], ctx: "_TypesCtx",
                       enum_id_by_type: Dict[str, int], src_files: List[str],
                       target_files: List[str], hdl_fallback: Optional[str],
                       files: _FileInfo) -> Dict[str, Any]:
    value = _variable_value(var, ctx)
    var_source = var.get("source") or {}
    var_name = var_source.get("name") or name
    if (ctx.types.get(var.get("typeRef", "")) or {}).get("kind") == "vector":
        if first := _first_leaf_sig(value):
            var_name = first
    out: Dict[str, Any] = {"var_name": var_name}
    out.update(_type_description(var.get("typeRef", ""), ctx))
    if value:
        out["value"] = value
    if loc := _loc_to_hgldd2(var_source.get("loc"), src_files, False, None, files):
        out["hgl_loc"] = loc
    if loc := _variable_hdl_loc(var, target_files, hdl_fallback, files):
        out["hdl_loc"] = loc
    if slti := _root_variable_source_lang_type_info(var_source, var.get("typeRef", ""), ctx):
        out["source_lang_type_info"] = slti
    if (eid := enum_id_by_type.get(var.get("typeRef", ""))) is not None:
        out["enum_def_ref"] = eid
    return out


def _module_object(mid: str, mod: Dict[str, Any], ctx: "_TypesCtx",
                   enum_id_by_type: Dict[str, int],
                   enum_defs_by_module: Dict[str, Dict[str, Any]],
                   doc_v2: Dict[str, Any], src_files: List[str],
                   target_files: List[str], hdl_fallback: Optional[str],
                   files: _FileInfo) -> Dict[str, Any]:
    source = mod.get("source") or {}
    target = mod.get("target") or {}
    out: Dict[str, Any] = {
        "kind": "module",
        "obj_name": source.get("name") or mid,
        "module_name": mid,
    }
    if mod.get("kind") == "extmodule":
        out["isExtModule"] = 1
    if loc := _loc_to_hgldd2(source.get("loc"), src_files, False, None, files):
        out["hgl_loc"] = loc
    if loc := _loc_to_hgldd2(target.get("loc"), target_files, True, hdl_fallback, files):
        out["hdl_loc"] = loc
    if slti := _module_source_lang_type_info(source):
        out["source_lang_type_info"] = slti
    if defs := enum_defs_by_module.get(mid):
        out["enum_defs"] = defs

    out["port_vars"] = [
        _variable_port_var(name, var, ctx, enum_id_by_type, src_files,
                          target_files, hdl_fallback, files)
        for name, var in (mod.get("variables") or {}).items()
    ]

    children = []
    for as_name, inst in (mod.get("instances") or {}).items():
        inst_source = inst.get("source") or {}
        inst_target = inst.get("target") or {}
        # The referenced module's own target.name is gone (it always
        # equalled the module key); moduleRef already is that key.
        target_name = inst_target.get("name") or inst.get("moduleRef")
        child: Dict[str, Any] = {"name": as_name, "obj_name": inst.get("moduleRef"),
                                 "module_name": target_name}
        if loc := _loc_to_hgldd2(inst_source.get("loc"), src_files, False, None, files):
            child["hgl_loc"] = loc
        if loc := _loc_to_hgldd2(inst_target.get("loc"), target_files, True, hdl_fallback, files):
            child["hdl_loc"] = loc
        children.append(child)
    out["children"] = children
    return out


def convert(doc_v2: Dict[str, Any]) -> Dict[str, Any]:
    ctx = _TypesCtx(types=doc_v2.get("types", {}))
    files = _FileInfo()

    src_files = doc_v2.get("source", {}).get("files", [])
    target = doc_v2.get("target", {})
    target_files = target.get("files", [])

    hdl_path = None
    for f in target_files or []:
        if f:
            hdl_path = str(f)
            break
    if hdl_path is None:
        referenced = {inst.get("moduleRef")
                     for mod in doc_v2["modules"].values()
                     for inst in (mod.get("instances") or {}).values()}
        roots = [mid for mid in doc_v2["modules"] if mid not in referenced]
        if roots:
            hdl_name = roots[0]
            lang = target.get("language", "")
            if lang and lang not in _HDL_LANGUAGE_EXTENSIONS:
                raise HGLDD2ConversionError(f"unknown HDL language '{lang}'")
            hdl_path = hdl_name + _HDL_LANGUAGE_EXTENSIONS.get(lang, ".sv")
    if hdl_path:
        files.add_hdl(hdl_path)

    enum_id_by_type, enum_defs_by_module = _populate_enum_index(doc_v2, ctx)

    objects = _struct_objects(doc_v2, ctx, enum_id_by_type, files)
    objects.extend(
        _module_object(mid, mod, ctx, enum_id_by_type, enum_defs_by_module, doc_v2,
                       src_files, target_files, hdl_path, files)
        for mid, mod in doc_v2["modules"].items())

    hdl_start = files.hdl_start if files.hdl_start is not None else len(files.ordered)
    hgldd: Dict[str, Any] = {"version": "1.0", "file_info": list(files.ordered)}
    if files.ordered:
        hgldd["hdl_file_index"] = hdl_start + 1
    return {"HGLDD": hgldd, "objects": objects}
