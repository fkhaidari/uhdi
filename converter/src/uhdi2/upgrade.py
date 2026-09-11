"""Pool-shaped (June) UHDI -> tree-shaped UHDI ("variant B") upgrader.

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
  * a variable's `target.verilog` is a flat scalar binding for a scalar
    variable, or a `dottedPath -> scalar` map for an aggregate one, walked
    from `memberRefs` (preferred) or, absent those, from a `'{`
    struct-literal `exprRef` chain -- both encodings occur in the
    fixtures and must be handled uniformly and recursively.
  * a `bindKind: port` variable is dropped when its own sigName is also
    reachable as a leaf of some other aggregate variable in the same
    scope (the producer's "emitter-added" duplicate port record).

`bindKind: synthetic` variables never become v2 records (their data is
folded into the parent aggregate's `target`, or into `types[X].source`, see
below). `bindKind: instance` variables are also dropped from `Module.
variables`, but not lost: a v1 instance variable's own `typeRef` is a
struct describing that instance's ports (see the "Cpu_alu" style type in
the fixtures), so its `memberRefs` walk the same way a regular aggregate
variable's does, landing in `instances[<as>].target.verilog` instead --
linked to its `instantiates` entry by matching chisel name to `as` (see
`_find_instance_var`/`_instance_bind`). Unlike a plain aggregate
variable's flat dotted-path target, an instance's own top level is never
flattened (each port name is its own key; only an aggregate port's own
subtree flattens below that) -- ports without a bound leaf are omitted
the same way `_walk_bind` omits them elsewhere.

Type source-name derivation (recovers what v1 sourced from synthetic
subfield Variables' `sourceLangType.typeName`, e.g. "IO[UInt<8>]"):
  * every v1 variable/leaf's typeName has the form `Binding[X]` (Binding
    is the declaration kind -- IO/Wire/Reg/...; X is a property of the
    variable's/leaf's own TYPE) or, without a `[...]` wrapper, is itself X
    with no binding.
  * X is recorded as `types[typeRef].source.name` (plus `.source.params`,
    an extension beyond the spec text -- see module docstring note below).
    When every v1 variable/leaf sharing a typeRef derives the *same* X,
    the pool entry keeps its v1 key. When they don't (observed: the
    "bool" ground type is reused for both Chisel `Clock` and plain `Bool`
    ports, giving X = "Clock" for one variable and "Bool" for another),
    the entry is split into one pool key per distinct X --
    `<v1TypeRef>_<X>` (e.g. "bool_Clock"/"bool_Bool") -- and every
    variable's own `typeRef`, plus every struct-member/vector-elementRef
    pointer that used the original key, is rewritten to the split key
    matching its own X. This mirrors the native emitter's own type-pool
    identity rule (structure + source name). `_type_source_conflicts()`
    exposes the cases where a split target can't be determined from
    local data (unobserved in the corpus; see `_derive_type_layout`) for
    reporting.

`params` (UHDI Sec.6.9 constructor params, e.g. Chisel's `Vec(length,
gen)`) is not mentioned by the v2 amendments at all, but one fixture
(`vec_of_struct`) needs it to reach byte-identity with the v1 golden: the
"items" member's `source_lang_type_info` carries `params: [gen, length]`
in v1, sourced from the same synthetic-Variable `sourceLangType.params`
that supplies `typeName`. This module extends `types[X].source` with an
analogous, equally-optional `params` field, using the same
"omit-if-inconsistent" rule as `name`. `Module.source.params` and
`Variable.source.params` are the same escape hatch one level up: a
module's own `sourceLangType.params` (e.g. rfc-alu's `Cpu`, parameterized
by `width`) always lands on `Module.source.params` (there is no type
pool for modules to share params through); a root variable's own params
land on `Variable.source.params` only when they aren't already
recoverable from `types[typeRef].source.params` (not observed in the
corpus).

Aggregate-variable HDL-side location (v1's `hdl_loc`, e.g. `bundle_io`'s
"io.in" aggregate port) is likewise unaddressed by the amendments: a
scalar binding's `loc` (added by the amendments) has nowhere to live when
the binding is a dotted-path map. This module adds an optional sibling
`Variable.target.loc`, populated only for aggregate variables that carry
their own verilog-side location (one fixture, one variable, in the
corpus) -- scalar variables keep using the embedded `target.verilog.loc`
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

    types_out, resolve_root, _conflicts = _derive_type_layout(ctx)

    doc_v2: Dict[str, Any] = {
        "format": {"version": "1.0"},
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
            sid: _build_module(sid, scope, ctx, resolve_root, types_out)
            for sid, scope in ctx.scopes.items()
            if (scope or {}).get("kind", "module") in ("module", "extmodule")
        },
    }
    return doc_v2


def _type_source_conflicts(doc_v1: Dict[str, Any]) -> Dict[str, List[Tuple[str, str, str]]]:
    """Public diagnostic: `typeRef -> [(X, site, typeName), ...]` for every
    type where a split target couldn't be determined from local data --
    a root variable whose own typeName didn't survive to name an X
    (`site == "<root>"`), or struct-member/vector-element occurrences of
    a split typeRef that themselves disagree (`site == "<member>"`). Not
    used by `upgrade()` itself (which still picks a deterministic
    fallback, see `_derive_type_layout`); exposed for the upgrade report
    / tooling. Unobserved in the current corpus."""
    ctx = BaseContext.from_uhdi(doc_v1)
    _, _, conflicts = _derive_type_layout(ctx)
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


def _majority(names: List[str]) -> str:
    """Deterministic fallback pick among disagreeing occurrences: most
    frequent first, ties broken by first-seen order."""
    return max(dict.fromkeys(names), key=lambda n: (names.count(n), -names.index(n)))


def _derive_type_layout(
    ctx: BaseContext,
) -> Tuple[Dict[str, Any], Any, Dict[str, List[Tuple[str, str, str]]]]:
    """Builds the v2 `types` pool and a `resolve_root(type_ref, own_x)`
    helper `_build_variables` uses to pick a variable's own `typeRef`.

    A v1 typeRef whose variables/leaves all derive the same source-name X
    (see module docstring) keeps its v1 key, with `source.name` (and
    `.params`, see below) attached. When they disagree (observed: the
    "bool" ground type shared by Chisel `Clock` and plain `Bool` ports),
    the entry is split into one pool key per distinct X --
    `<v1TypeRef>_<X>` -- mirroring the native emitter's own type-pool
    identity rule (structure + source name); every struct
    member/vector element/enum underlying-type pointer that used the
    original key is rewritten to the split key matching its own
    occurrences (`_majority` picks a deterministic fallback when a
    member position's occurrences disagree or say nothing at all -- not
    observed in the corpus; recorded in `conflicts`). A *root* variable's
    own `typeRef` is resolved the same way by `resolve_root`, using its
    own derived X first and the same fallback otherwise (see
    `_build_variables`) -- unlike the pre-split design, this never needs
    a `Variable.source.typeName` escape hatch: the variable's `typeRef`
    itself always names the entry with the right source name."""
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

    names_by_type: Dict[str, List[str]] = {
        tref: [x for x, _, _, _, _ in occs] for tref, occs in occurrences.items()}
    member_names_by_type: Dict[str, List[str]] = {
        tref: [x for x, _, _, _, is_member in occs if is_member]
        for tref, occs in occurrences.items()}
    conflicts: Dict[str, List[Tuple[str, str, str]]] = {}

    def is_split(type_ref: str) -> bool:
        return len(set(names_by_type.get(type_ref, ()))) > 1

    def split_key(type_ref: str, name: str) -> str:
        return f"{type_ref}_{name}"

    def params_for(type_ref: str, name: str) -> Optional[List[Dict[str, Any]]]:
        variants: Dict[str, List[Dict[str, Any]]] = {}
        for x, params, _, _, _ in occurrences.get(type_ref, []):
            if x == name and params is not None:
                variants.setdefault(json.dumps(params, sort_keys=True), params)
        return next(iter(variants.values())) if len(variants) == 1 else None

    def member_target(type_ref: str) -> str:
        if not is_split(type_ref):
            return type_ref
        member_distinct = list(dict.fromkeys(member_names_by_type.get(type_ref, [])))
        if len(member_distinct) == 1:
            return split_key(type_ref, member_distinct[0])
        name = _majority(names_by_type[type_ref])
        for n in member_distinct:
            if n != name:
                conflicts.setdefault(type_ref, []).append((n, "<member>", ""))
        return split_key(type_ref, name)

    def resolve_root(type_ref: str, own_x: Optional[str]) -> str:
        if not is_split(type_ref):
            return type_ref
        if own_x is not None and own_x in names_by_type[type_ref]:
            return split_key(type_ref, own_x)
        conflicts.setdefault(type_ref, []).append((own_x or "", "<root>", ""))
        return split_key(type_ref, _majority(names_by_type[type_ref]))

    def rewrite_refs(tdef: Dict[str, Any]) -> Dict[str, Any]:
        kind = tdef.get("kind")
        if kind == "struct":
            tdef = dict(tdef)
            tdef["members"] = [
                {**m, "typeRef": member_target(m["typeRef"])}
                for m in tdef.get("members") or []
            ]
        elif kind == "vector":
            tdef = dict(tdef)
            tdef["elementRef"] = member_target(tdef.get("elementRef", ""))
        elif kind == "enum":
            tdef = dict(tdef)
            tdef["underlyingTypeRef"] = member_target(tdef.get("underlyingTypeRef", ""))
        return tdef

    types_out: Dict[str, Any] = {}
    for tid, tdef in ctx.types.items():
        tdef = dict(tdef or {})
        distinct = list(dict.fromkeys(names_by_type.get(tid, [])))
        if len(distinct) <= 1:
            if distinct:
                entry: Dict[str, Any] = {"name": distinct[0]}
                if params := params_for(tid, distinct[0]):
                    entry["params"] = params
                tdef["source"] = entry
            types_out[tid] = rewrite_refs(tdef)
        else:
            for name in distinct:
                entry = {"name": name}
                if params := params_for(tid, name):
                    entry["params"] = params
                split_tdef = dict(tdef)
                split_tdef["source"] = entry
                types_out[split_key(tid, name)] = rewrite_refs(split_tdef)

    return types_out, resolve_root, conflicts


# ---- module / scope -------------------------------------------------------


def _build_module(scope_id: str, scope: Dict[str, Any], ctx: BaseContext,
                  resolve_root: Any, types_out: Dict[str, Any],
                  top_level: bool = True) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    kind = scope.get("kind", "module")
    if kind != "module":
        out["kind"] = kind

    if src := _module_source(scope, ctx):
        out["source"] = src
    if tgt := _module_target(scope, ctx, top_level):
        out["target"] = tgt

    out["variables"] = _build_variables(scope_id, scope, ctx, resolve_root, types_out)

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
            {**_build_module(sid, ctx.scopes[sid], ctx, resolve_root, types_out, top_level=False),
             "kind": "inline"}
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
    if params := _render_params(slt.get("params")):
        out["params"] = params
    if loc := _loc_dict(chisel.get("location")):
        out["loc"] = loc
    return out


def _module_target(scope: Dict[str, Any], ctx: BaseContext, top_level: bool) -> Dict[str, Any]:
    """`name` is dropped for a top-level module: it always equals the
    module's own `modules` key there, so the key already carries it. A
    nested inline scope has no key of its own, so it keeps `name` if the
    v1 data has one (not observed in the corpus)."""
    verilog = (scope.get("representations", {}) or {}).get(ctx.simulation_repr, {}) or {}
    out: Dict[str, Any] = {}
    if not top_level and (name := verilog.get("name")):
        out["name"] = name
    if loc := _loc_dict(verilog.get("location")):
        out["loc"] = loc
    return out


def _find_instance_var(scope: Dict[str, Any], ctx: BaseContext,
                       as_key: str) -> Optional[str]:
    """The `bindKind: instance` variable (root, listed on `scope.
    variableRefs`) whose own chisel name matches an `instantiates` entry's
    `as` -- the link v1 carries between the two (see `_build_instances`)."""
    for var_id in scope.get("variableRefs") or []:
        var = ctx.variables.get(var_id) or {}
        if var.get("bindKind") != "instance":
            continue
        chisel = (var.get("representations", {}) or {}).get(ctx.authoring_repr, {}) or {}
        if chisel.get("name") == as_key:
            return var_id
    return None


def _instance_bind(var_id: str, ctx: BaseContext) -> Optional[Dict[str, Any]]:
    """A bound instance's ports, one entry per top-level port name
    (`clock`/`reset`/`io`/...), each either a scalar leaf or -- for an
    aggregate port -- the same dotted-path flattened map a regular
    aggregate `Variable.target.verilog` uses (`_walk_bind` rooted one level
    down, at the port's own type). Unlike `_variable_bind`, the top level
    itself is never flattened: an instance's own `typeRef` names its
    ports' *positions*, not a single value tree, so `portPath` starts
    fresh at each port."""
    var = ctx.variables.get(var_id) or {}
    type_def = ctx.types.get(var.get("typeRef", "")) or {}
    if type_def.get("kind") != "struct":
        return None
    out: Dict[str, Any] = {}
    for member, child_id in zip(type_def.get("members") or [], var.get("memberRefs") or []):
        name = member["name"]
        member_type_ref = member.get("typeRef", "")
        if _is_aggregate(member_type_ref, ctx):
            sub: Dict[str, Any] = {}
            _walk_bind(child_id, None, member_type_ref, "", sub, ctx)
            if sub:
                out[name] = sub
        elif (leaf := _leaf(child_id, None, ctx)) is not None:
            out[name] = leaf
    return out or None


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
        if loc := _loc_dict(verilog.get("location")):
            target["loc"] = loc

        if (var_id := _find_instance_var(scope, ctx, as_key)) is not None:
            if bind := _instance_bind(var_id, ctx):
                target["verilog"] = bind

        if target:
            entry["target"] = target

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
                     resolve_root: Any, types_out: Dict[str, Any]) -> Dict[str, Any]:
    aggregated_leaves = _scope_aggregated_leaves(scope, ctx)

    kept: List[tuple] = []  # (var_id, var)
    for var_id in scope.get("variableRefs") or []:
        var = ctx.variables.get(var_id) or {}
        bind_kind = var.get("bindKind")
        if bind_kind == "synthetic":
            continue
        if bind_kind == "instance":
            # Folded into instances[<as>].target.verilog instead -- see
            # `_build_instances`/`_instance_bind`.
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
        if name and key != name:
            # key is the var_id, not name -- a name collision forced the
            # disambiguation (see `key` above); record the dropped name.
            source["name"] = name
        slt = chisel.get("sourceLangType") or {}
        type_name = slt.get("typeName")
        type_ref = var.get("typeRef", "")
        own_x: Optional[str] = None
        if type_name:
            m = _TYPE_NAME_PAT.match(type_name)
            own_x = m.group(2) if m else type_name
            if m and (binding := m.group(1)):
                source["binding"] = binding
        resolved_type_ref = resolve_root(type_ref, own_x)
        if own_x is not None:
            own_params = _render_params(slt.get("params"))
            type_params = (types_out.get(resolved_type_ref) or {}).get("source", {}).get("params")
            if own_params is not None and own_params != type_params:
                # This variable's own params aren't already recoverable
                # from `types[resolved_type_ref].source.params` (not
                # observed in the corpus -- see module docstring).
                source["params"] = own_params
        if loc := _loc_dict(chisel.get("location")):
            source["loc"] = loc

        entry: Dict[str, Any] = {"typeRef": resolved_type_ref}
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
            entry["target"] = bind_entry

        out[key] = entry
    return out
