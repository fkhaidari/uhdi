"""UHDI v2 layout -> HGLDD 1.0, read straight from `original` / `generated` /
`bindings` / `types`. The document is never rebuilt in the older shapes.

The v2 layout keeps three things apart:

* `original` -- pre-Dedup FIRRTL modules and their variables;
* `generated` -- Verilog modules (declaration `loc`, extern kind);
* `bindings` -- which source lands in which target, and what Verilog
  signal carries each variable.

A target module becomes one HGLDD object (one per source after Dedup,
see below):

* Variables come from the *unscoped* bindings of the target, each joined
  with `original[binding.original].vars` (hgl_loc, type, class info) and
  `binding.vars` (value) and `generated[...].signals` (hdl_loc). A
  variable is named by its `name` when it has one, else its key. Order is
  the order of the source variables. `{"$optimized_out": ...}` or a missing entry
  gives a variable without `value`. A `$op` (expr profile) becomes the
  HGLDD expression with that opcode.
* An instance is a source variable whose type has `kind: "module"`, or a
  vector of them. It is not a port variable. A `{"$inst": I, "module": T}`
  value makes a child `{name (= I), obj_name (= the type's source module),
  module_name (= T), hgl_loc (= the variable's loc)}`. tywaves finds the
  instance's scope in the waveform by `name` and ignores `hdl_obj_name`, so
  the name is the Verilog one, as native HGLDD writes it.
* A *scoped* binding (`inline_path`, the instance names inside the target) is
  an inlined module. It becomes a nested child `{name, hgl_loc,
  port_vars, children}` named by each path component, as the pool
  converter does for `dbg.scope` inline scopes. `hgl_loc` is the loc of
  the instance variable whose value is `{"$inline": path}`.
* The class of a module (`name` and `x-chisel.params`) becomes its
  `source_lang_type_info`. It is read from the `module` type of the
  variable that instantiates the module in its parent, and from the source
  module when no instance names a single class (the root, or instances
  that differ).
* The `source_lang_type_info` of a variable or a struct field is
  `<binding>[<type name>]`, as native HGLDD writes it. The binding comes
  from the variable: `IO` for a port, `Reg` and `Mem` from `storage`,
  `OpResult` for a node, `Wire` otherwise. A field takes the binding of the
  variable. The type name is the type's `name`, or one derived from its
  kind (`UInt<8>`, `Clock`, `Bool[2]`). Parameters come from
  `x-chisel.params` of the type.

Dedup: several unscoped bindings can point at one target. Each becomes
its own object, `obj_name` = the source and `module_name` = the target,
with only that source's variables. An instance's type names the source,
so it picks which object its child entry names.

Locations are `[file, line, col]` or `[file, line, col, endLine,
endCol]` into `files`; a column of 0 means "no column". Source paths lose
their leading `/`, as in the other HGLDD converters.

Known differences from the tree-shaped converter (`uhdi2.to_hgldd`), by
design of the layout: no instance-level `hdl_loc` (the layout locates
modules, not instances), no synthetic leaf ports. An extern module has
ports only when a binding gives their values.

Known differences from the pool converter (`uhdi_to_hgldd`) run on the
layout-1 document of the same design:

* No `<Module>#instance` struct objects and no instance variables
  (`sub0`, `alu`): the layout drops instance port maps. With them go the
  `enum_defs` a parent got only through an instance's ports (`Cpu` in
  rfc-alu); a module lists the enums of its own variables.
* `clock` / `reset` stay. The pool converter drops a port whose signal
  name is a leaf of any aggregate in the whole document, so a parent's
  instance port map (`clock -> clock`) hides them in both modules.
* A struct's `hgl_loc` is the declaration of the first variable of that
  type, by source module name and line (`val io = IO(...)`). The pool
  converter also sees the instance port maps of the parent and can pick
  the instantiation site instead.
* Synthetic ports that duplicate an aggregate leaf by name (`io_out`,
  `io_count`) are gone, even when the field binds to another signal or
  to none.
* A layer-bound module (`*_Verification_*`) gets an object like any
  other target; the pool converter drops the object but keeps the
  child that points at it.
* Enum values are listed by value, as the pool converter does.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterator, List, Optional, Tuple

from uhdi_common.context import ConversionError
from uhdi_to_hgldd.convert import _FileInfo
from . import require_v2
from .types import named_types
from .walk import const_bits
from .walk import check_bindings

EXT_KEY = "x-chisel"


class HGLDDV2ConversionError(ConversionError):
    pass


Types = Dict[str, Any]


# ---- locations ---------------------------------------------------------


def _hgldd_loc(loc: Optional[List[int]], files_pool: List[Dict[str, Any]],
               is_hdl: bool, hdl_fallback: Optional[str],
               files: _FileInfo) -> Optional[Dict[str, Any]]:
    if not loc or len(loc) < 2:
        return None
    idx = loc[0]
    if not (0 <= idx < len(files_pool)):
        return None
    raw_path = str(files_pool[idx].get("path", ""))
    if is_hdl and not raw_path and hdl_fallback:
        raw_path = hdl_fallback
    if not raw_path:
        return None
    if not is_hdl:
        raw_path = raw_path.lstrip("/")
    add = files.add_hdl if is_hdl else files.add_source
    out: Dict[str, Any] = {"file": add(raw_path) + 1}
    out["begin_line"] = loc[1]
    out["end_line"] = loc[3] if len(loc) > 3 else loc[1]
    if len(loc) > 2 and loc[2]:
        out["begin_column"] = loc[2]
        out["end_column"] = loc[4] if len(loc) > 4 else loc[2]
    return out


# ---- types --------------------------------------------------------------


def _type_description(tref: str, types: Types) -> Dict[str, Any]:
    t = types.get(tref)
    if t is None:
        return {"type_name": "logic"}
    kind = t.get("kind")
    if kind == "struct":
        return {"type_name": tref}
    if kind == "vector":
        elem_ref = t.get("elem", "")
        size = int(t.get("size", 0))
        # Vec[Bundle]: HGLDD names the type after the last numbered
        # element object (<vec>_<size-2>), or the base name for size 1.
        if (types.get(elem_ref) or {}).get("kind") == "struct" and size > 0:
            name = f"{tref}_{size - 2}" if size >= 2 else tref
            return {"type_name": name, "unpacked_range": [size - 1, 0]}
        elem = dict(_type_description(elem_ref, types))
        if size > 0:
            elem["unpacked_range"] = [size - 1, 0, *(elem.get("unpacked_range") or [])]
        return elem
    if kind == "enum":
        return _type_description(t.get("base", ""), types)
    # Any other kind, including one this reader does not know, is its bits.
    width = int(t.get("width", 1))
    return {"type_name": "logic"} if width <= 1 else \
           {"type_name": "logic", "packed_range": [width - 1, 0]}


def _topo_structs(types: Types) -> List[str]:
    """Struct ids, inner before outer."""
    visiting: set = set()
    visited: set = set()
    order: List[str] = []

    def visit(tid: str) -> None:
        if tid in visited:
            return
        if tid in visiting:
            raise HGLDDV2ConversionError(
                f"cycle in type pool: '{tid}' references itself transitively")
        t = types.get(tid)
        if t is None:
            return
        kind = t.get("kind")
        if kind == "struct":
            visiting.add(tid)
            for f in t.get("fields") or []:
                visit(f.get("type", ""))
            visiting.discard(tid)
            visited.add(tid)
            order.append(tid)
        elif kind == "vector":
            visiting.add(tid)
            visit(t.get("elem", ""))
            visiting.discard(tid)
            visited.add(tid)

    for tid in types:
        visit(tid)
    return order


def _hgldd_params(params: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for p in params:
        q = dict(p)
        v = q.get("value")
        if v is not None and not isinstance(v, str):
            q["value"] = json.dumps(v)
        out.append(q)
    return out


def _class_info(entry: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The class of a `module` type or a source module."""
    if not entry or not entry.get("name"):
        return None
    out: Dict[str, Any] = {"type_name": entry["name"]}
    if params := (entry.get(EXT_KEY) or {}).get("params"):
        out["params"] = _hgldd_params(params)
    return out


def _binding(var: Dict[str, Any]) -> str:
    """The Chisel binding of a variable, as native HGLDD names it."""
    if var.get("dir"):
        return "IO"
    if (storage := var.get("storage")) == "reg":
        return "Reg"
    if storage == "mem":
        return "Mem"
    return "OpResult" if var.get("node") else "Wire"


_KIND_NAMES = {"clock": "Clock", "reset": "Reset", "asyncreset": "AsyncReset"}


def _type_name(tref: str, types: Types) -> Optional[str]:
    """The source name of a type, derived from its kind when it has none."""
    t = types.get(tref) or {}
    if name := t.get("name"):
        return name
    kind = t.get("kind")
    if kind == "uint":
        return f"UInt<{t.get('width')}>"
    if kind == "sint":
        return f"SInt<{t.get('width')}>"
    if kind == "vector":
        elem = _type_name(t.get("elem", ""), types)
        return f"{elem}[{t.get('size', 0)}]" if elem else None
    return _KIND_NAMES.get(kind)


def _var_type_info(binding: str, tref: str,
                   types: Types) -> Optional[Dict[str, Any]]:
    """`binding[TypeName]` of a variable, params from the type."""
    t = types.get(tref) or {}
    name = _type_name(tref, types)
    if not name:
        return None
    out: Dict[str, Any] = {"type_name": f"{binding}[{name}]"}
    params = (t.get(EXT_KEY) or {}).get("params")
    if not params and t.get("kind") == "vector":
        # The parameters of Chisel's `Vec(length, gen)`, as native HGLDD
        # lists them.
        params = [{"name": "gen", "type": "=> T"},
                  {"name": "length", "type": "Int",
                   "value": str(t.get("size", 0))}]
    if params:
        out["params"] = _hgldd_params(params)
    return out


# ---- values -------------------------------------------------------------


def _scalar(v: Any, tref: str, types: Types) -> Dict[str, Any]:
    if isinstance(v, str):
        return {"sig_name": v}
    if isinstance(v, dict) and "$zero_width" in v:
        # HGLDD has no zero-width value; EmitHGLDD writes one zero bit.
        return {"bit_vector": "0"}
    if isinstance(v, dict) and "$slice" in v:
        return {"opcode": "[]", "operands": [
            {"sig_name": v["$slice"]},
            {"integer_num": v["hi"]}, {"integer_num": v["lo"]}]}
    if isinstance(v, dict) and "$const" in v:
        c = v["$const"]
        if isinstance(c, str):
            # HGLDD reads the width from the string.
            bits = const_bits(c)
            return {} if bits is None else {"bit_vector": bits}
        width = int((types.get(tref) or {}).get("width", 0))
        return ({"bit_vector": format(c & ((1 << width) - 1), f"0{width}b")}
                if width > 0 else {"integer_num": c})
    if isinstance(v, dict) and "$op" in v:
        # The expr profile uses HGLDD's opcodes; a plain number is a count
        # or a bit index. An operand without a value leaves the whole
        # expression without one, except a branch of `?:`.
        operands = [
            {"integer_num": a} if isinstance(a, int) else _scalar(a, "", types)
            for a in v.get("args") or []]
        if v["$op"] == "?:" and len(operands) == 3 and operands[0] != {}:
            # `?:` needs a value only in the branch it selects, and HGLDD has
            # no operand for "no value". A branch without one takes the
            # other branch's operand: the expression is right whenever the
            # selected branch is the known one, which is when it has a value.
            _, a, b = operands
            if a == {} and b != {}:
                operands[1] = b
            elif b == {} and a != {}:
                operands[2] = a
        return {} if {} in operands else {"opcode": v["$op"], "operands": operands}
    return {}


def _is_none(v: Any) -> bool:
    return v is None or (isinstance(v, dict) and "$optimized_out" in v)


def _value(v: Any, tref: str, types: Types) -> Dict[str, Any]:
    t = types.get(tref) or {}
    kind = t.get("kind")
    if kind == "struct":
        operands = [_value(v.get(f["name"]) if isinstance(v, dict) else None,
                           f["type"], types) for f in t.get("fields") or []]
        return {"opcode": "'{", "operands": operands}
    if kind == "vector":
        elem = t.get("elem", "")
        operands = []
        for i in range(int(t.get("size", 0))):
            if isinstance(v, list):
                e = v[i] if i < len(v) else None
            elif isinstance(v, dict):
                e = v.get(str(i))
            else:
                e = None
            operands.append(_value(e, elem, types))
        return {"opcode": "'{", "operands": operands}
    if not _is_none(v):
        if value := _scalar(v, tref, types):
            return value
    # HGLDD has no empty operand, and readers reject one. An unknown leaf is
    # x bits of its width, so the aggregate keeps its shape.
    if t.get("kind") == "enum":
        t = types.get(t.get("base", "")) or {}
    return {"bit_vector": "x" * max(int(t.get("width", 1)), 1)}


def _top_value(v: Any, tref: str, types: Types) -> Optional[Dict[str, Any]]:
    if _is_none(v):
        return None
    kind = (types.get(tref) or {}).get("kind")
    if kind == "vector" and isinstance(v, str):
        # One array signal holds the whole vector, such as a memory. The
        # waveform has a trace per element, `v[i]` as Verilator dumps it;
        # tywaves finds no trace for the whole array.
        size = int((types.get(tref) or {}).get("size", 0))
        return {"opcode": "'{", "operands": [{"sig_name": f"{v}[{i}]"}
                                              for i in range(size)]}
    if kind in ("struct", "vector"):
        return _value(v, tref, types) if isinstance(v, (dict, list)) else None
    return _scalar(v, tref, types) or None


# ---- document access ----------------------------------------------------


def _type_refs(tref: str, types: Types) -> Iterator[str]:
    """`tref` and every type nested under it."""
    stack, seen = [tref], set()
    while stack:
        cur = stack.pop()
        if not cur or cur in seen:
            continue
        seen.add(cur)
        yield cur
        t = types.get(cur) or {}
        if t.get("kind") == "struct":
            stack.extend(f.get("type", "") for f in t.get("fields") or [])
        elif t.get("kind") == "vector":
            stack.append(t.get("elem", ""))


def _location_types(tref: str, types: Types,
                    seen: Optional[set] = None) -> Iterator[str]:
    """`tref` and every struct-member type, not walking into vectors."""
    seen = seen if seen is not None else set()
    if not tref or tref in seen:
        return
    seen.add(tref)
    yield tref
    t = types.get(tref) or {}
    if t.get("kind") == "struct":
        for f in t.get("fields") or []:
            yield from _location_types(f.get("type", ""), types, seen)


class _Doc:
    def __init__(self, doc: Dict[str, Any]):
        require_v2(doc)
        doc = named_types(doc)
        self.types: Types = doc.get("types", {})
        self.files: List[Dict[str, Any]] = doc.get("files", [])
        self.source: Dict[str, Any] = doc.get("original", {})
        self.target: Dict[str, Any] = doc.get("generated", {})
        self.info = _FileInfo()
        self.unscoped: Dict[str, List[Dict[str, Any]]] = {}
        self.scoped: Dict[str, List[Dict[str, Any]]] = {}
        self.doc_bindings: List[Dict[str, Any]] = doc.get("bindings", [])
        self._infos: Optional[Dict[Tuple[str, str], List[Dict[str, Any]]]] = None
        check_bindings(doc.get("bindings", []))
        for b in doc.get("bindings", []):
            (self.scoped if b.get("inline_path") else self.unscoped).setdefault(
                b["generated"], []).append(b)

    def inst_type(self, var: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """The `module` type of a variable, or of the elements of a vector
        of instances."""
        tref, seen = var.get("type", ""), set()
        while tref not in seen:
            seen.add(tref)
            t = self.types.get(tref) or {}
            if t.get("kind") == "module":
                return t
            if t.get("kind") != "vector":
                return None
            tref = t.get("elem", "")
        return None

    def inst_module(self, var: Dict[str, Any]) -> Optional[str]:
        """The source module a variable instantiates."""
        return (self.inst_type(var) or {}).get("module")

    def is_instance(self, var: Dict[str, Any]) -> bool:
        return self.inst_module(var) is not None

    def instances(self, bindings: List[Dict[str, Any]]
                  ) -> Iterator[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
        """(name, source var, binding value) of every instance in `bindings`.
        The name is the variable's, with `_<index>` for each element of a
        vector of instances."""
        for b in bindings:
            svars = (self.source.get(b["original"]) or {}).get("vars") or {}
            for vname, value in (b.get("vars") or {}).items():
                var = svars.get(vname)
                if var is not None and self.is_instance(var):
                    yield from self._elements(var.get("name") or vname, var,
                                              var.get("type", ""), value)

    def _elements(self, name: str, var: Dict[str, Any], tref: str, value: Any
                  ) -> Iterator[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
        t = self.types.get(tref) or {}
        if t.get("kind") == "vector":
            for i in range(int(t.get("size", 0))):
                if isinstance(value, list):
                    sub = value[i] if i < len(value) else None
                elif isinstance(value, dict):
                    sub = value.get(str(i))
                else:
                    sub = None
                yield from self._elements(f"{name}_{i}", var, t.get("elem", ""),
                                          sub)
        elif isinstance(value, dict):
            yield name, var, value

    def class_infos(self, source: str, target: str) -> List[Dict[str, Any]]:
        """Distinct class records of the variables that instantiate `source`
        as `target`, in document order."""
        if self._infos is None:
            self._infos = {}
            for b in self.doc_bindings:
                for _, var, value in self.instances([b]):
                    info = _class_info(self.inst_type(var))
                    if "$inst" in value and info:
                        key = (self.inst_module(var), value.get("module"))
                        records = self._infos.setdefault(key, [])
                        if info not in records:
                            records.append(info)
        return self._infos.get((source, target), [])

    def src_loc(self, loc: Optional[List[int]]) -> Optional[Dict[str, Any]]:
        return _hgldd_loc(loc, self.files, False, None, self.info)

    def hdl_loc(self, loc: Optional[List[int]],
                fallback: Optional[str]) -> Optional[Dict[str, Any]]:
        return _hgldd_loc(loc, self.files, True, fallback, self.info)



# ---- enums ---------------------------------------------------------------


def _enum_index(d: _Doc) -> Tuple[Dict[str, int], Dict[str, Dict[str, Any]]]:
    ids: Dict[str, int] = {}
    defs_by_target: Dict[str, Dict[str, Any]] = {}
    for tname in d.target:
        defs: Dict[str, Any] = {}
        for b in d.unscoped.get(tname, []) + d.scoped.get(tname, []):
            for var in (d.source.get(b["original"], {}).get("vars") or {}).values():
                for tref in _type_refs(var.get("type", ""), d.types):
                    t = d.types.get(tref) or {}
                    if t.get("kind") != "enum":
                        continue
                    eid = ids.setdefault(tref, len(ids))
                    values = sorted((t.get("values") or {}).items(), key=lambda lv: int(lv[1]))
                    defs[str(eid)] = {str(v): lab for lab, v in values}
        if defs:
            defs_by_target[tname] = defs
    return ids, defs_by_target


# ---- struct objects ------------------------------------------------------


def _struct_objects(d: _Doc, enum_ids: Dict[str, int]) -> List[Dict[str, Any]]:
    structs: Dict[str, List[Tuple[List[int], str, Optional[str]]]] = {}
    vecs: Dict[str, Tuple[List[int], str, Optional[str]]] = {}
    for skey, smod in d.source.items():
        for var in (smod.get("vars") or {}).values():
            loc = var.get("loc")
            if not loc:
                continue
            binding = _binding(var)
            for tref in _location_types(var.get("type", ""), d.types):
                kind = (d.types.get(tref) or {}).get("kind")
                if kind == "struct":
                    structs.setdefault(tref, []).append((loc, skey, binding))
                elif kind == "vector" and tref not in vecs:
                    vecs[tref] = (loc, skey, binding)

    struct_loc: Dict[str, Any] = {}
    struct_binding: Dict[str, str] = {}
    for tid, cands in structs.items():
        loc, _skey, binding = min(
            cands, key=lambda c: (str(c[1]), int(c[0][1]), int(c[0][2]) if len(c[0]) > 2 else 0))
        struct_loc[tid] = d.src_loc(loc)
        struct_binding[tid] = binding

    vec_by_elem: Dict[str, List[Tuple[str, int, Optional[Dict[str, Any]],
                                      Optional[str]]]] = {}
    for vtid, vd in d.types.items():
        if vd.get("kind") != "vector":
            continue
        eref = vd.get("elem", "")
        if (d.types.get(eref) or {}).get("kind") != "struct":
            continue
        size = int(vd.get("size", 0))
        if size < 1:
            continue
        vloc = d.src_loc(vecs[vtid][0]) if vtid in vecs else None
        vbinding = vecs[vtid][2] if vtid in vecs else None
        vec_by_elem.setdefault(eref, []).append((vtid, size, vloc, vbinding))

    out = []
    for tid in _topo_structs(d.types):
        t = d.types[tid]
        loc = struct_loc.get(tid)
        binding = struct_binding.get(tid)
        port_vars = []
        for f in t.get("fields") or []:
            pv = {"var_name": f["name"], **_type_description(f["type"], d.types)}
            if loc:
                pv["hgl_loc"] = loc
            if (eid := enum_ids.get(f["type"])) is not None:
                pv["enum_def_ref"] = eid
            if binding and (slti := _var_type_info(binding, f["type"], d.types)):
                pv["source_lang_type_info"] = slti
            port_vars.append(pv)
        obj: Dict[str, Any] = {"kind": "struct", "obj_name": tid, "port_vars": port_vars}
        if loc:
            obj["hgl_loc"] = loc
        out.append(obj)

        for vtid, size, vloc, vbinding in vec_by_elem.get(tid, []):
            elem_pvs = []
            for f in t.get("fields") or []:
                epv = {"var_name": f["name"], **_type_description(f["type"], d.types)}
                if l := (vloc or loc):
                    epv["hgl_loc"] = l
                if (eid := enum_ids.get(f["type"])) is not None:
                    epv["enum_def_ref"] = eid
                if vbinding and (slti := _var_type_info(
                        vbinding, f["type"], d.types)):
                    epv["source_lang_type_info"] = slti
                elem_pvs.append(epv)
            base = {"kind": "struct", "obj_name": vtid, "port_vars": elem_pvs}
            if vloc:
                base["hgl_loc"] = vloc
            out.append(base)
            for i in range(size - 1):
                eo = {"kind": "struct", "obj_name": f"{vtid}_{i}", "port_vars": elem_pvs}
                if vloc:
                    eo["hgl_loc"] = vloc
                out.append(eo)
    return out


# ---- modules -------------------------------------------------------------


def _port_var(d: _Doc, name: str, var: Dict[str, Any], binding: Dict[str, Any],
              enum_ids: Dict[str, int], hdl_path: Optional[str]) -> Dict[str, Any]:
    tref = var.get("type", "")
    value = _top_value((binding.get("vars") or {}).get(name), tref, d.types)
    var_name = var.get("name") or name
    out: Dict[str, Any] = {"var_name": var_name}
    out.update(_type_description(tref, d.types))
    if value:
        out["value"] = value
    if loc := d.src_loc(var.get("loc")):
        out["hgl_loc"] = loc
    raw = (binding.get("vars") or {}).get(name)
    signals = (d.target.get(binding.get("generated", "")) or {}).get("signals") or {}
    vloc = (signals.get(raw) or {}).get("loc") if isinstance(raw, str) else None
    if loc := d.hdl_loc(vloc, hdl_path):
        out["hdl_loc"] = loc
    if slti := _var_type_info(_binding(var), tref, d.types):
        out["source_lang_type_info"] = slti
    if (eid := enum_ids.get(tref)) is not None:
        out["enum_def_ref"] = eid
    return out


def _module_class_info(d: _Doc, bindings: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The class of the module: the one record all instances agree on, else
    the source module's own, which is all a root has."""
    records = []
    for b in bindings:
        infos = d.class_infos(b["original"], b["generated"])
        info = infos[0] if len(infos) == 1 else _class_info(
            d.source.get(b["original"]))
        if info and info not in records:
            records.append(info)
    return records[0] if len(records) == 1 else None


def _inline_children(d: _Doc, tname: str, used: set, enum_ids: Dict[str, int],
                     hdl_path: Optional[str]) -> List[Dict[str, Any]]:
    suffix: Dict[str, int] = {}

    def uniquify(name: str) -> str:
        if name not in used:
            used.add(name)
            return name
        n = suffix.get(name, 0)
        while True:
            cand = f"{name}_{n}"
            n += 1
            if cand not in used:
                suffix[name] = n
                used.add(cand)
                return cand

    scoped = d.scoped.get(tname, [])
    inline_locs = {tuple(value["$inline"]): var.get("loc")
                   for _, var, value in d.instances(d.unscoped.get(tname, []) + scoped)
                   if "$inline" in value}
    roots: List[Dict[str, Any]] = []
    for b in scoped:
        level = roots
        node: Dict[str, Any] = {}
        for comp in b["inline_path"]:
            node = next((n for n in level if n["name"] == comp), None)
            if node is None:
                node = {"name": comp, "port_vars": [], "children": []}
                level.append(node)
            level = node["children"]
        if loc := d.src_loc(inline_locs.get(tuple(b["inline_path"]))):
            node["hgl_loc"] = loc
        for vname, var in (d.source.get(b["original"], {}).get("vars") or {}).items():
            if d.is_instance(var):
                continue
            pv = _port_var(d, vname, var, b, enum_ids, hdl_path)
            pv["var_name"] = uniquify(pv["var_name"])
            node["port_vars"].append(pv)
    return roots


def _ports(d: _Doc, bindings: List[Dict[str, Any]], enum_ids: Dict[str, int],
           hdl_path: Optional[str]) -> List[Dict[str, Any]]:
    port_vars: List[Dict[str, Any]] = []
    seen: set = set()
    for b in bindings:
        for vname, var in (d.source.get(b["original"], {}).get("vars") or {}).items():
            if vname in seen or d.is_instance(var):
                continue
            seen.add(vname)
            port_vars.append(_port_var(d, vname, var, b, enum_ids, hdl_path))
    return port_vars


def _module_object(d: _Doc, tname: str, bindings: List[Dict[str, Any]],
                   enum_ids: Dict[str, int],
                   enum_defs: Dict[str, Dict[str, Any]],
                   hdl_path: Optional[str]) -> Dict[str, Any]:
    tmod = d.target[tname]
    first_src = bindings[0]["original"] if bindings else tname
    out: Dict[str, Any] = {"kind": "module", "obj_name": first_src, "module_name": tname}
    hdl_loc = d.hdl_loc(tmod.get("loc"), hdl_path)
    if tmod.get("kind") == "extern":
        out["isExtModule"] = 1
        if defs := enum_defs.get(tname):
            out["enum_defs"] = defs
        out["port_vars"] = _ports(d, bindings, enum_ids, hdl_path)
        out["children"] = []
        return out
    for b in bindings:
        if loc := d.src_loc((d.source.get(b["original"]) or {}).get("loc")):
            out["hgl_loc"] = loc
            break
    if hdl_loc:
        out["hdl_loc"] = hdl_loc
    if slti := _module_class_info(d, bindings):
        out["source_lang_type_info"] = slti
    if defs := enum_defs.get(tname):
        out["enum_defs"] = defs

    port_vars = _ports(d, bindings, enum_ids, hdl_path)
    out["port_vars"] = port_vars

    children = []
    seen = set()
    for _, var, value in d.instances(bindings):
        if "$inst" not in value or value["$inst"] in seen:
            continue
        seen.add(value["$inst"])
        child: Dict[str, Any] = {"name": value["$inst"]}
        child["obj_name"] = d.inst_module(var)
        child["module_name"] = value["module"]
        if loc := d.src_loc(var.get("loc")):
            child["hgl_loc"] = loc
        children.append(child)
    used = {pv["var_name"] for pv in port_vars}
    children.extend(_inline_children(d, tname, used, enum_ids, hdl_path))
    out["children"] = children
    return out


def _hdl_path(d: _Doc) -> Optional[str]:
    for f in d.files:
        if f.get("generated") and f.get("path"):
            return str(f["path"])
    referenced = {value["module"]
                  for bs in d.unscoped.values()
                  for _, _, value in d.instances(bs) if "$inst" in value}
    roots = [t for t in d.target if t not in referenced]
    return roots[0] + ".sv" if roots else None


def convert(doc: Dict[str, Any]) -> Dict[str, Any]:
    d = _Doc(doc)
    hdl_path = _hdl_path(d)
    if hdl_path:
        d.info.add_hdl(hdl_path)
    enum_ids, enum_defs = _enum_index(d)

    objects = _struct_objects(d, enum_ids)
    for t in d.target:
        # Dedup merged several sources into this target: one object per
        # source, each with its own variables, so an instance's type
        # names the object it shows.
        bindings = d.unscoped.get(t, [])
        groups = [[b] for b in bindings] if len(bindings) > 1 else [bindings]
        objects.extend(_module_object(d, t, g, enum_ids, enum_defs, hdl_path)
                       for g in groups)

    info = d.info
    hdl_start = info.hdl_start if info.hdl_start is not None else len(info.ordered)
    hgldd: Dict[str, Any] = {"version": "1.0", "file_info": list(info.ordered)}
    if info.ordered:
        hgldd["hdl_file_index"] = hdl_start + 1
    return {"HGLDD": hgldd, "objects": objects}
