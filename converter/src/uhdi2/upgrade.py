"""UHDI 1.0 -> UHDI 2.0 ("variant B") upgrader.

Reshapes the pool-based v1 document (flat `types`/`variables`/`scopes`
pools + cross-pool refs) into v2's nested per-module shape: `modules` keyed
by v1 scope id, each with inline `variables`/`instances`/`scopes`. `types`
carries over unchanged.

Two rules drive the reshape, both stated in terms of the *target* type
(struct/vector), never of ids:
  * a variable's `bind.verilog` is a flat scalar binding for a scalar
    variable, or a `dottedPath -> scalar` map for an aggregate one, walked
    from `memberRefs` (preferred) or, absent those, from a `'{`
    struct-literal `exprRef` chain -- both encodings occur in the
    fixtures and must be handled uniformly and recursively.
  * a `bindKind: port` variable is dropped when its own sigName is also
    reachable as a leaf of some other aggregate variable in the same
    scope (the producer's "emitter-added" duplicate port record).

`bindKind: synthetic` variables never become v2 records (their data is
folded into the parent aggregate's `bind`). `bindKind: instance` variables
are also dropped: none of the fixtures exercise them, so nothing here
maps them into `instances[].bind` (the target for a bound instance port,
per the v2 shape) -- see the upgrade report for that as an open item.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from uhdi_common.context import BaseContext

Binding = Union[str, Dict[str, Any]]


def upgrade(doc_v1: Dict[str, Any]) -> Dict[str, Any]:
    """Pure, deterministic v1 -> v2 reshape. Does not mutate `doc_v1`."""
    ctx = BaseContext.from_uhdi(doc_v1)
    reprs = ctx.representations

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
        "types": dict(ctx.types),
        "modules": {
            sid: _build_module(sid, ctx.scopes[sid], ctx)
            for sid in sorted(
                s for s, scope in ctx.scopes.items()
                if (scope or {}).get("kind", "module") in ("module", "extmodule")
            )
        },
    }
    return doc_v2


# ---- module / scope -------------------------------------------------------


def _build_module(scope_id: str, scope: Dict[str, Any],
                  ctx: BaseContext) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    kind = scope.get("kind", "module")
    if kind != "module":
        out["kind"] = kind

    if src := _module_source(scope, ctx):
        out["source"] = src
    if tgt := _module_target(scope, ctx):
        out["target"] = tgt

    out["variables"] = _build_variables(scope_id, scope, ctx)

    instances = _build_instances(scope, ctx)
    if instances:
        out["instances"] = instances

    inline = sorted(
        sid for sid, s in ctx.scopes.items()
        if (s or {}).get("kind") == "inline"
        and (s or {}).get("containerScopeRef") == scope_id
    )
    if inline:
        out["scopes"] = [
            {**_build_module(sid, ctx.scopes[sid], ctx), "kind": "inline"}
            for sid in inline
        ]

    return out


def _module_source(scope: Dict[str, Any], ctx: BaseContext) -> Dict[str, Any]:
    chisel = (scope.get("representations", {}) or {}).get(ctx.authoring_repr, {}) or {}
    out: Dict[str, Any] = {}
    if name := chisel.get("name"):
        out["name"] = name
    slt = chisel.get("sourceLangType") or {}
    if type_name := slt.get("typeName"):
        out["typeName"] = type_name
    _add_loc(out, chisel.get("location"))
    return out


def _module_target(scope: Dict[str, Any], ctx: BaseContext) -> Dict[str, Any]:
    verilog = (scope.get("representations", {}) or {}).get(ctx.simulation_repr, {}) or {}
    return {"name": verilog["name"]} if verilog.get("name") else {}


def _build_instances(scope: Dict[str, Any], ctx: BaseContext) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for inst in scope.get("instantiates") or []:
        as_key = inst.get("as") or inst.get("scopeRef")
        entry: Dict[str, Any] = {"moduleRef": inst.get("scopeRef")}
        verilog = (inst.get("representations", {}) or {}).get(
            ctx.simulation_repr, {}) or {}
        if verilog.get("name"):
            entry["target"] = {"name": verilog["name"]}
        # No fixture provides instance-port-binding data to populate
        # entry["bind"]["verilog"]; left unset (see module docstring).
        out[as_key] = entry
    return dict(sorted(out.items()))


# ---- variables --------------------------------------------------------


def _is_aggregate(type_ref: str, ctx: BaseContext) -> bool:
    return (ctx.types.get(type_ref) or {}).get("kind") in ("struct", "vector")


def _add_loc(out: Dict[str, Any], loc: Optional[Dict[str, Any]]) -> None:
    loc = loc or {}
    if "file" in loc:
        out["file"] = loc["file"]
    if "beginLine" in loc:
        out["line"] = loc["beginLine"]
    if "beginColumn" in loc:
        out["column"] = loc["beginColumn"]


def _scalar_binding(value: Any) -> Optional[Binding]:
    """Render a `PerRepresentation.value` object as a scalar Binding, or
    None if absent/unrecognised (e.g. a bare `varRef`, unused in fixtures)."""
    if not isinstance(value, dict):
        return None
    if "sigName" in value:
        return value["sigName"]
    if "constant" in value:
        return {"constant": value["constant"]}
    if "bitVector" in value:
        return {"bitVector": value["bitVector"]}
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
        return _scalar_binding(hdl.get("value") if isinstance(hdl, dict) else None)
    return _scalar_binding(op)


def _walk_bind(var_id: Optional[str], op: Optional[Dict[str, Any]],
              type_ref: str, prefix: str, out: Dict[str, str],
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
        out: Dict[str, str] = {}
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
            leaves.update(v for v in bind.values() if isinstance(v, str))
    return leaves


def _build_variables(scope_id: str, scope: Dict[str, Any],
                     ctx: BaseContext) -> Dict[str, Any]:
    aggregated_leaves = _scope_aggregated_leaves(scope, ctx)

    kept: List[tuple] = []  # (var_id, var)
    for var_id in scope.get("variableRefs") or []:
        var = ctx.variables.get(var_id) or {}
        bind_kind = var.get("bindKind")
        if bind_kind in ("synthetic", "instance"):
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
        if type_name := slt.get("typeName"):
            source["typeName"] = type_name
        _add_loc(source, chisel.get("location"))

        entry: Dict[str, Any] = {
            "typeRef": var.get("typeRef", ""),
            "role": "port" if var.get("bindKind") == "port" else "node",
        }
        if direction := var.get("direction"):
            entry["direction"] = direction
        entry["source"] = source

        if (bind := _variable_bind(var_id, var, ctx)) is not None:
            entry["bind"] = {"verilog": bind}

        out[key] = entry
    return out
