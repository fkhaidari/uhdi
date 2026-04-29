"""uhdi -> hgdb JSON. Sibling of uhdi_to_hgdb (SQLite); hgdb-circt emits
this shape. ID assignment matches lib/Debug/HWDebug.cpp."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from uhdi_common.backend import Backend, register
from uhdi_common.context import BaseContext, ConversionError
from uhdi_common.refs import (
    loc_column,
    loc_file_path,
    loc_line,
    resolve_authoring_name,
    resolve_sig_name,
)


class HGDBJsonConversionError(ConversionError):
    pass


@dataclass
class _Ctx(BaseContext):
    pass


def _is_port(var: Dict[str, Any]) -> bool:
    return var.get("bindKind") == "port"


def _inline_var_def(stable_id: str, var: Dict[str, Any], ctx: _Ctx
                    ) -> Optional[Dict[str, Any]]:
    """Inline VarDef `{name, value, rtl}` for module-scope ports.
    DCE'd outputs fall back to authoring name (matches hgdb-circt)."""
    sig = resolve_sig_name(stable_id, ctx)
    name = resolve_authoring_name(stable_id, ctx) or stable_id
    if not sig:
        sig = name
    if not sig:
        return None
    return {"name": name, "value": sig, "rtl": True}


def _stmt_to_entry(stmt: Dict[str, Any], ctx: _Ctx,
                   id_for: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """`decl`/`connect`/`block` -> scope entry. None on unknown kinds."""
    kind = stmt.get("kind")
    locs = stmt.get("locations") or {}
    loc = locs.get(ctx.authoring_repr) if isinstance(locs, dict) else None

    if kind in ("decl", "connect"):
        var_ref = stmt.get("varRef") or ""
        var_id = id_for.get(var_ref)
        if var_id is None:
            return None
        entry: Dict[str, Any] = {
            "type": "decl" if kind == "decl" else "assign",
            "variable": var_id,
        }
        line = loc_line(loc)
        col = loc_column(loc)
        if line:
            entry["line"] = line
        if col:
            entry["column"] = col
        return entry

    if kind == "block":
        nested: List[Dict[str, Any]] = []
        for child in stmt.get("body") or []:
            child_entry = _stmt_to_entry(child, ctx, id_for)
            if child_entry is not None:
                nested.append(child_entry)
        block: Dict[str, Any] = {"type": "block", "scope": nested}
        guard = stmt.get("guardRef")
        if guard:
            cond = resolve_sig_name(guard, ctx) or guard
            if stmt.get("negated"):
                cond = f"!({cond})"
            block["condition"] = cond
        return block

    return None


def _module_filename(scope: Dict[str, Any], ctx: _Ctx) -> Optional[str]:
    """First body statement's filename (hgdb runtime walks scope chain
    for nearest Block with filename; outer block sets default)."""
    body = scope.get("body") or []
    for stmt in body:
        locs = stmt.get("locations") or {}
        loc = locs.get(ctx.authoring_repr) if isinstance(locs, dict) else None
        path = loc_file_path(loc, ctx.authoring_repr, ctx)
        if path:
            return path
    return None


def _module_entry(scope_id: str, scope: Dict[str, Any], ctx: _Ctx,
                  id_for: Dict[str, str]) -> Dict[str, Any]:
    """Build a `table[]` entry from a scope (ports inline, non-ports as refs)."""
    var_list: List[Any] = []
    for vid in scope.get("variableRefs") or []:
        var = ctx.variables.get(vid)
        if var is None:
            continue
        if _is_port(var):
            inline = _inline_var_def(vid, var, ctx)
            if inline is not None:
                var_list.append(inline)
        elif vid in id_for:
            var_list.append(id_for[vid])

    body_entries: List[Dict[str, Any]] = []
    for stmt in scope.get("body") or []:
        entry = _stmt_to_entry(stmt, ctx, id_for)
        if entry is not None:
            body_entries.append(entry)

    scope_list: List[Dict[str, Any]] = []
    if body_entries:
        outer: Dict[str, Any] = {"type": "block", "scope": body_entries}
        if (filename := _module_filename(scope, ctx)):
            outer["filename"] = filename
        scope_list.append(outer)

    out: Dict[str, Any] = {
        "type": "module",
        "name": scope.get("name") or scope_id,
        "variables": var_list,
        "scope": scope_list,
    }

    instances = []
    for inst in scope.get("instantiates") or []:
        if not isinstance(inst, dict):
            continue
        target = inst.get("scopeRef")
        target_scope = ctx.scopes.get(target, {}) if target else {}
        instances.append({
            "name": inst.get("as") or target or "",
            "module": target_scope.get("name") or target or "",
        })
    if instances:
        out["instances"] = instances
    return out


def _collect_body_refs(scopes: Dict[str, Any]) -> set:
    """stable_ids referenced from any body decl/connect (need pool entries
    so body statements can address them by id-string)."""
    refs: set = set()

    def walk(stmts):
        for stmt in stmts:
            kind = stmt.get("kind")
            if kind in ("decl", "connect"):
                if (ref := stmt.get("varRef")):
                    refs.add(ref)
            elif kind == "block":
                walk(stmt.get("body") or [])

    for scope in scopes.values():
        walk(scope.get("body") or [])
    return refs


def _build_global_pool(ctx: _Ctx) -> tuple[List[Dict[str, Any]], Dict[str, str]]:
    """Document-level `variables[]` + stable_id -> seq-id map. Body-referenced
    ports get double-listed (inline under module.variables[] and pooled),
    matching hgdb-circt."""
    body_refs = _collect_body_refs(ctx.scopes)

    pool: List[Dict[str, Any]] = []
    id_for: Dict[str, str] = {}
    next_id = 0
    for stable_id, var in ctx.variables.items():
        if stable_id not in body_refs:
            continue
        sig = resolve_sig_name(stable_id, ctx)
        if not sig:
            continue
        name = resolve_authoring_name(stable_id, ctx) or stable_id
        pool_id = str(next_id)
        next_id += 1
        id_for[stable_id] = pool_id
        pool.append({"id": pool_id, "name": name, "value": sig, "rtl": True})
    return pool, id_for


def convert(uhdi: Dict[str, Any]) -> Dict[str, Any]:
    """Translate a uhdi document into the hgdb JSON symbol table."""
    try:
        ctx = _Ctx.from_uhdi(uhdi)
    except ConversionError as e:
        raise HGDBJsonConversionError(str(e)) from None

    top_names = list(uhdi.get("top") or [])
    for sid in top_names:
        if sid not in ctx.scopes:
            raise HGDBJsonConversionError(
                f"top references unknown scope {sid!r}")

    pool, id_for = _build_global_pool(ctx)

    table: List[Dict[str, Any]] = []
    for top in top_names:
        scope = ctx.scopes.get(top)
        if scope is None:  # pragma: no cover
            continue
        table.append(_module_entry(top, scope, ctx, id_for))

    top_field: Any = (top_names[0] if len(top_names) == 1
                      else list(top_names))

    return {
        "generator": "uhdi",
        "top": top_field,
        "variables": pool,
        "table": table,
    }


@register
class HGDBJsonBackend(Backend):
    name = "hgdb_json"
    description = (
        "uhdi -> hgdb JSON symbol table.  Alternative to the SQLite "
        "format (uhdi_to_hgdb); both are first-class for the hgdb "
        "runtime.  This is what hgdb-circt's `firtool --hgdb=<file>` "
        "emits, so it's the bench's reference for comparing against "
        "the modern hgdb-circt path.")
    binary_output = False
    output_extension = "json"

    def convert(self,
                uhdi: Dict[str, Any],
                output: Optional[Any] = None
                ) -> Dict[str, Any]:
        del output
        return convert(uhdi)
