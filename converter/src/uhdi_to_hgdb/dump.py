"""Read an hgdb SQLite back into a canonical, comparable JSON shape.
Drops auto-increment ids, resolves FKs to readable names, sorts by
identity tuple -- two equivalent DBs compare `==`."""
from __future__ import annotations

import contextlib
import sqlite3
from typing import Any, Dict, List

# Per-table identity columns (sort key).
_SORT_KEYS: Dict[str, List[str]] = {
    "instance":           ["name", "annotation"],
    "variable":           ["value", "is_rtl"],
    "breakpoint":         ["filename", "line_num", "column_num", "condition",
                           "trigger", "instance_name"],
    "scope":              ["breakpoints"],
    "context_variable":   ["name", "type", "variable_value", "breakpoint_key"],
    "generator_variable": ["name", "annotation", "instance_name",
                           "variable_value"],
    "annotation":         ["name", "value"],
    "event":              ["name", "transaction", "action", "fields",
                           "matches", "breakpoint_key"],
    "assignment":         ["name", "value", "condition", "breakpoint_key",
                           "scope_id"],
}

# Auto-increment ids dropped before serialising; replaced by synthesised name columns.
_DROP_COLS: Dict[str, List[str]] = {
    "instance":           ["id"],
    "variable":           ["id"],
    "breakpoint":         ["id", "instance_id"],
    "context_variable":   ["breakpoint_id", "variable_id"],
    "generator_variable": ["instance_id", "variable_id"],
    "event":              ["breakpoint_id"],
    "assignment":         ["breakpoint_id"],
}


def _check_table_name(table: str) -> str:
    # SQLite identifiers can't be bound; allow-list before f-stringing.
    if table not in _SORT_KEYS:
        raise ValueError(f"refusing to query unknown table: {table!r}")
    return table


def _sort_value(v: Any) -> tuple:
    """Sort key that orders ints numerically (line_num=2 before 10, not
    after). Leading bucket avoids cross-type compares on mixed columns."""
    if v is None:
        return (0, 0.0, "")
    if isinstance(v, (bool, int, float)):
        return (1, float(v), "")
    return (2, 0.0, str(v))


def _row_to_dict(cursor: sqlite3.Cursor, row: tuple) -> Dict[str, Any]:
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))


def canonical_dump(db_path) -> Dict[str, List[Dict[str, Any]]]:
    """Read `db_path` and return `{table: [row_dict, ...]}` with FKs
    resolved to readable names and rows sorted by identity tuple."""
    # closing(): sqlite3's own __exit__ commits but doesn't close.
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        cur = conn.cursor()

        inst_id_to_name: Dict[int, str] = {}
        for row in cur.execute("SELECT id, name FROM instance").fetchall():
            inst_id_to_name[row[0]] = row[1]
        var_id_to_value: Dict[int, str] = {}
        for row in cur.execute("SELECT id, value FROM variable").fetchall():
            var_id_to_value[row[0]] = row[1]
        bp_id_to_key: Dict[int, str] = {}
        for row in cur.execute(
                "SELECT id, instance_id, filename, line_num, column_num, "
                "condition FROM breakpoint").fetchall():
            bp_id, inst_id, fn, ln, col, cond = row
            bp_id_to_key[bp_id] = (
                f"{inst_id_to_name.get(inst_id, '?')}@{fn}:{ln}:{col}"
                f"|{cond}")

        out: Dict[str, List[Dict[str, Any]]] = {}
        for table, sort_key in _SORT_KEYS.items():
            rows = cur.execute(
                f"SELECT * FROM {_check_table_name(table)}").fetchall()
            rendered: List[Dict[str, Any]] = []
            for raw in rows:
                d = _row_to_dict(cur, raw)
                if "instance_id" in d:
                    d["instance_name"] = inst_id_to_name.get(d["instance_id"])
                if "variable_id" in d:
                    d["variable_value"] = var_id_to_value.get(d["variable_id"])
                if "breakpoint_id" in d:
                    d["breakpoint_key"] = bp_id_to_key.get(d["breakpoint_id"])
                for c in _DROP_COLS.get(table, []):
                    d.pop(c, None)
                rendered.append(d)
            rendered.sort(key=lambda r: tuple(
                _sort_value(r.get(k)) for k in sort_key))
            out[table] = rendered

    return out
