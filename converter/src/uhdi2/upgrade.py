"""UHDI 1.0 -> UHDI 2.0 ("variant B") upgrader.

Reshapes the pool-based v1 document (flat `types`/`variables`/`scopes`
pools + cross-pool refs) into v2's nested per-module shape: `modules` keyed
by v1 scope id, each with inline `variables`/`instances`/`scopes`. `types`
carries over unchanged except for an added, optional `source` block (see
below). Document order is preserved throughout (module/instance/variable
dict order mirrors the v1 pool's own key order) -- v1's HGLDD emission is
order-sensitive (struct/vec winner selection, first-vs-later declarations)
and v2 must reproduce it exactly.

Two rules drive the variable reshape, both stated in terms of the *target*
type (struct/vector), never of ids:
  * a variable's `bind.verilog` is a flat scalar binding for a scalar
    variable, or a `dottedPath -> scalar` map for an aggregate one, walked
    from `memberRefs` (preferred) or, absent those, from a `'{`
    struct-literal `exprRef` chain -- both encodings occur in the
    fixtures and must be handled uniformly and recursively.
  * a `bindKind: port` variable is dropped when its own sigName is also
    reachable as a leaf of some other aggregate variable in the same
    scope (the producer's "emitter-added" duplicate port record).

`bindKind: synthetic` variables never become v2 records (their data is
folded into the parent aggregate's `bind`, or into `types[X].source`, see
below). `bindKind: instance` variables are also dropped: none of the
fixtures exercise them, so nothing here maps them into
`instances[<as>].bind.verilog` (the target slot for a bound instance port,
per the v2 shape) -- see the upgrade report for that as an open TODO.

Type source-name derivation (recovers what v1 sourced from synthetic
subfield Variables' `sourceLangType.typeName`, e.g. "IO[UInt<8>]"):
  * every v1 variable/leaf's typeName has the form `Binding[X]` (Binding
    is the declaration kind -- IO/Wire/Reg/...; X is a property of the
    variable's/leaf's own TYPE) or, without a `[...]` wrapper, is itself X
    with no binding.
  * X is recorded as `types[typeRef].source.name` (plus `.source.params`,
    an extension beyond the spec text -- see module docstring note below)
    -- but only when every v1 variable/leaf sharing that typeRef derives
    the *same* X. When it doesn't (observed: the "bool" ground type is
    reused for both Chisel `Clock` and plain `Bool` ports, giving X =
    "Clock" for one variable and "Bool" for another), `types[typeRef]`
    gets NO `source` at all -- picking one would silently lose the other,
    which the spec explicitly rules out. Instead, the *variables* whose
    typeRef landed in this conflict keep their own raw `typeName` on
    `Variable.source.typeName` as a fallback (a form the spec otherwise
    drops), so `to_hgldd.py` can still reconstruct their
    `source_lang_type_info` byte-for-byte. `_type_source_conflicts()`
    exposes the detected conflicts for reporting.

`params` (UHDI Sec.6.9 constructor params, e.g. Chisel's `Vec(length,
gen)`) is not mentioned by the v2 amendments at all, but one fixture
(`vec_of_struct`) needs it to reach byte-identity with the v1 golden: the
"items" member's `source_lang_type_info` carries `params: [gen, length]`
in v1, sourced from the same synthetic-Variable `sourceLangType.params`
that supplies `typeName`. This module extends `types[X].source` with an
analogous, equally-optional `params` field, using the same
"omit-if-inconsistent" rule as `name`.

Aggregate-variable HDL-side location (v1's `hdl_loc`, e.g. `bundle_io`'s
"io.in" aggregate port) is likewise unaddressed by the amendments: a
scalar binding's `loc` (added by the amendments) has nowhere to live when
the binding is a dotted-path map. This module adds an optional sibling
`Variable.bind.loc`, populated only for aggregate variables that carry
their own verilog-side location (one fixture, one variable, in the
corpus) -- scalar variables keep using the embedded `bind.verilog.loc`
form the amendments specify, never both.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from uhdi_common.context import BaseContext

Binding = Union[str, Dict[str, Any]]

# `Binding[X]` -- Binding is the declaration kind (IO/Wire/Reg/...); X is
# greedy so a bracketed X (e.g. "Item[2]") stays intact.
_TYPE_NAME_PAT = re.compile(r"^([A-Za-z0-9_]+)\[(.*)\]$")


def upgrade(doc_v1: Dict[str, Any]) -> Dict[str, Any]:
    """Pure, deterministic v1 -> v2 reshape. Does not mutate `doc_v1`."""
    ctx = BaseContext.from_uhdi(doc_v1)
    reprs = ctx.representations

    type_source, conflicts = _derive_type_sources(ctx)

    types_out: Dict[str, Any] = {}
    for tid, tdef in ctx.types.items():
        tdef = dict(tdef or {})
        if src := type_source.get(tid):
            tdef["source"] = src
        types_out[tid] = tdef

    doc_v2: Dict[str, Any] = {
        "format": {"name": "uhdi", "version": "2.0", "layers": ["core"]},
        "source": {
            "language": (reprs.get(ctx.authoring_repr, {}) or {}).get("language", ""),
            "files": list((reprs.get(ctx.authoring_repr, {}) or {}).get("files", [])),
        },
        "target": {
            "language": (reprs.get(ctx.simulation_repr, {}) or {}).get("language", ""),
            "files": list((reprs.get(ctx.simulation_repr, {}) or {}).get("files", [])),
        },
        "types": types_out,
        "modules": {
            sid: _build_module(sid, scope, ctx, type_source)
            for sid, scope in ctx.scopes.items()
            if (scope or {}).get("kind", "module") in ("module", "extmodule")
        },
    }
    return doc_v2


def _type_source_conflicts(doc_v1: Dict[str, Any]) -> Dict[str, List[Tuple[str, str, str]]]:
    """Public diagnostic: `typeRef -> [(X_a, var_id_a, typeName_a), (X_b, ...)]`
    for every type where variables/leaves disagree on the derived X. Not
    used by `upgrade()` itself (which already resolves the conflict per
    the module docstring); exposed for the upgrade report / tooling."""
    ctx = BaseContext.from_uhdi(doc_v1)
    _, conflicts = _derive_type_sources(ctx)
    return conflicts


# ---- type source-name derivation --------------------------------------


def _render_params(params: Any) -> Optional[List[Dict[str, Any]]]:
    if not isinstance(params, list):
        return None
    rendered = []
    for p in params:
        if not isinstance(p, dict) or "name" not in p:
            continue
        entry: Dict[str, Any] = {"name": p["name"]}
        if (tpe := p.get("typeName")) is not None:
            entry["type"] = tpe
        if (val := p.get("value")) is not None:
            entry["value"] = val
        rendered.append(entry)
    return rendered or None


def _derive_type_sources(
    ctx: BaseContext,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, List[Tuple[str, str, str]]]]:
    """`types[type_ref].source.name` needs one canonical X per type, but a
    struct-member (`bindKind: synthetic`) occurrence has no escape hatch --
    unlike a root variable, it can never fall back to its own raw
    `typeName` (there is no v2 record for it any more, its data lives only
    in the flattened `bind`). So the canonical X is picked to satisfy
    every *member* occurrence first (the one observed conflict, the
    "bool" ground type shared by Chisel Clock and Bool, only ever
    disagrees between a *root* Clock port and everything else -- struct
    members needing a bool are always plain Bool, never Clock). Every
    occurrence -- root or member -- that disagrees with the canonical X is
    recorded in `conflicts` for the upgrade report; only root occurrences
    can actually act on it (via `Variable.source.typeName`, see
    `_build_variables`). If members themselves disagree (not observed:
    would mean two struct fields of the same typeRef derive different
    source names with neither able to override), there is truly no
    canonical X to pick -- `type_ref` gets no `type_source` entry at all,
    every occurrence goes to `conflicts`, and every root variable of that
    type falls back to its own typeName (members stay unrecoverable)."""
    # type_ref -> [(X, params_or_None, var_id, raw_typeName, is_member), ...]
    occurrences: Dict[str, List[Tuple[str, Any, str, str, bool]]] = {}
    for var_id, var in ctx.variables.items():
        chisel = (var.get("representations", {}) or {}).get(ctx.authoring_repr, {}) or {}
        slt = chisel.get("sourceLangType") or {}
        type_name = slt.get("typeName")
        if not type_name:
            continue
        type_ref = var.get("typeRef", "")
        if not type_ref:
            continue
        m = _TYPE_NAME_PAT.match(type_name)
        x = m.group(2) if m else type_name
        is_member = var.get("bindKind") == "synthetic"
        occurrences.setdefault(type_ref, []).append(
            (x, _render_params(slt.get("params")), var_id, type_name, is_member))

    type_source: Dict[str, Dict[str, Any]] = {}
    conflicts: Dict[str, List[Tuple[str, str, str]]] = {}
    for type_ref, occs in occurrences.items():
        member_names = {x for x, _, _, _, is_member in occs if is_member}
        canonical: Optional[str] = None
        if len(member_names) == 1:
            canonical = next(iter(member_names))
        elif not member_names:
            # No member occurrence constrains this type -- every occurrence
            # is a root variable, each with its own escape hatch, so any
            # deterministic pick is safe. Break ties by occurrence count,
            # then by first-seen order.
            order = [x for x, _, _, _, _ in occs]
            canonical = max(dict.fromkeys(order), key=lambda n: (order.count(n), -order.index(n)))
        # else: member_names has >1 distinct value -- no canonical, every
        # occurrence (member and root alike) is a "conflict".

        disagreeing = [(x, vid, tn) for x, _, vid, tn, _ in occs if x != canonical]
        if disagreeing:
            conflicts[type_ref] = disagreeing

        if canonical is None:
            continue
        entry: Dict[str, Any] = {"name": canonical}
        params_variants: Dict[str, List[Dict[str, Any]]] = {}
        for x, params, _, _, _ in occs:
            if x == canonical and params is not None:
                params_variants.setdefault(json.dumps(params, sort_keys=True), params)
        if len(params_variants) == 1:
            entry["params"] = next(iter(params_variants.values()))
        type_source[type_ref] = entry
    return type_source, conflicts


# ---- module / scope -------------------------------------------------------


def _build_module(scope_id: str, scope: Dict[str, Any], ctx: BaseContext,
                  type_source: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    kind = scope.get("kind", "module")
    if kind != "module":
        out["kind"] = kind

    if src := _module_source(scope, ctx):
        out["source"] = src
    if tgt := _module_target(scope, ctx):
        out["target"] = tgt

    out["variables"] = _build_variables(scope_id, scope, ctx, type_source)

    instances = _build_instances(scope, ctx)
    if instances:
        out["instances"] = instances

    inline = [
        sid for sid, s in ctx.scopes.items()
        if (s or {}).get("kind") == "inline"
        and (s or {}).get("containerScopeRef") == scope_id
    ]
    if inline:
        out["scopes"] = [
            {**_build_module(sid, ctx.scopes[sid], ctx, type_source), "kind": "inline"}
            for sid in inline
        ]

    return out


def _loc_dict(loc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """v1 `Location`, unflattened: `{file, beginLine?, beginColumn?,
    endLine?, endColumn?}`, `file` unchanged (still an index into
    `source.files`/`target.files`, per whichever representation `loc`
    came from)."""
    if not loc or "file" not in loc:
        return None
    out: Dict[str, Any] = {"file": loc["file"]}
    for k in ("beginLine", "beginColumn", "endLine", "endColumn"):
        if k in loc:
            out[k] = loc[k]
    return out


def _module_source(scope: Dict[str, Any], ctx: BaseContext) -> Dict[str, Any]:
    chisel = (scope.get("representations", {}) or {}).get(ctx.authoring_repr, {}) or {}
    out: Dict[str, Any] = {}
    if name := chisel.get("name"):
        out["name"] = name
    slt = chisel.get("sourceLangType") or {}
    if type_name := slt.get("typeName"):
        out["typeName"] = type_name
    if loc := _loc_dict(chisel.get("location")):
        out["loc"] = loc
    return out


def _module_target(scope: Dict[str, Any], ctx: BaseContext) -> Dict[str, Any]:
    verilog = (scope.get("representations", {}) or {}).get(ctx.simulation_repr, {}) or {}
    out: Dict[str, Any] = {}
    if name := verilog.get("name"):
        out["name"] = name
    if loc := _loc_dict(verilog.get("location")):
        out["loc"] = loc
    return out


def _build_instances(scope: Dict[str, Any], ctx: BaseContext) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for inst in scope.get("instantiates") or []:
        as_key = inst.get("as") or inst.get("scopeRef")
        entry: Dict[str, Any] = {"moduleRef": inst.get("scopeRef")}
        inst_reprs = inst.get("representations", {}) or {}
        chisel = inst_reprs.get(ctx.authoring_repr, {}) or {}
        verilog = inst_reprs.get(ctx.simulation_repr, {}) or {}

        source: Dict[str, Any] = {}
        if loc := _loc_dict(chisel.get("location")):
            source["loc"] = loc
        if source:
            entry["source"] = source

        target: Dict[str, Any] = {}
        if name := verilog.get("name"):
            target["name"] = name
        if loc := _loc_dict(verilog.get("location")):
            target["loc"] = loc
        if target:
            entry["target"] = target

        # TODO: no fixture provides instance-port-binding data to populate
        # entry["bind"]["verilog"][<portPath>] (the target slot for a
        # bound instance port, per the v2 Instance shape).
        out[as_key] = entry
    return out


# ---- variables --------------------------------------------------------


def _is_aggregate(type_ref: str, ctx: BaseContext) -> bool:
    return (ctx.types.get(type_ref) or {}).get("kind") in ("struct", "vector")


def _scalar_binding_from_value_loc(value: Any, loc: Optional[Dict[str, Any]]) -> Optional[Binding]:
    """Render a `PerRepresentation.value` object (or a bare exprRef
    operand, which has the same shape but never a sibling `location`) as
    a scalar Binding. `sigName` promotes to `{"signal", "loc"}` only when
    a `loc` is available; a bare `constant`/`bitVector` never carries one
    in this corpus, so those stay in their plain form (the amendments
    don't specify a combined shape for them)."""
    if not isinstance(value, dict):
        return None
    if "sigName" in value:
        sig = value["sigName"]
        return {"signal": sig, "loc": loc} if loc else sig
    if "constant" in value:
        return {"constant": value["constant"]}
    if "bitVector" in value:
        return {"bitVector": value["bitVector"]}
    return None


def _scalar_binding_from_hdl(hdl: Any) -> Optional[Binding]:
    if not isinstance(hdl, dict):
        return None
    return _scalar_binding_from_value_loc(hdl.get("value"), _loc_dict(hdl.get("location")))


def _scalar_binding_from_op(op: Optional[Dict[str, Any]]) -> Optional[Binding]:
    # An exprRef operand IS the value; operands carry no location sibling.
    return _scalar_binding_from_value_loc(op, None)


def _bind_sig_name(binding: Any) -> Optional[str]:
    if isinstance(binding, str):
        return binding
    if isinstance(binding, dict) and "signal" in binding:
        return binding["signal"]
    return None


def _children(var_id: Optional[str], op: Optional[Dict[str, Any]],
             ctx: BaseContext) -> Optional[List[Dict[str, Any]]]:
    """Ordered child value-sources of an aggregate node, or None if this
    node carries no aggregate data at all. A child is `{"var": id}` (a
    memberRefs member -- has its own pool record) or `{"op": operand}` (an
    exprRef struct-literal operand -- has none)."""
    if var_id is not None:
        var = ctx.variables.get(var_id) or {}
        if member_ids := var.get("memberRefs"):
            return [{"var": m} for m in member_ids]
        hdl = (var.get("representations", {}) or {}).get(ctx.simulation_repr, {}) or {}
        value = hdl.get("value") if isinstance(hdl, dict) else None
    else:
        value = op
    if isinstance(value, dict) and "exprRef" in value:
        expr = ctx.expressions.get(value["exprRef"]) or {}
        if expr.get("opcode") == "'{":
            return [{"op": o} for o in expr.get("operands", [])]
    return None


def _leaf(var_id: Optional[str], op: Optional[Dict[str, Any]],
         ctx: BaseContext) -> Optional[Binding]:
    if var_id is not None:
        var = ctx.variables.get(var_id) or {}
        hdl = (var.get("representations", {}) or {}).get(ctx.simulation_repr, {}) or {}
        return _scalar_binding_from_hdl(hdl)
    return _scalar_binding_from_op(op)


def _walk_bind(var_id: Optional[str], op: Optional[Dict[str, Any]],
              type_ref: str, prefix: str, out: Dict[str, Any],
              ctx: BaseContext) -> None:
    """Recursively flatten an aggregate node's value tree into `out`,
    keyed by dotted member path, walking struct members / vector indices
    in declared order (never by id)."""
    type_def = ctx.types.get(type_ref) or {}
    kind = type_def.get("kind")
    if kind == "struct":
        children = _children(var_id, op, ctx)
        if children is None:
            return
        for member, child in zip(type_def.get("members") or [], children):
            path = f"{prefix}.{member['name']}" if prefix else member["name"]
            _walk_bind(child.get("var"), child.get("op"),
                      member["typeRef"], path, out, ctx)
    elif kind == "vector":
        children = _children(var_id, op, ctx)
        if children is None:
            return
        elem_ref = type_def.get("elementRef", "")
        for i, child in enumerate(children):
            path = f"{prefix}.{i}" if prefix else str(i)
            _walk_bind(child.get("var"), child.get("op"), elem_ref, path, out, ctx)
    else:
        if (leaf := _leaf(var_id, op, ctx)) is not None:
            out[prefix] = leaf


def _variable_bind(var_id: str, var: Dict[str, Any],
                   ctx: BaseContext) -> Optional[Binding]:
    type_ref = var.get("typeRef", "")
    if _is_aggregate(type_ref, ctx):
        out: Dict[str, Any] = {}
        _walk_bind(var_id, None, type_ref, "", out, ctx)
        return out or None
    return _leaf(var_id, None, ctx)


def _scope_aggregated_leaves(scope: Dict[str, Any], ctx: BaseContext) -> set:
    """sigNames reachable as leaves of some aggregate variable owned by
    this scope -- the emitter-added duplicate ports get dropped against
    this set. Scoped per-module, per the upgrade spec ("... in the same
    scope"); no fixture has the same sigName duplicated across scopes."""
    leaves: set = set()
    for var_id in scope.get("variableRefs") or []:
        var = ctx.variables.get(var_id) or {}
        type_ref = var.get("typeRef", "")
        if not (_is_aggregate(type_ref, ctx) or var.get("memberRefs")):
            continue
        bind = _variable_bind(var_id, var, ctx)
        if isinstance(bind, dict):
            for v in bind.values():
                if (sig := _bind_sig_name(v)) is not None:
                    leaves.add(sig)
    return leaves


def _build_variables(scope_id: str, scope: Dict[str, Any], ctx: BaseContext,
                     type_source: Dict[str, Any]) -> Dict[str, Any]:
    aggregated_leaves = _scope_aggregated_leaves(scope, ctx)

    kept: List[tuple] = []  # (var_id, var)
    for var_id in scope.get("variableRefs") or []:
        var = ctx.variables.get(var_id) or {}
        bind_kind = var.get("bindKind")
        if bind_kind in ("synthetic", "instance"):
            # TODO: instance-bound variables are dropped here; v1 has no
            # fixture exercising them. When one exists, resurrect as
            # instances[<as>].bind.verilog[<portPath>] instead of dropping.
            continue
        if bind_kind == "port":
            hdl = (var.get("representations", {}) or {}).get(
                ctx.simulation_repr, {}) or {}
            sig = (hdl.get("value") or {}).get("sigName") if isinstance(hdl, dict) else None
            if sig and sig in aggregated_leaves:
                continue
        kept.append((var_id, var))

    name_counts: Dict[str, int] = {}
    for var_id, var in kept:
        chisel = (var.get("representations", {}) or {}).get(ctx.authoring_repr, {}) or {}
        if name := chisel.get("name"):
            name_counts[name] = name_counts.get(name, 0) + 1

    out: Dict[str, Any] = {}
    for var_id, var in kept:
        chisel = (var.get("representations", {}) or {}).get(ctx.authoring_repr, {}) or {}
        name = chisel.get("name")
        key = name if name and name_counts.get(name) == 1 else var_id

        source: Dict[str, Any] = {}
        if name:
            source["name"] = name
        slt = chisel.get("sourceLangType") or {}
        type_name = slt.get("typeName")
        type_ref = var.get("typeRef", "")
        if type_name:
            m = _TYPE_NAME_PAT.match(type_name)
            own_x = m.group(2) if m else type_name
            if m and (binding := m.group(1)):
                source["binding"] = binding
            if (type_source.get(type_ref) or {}).get("name") != own_x:
                # Either types[type_ref] has no canonical source name at
                # all, or this variable's own X disagrees with it (see
                # `_derive_type_sources`); keep the raw typeName on the
                # variable so to_hgldd can still reconstruct it exactly.
                source["typeName"] = type_name
        if loc := _loc_dict(chisel.get("location")):
            source["loc"] = loc

        entry: Dict[str, Any] = {
            "typeRef": type_ref,
            "role": "port" if var.get("bindKind") == "port" else "node",
        }
        if direction := var.get("direction"):
            entry["direction"] = direction
        entry["source"] = source

        if (bind := _variable_bind(var_id, var, ctx)) is not None:
            bind_entry: Dict[str, Any] = {"verilog": bind}
            if _is_aggregate(type_ref, ctx):
                # An aggregate (dottedPath -> scalar) binding: its own
                # verilog-side location, if any, has no natural home
                # inside the map -- see module docstring.
                hdl = (var.get("representations", {}) or {}).get(
                    ctx.simulation_repr, {}) or {}
                if loc := _loc_dict(hdl.get("location") if isinstance(hdl, dict) else None):
                    bind_entry["loc"] = loc
            entry["bind"] = bind_entry

        out[key] = entry
    return out
