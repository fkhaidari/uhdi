"""UHDI 2.0 -> HGLDD 1.0. One pass over modules -> variables, no id parsing,
no intermediate parent maps -- the v2 shape is already module-nested.

Reuses v1's pure, ctx.types-only helpers (`_type_description`,
`_topo_sorted_struct_ids`) and the pure `_FileInfo` dataclass from
`uhdi_to_hgldd.convert` unchanged, via a minimal `_TypesCtx` shim.

Known, spec-mandated data loss vs. the v1 path (see `test/test_uhdi2.py`
for the fixture-by-fixture accounting):
  * `hdl_loc` (simulation-side location) is never emitted: v2's
    `Module.target`/`Variable` carry no verilog-side location field at
    all, only `source` (chisel-side) and `bind` (chisel-side name +
    simulation-side VALUE, never simulation-side location).
  * An instance's `hgl_loc`/`hdl_loc` are both lost: v2's `Instance` has
    no location field whatsoever (`moduleRef`/`target`/`bind` only).
  * Per-field `source_lang_type_info` on struct port_vars is lost: v1
    sources it from `bindKind: synthetic` subfield Variables, which v2
    never materializes as records (folded into the parent's `bind`).
    The same synthetic Variables also supplied the precise per-field
    "backing" hgl_loc used for unflipped members; v2 falls back to the
    struct's own representative location for every member instead.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

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


def _loc_from_source(src: Optional[Dict[str, Any]], files_pool: List[str],
                     files: _FileInfo) -> Optional[Dict[str, Any]]:
    """v2 `source.{file,line,column}` -> HGLDD hgl_loc. No end_line/
    end_column in v2 (collapsed to a single point), so begin==end."""
    src = src or {}
    if "file" not in src:
        return None
    try:
        idx = int(src["file"])
    except (TypeError, ValueError):
        return None
    if not (0 <= idx < len(files_pool)):
        return None
    path = str(files_pool[idx]).lstrip("/")
    out: Dict[str, Any] = {"file": files.add_source(path) + 1}
    if "line" in src:
        out["begin_line"] = src["line"]
        out["end_line"] = src["line"]
    if "column" in src:
        out["begin_column"] = src["column"]
        out["end_column"] = src["column"]
    return out


def _source_lang_type_info(source: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    source = source or {}
    type_name = source.get("typeName")
    return {"type_name": type_name} if type_name else None


def _scalar_value(binding: Any, type_ref: str, ctx: _TypesCtx) -> Dict[str, Any]:
    if isinstance(binding, str):
        return {"sig_name": binding}
    if isinstance(binding, dict) and "constant" in binding:
        width = int((ctx.types.get(type_ref) or {}).get("width", 0))
        n = int(binding["constant"])
        return ({"bit_vector": format(n & ((1 << width) - 1), f"0{width}b")}
                if width > 0 else {"integer_num": n})
    if isinstance(binding, dict) and "bitVector" in binding:
        return {"bit_vector": binding["bitVector"]}
    return {}


def _value_at_path(bind_map: Dict[str, Any], path: str, type_ref: str,
                   ctx: _TypesCtx) -> Dict[str, Any]:
    type_def = ctx.types.get(type_ref) or {}
    if type_def.get("kind") in ("struct", "vector"):
        return _aggregate_value(bind_map, type_ref, path, ctx)
    leaf = bind_map.get(path)
    return {} if leaf is None else _scalar_value(leaf, type_ref, ctx)


def _aggregate_value(bind_map: Dict[str, Any], type_ref: str, prefix: str,
                     ctx: _TypesCtx) -> Dict[str, Any]:
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


def _variable_value(var: Dict[str, Any], ctx: _TypesCtx) -> Optional[Dict[str, Any]]:
    bind = (var.get("bind") or {}).get("verilog")
    if bind is None:
        return None
    type_ref = var.get("typeRef", "")
    if (ctx.types.get(type_ref) or {}).get("kind") in ("struct", "vector"):
        return _aggregate_value(bind, type_ref, "", ctx) if isinstance(bind, dict) else None
    return _scalar_value(bind, type_ref, ctx) or None


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


def _populate_enum_index(doc_v2: Dict[str, Any], ctx: _TypesCtx
                         ) -> tuple[Dict[str, int], Dict[str, Dict[str, Any]]]:
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


# ---- struct objects ---------------------------------------------------


def _struct_objects(doc_v2: Dict[str, Any], ctx: _TypesCtx,
                    enum_id_by_type: Dict[str, int],
                    files: _FileInfo) -> List[Dict[str, Any]]:
    src_files = doc_v2.get("source", {}).get("files", [])
    struct_loc: Dict[str, Any] = {}

    def _loc_key(loc):
        return (int(loc.get("begin_line", 0) or 0), int(loc.get("begin_column", 0) or 0))

    for mid, mod in doc_v2["modules"].items():
        for var in (mod.get("variables") or {}).values():
            tref = var.get("typeRef", "")
            if (ctx.types.get(tref) or {}).get("kind") != "struct":
                continue
            loc = _loc_from_source(var.get("source"), src_files, files)
            if loc and (tref not in struct_loc or _loc_key(loc) < _loc_key(struct_loc[tref])):
                struct_loc[tref] = loc

    vec_by_elem: Dict[str, List[tuple]] = {}
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
        for mod in doc_v2["modules"].values():
            for var in (mod.get("variables") or {}).values():
                if var.get("typeRef") == vtid:
                    vec_loc = _loc_from_source(var.get("source"), src_files, files)
                    if vec_loc:
                        break
            if vec_loc:
                break
        vec_by_elem.setdefault(eref, []).append((vtid, size, vec_loc))

    out = []
    for tid in _topo_sorted_struct_ids(ctx):
        d = ctx.types.get(tid) or {}
        if d.get("kind") != "struct":
            continue
        loc = struct_loc.get(tid)
        port_vars = []
        for m in d.get("members") or []:
            pv = {"var_name": m.get("name", ""), **_type_description(m.get("typeRef", ""), ctx)}
            if loc:
                pv["hgl_loc"] = loc
            if (eid := enum_id_by_type.get(m.get("typeRef", ""))) is not None:
                pv["enum_def_ref"] = eid
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


def _variable_port_var(name: str, var: Dict[str, Any], ctx: _TypesCtx,
                       enum_id_by_type: Dict[str, int], src_files: List[str],
                       files: _FileInfo) -> Dict[str, Any]:
    value = _variable_value(var, ctx)
    var_name = (var.get("source") or {}).get("name") or name
    if (ctx.types.get(var.get("typeRef", "")) or {}).get("kind") == "vector":
        if first := _first_leaf_sig(value):
            var_name = first
    out: Dict[str, Any] = {"var_name": var_name}
    out.update(_type_description(var.get("typeRef", ""), ctx))
    if value:
        out["value"] = value
    if loc := _loc_from_source(var.get("source"), src_files, files):
        out["hgl_loc"] = loc
    if slti := _source_lang_type_info(var.get("source")):
        out["source_lang_type_info"] = slti
    if (eid := enum_id_by_type.get(var.get("typeRef", ""))) is not None:
        out["enum_def_ref"] = eid
    return out


def _module_object(mid: str, mod: Dict[str, Any], ctx: _TypesCtx,
                   enum_id_by_type: Dict[str, int],
                   enum_defs_by_module: Dict[str, Dict[str, Any]],
                   doc_v2: Dict[str, Any], files: _FileInfo) -> Dict[str, Any]:
    src_files = doc_v2.get("source", {}).get("files", [])
    source = mod.get("source") or {}
    target = mod.get("target") or {}
    out: Dict[str, Any] = {
        "kind": "module",
        "obj_name": source.get("name") or mid,
        "module_name": target.get("name") or mid,
    }
    if mod.get("kind") == "extmodule":
        out["isExtModule"] = 1
    if loc := _loc_from_source(source, src_files, files):
        out["hgl_loc"] = loc
    if slti := _source_lang_type_info(source):
        out["source_lang_type_info"] = slti
    if defs := enum_defs_by_module.get(mid):
        out["enum_defs"] = defs

    out["port_vars"] = [
        _variable_port_var(name, var, ctx, enum_id_by_type, src_files, files)
        for name, var in (mod.get("variables") or {}).items()
    ]

    children = []
    for as_name, inst in (mod.get("instances") or {}).items():
        target_mod = doc_v2["modules"].get(inst.get("moduleRef"), {})
        target_name = ((inst.get("target") or {}).get("name")
                       or (target_mod.get("target") or {}).get("name")
                       or inst.get("moduleRef"))
        children.append({"name": as_name, "obj_name": inst.get("moduleRef"),
                         "module_name": target_name})
    out["children"] = children
    return out


def convert(doc_v2: Dict[str, Any]) -> Dict[str, Any]:
    ctx = _TypesCtx(types=doc_v2.get("types", {}))
    files = _FileInfo()

    target = doc_v2.get("target", {})
    hdl_path = None
    for f in target.get("files") or []:
        if f:
            hdl_path = str(f)
            break
    if hdl_path is None:
        referenced = {inst.get("moduleRef")
                     for mod in doc_v2["modules"].values()
                     for inst in (mod.get("instances") or {}).values()}
        roots = [mid for mid in doc_v2["modules"] if mid not in referenced]
        if roots:
            root = doc_v2["modules"][roots[0]]
            hdl_name = (root.get("target") or {}).get("name") or roots[0]
            lang = target.get("language", "")
            if lang and lang not in _HDL_LANGUAGE_EXTENSIONS:
                raise HGLDD2ConversionError(f"unknown HDL language '{lang}'")
            hdl_path = hdl_name + _HDL_LANGUAGE_EXTENSIONS.get(lang, ".sv")
    if hdl_path:
        files.add_hdl(hdl_path)

    enum_id_by_type, enum_defs_by_module = _populate_enum_index(doc_v2, ctx)

    objects = _struct_objects(doc_v2, ctx, enum_id_by_type, files)
    objects.extend(
        _module_object(mid, mod, ctx, enum_id_by_type, enum_defs_by_module, doc_v2, files)
        for mid, mod in doc_v2["modules"].items())

    hdl_start = files.hdl_start if files.hdl_start is not None else len(files.ordered)
    hgldd: Dict[str, Any] = {"version": "1.0", "file_info": list(files.ordered)}
    if files.ordered:
        hgldd["hdl_file_index"] = hdl_start + 1
    return {"HGLDD": hgldd, "objects": objects}
