"""UHDI -> HGDB SQLite symbol-table converter."""
from __future__ import annotations

import os
import pathlib
import sqlite3
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from uhdi_common.backend import Backend, register
from uhdi_common.context import BaseContext, ConversionError
from uhdi_common.expressions import walk as walk_expression
from uhdi_common.refs import (
    loc_column,
    loc_file_path,
    loc_line,
    resolve_authoring_name,
    resolve_sig_name,
    resolve_var_by_ref,
)


class HGDBConversionError(ConversionError):
    pass


# Byte-for-byte aligned with hgdb's sqlite_orm (divergence = migration)
_SCHEMA = """
CREATE TABLE 'instance' ( 'id' INTEGER PRIMARY KEY NOT NULL , 'name' TEXT NOT NULL , 'annotation' TEXT NOT NULL );
CREATE TABLE 'variable' ( 'id' INTEGER PRIMARY KEY NOT NULL , 'value' TEXT NOT NULL , 'is_rtl' INTEGER NOT NULL );
CREATE TABLE 'breakpoint' ( 'id' INTEGER PRIMARY KEY NOT NULL , 'instance_id' INTEGER , 'filename' TEXT NOT NULL , 'line_num' INTEGER NOT NULL , 'column_num' INTEGER NOT NULL , 'condition' TEXT NOT NULL , 'trigger' TEXT NOT NULL , FOREIGN KEY('instance_id') REFERENCES 'instance'('id'));
CREATE TABLE 'scope' ( 'scope' INTEGER PRIMARY KEY NOT NULL , 'breakpoints' TEXT NOT NULL );
CREATE TABLE 'context_variable' ( 'name' TEXT NOT NULL , 'breakpoint_id' INTEGER , 'variable_id' INTEGER , 'type' INTEGER NOT NULL , FOREIGN KEY('breakpoint_id') REFERENCES 'breakpoint'('id'), FOREIGN KEY('variable_id') REFERENCES 'variable'('id'));
CREATE TABLE 'generator_variable' ( 'name' TEXT NOT NULL , 'instance_id' INTEGER , 'variable_id' INTEGER , 'annotation' TEXT NOT NULL , FOREIGN KEY('instance_id') REFERENCES 'instance'('id'), FOREIGN KEY('variable_id') REFERENCES 'variable'('id'));
CREATE TABLE 'annotation' ( 'name' TEXT NOT NULL , 'value' TEXT NOT NULL );
CREATE TABLE 'event' ( 'name' TEXT NOT NULL , 'transaction' TEXT NOT NULL , 'action' INTEGER NOT NULL , 'fields' TEXT NOT NULL , 'matches' TEXT NOT NULL , 'breakpoint_id' INTEGER , FOREIGN KEY('breakpoint_id') REFERENCES 'breakpoint'('id'));
CREATE TABLE 'assignment' ( 'name' TEXT NOT NULL , 'value' TEXT NOT NULL , 'breakpoint_id' INTEGER , 'condition' TEXT NOT NULL , 'scope_id' INTEGER , FOREIGN KEY('breakpoint_id') REFERENCES 'breakpoint'('id'), FOREIGN KEY('scope_id') REFERENCES 'scope'('scope'));
"""

# HGDB runtime's clock_names_ heuristic (src/rtl.hh)
_CLOCK_NAMES = {"clk", "clock", "clk_in", "clock_in", "CLK", "CLOCK"}


@dataclass
class _Ctx(BaseContext):
    next_variable_id: int = 1
    next_breakpoint_id: int = 1
    next_scope_id: int = 1
    next_instance_id: int = 1
    variable_ids: Dict[Tuple[str, str], int] = field(default_factory=dict)
    unresolved_sig_refs: Set[str] = field(default_factory=set)
    unresolved_locs: int = 0


def _emit_unresolved_warnings(ctx):
    """Print warnings for unresolved refs to stderr."""
    if not ctx.unresolved_sig_refs and not ctx.unresolved_locs:
        return
    if ctx.unresolved_sig_refs:
        sample = sorted(ctx.unresolved_sig_refs)[:5]
        more = len(ctx.unresolved_sig_refs) - len(sample)
        suffix = f" (+{more} more)" if more > 0 else ""
        print(f"warning: {len(ctx.unresolved_sig_refs)} stable_id(s) in guard "
              f"conditions did not resolve to a variable's verilog sig_name; "
              f"hgdb will see the raw token instead. e.g. {', '.join(sample)}"
              f"{suffix}", file=sys.stderr)
    if ctx.unresolved_locs:
        print(f"warning: {ctx.unresolved_locs} statement location(s) "
              f"referenced a file index outside the authoring "
              f"`representations` files list; their breakpoints were dropped",
              file=sys.stderr)


def _filename_for(loc, ctx):
    """Return path verbatim (including chisel's missing-slash quirk). "" on missing/unresolved."""
    if not loc:
        return ""
    raw = loc_file_path(loc, ctx.authoring_repr, ctx)
    if raw is None:
        ctx.unresolved_locs += 1
        return ""
    return raw


_line_for = loc_line
_column_for = loc_column


def _instance_rows(ctx):
    """Walk scope tree from `top`. Returns (rows, top_inst_ids).
    Raises on unknown top or instantiation cycles."""
    rows: List[Tuple[int, str, str, str]] = []
    top_inst_ids: List[int] = []
    on_path: set = set()

    def visit(path, scope_id):
        scope = ctx.scopes.get(scope_id)
        if scope is None:
            return None
        if scope_id in on_path:
            raise HGDBConversionError(
                f"instantiation cycle through scope '{scope_id}' at '{path}'")
        on_path.add(scope_id)
        try:
            inst_id = ctx.next_instance_id
            ctx.next_instance_id += 1
            rows.append((inst_id, path, scope_id, ""))
            for inst in scope.get("instantiates") or []:
                visit(f"{path}.{inst.get('as') or inst.get('scopeRef')}",
                      inst.get("scopeRef"))
            return inst_id
        finally:
            on_path.discard(scope_id)

    for top in ctx.uhdi.get("top") or []:
        if top not in ctx.scopes:
            raise HGDBConversionError(f"top references unknown scope '{top}'")
        top_id = visit(top, top)
        if top_id is not None:
            top_inst_ids.append(top_id)
    return rows, top_inst_ids


def _resolve_sig_name(stable_id, ctx):
    resolved = resolve_sig_name(stable_id, ctx)
    if not resolved:
        ctx.unresolved_sig_refs.add(stable_id)
        return stable_id
    return resolved


# SystemVerilog operator precedence (highest -> lowest), spec §15.4.2
_SV_PRECEDENCE = {
    "!": 12, "~": 12, "neg": 12,
    "*": 10, "/": 10, "%": 10,
    "+": 9, "-": 9,
    "<<": 8, ">>": 8, ">>>": 8,
    "<": 7, "<=": 7, ">": 7, ">=": 7,
    "==": 6, "!=": 6, "===": 6, "!==": 6, "==?": 6, "!=?": 6,
    "&": 5, "|": 3, "^": 4,
    "&&": 2, "||": 1,
    "?:": 0,
}


def _terminal_to_sv(operand, ctx):
    """Render non-opnode operand shapes; structural dispatch lives in walk()."""
    if not isinstance(operand, dict):
        return ""
    if "sigName" in operand:
        return operand["sigName"]
    if "constant" in operand:
        n = int(operand["constant"])
        if (w := int(operand.get("width", 0))) > 0:
            # SV forbids sign in sized literals; emit two's-complement.
            return f"{w}'d{n & ((1 << w) - 1)}"
        return str(n)
    if "bitVector" in operand:
        bits = operand["bitVector"]
        return f"{len(bits)}'b{bits}"
    if "varRef" in operand:
        return _resolve_sig_name(operand["varRef"], ctx)
    return ""


def _render_operand(operand, parent_prec, ctx, seen=None):
    """Render operand with parentheses only when needed."""
    return walk_expression(
        operand, ctx,
        on_terminal=lambda op: _terminal_to_sv(op, ctx),
        on_opnode=lambda op, s: _render_expression(op, parent_prec, ctx, s),
        exc_type=HGDBConversionError,
        seen=seen)


def _render_expression(expr, parent_prec, ctx, seen=None):
    """Render expression with proper parentheses for precedence."""
    if not isinstance(expr, dict):
        return ""
    opcode = expr.get("opcode", "")
    operands = expr.get("operands") or []
    own = _SV_PRECEDENCE.get(opcode, 0)
    rendered = [_render_operand(o, own, ctx, seen) for o in operands]

    fallback = False
    if opcode == "?:" and len(rendered) == 3:
        body = f"{rendered[0]} ? {rendered[1]} : {rendered[2]}"
    elif opcode in ("!", "~", "neg") and len(rendered) == 1:
        sym = "-" if opcode == "neg" else opcode
        body = f"{sym}{rendered[0]}"
    elif opcode in ("andr", "orr", "xorr") and len(rendered) == 1:
        sym = {"andr": "&", "orr": "|", "xorr": "^"}[opcode]
        body = f"{sym}{rendered[0]}"
    elif opcode == "{}":
        body = "{" + ", ".join(rendered) + "}"
    elif opcode == "R{}" and len(rendered) == 2:
        body = "{" + rendered[1] + "{" + rendered[0] + "}}"
    elif len(rendered) == 2:
        body = f"{rendered[0]} {opcode} {rendered[1]}"
    else:
        # Function-call-style fallback: already self-bracketed.
        body = opcode + "(" + ", ".join(rendered) + ")"
        fallback = True

    if fallback:
        return body
    return f"({body})" if parent_prec >= own and opcode else body


def _expression_for(stable_id, ctx):
    return ctx.expressions.get(stable_id) if stable_id else None


_resolve_body_var_ref = resolve_var_by_ref


def _source_name(stable_id, ctx):
    return resolve_authoring_name(stable_id, ctx) or stable_id


def _serialize_enable(enable_ref, ctx):
    """Serialize capture-when's `&`-joined tokens to SV fragment.

    Drops `<complex>` and bare `!` (fail hgdb condition parser)."""
    if not enable_ref:
        return ""
    parts = []
    for tok in enable_ref.split("&"):
        tok = tok.strip()
        if not tok:
            continue
        negated = tok.startswith("!")
        if negated:
            tok = tok[1:].strip()
        if not tok or tok == "<complex>":
            continue
        if (expr := _expression_for(tok, ctx)) is not None:
            rendered = _render_expression(expr, 0, ctx)
            parts.append(f"!({rendered})" if negated else rendered)
            continue
        sig = _resolve_sig_name(tok, ctx)
        parts.append(f"!{sig}" if negated else sig)
    return " && ".join(parts)


def _merge_enable(guard_stack, own_enable):
    """Merge guard stack with own enable. Capture-when folded whens into
    bp.enableRef for `connect`; `decl` falls back to guard_stack."""
    if own_enable:
        return own_enable
    parts = [tok.strip() for tok in guard_stack if tok.strip()]
    return "&".join(parts)


def _walk_body(body, ctx, instance_id, out_bps, out_scope_bps,
               out_assignments, guard_stack=()):
    """Walk body statements, emitting one bp per FIRRTL statement (match hgdb-firrtl shape)."""
    def emit(stmt, kind):
        locs = stmt.get("locations") or {}
        loc = locs.get(ctx.authoring_repr) if isinstance(locs, dict) else None
        filename = _filename_for(loc, ctx)
        line = _line_for(loc)
        if not filename or line == 0:
            return
        own = ((stmt.get("bp") or {}).get("enableRef", "")
               if stmt.get("bp") else "")
        if kind == "decl":
            # Decls fire combinationally regardless of enclosing whens;
            # native uses "1" (always-true).
            condition = "1"
        else:
            merged = _merge_enable(guard_stack, own)
            condition = _serialize_enable(merged, ctx) if merged else "1"
        bp_id = ctx.next_breakpoint_id
        ctx.next_breakpoint_id += 1
        out_bps.append((bp_id, instance_id, filename, line,
                        _column_for(loc), condition, ""))
        out_scope_bps.append(bp_id)
        if kind == "connect":
            lhs = _source_name(stmt.get("varRef") or "", ctx)
            if lhs:
                out_assignments.append(
                    (lhs, lhs, bp_id, "", None))

    def _is_self_connect(stmt):
        """Check if stmt is synthetic no-op self-connect from bundle decomposition."""
        v = stmt.get("valueRef")
        return (isinstance(v, dict)
                and len(v) == 1
                and v.get("varRef") == stmt.get("varRef"))

    # Pass 1: connects + block-openers
    for stmt in body:
        kind = stmt.get("kind")
        if kind == "block":
            emit(stmt, "decl")
            guard = stmt.get("guardRef") or ""
            token = f"!{guard}" if guard and stmt.get("negated") else guard
            _walk_body(stmt.get("body") or [], ctx, instance_id,
                       out_bps, out_scope_bps, out_assignments,
                       (*guard_stack, token))
            continue
        if kind == "connect":
            if _is_self_connect(stmt):
                continue
            emit(stmt, "connect")
    # Pass 2: decls (skip ports - native treats as boundary)
    for stmt in body:
        if stmt.get("kind") == "decl":
            var = _resolve_body_var_ref(stmt.get("varRef") or "", ctx)
            if var.get("bindKind") == "port":
                continue
            emit(stmt, "decl")


def convert(uhdi, output_path):
    """Write hgdb SQLite symbol table to `output_path`.

    On failure existing file preserved (writes to .tmp first)."""
    try:
        ctx = _Ctx.from_uhdi(uhdi)
    except ConversionError as e:
        raise HGDBConversionError(str(e)) from None

    output_path = pathlib.Path(output_path)
    tmp_path = output_path.with_name(output_path.name + ".tmp")
    if tmp_path.exists():
        tmp_path.unlink()
    conn = sqlite3.connect(str(tmp_path))
    committed = False
    try:
        conn.executescript(_SCHEMA)

        # 1. Instances table
        instances, top_inst_ids = _instance_rows(ctx)
        conn.executemany(
            "INSERT INTO instance(id, name, annotation) VALUES (?, ?, ?)",
            ((iid, p, ann) for iid, p, _sid, ann in instances))

        by_scope: Dict[str, List[Tuple[int, str]]] = {}
        for iid, p, sid, _ann in instances:
            by_scope.setdefault(sid, []).append((iid, p))

        # 2. Variable + generator_variable tables (one per instance, dbg.variable)
        clock_sigs_by_inst: Dict[int, set] = {}
        var_rows: List[Tuple[int, str, int]] = []
        gen_var_rows: List[Tuple[str, int, int, str]] = []
        for sid, insts in by_scope.items():
            scope = ctx.scopes.get(sid, {})
            # Dedup by chisel source name (pool may store port+node binding)
            seen_chisel_names: set = set()
            for var_id in scope.get("variableRefs") or []:
                var = ctx.variables.get(var_id)
                if var is None:
                    continue
                src_name = _source_name(var_id, ctx)
                if src_name and src_name in seen_chisel_names:
                    continue
                verilog = (var.get("representations", {})
                           .get(ctx.simulation_repr, {}) or {})
                sig = (verilog.get("value") or {}).get("sigName") \
                    if isinstance(verilog.get("value"), dict) else None
                if not sig:
                    # Fall back to chisel name for ports without verilog repr
                    if (var.get("bindKind") == "port"
                            and not isinstance(verilog.get("value"), dict)):
                        fallback = _source_name(var_id, ctx)
                        if fallback and fallback != var_id:
                            sig = fallback
                    if not sig:
                        continue
                src = src_name
                if src:
                    seen_chisel_names.add(src)
                for iid, path in insts:
                    vid = ctx.next_variable_id
                    ctx.next_variable_id += 1
                    ctx.variable_ids[(path, var_id)] = vid
                    var_rows.append((vid, sig, 1))
                    gen_var_rows.append((src, iid, vid, ""))
                    if src in _CLOCK_NAMES or sig in _CLOCK_NAMES:
                        clock_sigs_by_inst.setdefault(iid, set()).add(sig)
        if var_rows:
            conn.executemany(
                "INSERT INTO variable(id, value, is_rtl) VALUES (?, ?, ?)",
                var_rows)
        if gen_var_rows:
            conn.executemany(
                "INSERT INTO generator_variable(name, instance_id,"
                " variable_id, annotation) VALUES (?, ?, ?, ?)",
                gen_var_rows)

        # 3. Breakpoint + scope + context_variable rows.
        bp_rows: List[Tuple] = []
        scope_rows: List[Tuple[int, str]] = []
        ctx_rows: List[Tuple] = []
        assignment_rows: List[Tuple] = []
        for sid, insts in by_scope.items():
            scope = ctx.scopes.get(sid, {})
            body = scope.get("body") or []
            if not body:
                continue
            for iid, path in insts:
                scope_bps: List[int] = []
                bps: List[Tuple[int, int, str, int, int, str, str]] = []
                asgs: List[Tuple] = []
                _walk_body(body, ctx, iid, bps, scope_bps, asgs)
                bp_rows.extend(bps)
                assignment_rows.extend(asgs)
                if scope_bps:
                    sid_row = ctx.next_scope_id
                    ctx.next_scope_id += 1
                    scope_rows.append(
                        (sid_row, " ".join(map(str, scope_bps))))
                # Link every in-scope variable to every bp (locals panel).
                for var_id in scope.get("variableRefs") or []:
                    vid = ctx.variable_ids.get((path, var_id))
                    if vid is None:
                        continue
                    src_name = _source_name(var_id, ctx)
                    for bp_id in scope_bps:
                        ctx_rows.append((src_name, bp_id, vid, 0))

        if bp_rows:
            conn.executemany(
                "INSERT INTO breakpoint(id, instance_id, filename,"
                " line_num, column_num, condition, trigger)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)", bp_rows)
        if scope_rows:
            conn.executemany(
                "INSERT INTO scope(scope, breakpoints) VALUES (?, ?)",
                scope_rows)
        if ctx_rows:
            conn.executemany(
                "INSERT INTO context_variable(name, breakpoint_id,"
                " variable_id, type) VALUES (?, ?, ?, ?)",
                ctx_rows)
        if assignment_rows:
            conn.executemany(
                "INSERT INTO assignment(name, value, breakpoint_id,"
                " condition, scope_id) VALUES (?, ?, ?, ?, ?)",
                assignment_rows)

        # 4. Clock annotation (qualify with top instance path for VPI lookup)
        top_inst = None
        if top_inst_ids:
            top_id = top_inst_ids[0]
            top_inst = next((p for iid, p, _sid, _ann in instances
                             if iid == top_id), None)
        clock_sigs = set().union(*clock_sigs_by_inst.values()) \
            if clock_sigs_by_inst else set()
        ann_rows = [
            ("clock", f"{top_inst}.{sig}" if top_inst else sig)
            for sig in sorted(clock_sigs)
        ]
        if ann_rows:
            conn.executemany(
                "INSERT INTO annotation(name, value) VALUES (?, ?)",
                ann_rows)

        conn.commit()
        committed = True
    finally:
        conn.close()
        if committed:
            os.replace(tmp_path, output_path)
        elif tmp_path.exists():  # pragma: no branch
            tmp_path.unlink()

    _emit_unresolved_warnings(ctx)


@register
class HGDBBackend(Backend):
    name = "hgdb"
    description = (
        "uhdi -> hgdb SQLite symbol table (libhgdb / hgdb-VSCode "
        "consumes this).  Drop-in replacement for "
        "`hgdb-firrtl + toml2hgdb` on the modern FIRRTL flow.")
    binary_output = True
    output_extension = "db"

    def convert(self,
                uhdi: Dict[str, Any],
                output: Optional[pathlib.Path] = None
                ) -> None:
        if output is None:
            raise HGDBConversionError(
                "hgdb backend requires an output path (binary SQLite)")
        convert(uhdi, output)
        return None

    def canonical_dump(self, output):
        from .dump import canonical_dump
        return canonical_dump(output)
