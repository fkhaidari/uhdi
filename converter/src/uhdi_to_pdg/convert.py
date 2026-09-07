"""uhdi -> PDG (chiseltrace format). See uhdi-spec.md Sec.15.5.

Pipeline:

  1. _collect_scopes  -- walk uhdi.top + instantiates; flat per-module list.
  2. _emit_variables  -- per scope: ports/regs/wires/literals -> vertices,
                         probes -> predicates. Build (scope_id,var_id) ->
                         vertex_index map.
  3. _emit_body       -- pre-order walk of scope.body (Sec.15.5.2):
                         block       -> ControlFlow vertex (+ recurse)
                         connect     -> Connection vertex
                         decl        -> already emitted in step 2
                         assert/...  -> ControlFlow with annotation
                         Build cfg[] mirroring this structure.
  4. _emit_edges      -- project Sec.10 dataflow if present, otherwise call
                         derive_dataflow() (synthesise from Sec.5/Sec.7)."""
from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from uhdi_common.backend import Backend, register
from uhdi_common.context import BaseContext, ConversionError
from uhdi_common.refs import (
    loc_column,
    loc_file_path,
    loc_line,
    resolve_authoring_name,
    resolve_var_by_ref,
    root_scopes,
)


class PDGConversionError(ConversionError):
    pass


# Map uhdi Sec.6 bindKind -> chiseltrace PDGSpecNodeKind for variable vertices.
# Probes are pulled out separately (Sec.15.5.1) into the predicates[] list.
_BIND_TO_KIND: Dict[str, str] = {
    "port":    "IO",
    "wire":    "DataDefinition",
    "node":    "DataDefinition",
    "literal": "DataDefinition",
    "reg":     "Definition",
    "mem":     "Definition",
}
_PROBE_BINDKINDS = frozenset({"probe", "rwprobe"})

# firtool --emit-uhdi maps both wire and reg to bindKind="node" when the
# variable is preserved but its kind information lives in
# sourceLangType.typeName ("Reg[...]" / "Mem[...]").  Fall back to that
# field when bindKind alone is not enough.
def _is_clocked(var: Dict[str, Any]) -> bool:
    if (var.get("bindKind") or "") in ("reg", "mem"):
        return True
    type_name: str = (
        ((var.get("representations") or {}).get("chisel") or {})
        .get("sourceLangType", {})
        .get("typeName", "")
    )
    return type_name.startswith(("Reg[", "Mem[", "SyncReadMem[", "Mem."))


# Sec.10 edge kinds that chiseltrace's PDGSpecEdgeKind enum does not have.
# Sec.15.7 compatibility matrix: PDG keeps only the `clocked` bit from clocks/resets.
_DROPPED_EDGE_KINDS = frozenset({"Clock", "Reset"})


@dataclass
class _Ctx(BaseContext):
    require_dataflow: bool = False
    # (scope_id, var_id) -> index into vertices[] (or predicates[] for probes).
    var_to_vertex: Dict[Tuple[str, str], int] = field(default_factory=dict)
    var_to_predicate: Dict[Tuple[str, str], int] = field(default_factory=dict)
    # Reverse instance map: scope_id -> [(parent_scope_id, instance_name), ...].
    # First entry wins for modulePath; matches "first emitted instance" order.
    parents: Dict[str, List[Tuple[str, str]]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Scope traversal
# ---------------------------------------------------------------------------


def _collect_scopes(ctx: _Ctx) -> List[str]:
    """Reachable scopes via uhdi.top + instantiates, DFS, dedup preserving order.
    Cycles are silently broken (already-visited scopes are skipped)."""
    top_ids = list(root_scopes(ctx))
    order: List[str] = []
    seen: Set[str] = set()

    def visit(scope_id: str, parent: Optional[str], inst_name: Optional[str]):
        if parent is not None and inst_name is not None:
            ctx.parents.setdefault(scope_id, []).append((parent, inst_name))
        if scope_id in seen:
            return
        seen.add(scope_id)
        order.append(scope_id)
        scope = ctx.scopes.get(scope_id) or {}
        for inst in scope.get("instantiates") or []:
            if not isinstance(inst, dict):
                continue
            target = inst.get("scopeRef")
            if not target:
                continue
            visit(target, scope_id, inst.get("as") or target)

    for top in top_ids:
        if top not in ctx.scopes:
            raise PDGConversionError(f"top references unknown scope {top!r}")
        visit(top, None, None)
    return order


def _module_path(scope_id: str, ctx: _Ctx) -> List[str]:
    """First-parent chain from top to this scope. For an un-instantiated top,
    just [scope.name]. Matches chiseltrace's per-instance modulePath."""
    path: List[str] = []
    cur = scope_id
    seen: Set[str] = set()
    while cur and cur not in seen:
        seen.add(cur)
        parents = ctx.parents.get(cur) or []
        if not parents:
            scope = ctx.scopes.get(cur) or {}
            path.insert(0, scope.get("name") or cur)
            break
        parent_id, inst_name = parents[0]
        path.insert(0, inst_name)
        cur = parent_id
        # Stash the top module's own name at the front when we finish.
    if cur in seen:
        # Cycle guard: just prepend whatever name we have for `cur`.
        return path
    return path


# ---------------------------------------------------------------------------
# Location / metadata helpers
# ---------------------------------------------------------------------------


def _loc_fields(loc: Optional[Dict[str, Any]], ctx: _Ctx) -> Dict[str, Any]:
    """Common PDGSpecNode location triple. PDG schema requires non-null
    file/line/char; fall back to "" / 0 when uhdi has no authoring loc."""
    path = loc_file_path(loc, ctx.authoring_repr, ctx) or ""
    return {"file": path, "line": loc_line(loc), "char": loc_column(loc)}


def _authoring_loc(obj: Dict[str, Any], ctx: _Ctx) -> Optional[Dict[str, Any]]:
    reprs = obj.get("representations") or {}
    return (reprs.get(ctx.authoring_repr) or {}).get("location")


def _stmt_loc(stmt: Dict[str, Any], ctx: _Ctx) -> Optional[Dict[str, Any]]:
    locs = stmt.get("locations") or {}
    return locs.get(ctx.authoring_repr) if isinstance(locs, dict) else None


def _related_signal(var: Dict[str, Any], ctx: _Ctx) -> Optional[Dict[str, str]]:
    """Split authoring name on first `.` or `[` -- mirrors chiseltrace bundles.
    `io.in` -> {signalPath: "io", fieldPath: ".in"}; flat names -> null."""
    name = ((var.get("representations") or {})
            .get(ctx.authoring_repr, {}) or {}).get("name") or ""
    for i, ch in enumerate(name):
        if ch in ".[":
            return {"signalPath": name[:i], "fieldPath": name[i:]}
    return None


# ---------------------------------------------------------------------------
# Vertex emission
# ---------------------------------------------------------------------------


def _io_name(var: Dict[str, Any]) -> str:
    name = ((var.get("representations") or {}).get("chisel", {}) or {}).get("name") \
           or var.get("representations", {}).get("verilog", {}).get("name", "") or ""
    direction = var.get("direction") or "port"
    return f"{direction}_{name}" if name else f"{direction}_unnamed"


def _var_vertex(scope_id: str, var_id: str, var: Dict[str, Any], ctx: _Ctx
                ) -> Optional[Dict[str, Any]]:
    """Build a PDGSpecNode for one variable. Returns None for probes (they
    go to predicates[] via _probe_vertex instead)."""
    bind = var.get("bindKind") or ""
    if bind in _PROBE_BINDKINDS:
        return None
    kind = _BIND_TO_KIND.get(bind)
    if kind is None:
        return None

    authoring = resolve_authoring_name(var_id, ctx) or var_id
    if kind == "IO":
        name = _io_name(var)
        assigns_to: Optional[str] = None
    elif kind == "Definition":
        prefix = "Reg" if bind == "reg" else "Mem"
        type_ref = var.get("typeRef") or ""
        name = f"{prefix}[{type_ref}]_{authoring}" if type_ref \
               else f"{prefix}_{authoring}"
        assigns_to = None
    else:  # DataDefinition (wire / node / literal)
        name = authoring
        assigns_to = authoring

    clocked = _is_clocked(var)
    is_chisel = (var.get("representations") or {}).get("chisel") is not None

    node = _loc_fields(_authoring_loc(var, ctx), ctx)
    node.update({
        "name": name,
        "kind": kind,
        "clocked": clocked,
        "modulePath": _module_path(scope_id, ctx),
        "relatedSignal": _related_signal(var, ctx),
        "assignsTo": assigns_to,
        "isChiselStatement": bool(is_chisel),
        "condition": None,
        "assignDelay": 0,
    })
    return node


def _probe_vertex(var_id: str, var: Dict[str, Any], scope_id: str, ctx: _Ctx
                  ) -> Dict[str, Any]:
    """Probe variable -> entry in predicates[]. Same node shape as a regular
    DataDefinition vertex; isChiselStatement is false (compiler-synthesised)."""
    authoring = resolve_authoring_name(var_id, ctx) or var_id
    node = _loc_fields(_authoring_loc(var, ctx), ctx)
    node.update({
        "name": authoring,
        "kind": "DataDefinition",
        "clocked": False,
        "modulePath": _module_path(scope_id, ctx),
        "relatedSignal": None,
        "assignsTo": authoring,
        "isChiselStatement": False,
        "condition": None,
        "assignDelay": 0,
    })
    return node


# ---------------------------------------------------------------------------
# Body flattening (Sec.15.5.2)
# ---------------------------------------------------------------------------


def _connection_vertex(stmt: Dict[str, Any], scope_id: str, ctx: _Ctx
                       ) -> Dict[str, Any]:
    target_ref = stmt.get("varRef") or ""
    target_var = resolve_var_by_ref(target_ref, ctx)
    target_name = resolve_authoring_name(target_ref, ctx) or target_ref
    clocked = _is_clocked(target_var) if target_var else False

    node = _loc_fields(_stmt_loc(stmt, ctx), ctx)
    node.update({
        "name": f"connect_{target_name}",
        "kind": "Connection",
        "clocked": clocked,
        "modulePath": _module_path(scope_id, ctx),
        "relatedSignal": _related_signal(target_var, ctx) if target_var else None,
        "assignsTo": target_name,
        "isChiselStatement": True,
        "condition": None,
        "assignDelay": 0,
    })
    return node


def _controlflow_vertex(guard_ref: Optional[str], stmt: Dict[str, Any],
                        scope_id: str, ctx: _Ctx,
                        annotation: Optional[str] = None) -> Dict[str, Any]:
    guard_name = resolve_authoring_name(guard_ref or "", ctx) or (guard_ref or "")
    label = f"cond_on_[{guard_name}]" if guard_name else "cond_on_[unknown]"
    if annotation:
        label = f"{annotation}_{label}"

    # uhdi blocks rarely carry their own `locations` (only the inner connect
    # does, see Sec.7).  PDG slicers display vertex line/file as the "where this
    # control branch is in source"; fall back to the guard variable's loc so
    # the CF vertex isn't anchored at 0:0.
    loc = _stmt_loc(stmt, ctx)
    if loc is None and guard_ref:
        guard_var = resolve_var_by_ref(guard_ref, ctx)
        if guard_var:
            loc = _authoring_loc(guard_var, ctx)
    node = _loc_fields(loc, ctx)
    node.update({
        "name": label,
        "kind": "ControlFlow",
        "clocked": False,
        "modulePath": _module_path(scope_id, ctx),
        "relatedSignal": None,
        "assignsTo": None,
        "isChiselStatement": True,
        "condition": None,
        "assignDelay": 0,
    })
    return node


def _walk_body(body: List[Dict[str, Any]], scope_id: str, ctx: _Ctx,
               vertices: List[Dict[str, Any]],
               # statement-vertex bookkeeping for Sec.10 derivation:
               stmt_vertex_index: List[int],
               stmt_guard_chain: List[List[int]],
               guard_chain: List[int],
               ) -> List[Dict[str, Any]]:
    """Pre-order traversal. Returns CFG[] for this body level.
    Side effect: appends Connection / ControlFlow vertices to `vertices`.

    `stmt_vertex_index` parallels `vertices` (one entry per appended vertex)
    so the derivation pass can map stmt-position -> vertex index.
    `stmt_guard_chain` parallels too: the list of ControlFlow vertex indices
    that surround each appended statement vertex (for Conditional edges)."""
    cfg: List[Dict[str, Any]] = []
    i = 0
    while i < len(body):
        stmt = body[i]
        kind = stmt.get("kind")
        if kind == "block":
            guard_ref = stmt.get("guardRef")
            cf = _controlflow_vertex(guard_ref, stmt, scope_id, ctx)
            cf_idx = len(vertices)
            vertices.append(cf)
            stmt_vertex_index.append(cf_idx)
            stmt_guard_chain.append(list(guard_chain))
            # The block guard itself sees only the OUTER chain;
            # the body of the block sees this CF added.
            inner_chain = guard_chain + [cf_idx]
            sub = _walk_body(stmt.get("body") or [], scope_id, ctx, vertices,
                             stmt_vertex_index, stmt_guard_chain, inner_chain)
            entry: Dict[str, Any] = {"stmtRef": cf_idx}
            # PDG predStmtRef references predicates[]; we wire it when the
            # guard is a probe variable (see _resolve_predicate_index).
            pred_idx = _resolve_predicate_index(guard_ref, ctx)
            if pred_idx is not None:
                entry["predStmtRef"] = pred_idx
            if sub:
                entry["trueBranch"] = sub
            # Lookahead: pair with negated sibling (otherwise-branch).
            nxt = body[i + 1] if i + 1 < len(body) else None
            if (
                nxt is not None
                and nxt.get("kind") == "block"
                and nxt.get("guardRef") == guard_ref
                and guard_ref is not None
                and nxt.get("negated") is True
                and stmt.get("negated") is not True
            ):
                false_sub = _walk_body(nxt.get("body") or [], scope_id, ctx,
                                       vertices, stmt_vertex_index,
                                       stmt_guard_chain, inner_chain)
                entry["falseBranch"] = false_sub if false_sub else None
                i += 2
            else:
                entry["falseBranch"] = None
                i += 1
            cfg.append(entry)
        elif kind == "connect":
            vertex = _connection_vertex(stmt, scope_id, ctx)
            v_idx = len(vertices)
            vertices.append(vertex)
            stmt_vertex_index.append(v_idx)
            stmt_guard_chain.append(list(guard_chain))
            cfg.append({"stmtRef": v_idx})
            i += 1
        elif kind == "decl":
            # Variable vertex was already emitted in the per-scope pre-pass;
            # the CFG carries a stmtRef to it so consumers see the declaration
            # in source order.
            var_ref = stmt.get("varRef") or ""
            decl_idx: Optional[int] = ctx.var_to_vertex.get((scope_id, var_ref))
            if decl_idx is None:
                canonical = _resolve_var_id(var_ref, ctx) or ""
                decl_idx = ctx.var_to_vertex.get((scope_id, canonical))
            if decl_idx is not None:
                cfg.append({"stmtRef": decl_idx})
            i += 1
        elif kind in ("assert", "assume", "cover"):
            cond_ref = stmt.get("condRef")
            cf = _controlflow_vertex(cond_ref, stmt, scope_id, ctx, annotation=kind)
            v_idx = len(vertices)
            vertices.append(cf)
            stmt_vertex_index.append(v_idx)
            stmt_guard_chain.append(list(guard_chain))
            cfg.append({"stmtRef": v_idx})
            i += 1
        else:
            # "none" / unknown: skip silently (spec Sec.15.5.2).
            i += 1
    return cfg


def _resolve_var_id(ref: str, ctx: _Ctx) -> Optional[str]:
    """ref might be stable_id or authoring name; return canonical stable_id."""
    if not ref:
        return None
    if ref in ctx.variables:
        return ref
    return ctx._var_id_by_authoring_name.get(ref)


def _resolve_predicate_index(ref: Optional[str], ctx: _Ctx) -> Optional[int]:
    if not ref:
        return None
    # Direct varRef / authoring-name case.
    canonical = _resolve_var_id(ref, ctx)
    if canonical is None:
        # exprRef case: resolve through the expression, accept iff it points
        # at exactly one probe variable. Multi-probe expressions cannot map
        # to a single predStmtRef slot in the PDG schema.
        candidates = _expand_guard(ref, ctx)
        probes = [
            c for c in candidates
            if (rid := _resolve_var_id(c, ctx)) is not None
            and (ctx.variables.get(rid) or {}).get("bindKind") in _PROBE_BINDKINDS
        ]
        if len(probes) != 1:
            return None
        canonical = _resolve_var_id(probes[0], ctx)
        if canonical is None:
            return None
    var = ctx.variables.get(canonical) or {}
    if var.get("bindKind") not in _PROBE_BINDKINDS:
        return None
    scope_id = var.get("ownerScopeRef")
    if scope_id is None:
        return None
    return ctx.var_to_predicate.get((scope_id, canonical))


# ---------------------------------------------------------------------------
# Edge projection (Sec.15.5.1) and derivation (Sec.15.5.4)
# ---------------------------------------------------------------------------


def _endpoint_to_vertices(endpoint: Dict[str, Any], ctx: _Ctx) -> List[int]:
    """An Sec.10 EndpointRef is `{varRef}` or `{exprRef}`. A varRef resolves to
    at most one vertex; an exprRef fans out over its constituent varRefs
    (mirrors _derive_edges's _collect_expr_vars fan-out for Sec.5 expressions)."""
    if not isinstance(endpoint, dict):
        return []
    if (vref := endpoint.get("varRef")):
        idx = _vertex_for_varref(vref, ctx)
        return [idx] if idx is not None else []
    if (eref := endpoint.get("exprRef")):
        vrefs: List[str] = []
        _collect_expr_vars({"exprRef": eref}, ctx, vrefs, set())
        out: List[int] = []
        seen: Set[int] = set()
        for vref in vrefs:
            idx = _vertex_for_varref(vref, ctx)
            if idx is not None and idx not in seen:
                seen.add(idx)
                out.append(idx)
        return out
    return []


def _project_explicit_edges(ctx: _Ctx) -> List[Dict[str, Any]]:
    """Direct copy of Sec.10 edges, with kind filtering (Sec.15.7) and condition
    dropped when it isn't already in PDG `{probeName, probeValue}` shape.
    Compound exprRef endpoints fan out over their constituent varRefs
    (Sec.10 allows compound named exprRefs as EndpointRef per spec Sec.10)."""
    dataflow = ctx.uhdi.get("dataflow") or {}
    edges_in = dataflow.get("edges") or []
    out: List[Dict[str, Any]] = []
    for edge in edges_in:
        if not isinstance(edge, dict):
            continue
        kind = edge.get("kind")
        if kind in _DROPPED_EDGE_KINDS or kind is None:
            continue
        frm_vs = _endpoint_to_vertices(edge.get("from") or {}, ctx)
        to_vs = _endpoint_to_vertices(edge.get("to") or {}, ctx)
        if not frm_vs or not to_vs:
            continue
        cond = edge.get("condition")
        if not (isinstance(cond, dict) and "probeName" in cond):
            cond = None
        clocked = bool(edge.get("clocked", False))
        for frm in frm_vs:
            for to in to_vs:
                if frm == to:
                    continue  # self-edge on flattened compound: skip
                out.append({
                    "from": frm,
                    "to": to,
                    "kind": kind,
                    "clocked": clocked,
                    "condition": cond,
                })
    return out


def _collect_expr_vars(operand: Any, ctx: _Ctx,
                       acc: List[str], seen_exprs: Set[str]) -> None:
    """Flatten an Sec.5 expression / endpoint into the list of varRefs it touches.
    Cycle guard raises on back-edge to match uhdi_common.expressions.walk."""
    if not isinstance(operand, dict):
        return
    if (vref := operand.get("varRef")):
        acc.append(vref)
        return
    if (eref := operand.get("exprRef")):
        if eref in seen_exprs:
            raise PDGConversionError(
                f"cycle in expression graph at exprRef {eref!r}")
        target = ctx.expressions.get(eref)
        if target is None:
            return
        _collect_expr_vars(target, ctx, acc, seen_exprs | {eref})
        return
    if "operands" in operand:
        for op in operand.get("operands") or []:
            _collect_expr_vars(op, ctx, acc, seen_exprs)


def _derive_edges(ctx: _Ctx,
                  body_stmt_index: List[int],
                  body_guard_chain: List[List[int]],
                  body_stmts_flat: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Synthesise PDG edges from Sec.5/Sec.7 when Sec.10 dataflow is absent.

    Per spec Sec.15.5.4 / chiseltrace example.pdg.jsonc:
      * connect target <- valueRef expression  =>  Data edge (per referenced var)
      * connect target reg/mem                 =>  Declaration edge to its def
      * connect inside block                   =>  Conditional edge to CF vertex"""
    edges: List[Dict[str, Any]] = []
    for stmt, v_idx, chain in zip(body_stmts_flat, body_stmt_index, body_guard_chain):
        kind = stmt.get("kind")
        if kind == "connect":
            # Data: walk valueRef expr tree, emit one edge per varRef.
            value = stmt.get("valueRef") or {}
            refs: List[str] = []
            _collect_expr_vars(value, ctx, refs, set())
            for vref in refs:
                target_idx = _vertex_for_varref(vref, ctx)
                if target_idx is not None and target_idx != v_idx:
                    edges.append({
                        "from": v_idx,
                        "to": target_idx,
                        "kind": "Data",
                        "clocked": False,
                        "condition": None,
                    })
            # Declaration: connect -> its target var vertex.
            target_ref = stmt.get("varRef") or ""
            decl_idx = _vertex_for_varref(target_ref, ctx)
            if decl_idx is not None and decl_idx != v_idx:
                edges.append({
                    "from": v_idx,
                    "to": decl_idx,
                    "kind": "Declaration",
                    "clocked": False,
                    "condition": None,
                })
            # Conditional: one edge per enclosing ControlFlow vertex.
            for cf_idx in chain:
                edges.append({
                    "from": v_idx,
                    "to": cf_idx,
                    "kind": "Conditional",
                    "clocked": False,
                    "condition": None,
                })
        elif kind == "block":
            # ControlFlow vertex's data dep is on its guard.  guardRef may
            # name a variable directly or an expression (see Sec.7); in the
            # expression case, fan out edges over every referenced var.
            guard_ref = stmt.get("guardRef") or ""
            for vref in _expand_guard(guard_ref, ctx):
                guard_idx = _vertex_for_varref(vref, ctx)
                if guard_idx is not None and guard_idx != v_idx:
                    edges.append({
                        "from": v_idx,
                        "to": guard_idx,
                        "kind": "Data",
                        "clocked": False,
                        "condition": None,
                    })
        elif kind in ("assert", "assume", "cover"):
            # Data: condRef may be a varRef or exprRef; fan out over all
            # referenced vars (mirrors block's guardRef handling).
            cond_ref = stmt.get("condRef") or ""
            for vref in _expand_guard(cond_ref, ctx):
                cond_idx = _vertex_for_varref(vref, ctx)
                if cond_idx is not None and cond_idx != v_idx:
                    edges.append({
                        "from": v_idx,
                        "to": cond_idx,
                        "kind": "Data",
                        "clocked": False,
                        "condition": None,
                    })
            # Conditional: one edge per enclosing ControlFlow vertex.
            for cf_idx in chain:
                edges.append({
                    "from": v_idx,
                    "to": cf_idx,
                    "kind": "Conditional",
                    "clocked": False,
                    "condition": None,
                })
    return edges


def _expand_guard(ref: str, ctx: _Ctx) -> List[str]:
    """A guardRef can name a variable (when-condition is a plain signal) or
    an expression (a compound predicate). Resolve to the underlying varRefs
    so the derivation pass can emit Data edges from the CF vertex to each."""
    if not ref:
        return []
    if _resolve_var_id(ref, ctx) is not None:
        return [ref]
    expr = ctx.expressions.get(ref)
    if expr is None:
        return []
    acc: List[str] = []
    _collect_expr_vars(expr, ctx, acc, set())
    return acc


def _vertex_for_varref(ref: str, ctx: _Ctx) -> Optional[int]:
    canonical = _resolve_var_id(ref, ctx)
    if canonical is None:
        return None
    var = ctx.variables.get(canonical) or {}
    owner = var.get("ownerScopeRef")
    if owner is None:
        return None
    return ctx.var_to_vertex.get((owner, canonical))


# ---------------------------------------------------------------------------
# Top-level convert
# ---------------------------------------------------------------------------


def convert(uhdi: Dict[str, Any], *,
            require_dataflow: bool = False) -> Dict[str, Any]:
    """Translate a uhdi document to chiseltrace's PDGSpec JSON shape.

    `require_dataflow=True` errors out when Sec.10 is missing; default behaviour
    derives a best-effort dataflow from Sec.5/Sec.7 (lossy -- see Sec.15.5.4)."""
    try:
        ctx = _Ctx.from_uhdi(uhdi, require_dataflow=require_dataflow)
    except ConversionError as e:
        raise PDGConversionError(str(e)) from None

    try:
        scope_order = _collect_scopes(ctx)

        vertices: List[Dict[str, Any]] = []
        predicates: List[Dict[str, Any]] = []
        cfg: List[Dict[str, Any]] = []

        # Phase 1: emit variable vertices and probe predicates per scope.
        for scope_id in scope_order:
            scope = ctx.scopes.get(scope_id) or {}
            for vid in scope.get("variableRefs") or []:
                var = ctx.variables.get(vid)
                if var is None:
                    continue
                if (var.get("bindKind") or "") in _PROBE_BINDKINDS:
                    ctx.var_to_predicate[(scope_id, vid)] = len(predicates)
                    predicates.append(_probe_vertex(vid, var, scope_id, ctx))
                    continue
                vertex = _var_vertex(scope_id, vid, var, ctx)
                if vertex is None:
                    continue
                ctx.var_to_vertex[(scope_id, vid)] = len(vertices)
                vertices.append(vertex)

        # Phase 2: walk bodies (statement vertices + CFG).
        body_stmt_index: List[int] = []
        body_guard_chain: List[List[int]] = []
        body_stmts_flat: List[Dict[str, Any]] = []

        for scope_id in scope_order:
            scope = ctx.scopes.get(scope_id) or {}
            stmts_before = len(body_stmt_index)
            sub_cfg = _walk_body(scope.get("body") or [], scope_id, ctx,
                                 vertices, body_stmt_index, body_guard_chain, [])
            cfg.extend(sub_cfg)
            # Mirror the flat-stmt list with the order vertices were appended.
            _flatten_stmts(scope.get("body") or [], body_stmts_flat)
            assert len(body_stmts_flat) - stmts_before == \
                   len(body_stmt_index) - stmts_before, "stmt/vertex bookkeeping mismatch"

        # Phase 3: edges.
        has_explicit = bool((ctx.uhdi.get("dataflow") or {}).get("edges"))
        if has_explicit:
            edges = _project_explicit_edges(ctx)
        elif require_dataflow:
            raise PDGConversionError(
                "input has no Sec.10 dataflow but --require-dataflow was passed; "
                "use --derive-dataflow to synthesise a best-effort graph "
                "from Sec.5/Sec.7 (uhdi-spec.md Sec.15.5.4)")
        else:
            edges = _derive_edges(ctx, body_stmt_index, body_guard_chain,
                                  body_stmts_flat)

        return {
            "vertices": vertices,
            "edges": edges,
            "predicates": predicates,
            "cfg": cfg,
        }
    except RecursionError:
        raise PDGConversionError(
            "recursion limit exceeded (deep exprRef chain or nested type)"
        ) from None


def _flatten_stmts(body: List[Dict[str, Any]],
                   out: List[Dict[str, Any]]) -> None:
    """Pre-order flatten mirroring _walk_body's vertex-emit order.
    Skips `decl` (no statement vertex emitted) and `none`."""
    i = 0
    while i < len(body):
        stmt = body[i]
        kind = stmt.get("kind")
        if kind == "block":
            guard_ref = stmt.get("guardRef")
            out.append(stmt)
            _flatten_stmts(stmt.get("body") or [], out)
            # Lookahead: pair with negated sibling (otherwise-branch).
            nxt = body[i + 1] if i + 1 < len(body) else None
            if (
                nxt is not None
                and nxt.get("kind") == "block"
                and nxt.get("guardRef") == guard_ref
                and guard_ref is not None
                and nxt.get("negated") is True
                and stmt.get("negated") is not True
            ):
                _flatten_stmts(nxt.get("body") or [], out)
                i += 2
            else:
                i += 1
        elif kind in ("connect", "assert", "assume", "cover"):
            out.append(stmt)
            i += 1
        else:
            # `decl` and `none`: not vertex-emitting in _walk_body.
            i += 1


@register
class PDGBackend(Backend):
    name = "pdg"
    description = (
        "uhdi -> PDG (chiseltrace Program Dependency Graph format).  The "
        "third arm of the spec's three legacy targets; consumed by the "
        "chiseltrace slicer and its GUI.  When Sec.10 dataflow is present "
        "in the input, edges are projected directly; otherwise a "
        "best-effort derivation pass synthesises Data/Conditional/"
        "Declaration edges from Sec.5 expressions and Sec.7 body (lossier; "
        "see uhdi-spec.md Sec.15.5.4).")
    binary_output = False
    output_extension = "pdg.json"

    def convert(self,
                uhdi: Dict[str, Any],
                output: Optional[pathlib.Path] = None
                ) -> Dict[str, Any]:
        del output
        return convert(uhdi)
