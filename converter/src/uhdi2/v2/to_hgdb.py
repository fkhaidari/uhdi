"""UHDI v2 layout -> hgdb SQLite symbol table, read straight from
`original` / `bindings` (profiles `stmt` and `deps`).

The table follows what hgdb-firrtl + toml2hgdb write for the same design:

* `instance`: one row per instance path (`Top`, `Top.sub0`). A module
  inlined into an instance is not an instance; its rows go to the
  instance that absorbed it, with names under the inline path.
* `variable` / `generator_variable`: every ground leaf of every source
  variable bound to a Verilog signal, named by its dotted source path
  (`io.a`, `v[0]`).
* `breakpoint`: one row per statement and instance.
  - `when`: the enable of the block it sits in;
  - `connect`: a reset term, then the enable. The reset term is
    `!<reset>` for a register with a reset and `!0` for one without, as
    hgdb-firrtl writes it; other targets get none;
  - named `node`: `1`.
  The enable is the conjunction of the guards of the enclosing `when`
  blocks, innermost first, each negated in an `else` branch. Guards come
  from `binding.stmts`. Declarations (reg, wire, mem, inst) get no
  breakpoint.
* `assignment`: one row per `connect` breakpoint, `name` = the lowered
  FIRRTL name of the target (`io_q`), `value` = its source path (`io.q`).
  A write through a dynamic index (`v[cnt]`) gets none.
* `annotation`: the clock signals of the top instance, for the runtime.
"""
from __future__ import annotations

import os
import pathlib
import sqlite3
import sys
from typing import Any, Dict, List, Optional, Tuple

from uhdi_common.context import ConversionError
from uhdi_to_hgdb.convert import _CLOCK_NAMES, _SCHEMA

from .walk import (Doc, Instance, Part, flat_name, is_dynamic, iter_stmts,
                   render, root_var, term)


class HGDBV2ConversionError(ConversionError):
    pass


def reset_terms(d: Doc, part: Part) -> Dict[str, str]:
    """Register name -> its reset term."""
    out: Dict[str, str] = {}
    for s, _ in iter_stmts(d.stmts(part.source)):
        if s.get("kind") != "reg":
            continue
        reset = s.get("reset")
        if reset is None:
            out[s["var"]] = "!0"
            continue
        sig = render(d.value_at(part, reset))
        out[s["var"]] = f"!{sig}" if sig else ""
    return out


def condition(d: Doc, part: Part, path: Tuple, unknown: List[str]) -> List[str]:
    """The enable terms of `path`, innermost first."""
    guards = part.binding.get("stmts") or {}
    terms = []
    for wid, taken in reversed(path):
        g = render(guards.get(wid))
        if g is None:
            unknown.append(f"{part.source}:{wid}")
            continue
        terms.append(term(g, taken))
    return terms


def breakpoints(d: Doc, part: Part, unknown: List[str]
                ) -> List[Tuple[Dict[str, Any], str]]:
    """(stmt, condition) of every statement that gets a breakpoint."""
    resets = reset_terms(d, part)
    out = []
    for s, path in iter_stmts(d.stmts(part.source)):
        kind = s.get("kind")
        if not s.get("loc"):
            continue
        if kind == "node":
            out.append((s, "1"))
            continue
        if kind not in ("when", "connect"):
            continue
        terms = condition(d, part, path, unknown)
        if kind == "connect":
            reset = resets.get(root_var(s.get("lhs", "")))
            if reset:
                terms.insert(0, reset)
        out.append((s, " && ".join(terms) if terms else "1"))
    return out


def convert(doc: Dict[str, Any], output_path) -> None:
    d = Doc(doc)
    instances: List[Instance] = list(d.walk())
    if not instances:
        raise HGDBV2ConversionError("no root target module with a binding")

    output_path = pathlib.Path(output_path)
    tmp_path = output_path.with_name(output_path.name + ".tmp")
    if tmp_path.exists():
        tmp_path.unlink()
    conn = sqlite3.connect(str(tmp_path))
    committed = False
    unknown: List[str] = []
    try:
        conn.executescript(_SCHEMA)
        inst_rows, var_rows, gen_rows = [], [], []
        bp_rows, asg_rows, ann_rows = [], [], []
        asg_paths: Dict[Tuple[int, str], set] = {}
        for iid, inst in enumerate(instances, 1):
            inst_rows.append((iid, inst.path, ""))
            for part in inst.parts:
                for name, value, tref in d.leaves(part):
                    if not isinstance(value, str):
                        continue
                    vid = len(var_rows) + 1
                    var_rows.append((vid, value, 1))
                    gen_rows.append((name, iid, vid, ""))
                    if iid == 1 and ((d.types.get(tref) or {}).get("kind") == "clock"
                                     or name in _CLOCK_NAMES):
                        ann_rows.append(("clock", f"{inst.path}.{value}"))
                for s, cond in breakpoints(d, part, unknown):
                    bid = len(bp_rows) + 1
                    loc = s["loc"]
                    bp_rows.append((bid, iid, d.file(loc), loc[1],
                                    loc[2] if len(loc) > 2 else 0, cond, ""))
                    # A write through a dynamic index has no single name.
                    if s["kind"] == "connect" and not is_dynamic(s["lhs"]):
                        lhs = part.prefix + s["lhs"]
                        flat = flat_name(lhs)
                        asg_paths.setdefault((iid, flat), set()).add(lhs)
                        asg_rows.append((iid, flat, d.shown(part, s["lhs"]),
                                         bid))
        conn.executemany("INSERT INTO instance(id, name, annotation)"
                         " VALUES (?, ?, ?)", inst_rows)
        conn.executemany("INSERT INTO variable(id, value, is_rtl)"
                         " VALUES (?, ?, ?)", var_rows)
        conn.executemany("INSERT INTO generator_variable(name, instance_id,"
                         " variable_id, annotation) VALUES (?, ?, ?, ?)",
                         gen_rows)
        conn.executemany("INSERT INTO breakpoint(id, instance_id, filename,"
                         " line_num, column_num, condition, trigger)"
                         " VALUES (?, ?, ?, ?, ?, ?, ?)", bp_rows)
        # `v[2].x` and `v_2.x` flatten to one name; which one the lowered
        # name belongs to is not known, so neither gets a row.
        clash = {k for k, paths in asg_paths.items() if len(paths) > 1}
        conn.executemany("INSERT INTO assignment(name, value, breakpoint_id,"
                         " condition, scope_id) VALUES (?, ?, ?, ?, ?)",
                         [(flat, value, bid, "", None)
                          for iid, flat, value, bid in asg_rows
                          if (iid, flat) not in clash])
        if clash:
            print(f"warning: {len(clash)} lowered name(s) stand for more than "
                  f"one source path; their assignments are left out, e.g. "
                  f"{', '.join(sorted(f for _, f in clash)[:5])}",
                  file=sys.stderr)
        conn.executemany("INSERT INTO annotation(name, value) VALUES (?, ?)",
                         sorted(set(ann_rows)))
        conn.commit()
        committed = True
    finally:
        conn.close()
        if committed:
            os.replace(tmp_path, output_path)
        elif tmp_path.exists():
            tmp_path.unlink()
    if unknown:
        print(f"warning: {len(set(unknown))} when block(s) have no hardware "
              f"guard; their term is left out of the breakpoint condition, "
              f"e.g. {', '.join(sorted(set(unknown))[:5])}", file=sys.stderr)
