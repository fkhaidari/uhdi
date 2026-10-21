"""UHDI v2 layout -> hgdb JSON symbol table (the shape hgdb-circt's
`firtool --hgdb=<file>` writes), read from `original` / `bindings`.

One `table` entry per source module that some instance shows, named by
the source module (a deduplicated target serves several of them):

* `variables`: every ground leaf of the source variables bound to a
  Verilog signal, inline `{name, value, rtl}`;
* `scope`: one block per source laid onto the module (the module and
  each module inlined into it), holding the statements in source order:
  `decl` for reg / wire / named node, `assign` for connect, and a nested
  block with `condition` for each branch of a `when` (the guard, negated
  for `else`). Statement variables come from the document-level
  `variables` pool by id;
* `instances`: `{name, module}` per instance variable.

A connect whose target has no static Verilog signal (a dynamic index, an
unbound leaf) is left out with a warning.
"""
from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional

from uhdi_common.context import ConversionError

from .to_hgdb import reset_terms
from .walk import Doc, Instance, Part, iter_stmts, render, root_var, term


class HGDBJsonV2ConversionError(ConversionError):
    pass


class _Pool:
    def __init__(self):
        self.entries: List[Dict[str, Any]] = []
        self.ids: Dict[tuple, str] = {}

    def id(self, name: str, value: str) -> str:
        key = (name, value)
        if key not in self.ids:
            self.ids[key] = str(len(self.entries))
            self.entries.append({"id": self.ids[key], "name": name,
                                 "value": value, "rtl": True})
        return self.ids[key]


def _place(entry: Dict[str, Any], loc: List[int]) -> Dict[str, Any]:
    if len(loc) > 1:
        entry["line"] = loc[1]
    if len(loc) > 2 and loc[2]:
        entry["column"] = loc[2]
    return entry


def _entries(d: Doc, part: Part, stmts: List[Dict[str, Any]], ctx: "_Ctx"
             ) -> List[Dict[str, Any]]:
    guards = part.binding.get("stmts") or {}
    out: List[Dict[str, Any]] = []
    for s in stmts:
        kind = s.get("kind")
        loc = s.get("loc") or []
        if kind == "when":
            # The block itself stops under the enable it sits in.
            if loc:
                out.append(_place({"type": "none"}, loc))
            g = render(guards.get(s["id"]))
            for taken, branch in ((True, "then"), (False, "else")):
                body = _entries(d, part, s.get(branch) or [], ctx)
                if not body:
                    continue
                block: Dict[str, Any] = {"type": "block", "scope": body}
                if g is not None:
                    block["condition"] = term(g, taken)
                out.append(block)
            continue
        if kind in ("reg", "wire", "node"):
            path, etype = s.get("var", ""), "decl"
        elif kind == "connect":
            path, etype = s.get("lhs", ""), "assign"
        else:
            continue
        if etype == "decl" and tuple(loc) in ctx.connect_locs:
            # `RegNext` and friends: the connect at the same spot stops
            # there, under its own condition.
            continue
        value = d.value_at(part, path)
        if not isinstance(value, str):
            if kind == "connect":
                ctx.skipped.append(f"{part.source}:{s['id']}")
            continue
        entry = _place({"type": etype,
                        "variable": ctx.pool.id(part.prefix + path, value)},
                       loc)
        if kind == "connect":
            reset = ctx.resets.get(root_var(path))
            if reset and reset != "!0":
                entry["condition"] = reset
        # A node is evaluated whatever the enclosing blocks say.
        (ctx.hoisted if kind == "node" else out).append(entry)
    return out


class _Ctx:
    def __init__(self, pool: _Pool, skipped: List[str]):
        self.pool, self.skipped = pool, skipped
        self.resets: Dict[str, str] = {}
        self.hoisted: List[Dict[str, Any]] = []
        self.connect_locs: set = set()


def _module_entry(d: Doc, inst: Instance, pool: _Pool,
                  skipped: List[str]) -> Dict[str, Any]:
    variables: List[Any] = []
    scope: List[Dict[str, Any]] = []
    for part in inst.parts:
        variables.extend({"name": name, "value": value, "rtl": True}
                         for name, value, _ in d.leaves(part)
                         if isinstance(value, str))
        stmts = d.stmts(part.source)
        ctx = _Ctx(pool, skipped)
        ctx.resets = reset_terms(d, part)
        ctx.connect_locs = {tuple(s["loc"]) for s, _ in iter_stmts(stmts)
                            if s.get("kind") == "connect" and s.get("loc")}
        body = _entries(d, part, stmts, ctx)
        body = ctx.hoisted + body
        if body:
            block: Dict[str, Any] = {"type": "block", "scope": body}
            loc = next((s["loc"] for s in stmts if s.get("loc")), None)
            if d.file(loc):
                block["filename"] = d.file(loc)
            scope.append(block)
    out: Dict[str, Any] = {"type": "module", "name": inst.parts[0].source,
                           "variables": variables, "scope": scope}
    if inst.children:
        out["instances"] = [{"name": c.path.rsplit(".", 1)[1],
                             "module": c.parts[0].source}
                            for c in inst.children]
    return out


def convert(doc: Dict[str, Any]) -> Dict[str, Any]:
    d = Doc(doc)
    roots = d.roots()
    if not roots:
        raise HGDBJsonV2ConversionError("no root target module with a binding")
    pool = _Pool()
    skipped: List[str] = []
    table: List[Dict[str, Any]] = []
    seen = set()
    for inst in d.walk():
        source = inst.parts[0].source
        if source in seen:
            continue
        seen.add(source)
        table.append(_module_entry(d, inst, pool, skipped))
    if skipped:
        print(f"warning: {len(skipped)} connect(s) have no static Verilog "
              f"signal to name and were left out, e.g. "
              f"{', '.join(skipped[:5])}", file=sys.stderr)
    top: Any = [r.parts[0].source for r in roots]
    return {"generator": "uhdi", "top": top[0] if len(top) == 1 else top,
            "variables": pool.entries, "table": table}
