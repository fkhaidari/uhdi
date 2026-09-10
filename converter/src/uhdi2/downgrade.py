"""UHDI 2.0 -> UHDI 1.0 downgrader (inverse of `upgrade.py`).

Lets the three backends that still only understand v1 shape (hgdb,
hgdb_json, pdg) accept a v2 document transparently: `uhdi_common.context.
BaseContext.from_uhdi` downgrades in memory before the existing v1 code
runs. HGLDD keeps its own native v2 path (`uhdi2/to_hgldd.py`).

Scope: v2's schema has no slot for FIRRTL-statement data at all (`Module`
allows only `kind/source/target/variables/instances/scopes`) -- `upgrade.py`
never captured `scopes.*.body`, the `expressions` pool, or top-level
`dataflow`, so this module cannot reconstruct them either. Every fixture
that uses them loses exactly that on a v2 round trip for hgdb/hgdb_json/pdg
(HGLDD never reads them, so it is unaffected); see
`test/test_uhdi2_roundtrip.py` for the precise per-fixture/per-backend
accounting.

This module fully recovers the struct/vector member tree a variable's
flattened `bind.verilog` dotted-path map came from -- one
`bindKind: synthetic` Variable per member/index, walking `types[X].kind`
the same way `upgrade.py._walk_bind` walked it forward, with each scalar
leaf's own `representations.verilog.value.sigName` reconstructed from the
matching entry of that dotted-path map. `types[X].source` (added by the
upgrade for exactly this purpose) supplies each node's authoring
`sourceLangType.typeName`/`params`. Crucially, only the tree's *root* is
ever added to its scope's `variableRefs` -- every v1 backend here reads
variables exclusively through `scope.variableRefs` (never by walking
`memberRefs` on its own), so a member's reconstructed `verilog` is only
ever seen by `upgrade()`'s own pool-wide `memberRefs` walk (needed to
rebuild `bind.verilog` for the `upgrade(downgrade(upgrade(v1))) ==
upgrade(v1)` fixed point), never by hgdb/hgdb_json/pdg directly.

This module also reconstructs the `bindKind: instance` variable an
`instances[<as>].bind.verilog` came from (inverse of `upgrade.py`'s
`_instance_bind`/`_find_instance_var`): one member per bound port key,
typed by the instantiated module's own `variables[<port>].typeRef` (v1's
type pool is flat/shared, so reusing it here is safe), an aggregate
port's own subtree rebuilt the same way `_walk_var_tree` already rebuilds
any other member tree -- just rooted at the port instead of at the whole
instance, since an instance's own top level is never flattened (see
`_emit_instance_bind`). Only recoverable when the instantiated module is
still present in the same document (`modules[moduleRef]`, top-level or
nested inline) to supply those port typeRefs -- always true for anything
the native emitter or `upgrade()` itself produces.

The reconstructed instance variable needs its own struct `typeRef`
(`upgrade.py`'s `_instance_bind` requires `types[typeRef].kind ==
"struct"`) and v2 keeps no record of what v1's own key for that type
was, so this module mints a fresh one (`<moduleRef>#instance`). That key
is new relative to the original v1 document, so
`upgrade(downgrade(upgrade(v1)))`'s `types` pool gains one extra,
unreferenced-elsewhere entry per bound instance that `upgrade(v1)` never
had -- `modules` (including `instances[*].bind`) itself is an exact
fixed point; only this one extra type-pool entry is not. See
`test_uhdi2.py::test_instance_bind_downgrade_roundtrip`, the only test
that exercises `bindKind: instance` through `downgrade()` (no fixture in
the byte-identity glob has one).

Not recoverable, and not attempted:

  * The emitter-added duplicate port record beside a member (e.g.
    `io_in_a` next to `io.in.a`, exposed independently through
    `scope.variableRefs` with its own RTL port identity). In some
    fixtures (`alu_member_refs`, `nested_bundle`) v1 gives each aggregate
    leaf such a record; in others (`bundle_io`, `vec_of_struct`) the whole
    aggregate binds through one `'{`-expression chain and no such record
    exists at all. Both shapes flatten to the *same* `bind.verilog`
    dotted-path map in v2 (confirmed: `ModuleTarget` has no port list and
    `StructMember` has no direction/port flag), so v2 cannot tell which
    one v1 used. Guessing "always recreate" was tried and produces
    spurious extra rows for the second family; guessing "never recreate"
    loses real rows for the first. This module always omits it, so the
    extra alias rows/vertices a `pool-record-tree` fixture's
    hgdb/hgdb_json/pdg output relies on are the one confirmed,
    unrecoverable-from-v2 gap left in this reconstruction (see
    `test/test_uhdi2_roundtrip.py`).
  * The original v1 `bindKind` of a non-port declared variable whose
    `sourceLangType` was itself absent (e.g. a `reg` with no recorded
    Chisel type name) -- falls back to a generic `"wire"`, which is
    what every backend here treats identically to `"node"`/`"literal"`
    anyway, except PDG's `Definition` vs `DataDefinition` vertex-kind
    split (see the round-trip tests for where this shows up, always on
    a fixture already gap-affected by the missing `body`)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

Binding = Any

_BINDING_TO_BINDKIND = {
    "reg": "reg",
    "mem": "mem",
    "syncreadmem": "mem",
    "probe": "probe",
    "rwprobe": "rwprobe",
}


def downgrade(doc_v2: Dict[str, Any]) -> Dict[str, Any]:
    """Pure, deterministic v2 -> v1 reshape. Does not mutate `doc_v2`."""
    types_v2 = doc_v2.get("types") or {}

    types_out: Dict[str, Any] = {}
    for tid, tdef in types_v2.items():
        tdef = dict(tdef or {})
        tdef.pop("source", None)
        types_out[tid] = tdef

    src = doc_v2.get("source") or {}
    tgt = doc_v2.get("target") or {}
    representations = {
        "chisel": {"kind": "source", "language": src.get("language", ""),
                  "files": list(src.get("files") or [])},
        "verilog": {"kind": "hdl", "language": tgt.get("language", ""),
                   "files": list(tgt.get("files") or [])},
    }

    module_vars = _collect_module_vars(doc_v2.get("modules") or {})

    variables_out: Dict[str, Any] = {}
    scopes_out: Dict[str, Any] = {}
    for mid, mod in (doc_v2.get("modules") or {}).items():
        _emit_module(mid, mod, None, types_v2, types_out, module_vars,
                    variables_out, scopes_out)

    return {
        "format": {"name": "uhdi", "version": "1.0"},
        "representations": representations,
        "types": types_out,
        "variables": variables_out,
        "scopes": scopes_out,
    }


# ---- scope / module ----------------------------------------------------


def _collect_module_vars(modules_v2: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Map every module id (top-level key, or nested inline `<parent>#<i>`
    -- the same scheme `_emit_module`/`upgrade.py`'s `_build_module` use
    for `moduleRef`/`scopeRef`) to its own `variables` dict, so an
    instance's `moduleRef` can look up the instantiated module's own port
    typeRefs (see `_emit_instance_bind`)."""
    out: Dict[str, Dict[str, Any]] = {}

    def walk(scope_id: str, mod: Dict[str, Any]) -> None:
        out[scope_id] = mod.get("variables") or {}
        for i, nested in enumerate(mod.get("scopes") or []):
            walk(f"{scope_id}#{i}", nested)

    for key, mod in modules_v2.items():
        walk(key, mod)
    return out


def _emit_module(scope_id: str, mod: Dict[str, Any], container: Optional[str],
                 types_v2: Dict[str, Any], types_out: Dict[str, Any],
                 module_vars: Dict[str, Dict[str, Any]],
                 variables_out: Dict[str, Any],
                 scopes_out: Dict[str, Any]) -> None:
    # Always explicit, unlike upgrade.py's own default-omission: some v1
    # readers (uhdi_to_hgldd's module-object filter) check `s.get("kind")
    # in (...)` rather than `s.get("kind", "module")`, so an absent key
    # silently drops the scope instead of falling back to the schema
    # default.
    scope: Dict[str, Any] = {"kind": mod.get("kind", "module")}

    source = mod.get("source") or {}
    target = mod.get("target") or {}
    reprs: Dict[str, Any] = {}
    chisel: Dict[str, Any] = {}
    if name := source.get("name"):
        chisel["name"] = name
    if loc := source.get("loc"):
        chisel["location"] = dict(loc)
    if type_name := source.get("typeName"):
        st: Dict[str, Any] = {"typeName": type_name}
        if params := _v1_params(source.get("params")):
            st["params"] = params
        chisel["sourceLangType"] = st
    if chisel:
        reprs["chisel"] = chisel
    verilog: Dict[str, Any] = {}
    if name := target.get("name"):
        verilog["name"] = name
    if loc := target.get("loc"):
        verilog["location"] = dict(loc)
    if verilog:
        reprs["verilog"] = verilog
    if reprs:
        scope["representations"] = reprs

    if container is not None:
        scope["containerScopeRef"] = container

    var_refs: List[str] = []
    for name, var in (mod.get("variables") or {}).items():
        var_refs.append(_emit_variable_tree(scope_id, name, var, types_v2,
                                            variables_out))

    instantiates = []
    for as_name, inst in (mod.get("instances") or {}).items():
        entry: Dict[str, Any] = {"as": as_name, "scopeRef": inst.get("moduleRef")}
        inst_reprs: Dict[str, Any] = {}
        inst_source = inst.get("source") or {}
        if loc := inst_source.get("loc"):
            inst_reprs["chisel"] = {"location": dict(loc)}
        inst_target = inst.get("target") or {}
        inst_verilog: Dict[str, Any] = {}
        if name := inst_target.get("name"):
            inst_verilog["name"] = name
        if loc := inst_target.get("loc"):
            inst_verilog["location"] = dict(loc)
        if inst_verilog:
            inst_reprs["verilog"] = inst_verilog
        if inst_reprs:
            entry["representations"] = inst_reprs
        instantiates.append(entry)

        if (var_id := _emit_instance_bind(scope_id, as_name, inst, module_vars,
                                          types_v2, types_out, variables_out)) is not None:
            var_refs.append(var_id)
    if instantiates:
        scope["instantiates"] = instantiates

    scope["variableRefs"] = var_refs
    scopes_out[scope_id] = scope

    for i, nested in enumerate(mod.get("scopes") or []):
        _emit_module(f"{scope_id}#{i}", nested, scope_id, types_v2, types_out,
                    module_vars, variables_out, scopes_out)


# ---- variables ----------------------------------------------------------


def _infer_bind_kind(binding: Optional[str]) -> str:
    """bindKind for a non-port root: v2 drops the original v1 bindKind
    entirely (only `direction` survives, marking a port). Recovered from
    the `Binding[X]` prefix when one exists (`Reg`/`Mem`/...); every
    other case (no prefix, or `Wire`/`Node`/`Literal`) falls back to a
    generic `"wire"` -- every backend here maps wire/node/literal to the
    same output, so the fallback costs nothing except when the *true*
    kind was `reg`/`mem`/a probe and no `sourceLangType` survived to
    say so (see module docstring)."""
    return _BINDING_TO_BINDKIND.get((binding or "").lower(), "wire")


def _type_name_for(binding: Optional[str], type_ref: str,
                   types_v2: Dict[str, Any]) -> Optional[str]:
    """`Binding[types[type_ref].source.name]`, gated on `binding` like
    `to_hgldd.py`'s `_root_variable_source_lang_type_info`/
    `_struct_objects` gate reconstruction on it -- a falsy `binding`
    means the source data to rebuild this was never there, not that the
    bracket should be dropped."""
    if not binding:
        return None
    name = ((types_v2.get(type_ref) or {}).get("source") or {}).get("name")
    return f"{binding}[{name}]" if name else None


def _v1_params(params_v2: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    """v2 `TypeSourceParam` (`name`/`type`/`value`) -> v1 constructor-param
    shape (`name`/`typeName`/`value`)."""
    if not params_v2:
        return None
    out = []
    for p in params_v2:
        entry: Dict[str, Any] = {"name": p["name"]}
        if (t := p.get("type")) is not None:
            entry["typeName"] = t
        if (v := p.get("value")) is not None:
            entry["value"] = v
        out.append(entry)
    return out


def _params_for(type_ref: str, types_v2: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    return _v1_params(((types_v2.get(type_ref) or {}).get("source") or {}).get("params"))


def _binding_sig(binding: Binding) -> Optional[str]:
    if isinstance(binding, str):
        return binding
    if isinstance(binding, dict) and "signal" in binding:
        return binding["signal"]
    return None


def _scalar_verilog_repr(binding: Optional[Binding]) -> Dict[str, Any]:
    if binding is None:
        return {}
    if (sig := _binding_sig(binding)) is not None:
        out: Dict[str, Any] = {"name": sig, "value": {"sigName": sig}}
        if isinstance(binding, dict) and (loc := binding.get("loc")):
            out["location"] = dict(loc)
        return out
    if isinstance(binding, dict) and "constant" in binding:
        return {"value": {"constant": binding["constant"]}}
    if isinstance(binding, dict) and "bitVector" in binding:
        return {"value": {"bitVector": binding["bitVector"]}}
    return {}


def _emit_variable_tree(scope_id: str, name: str, var: Dict[str, Any],
                        types_v2: Dict[str, Any], variables_out: Dict[str, Any]
                        ) -> str:
    type_ref = var.get("typeRef", "")
    direction = var.get("direction")
    source = var.get("source") or {}
    bind = var.get("bind") or {}
    binding = source.get("binding")
    own_params = source.get("params")
    loc = source.get("loc")
    bind_kind = "port" if direction else _infer_bind_kind(binding)

    is_agg = (types_v2.get(type_ref) or {}).get("kind") in ("struct", "vector")
    bind_value = None if is_agg else bind.get("verilog")
    own_bind_loc = bind.get("loc") if is_agg else None
    # The dotted-path -> flat-name map v2 flattened an aggregate's leaves
    # into. Not exposed to any scope's `variableRefs` (see module
    # docstring -- that's the part of v1's shape this can't tell how to
    # rebuild), but still handed to each leaf below so `upgrade()` can
    # walk `memberRefs` and reconstruct this exact map on its own,
    # independent of scope membership -- see `_walk_bind`/`_children` in
    # `upgrade.py`, which only ever look a variable up by id.
    leaf_map = bind.get("verilog") if is_agg else None

    root_id = f"{scope_id}::{name}"
    _walk_var_tree(root_id, name, type_ref, bind_kind, direction, loc,
                  binding, own_params, bind_value, own_bind_loc,
                  leaf_map, "", types_v2, variables_out)
    return root_id


def _walk_var_tree(var_id: str, chisel_name: str, type_ref: str, bind_kind: str,
                   direction: Optional[str], loc: Optional[Dict[str, Any]],
                   binding: Optional[str], own_params: Optional[List[Dict[str, Any]]],
                   bind_value: Optional[Binding],
                   own_bind_loc: Optional[Dict[str, Any]],
                   leaf_map: Optional[Dict[str, Any]], dotted_path: str,
                   types_v2: Dict[str, Any], variables_out: Dict[str, Any]
                   ) -> None:
    """Emit `var_id` (pre-order: parent before its own subtree, matching
    v1's own document order so id-assignment-by-iteration-order backends
    reproduce the goldens) and, for a struct/vector type, recurse into
    one `bindKind: synthetic` Variable per member/index.

    A descendant member's own `bind_value` (if `leaf_map` has an entry at
    its `dotted_path`) only ever populates its own pool record -- it is
    never added to a scope's `variableRefs` (see `_emit_variable_tree`),
    so no backend that walks `variableRefs` ever sees it directly; only
    `upgrade()`'s `memberRefs`-walk can reach it. This is what makes the
    per-leaf verilog reconstruction here safe to do unconditionally: it
    restores exactly what `upgrade()` needs to rebuild `bind.verilog`,
    without resurrecting the emitter-added duplicate-port rows a v1
    backend would otherwise see -- see module docstring for why those
    stay unreconstructed."""
    type_def = types_v2.get(type_ref) or {}
    kind = type_def.get("kind")

    v1var: Dict[str, Any] = {"typeRef": type_ref, "bindKind": bind_kind}
    if direction:
        v1var["direction"] = direction

    chisel: Dict[str, Any] = {"name": chisel_name}
    if loc:
        chisel["location"] = dict(loc)
    type_name = _type_name_for(binding, type_ref, types_v2)
    if type_name:
        st: Dict[str, Any] = {"typeName": type_name}
        if (params := _v1_params(own_params) or _params_for(type_ref, types_v2)) is not None:
            st["params"] = params
        chisel["sourceLangType"] = st

    verilog: Dict[str, Any] = {}
    v1var["representations"] = {"chisel": chisel, "verilog": verilog}
    variables_out[var_id] = v1var

    if kind in ("struct", "vector"):
        if own_bind_loc:
            verilog["location"] = dict(own_bind_loc)
        if bind_kind == "port":
            # A dict (even without `sigName`) matches how v1 itself encodes
            # an aggregate port whose value resolves through an expression
            # chain rather than a scalar signal (`{"exprRef": ...}`) --
            # keeps uhdi_to_hgdb's `not isinstance(verilog.get("value"),
            # dict)` port-name fallback from firing on an aggregate port
            # (that fallback is meant for scalar ports only).
            verilog["value"] = {}
        member_ids: List[str] = []
        if kind == "struct":
            children = [(m["name"], m.get("typeRef", ""))
                       for m in type_def.get("members") or []]
        else:
            elem_ref = type_def.get("elementRef", "")
            children = [(str(i), elem_ref)
                       for i in range(int(type_def.get("size", 0)))]
        for member_name, member_type_ref in children:
            child_id = f"{var_id}__{member_name}"
            child_path = f"{dotted_path}.{member_name}" if dotted_path else member_name
            child_bind_value = (leaf_map or {}).get(child_path)
            _walk_var_tree(child_id, member_name, member_type_ref, "synthetic",
                          None, loc, binding, None, child_bind_value, None,
                          leaf_map, child_path, types_v2, variables_out)
            member_ids.append(child_id)
        v1var["memberRefs"] = member_ids
    elif bind_value is not None:
        verilog.update(_scalar_verilog_repr(bind_value))


# ---- instances ------------------------------------------------------------


def _emit_instance_bind(scope_id: str, as_name: str, inst: Dict[str, Any],
                        module_vars: Dict[str, Dict[str, Any]],
                        types_v2: Dict[str, Any], types_out: Dict[str, Any],
                        variables_out: Dict[str, Any]) -> Optional[str]:
    """Inverse of `upgrade.py`'s `_instance_bind`/`_find_instance_var`:
    rebuild the `bindKind: instance` variable an `instances[as_name].
    bind.verilog` came from, one member per bound port, typed by the
    instantiated module's own `variables[<port>].typeRef` -- looked up via
    `module_vars`, since `instances[as_name]` itself carries no typeRef of
    its own. An aggregate port's value is the same dotted-path leaf map
    `_walk_var_tree` already knows how to rebuild a member tree from,
    rooted at the port instead of at the whole instance: an instance's own
    top level is never flattened (see `upgrade.py`'s `_instance_bind`
    docstring), so each port starts its own fresh walk."""
    bind_verilog = (inst.get("bind") or {}).get("verilog")
    ports = module_vars.get(inst.get("moduleRef") or "")
    if not bind_verilog or not ports:
        return None

    type_ref = f"{inst.get('moduleRef')}#instance"
    if type_ref not in types_out:
        types_out[type_ref] = {
            "kind": "struct",
            "members": [{"name": name, "typeRef": port.get("typeRef", "")}
                       for name, port in ports.items()],
        }

    var_id = f"{scope_id}::{as_name}$instance"
    loc = (inst.get("source") or {}).get("loc")

    member_ids: List[str] = []
    for name, port in ports.items():
        if name not in bind_verilog:
            continue
        member_type_ref = port.get("typeRef", "")
        is_agg = (types_v2.get(member_type_ref) or {}).get("kind") in ("struct", "vector")
        value = bind_verilog[name]
        child_id = f"{var_id}__{name}"
        _walk_var_tree(child_id, name, member_type_ref, "synthetic", None, loc,
                      None, None, None if is_agg else value, None,
                      value if is_agg else None, "", types_v2, variables_out)
        member_ids.append(child_id)

    chisel: Dict[str, Any] = {"name": as_name}
    if loc:
        chisel["location"] = dict(loc)
    variables_out[var_id] = {
        "bindKind": "instance",
        "typeRef": type_ref,
        "memberRefs": member_ids,
        "representations": {"chisel": chisel, "verilog": {}},
    }
    return var_id
