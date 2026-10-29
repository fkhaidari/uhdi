"""UHDI v2 layout -> ChiselTrace PDG (`pdg_spec.rs`), read from `original`
statements (profiles `stmt` and `deps`) and `bindings`.

ChiselTrace replays the graph over a VCD. Per cycle it walks `cfg`,
taking a branch when its predicate signal is 1, and links each activated
statement to the latest activated provider of every symbol it reads; a
symbol is the `assignsTo` string of a vertex. So the graph needs:

* one vertex per statement and instance, with `assignsTo` = the
  instance-relative source path the statement writes (`stage.io.in`,
  `regs[1]`). A write through a dynamic index writes the whole aggregate;
* top-level input ports as `IO` providers, registers as clocked
  `DataDefinition` providers (their reset value);
* a `ControlFlow` vertex and a predicate per `when`, the predicate named
  by its guard signal in the VCD (`stage.io_en`). A guard that is no
  plain signal cannot be a predicate: its block is taken every cycle;
* `Data` edges from a statement to every vertex that may provide what it
  reads, `Declaration` edges to the declarations of what it reads, and a
  `Conditional` edge to the innermost enclosing `when`.

Statements come in source order; a child instance's statements sit at
its `inst` statement.
"""
from __future__ import annotations

import re
import sys
from typing import Any, Dict, List, Optional, Tuple

from uhdi_common.context import ConversionError

from .walk import Doc, Instance, Part, render, root_var



class PDGV2ConversionError(ConversionError):
    pass


def _static(path: str) -> str:
    """The part of a path before its first dynamic subscript."""
    m = re.search(r"\[[^\]0-9][^\]]*\]", path)
    return path[:m.start()] if m else path


def _overlaps(a: str, b: str) -> bool:
    short, long = sorted((a, b), key=len)
    return long == short or long.startswith(short + ".") \
        or long.startswith(short + "[")


class _Builder:
    def __init__(self, d: Doc):
        self.d = d
        self.vertices: List[Dict[str, Any]] = []
        self.edges: List[Dict[str, Any]] = []
        self.predicates: List[Dict[str, Any]] = []
        self.providers: List[Tuple[str, int]] = []   # (symbol, vertex)
        self.decls: Dict[str, int] = {}               # root symbol -> vertex
        self.pending: List[Tuple[int, List[str], bool]] = []
        self.no_pred: List[str] = []
        self.inst_decl: List[int] = []

    # ---- vertices ------------------------------------------------------

    def vertex(self, loc, name, kind, *, clocked=False, assigns=None,
               signal=None, inst: Instance, chisel=True, delay=0) -> int:
        loc = loc or []
        self.vertices.append({
            "file": self.d.file(loc), "line": loc[1] if len(loc) > 1 else 0,
            "char": loc[2] if len(loc) > 2 else 0, "name": name,
            "kind": kind, "clocked": clocked,
            "modulePath": [_rel(inst)],
            "relatedSignal": ({"signalPath": signal, "fieldPath": ""}
                              if signal else None),
            "assignsTo": assigns, "isChiselStatement": chisel,
            "condition": None, "assignDelay": delay})
        idx = len(self.vertices) - 1
        # Whatever a child instance does depends on the instance existing.
        if self.inst_decl:
            self.edge(idx, self.inst_decl[-1], "Declaration")
        if assigns is not None:
            self.providers.append((assigns, idx))
        return idx

    def edge(self, src: int, dst: int, kind: str, clocked=False):
        self.edges.append({"from": src, "to": dst, "kind": kind,
                           "clocked": clocked, "condition": None})

    # ---- instances -----------------------------------------------------

    def instance(self, inst: Instance, top: bool) -> List[Dict[str, Any]]:
        cfg: List[Dict[str, Any]] = []
        children = {c.path.rsplit(".", 1)[1]: c for c in inst.children}
        for part in inst.parts:
            p = _prefix(inst) + part.prefix
            if not part.prefix:
                cfg += self.ports(inst, part, p, top)
            cfg += self.block(inst, part, p, self.d.stmts(part.source), None,
                              children)
        return cfg

    def ports(self, inst, part, p, top) -> List[Dict[str, Any]]:
        cfg = []
        for name, var in self.d.vars(part.source).items():
            if var.get("dir") in (None, "inout") or \
                    (self.d.types.get(var.get("type", "")) or {}).get("kind") \
                    == "clock":
                continue
            for leaf, flipped in _leaf_dirs(self.d, name, var.get("type", "")):
                inp = (var["dir"] == "in") != flipped
                value = self.d.value_at(part, leaf)
                sig = _signal(inst, value)
                name = f"{'input' if inp else 'output'}_{leaf}"
                if not top:
                    # The parent's connect provides a child's input.
                    v = self.vertex(var.get("loc"), name, "Definition",
                                    signal=sig if inp else None, inst=inst)
                    self.decls[p + leaf] = v
                else:
                    v = self.vertex(var.get("loc"), name, "IO",
                                    assigns=p + leaf if inp else None,
                                    signal=sig if inp else None, inst=inst)
                    if not inp:
                        self.pending.append((v, [p + leaf], False))
                cfg.append({"stmtRef": v})
        return cfg

    def block(self, inst, part, p, stmts, when: Optional[int],
              children) -> List[Dict[str, Any]]:
        guards = part.binding.get("stmts") or {}
        cfg: List[Dict[str, Any]] = []
        for s in stmts:
            kind, loc = s.get("kind"), s.get("loc")
            if kind == "when":
                pred = self.predicate(inst, guards.get(s["id"]), loc)
                v = self.vertex(loc, f"cond_on_[{','.join(s.get('reads') or [])}]",
                                "ControlFlow", inst=inst,
                                signal=(self.predicates[pred]["name"]
                                        if pred is not None else None))
                self.pending.append((v, [p + r for r in s.get("reads") or []],
                                     False))
                if when is not None:
                    self.edge(v, when, "Conditional")
                then = self.block(inst, part, p, s.get("then") or [], v,
                                  children)
                other = self.block(inst, part, p, s.get("else") or [], v,
                                   children)
                if pred is None:
                    cfg.append({"stmtRef": v})
                    cfg += then
                    continue
                node: Dict[str, Any] = {"stmtRef": v, "predStmtRef": pred}
                if then:
                    node["trueBranch"] = then
                if other:
                    node["falseBranch"] = other
                cfg.append(node)
                continue
            v = self.statement(inst, part, p, s)
            if v is None:
                continue
            if when is not None:
                self.edge(v, when, "Conditional",
                          self.vertices[v]["clocked"])
            cfg.append({"stmtRef": v})
            if kind == "inst" and s.get("var") in children:
                self.inst_decl.append(v)
                cfg += self.instance(children[s["var"]], False)
                self.inst_decl.pop()
        return cfg

    def statement(self, inst, part, p, s) -> Optional[int]:
        kind, loc = s.get("kind"), s.get("loc")
        var = s.get("var") or ""
        if kind == "reg":
            v = self.vertex(loc, f"defreg_{var}", "DataDefinition",
                            clocked=True, assigns=p + var, inst=inst,
                            signal=_signal(inst, self.d.value_at(part, var)))
            self.decls[p + var] = v
            return v
        if kind == "wire":
            v = self.vertex(loc, f"defwire_{var}", "Definition", inst=inst)
            self.decls[p + var] = v
            return v
        if kind == "mem":
            v = self.vertex(loc, f"defmem_{var}", "Definition", clocked=True,
                            inst=inst)
            self.decls[p + var] = v
            return v
        if kind == "inst":
            return self.vertex(loc, f"definstance_{var}", "Definition",
                               inst=inst)
        if kind == "node":
            v = self.vertex(loc, f"defnode_{var}", "DataDefinition",
                            assigns=p + var, inst=inst,
                            signal=_signal(inst, self.d.value_at(part, var)))
            self.decls[p + var] = v
            self.pending.append((v, [p + r for r in s.get("reads") or []],
                                 False))
            return v
        if kind == "connect":
            lhs = s.get("lhs") or ""
            root = root_var(lhs)
            rkind = self._kind_of(part, root)
            clocked = rkind in ("reg", "mem")
            reads = s.get("reads") or []
            # A synchronous read port shows its data a read latency later.
            delay = max((self._latency(part, root_var(r)) for r in reads),
                        default=0)
            v = self.vertex(loc, f"connect_{lhs}", "Connection",
                            clocked=clocked, assigns=p + _static(lhs),
                            inst=inst, delay=delay,
                            signal=_signal(inst, self.d.value_at(part, lhs)))
            self.pending.append((v, [p + r for r in reads], clocked))
            return v
        return None

    def _kind_of(self, part: Part, root: str) -> Optional[str]:
        for s in _flat(self.d.stmts(part.source)):
            if s.get("var") == root and s.get("kind") in ("reg", "mem",
                                                         "wire", "node"):
                return s["kind"]
        return None

    def _latency(self, part: Part, root: str) -> int:
        for s in _flat(self.d.stmts(part.source)):
            if s.get("var") == root and s.get("kind") == "mem":
                return int(s.get("read_latency") or 0)
        return 0

    def predicate(self, inst, guard, loc) -> Optional[int]:
        g = render(guard)
        if not isinstance(guard, str) or g is None:
            self.no_pred.append(f"{inst.path}:{loc}")
            return None
        name = _signal(inst, guard)
        loc = loc or []
        self.predicates.append({
            "file": self.d.file(loc), "line": loc[1] if len(loc) > 1 else 0,
            "char": loc[2] if len(loc) > 2 else 0, "name": name,
            "kind": "DataDefinition", "clocked": False,
            "isChiselStatement": False})
        return len(self.predicates) - 1

    # ---- edges ---------------------------------------------------------

    def link(self):
        for v, reads, clocked in self.pending:
            for r in reads:
                path = _static(r)
                # Data from every vertex that may provide the value; a
                # statement reading what it writes reads its last value.
                for sym, src in self.providers:
                    if _overlaps(sym, path):
                        self.edge(v, src, "Data", clocked)
                for sym, decl in self.decls.items():
                    if decl != v and _overlaps(sym, path) and \
                            self.vertices[decl]["assignsTo"] is None:
                        self.edge(v, decl, "Declaration")


def _prefix(inst: Instance) -> str:
    rel = _rel(inst)
    return rel + "." if rel else ""


def _rel(inst: Instance) -> str:
    return inst.path.split(".", 1)[1] if "." in inst.path else ""


def _signal(inst: Instance, value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    return _prefix(inst) + value


def _flat(stmts):
    for s in stmts:
        yield s
        yield from _flat(s.get("then") or [])
        yield from _flat(s.get("else") or [])


def _leaf_dirs(d: Doc, name: str, tref: str, flipped=False):
    t = d.types.get(tref) or {}
    if t.get("kind") == "struct":
        for f in t.get("fields") or []:
            yield from _leaf_dirs(d, f"{name}.{f['name']}", f["type"],
                                  flipped != bool(f.get("flip")))
    elif t.get("kind") == "vector":
        for i in range(int(t.get("size", 0))):
            yield from _leaf_dirs(d, f"{name}[{i}]", t.get("elem", ""),
                                  flipped)
    else:
        yield name, flipped


def convert(doc: Dict[str, Any]) -> Dict[str, Any]:
    d = Doc(doc)
    roots = d.roots()
    if not roots:
        raise PDGV2ConversionError("no root target module with a binding")
    b = _Builder(d)
    cfg: List[Dict[str, Any]] = []
    for r in roots:
        cfg += b.instance(r, True)
    b.link()
    if b.no_pred:
        print(f"warning: {len(b.no_pred)} when block(s) have no guard signal "
              f"to use as a predicate and are always taken, e.g. "
              f"{', '.join(b.no_pred[:5])}", file=sys.stderr)
    return {"vertices": b.vertices, "edges": b.edges,
            "predicates": b.predicates, "cfg": cfg}
