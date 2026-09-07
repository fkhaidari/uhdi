"""Cross-pool reference resolvers.

Pure functions over `BaseContext` (requires pool accessors). Categories:

  * `resolve_*`: lookup stable_id/ref -> resolved value (no raise, None/"" on miss)
  * `loc_*`: read Location dict fields with defaulting strategy (HGLDD packs into hgl_loc,
             hgdb stores as separate columns)
  * `root_scopes`: ordered root scope ids, from `top` or derived
  * `owner_scope`: scope id owning a variable, from `ownerScopeRef` or derived"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, cast

from .context import BaseContext


def resolve_sig_name(ref: str, ctx: BaseContext) -> Optional[str]:
    """Map stable_id (or authoring name) to simulation-side sig_name.

    Lookup chain: representations[<sim>].value.sigName, then .name (DCE'd ports).
    `ref` may be a stable_id or an authoring name -- post-DCE CIRCT EmitUHDI
    can leave guardRef/enableRef as authoring names (issue #21).
    Returns None on miss (caller chooses fallback)."""
    var = resolve_var_by_ref(ref, ctx)
    if not var:
        return None
    sim_repr = (var.get("representations", {}) or {}).get(
        ctx.simulation_repr, {}) or {}
    value = sim_repr.get("value") if isinstance(sim_repr, dict) else None
    sig = value.get("sigName") if isinstance(value, dict) else None
    resolved = sig or sim_repr.get("name") or None
    # str() cast for mypy: jsonschema-shaped dicts give Any at every step.
    return str(resolved) if resolved is not None else None


def resolve_authoring_name(ref: str, ctx: BaseContext) -> Optional[str]:
    """Authoring-repr name (visible in user's HDL source).

    `ref` may be a stable_id or an authoring name. Used by hgdb for
    generator_variable.name (Generator pane in hgdb-VSCode)."""
    var = resolve_var_by_ref(ref, ctx)
    if not var:
        return None
    name = ((var.get("representations", {}) or {})
            .get(ctx.authoring_repr, {}) or {}).get("name") or None
    return str(name) if name is not None else None


def build_dotted_name_map(ctx: BaseContext) -> Dict[str, str]:
    """Map a flattened RTL-style name (`io_q`) to its dotted source name
    (`io.q`), reconstructed from the synthetic subfield variables the
    producer emits as `<parent_id>__<field>`.

    firtool flattens aggregate ports (`io.q` -> sig `io_q`); the authoring
    name of the flat port variable is the underscored leaf, so hgdb would
    register it under `io_q` and a source-level `io.q` lookup misses. The
    synthetic subfields carry the field structure (`io` bundle + field `q`),
    so joining their authoring names with `.` recovers the source path and
    with `_` recovers the flat key. Only entries where the two differ are
    returned, so scalar signals and literal underscore names are untouched."""
    def parts(vid: str) -> Optional[list]:
        leaf = resolve_authoring_name(vid, ctx)
        if leaf is None:
            return None
        if "__" not in vid:
            return [leaf]
        parent, _, _ = vid.rpartition("__")
        head = parts(parent)
        return None if head is None else head + [leaf]

    out: Dict[str, str] = {}
    for vid, var in ctx.variables.items():
        if var.get("bindKind") != "synthetic" or "__" not in vid:
            continue
        p = parts(vid)
        if not p:
            continue
        flat = "_".join(p)
        dotted = ".".join(p)
        if flat != dotted:
            out[flat] = dotted
    return out


def resolve_var_by_ref(ref: str, ctx: BaseContext) -> Dict[str, Any]:
    """Look up variable by either stable_id or authoring name.

    Circt's EmitUHDI may tag with `uhdi_stable_id`; otherwise pool uses authoring name.
    Returns variable dict or {} if unresolved."""
    if not ref:
        return {}
    if (direct := ctx.variables.get(ref)) is not None:
        return cast(Dict[str, Any], direct)
    if (vid := ctx._var_id_by_authoring_name.get(ref)) is not None:
        return cast(Dict[str, Any], ctx.variables[vid])
    return {}


def owner_scope(var_id: str, ctx: BaseContext) -> Optional[str]:
    """Scope id that owns a variable.

    `ownerScopeRef` when present (legacy format); otherwise the scope
    whose `variableRefs` lists the id. Format dropped the field --
    every variable appears in exactly one scope's `variableRefs`."""
    var = ctx.variables.get(var_id) or {}
    return var.get("ownerScopeRef") or ctx._scope_by_var_id.get(var_id)


def root_scopes(ctx: BaseContext) -> List[str]:
    """Ordered root scope ids to treat as top-level.

    Returns `uhdi["top"]` when present and non-empty. Otherwise derives
    roots from the instantiation graph: every `module`-kind scope that no
    other scope's `instantiates` references, in document order. Format
    dropped the required `top` field; this backfills it for documents
    that omit it."""
    top = ctx.uhdi.get("top")
    if top:
        return list(top)
    referenced = set()
    for scope in ctx.scopes.values():
        for inst in (scope or {}).get("instantiates") or []:
            ref = inst.get("scopeRef") if isinstance(inst, dict) else inst
            if ref:
                referenced.add(ref)
    return [sid for sid, scope in ctx.scopes.items()
            if (scope or {}).get("kind", "module") == "module"
            and sid not in referenced]


def loc_file_path(loc: Optional[Dict[str, Any]], repr_key: str,
                  ctx: BaseContext) -> Optional[str]:
    """Resolve `loc.file` index into representations[repr_key].files string.

    Returns None on missing loc, absent `file` key, non-int-coercible index,
    out-of-range index, or missing files list."""
    if not loc or "file" not in loc:
        return None
    files = (ctx.representations.get(repr_key, {}) or {}).get("files") or []
    try:
        idx = int(loc["file"])
    except (TypeError, ValueError):
        return None
    if not (0 <= idx < len(files)):
        return None
    return str(files[idx])


def loc_line(loc: Optional[Dict[str, Any]]) -> int:
    """Coerce loc.beginLine to int with 0 fallback (hgdb requires NOT NULL)."""
    return int((loc or {}).get("beginLine", 0) or 0)


def loc_column(loc: Optional[Dict[str, Any]]) -> int:
    """Coerce loc.beginColumn to int with 0 fallback (hgdb requires NOT NULL)."""
    return int((loc or {}).get("beginColumn", 0) or 0)
