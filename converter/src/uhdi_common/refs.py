"""Cross-pool reference resolvers.

Pure functions over `BaseContext` (requires pool accessors). Two categories:

  * `resolve_*`: lookup stable_id/ref -> resolved value (no raise, None/"" on miss)
  * `loc_*`: read Location dict fields with defaulting strategy (HGLDD packs into hgl_loc,
             hgdb stores as separate columns)"""
from __future__ import annotations

from typing import Any, Dict, Optional, cast

from .context import BaseContext


def resolve_sig_name(stable_id: str, ctx: BaseContext) -> Optional[str]:
    """Map stable_id to simulation-side sig_name.

    Lookup chain: representations[<sim>].value.sigName, then .name (DCE'd ports).
    Returns None on miss (caller chooses fallback)."""
    var = ctx.variables.get(stable_id)
    if var is None:
        return None
    sim_repr = (var.get("representations", {}) or {}).get(
        ctx.simulation_repr, {}) or {}
    value = sim_repr.get("value") if isinstance(sim_repr, dict) else None
    sig = value.get("sigName") if isinstance(value, dict) else None
    resolved = sig or sim_repr.get("name") or None
    # str() cast for mypy: jsonschema-shaped dicts give Any at every step.
    return str(resolved) if resolved is not None else None


def resolve_authoring_name(stable_id: str, ctx: BaseContext) -> Optional[str]:
    """Authoring-repr name (visible in user's HDL source).

    Used by hgdb for generator_variable.name (Generator pane in hgdb-VSCode)."""
    var = ctx.variables.get(stable_id)
    if var is None:
        return None
    name = ((var.get("representations", {}) or {})
            .get(ctx.authoring_repr, {}) or {}).get("name") or None
    return str(name) if name is not None else None


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




def loc_file_path(loc: Optional[Dict[str, Any]], repr_key: str,
                  ctx: BaseContext) -> Optional[str]:
    """Resolve `loc.file` index into representations[repr_key].files string.

    Returns None on missing loc, out-of-range index, or missing files list."""
    if not loc:
        return None
    files = (ctx.representations.get(repr_key, {}) or {}).get("files") or []
    idx = loc.get("file", 0)
    if not (0 <= idx < len(files)):
        return None
    return str(files[idx])


def loc_line(loc: Optional[Dict[str, Any]]) -> int:
    """Coerce loc.beginLine to int with 0 fallback (hgdb requires NOT NULL)."""
    return int((loc or {}).get("beginLine", 0) or 0)


def loc_column(loc: Optional[Dict[str, Any]]) -> int:
    """Coerce loc.beginColumn to int with 0 fallback (hgdb requires NOT NULL)."""
    return int((loc or {}).get("beginColumn", 0) or 0)
