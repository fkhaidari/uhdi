"""Walk a UHDI v2 layout document as the design's instance tree.

Shared by the converters that need statements (`stmt` / `deps`
profiles): hgdb SQLite, hgdb JSON and PDG.

An instance is a node of the tree: a hierarchical path, the source
module it shows and the binding that maps that source onto a target.
An inlined module (a scoped binding) is not an instance of its own: its
statements and variables live in the instance that absorbed it, under
the inline path as a name prefix.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .types import named_types

Types = Dict[str, Any]


@dataclass
class Part:
    """One source module laid onto an instance: the instance itself, or a
    module inlined into it (`prefix` = its inline path plus a dot)."""
    source: str
    binding: Dict[str, Any]
    prefix: str = ""


@dataclass
class Instance:
    path: str
    target: str
    parts: List[Part] = field(default_factory=list)
    children: List["Instance"] = field(default_factory=list)


def _is_none(v: Any) -> bool:
    return v is None or (isinstance(v, dict) and "$optimizedOut" in v)


class Doc:
    def __init__(self, doc: Dict[str, Any]):
        doc = named_types(doc)
        self.doc = doc
        self.types: Types = doc.get("types", {})
        self.files: List[Dict[str, Any]] = doc.get("files", [])
        self.source: Dict[str, Any] = doc.get("original", {})
        self.target: Dict[str, Any] = doc.get("generated", {})
        self.unscoped: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.by_target: Dict[str, List[Dict[str, Any]]] = {}
        self.scoped: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for b in doc.get("bindings", []):
            if b.get("scope"):
                self.scoped[(b["generated"], b["scope"])] = b
            else:
                self.unscoped[(b["original"], b["generated"])] = b
                self.by_target.setdefault(b["generated"], []).append(b)

    # ---- sources -------------------------------------------------------

    def vars(self, source: str) -> Dict[str, Any]:
        return (self.source.get(source) or {}).get("vars") or {}

    def stmts(self, source: str) -> List[Dict[str, Any]]:
        return (self.source.get(source) or {}).get("stmts") or []

    def module_of(self, var: Dict[str, Any]) -> Optional[str]:
        t = self.types.get(var.get("type", "")) or {}
        return t.get("module") if t.get("kind") == "module" else None

    def file(self, loc: Optional[List[int]]) -> str:
        if not loc or not (0 <= loc[0] < len(self.files)):
            return ""
        return str(self.files[loc[0]].get("path", "")).lstrip("/")

    # ---- instance tree -------------------------------------------------

    def roots(self) -> List[Instance]:
        referenced = set()
        for b in self.doc.get("bindings", []):
            for v in (b.get("vars") or {}).values():
                if isinstance(v, dict) and "$inst" in v:
                    referenced.add(v.get("module"))
        out = []
        for t in self.target:
            if t in referenced:
                continue
            bs = self.by_target.get(t) or []
            if not bs:
                continue
            # A root is not deduplicated; prefer the source of its own name.
            b = next((b for b in bs if b["original"] == t), bs[0])
            out.append(self._instance(t, t, b["original"], b, set()))
        return out

    def _instance(self, path: str, target: str, source: str,
                  binding: Dict[str, Any], on_path: set) -> Instance:
        inst = Instance(path, target)
        if (source, target) in on_path:
            return inst
        on_path = on_path | {(source, target)}
        self._lay(inst, Part(source, binding), on_path)
        return inst

    def _lay(self, inst: Instance, part: Part, on_path: set) -> None:
        inst.parts.append(part)
        svars = self.vars(part.source)
        for name, value in (part.binding.get("vars") or {}).items():
            var = svars.get(name)
            if var is None or not isinstance(value, dict):
                continue
            if "$inline" in value:
                scoped = self.scoped.get((inst.target, value["$inline"]))
                if scoped is not None:
                    self._lay(inst, Part(scoped["original"], scoped,
                                         value["$inline"] + "."), on_path)
            elif "$inst" in value:
                child_src = self.module_of(var) or ""
                child_tgt = value.get("module", "")
                b = self.unscoped.get((child_src, child_tgt))
                if b is None:
                    continue
                inst.children.append(self._instance(
                    f"{inst.path}.{value['$inst']}", child_tgt, child_src, b,
                    on_path))

    def walk(self) -> Iterator[Instance]:
        stack = list(reversed(self.roots()))
        while stack:
            inst = stack.pop()
            yield inst
            stack.extend(reversed(inst.children))

    # ---- values --------------------------------------------------------

    def leaves(self, part: Part) -> Iterator[Tuple[str, Any, str]]:
        """(dotted source name, bound value, type) of every ground leaf of
        the part's variables, in source order. Instances are skipped."""
        bvars = part.binding.get("vars") or {}
        for name, var in self.vars(part.source).items():
            if self.module_of(var) is not None:
                continue
            yield from self._leaves(part.prefix + name, bvars.get(name),
                                    var.get("type", ""))

    def _leaves(self, name: str, value: Any, tref: str
                ) -> Iterator[Tuple[str, Any, str]]:
        t = self.types.get(tref) or {}
        if t.get("kind") == "struct":
            for f in t.get("fields") or []:
                sub = value.get(f["name"]) if isinstance(value, dict) else None
                yield from self._leaves(f"{name}.{f['name']}", sub, f["type"])
        elif t.get("kind") == "vector":
            for i in range(int(t.get("size", 0))):
                if isinstance(value, list):
                    sub = value[i] if i < len(value) else None
                elif isinstance(value, dict) and "$op" not in value:
                    sub = value.get(str(i))
                else:
                    sub = None
                yield from self._leaves(f"{name}[{i}]", sub, t.get("elem", ""))
        else:
            yield name, value, tref

    def value_at(self, part: Part, path: str) -> Any:
        """The bound value of a source path (`io.a`, `v[0]`), or None when
        the path does not name a static leaf."""
        steps = re.findall(r"\[([^\]]*)\]|\.?([^.\[]+)", path)
        value: Any = part.binding.get("vars") or {}
        for n, (index, fname) in enumerate(steps):
            key = fname or index
            if isinstance(value, dict) and "$inst" in value and n == 1:
                # A port of an instance: its signal in the child, under the
                # instance's name.
                child = self.module_of(self.vars(part.source).get(
                    fname_of(steps[0])) or {})
                b = self.unscoped.get((child or "", value.get("module", "")))
                if b is None:
                    return None
                rest = path[len(fname_of(steps[0])):].lstrip(".")
                sub = self.value_at(Part(child, b), rest)
                return f"{value['$inst']}.{sub}" if isinstance(sub, str) else None
            if isinstance(value, dict) and "$inline" in value and n == 1:
                # A port of an inlined module: its signal in the binding
                # the module was inlined with.
                b = self.scoped.get((part.binding.get("generated", ""),
                                     value["$inline"]))
                if b is None:
                    return None
                rest = path[len(fname_of(steps[0])):].lstrip(".")
                return self.value_at(Part(b["original"], b), rest)
            if isinstance(value, dict) and not any(k.startswith("$")
                                                   for k in value):
                value = value.get(key)
            elif isinstance(value, list) and key.isdigit():
                i = int(key)
                value = value[i] if i < len(value) else None
            else:
                return None
        return value


def fname_of(step: Tuple[str, str]) -> str:
    return step[1] or step[0]


def root_var(path: str) -> str:
    return re.split(r"[.\[]", path, maxsplit=1)[0]


def is_dynamic(path: str) -> bool:
    """Whether the path indexes by a signal (`v[cnt]`)."""
    return re.search(r"\[[^\]0-9][^\]]*\]", path) is not None


def flat_name(path: str) -> str:
    """`io.a` -> `io_a`, `v[0]` -> `v_0`: the lowered FIRRTL name."""
    return re.sub(r"[.\[]", "_", path).replace("]", "")


def iter_stmts(stmts: List[Dict[str, Any]], path: Tuple = ()
               ) -> Iterator[Tuple[Dict[str, Any], Tuple]]:
    """(stmt, enclosing path) in source order. The path lists
    `(when id, True for then / False for else)` from the outermost block."""
    for s in stmts:
        yield s, path
        if s.get("kind") == "when":
            yield from iter_stmts(s.get("then") or [], path + ((s["id"], True),))
            yield from iter_stmts(s.get("else") or [], path + ((s["id"], False),))


# ---- expressions ----------------------------------------------------------

_UNARY = {"!", "~", "-"}
_REDUCE = {"andr": "&", "orr": "|", "xorr": "^"}


def render(v: Any) -> Optional[str]:
    """A guard value as a Verilog-like expression, or None when the
    value is unknown (`$optimizedOut`)."""
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    if not isinstance(v, dict) or _is_none(v):
        return None
    if "$const" in v:
        c = v["$const"]
        if isinstance(c, str):
            return str(int(c[2:], 2) if c.startswith("0b") else int(c, 0))
        return str(c)
    if "$op" in v:
        op = v["$op"]
        args = [render(a) for a in v.get("args") or []]
        if any(a is None for a in args):
            return None
        if op in _UNARY and len(args) == 1:
            return f"{op}{_wrap(args[0])}"
        if op in _REDUCE and len(args) == 1:
            return f"{_REDUCE[op]}{_wrap(args[0])}"
        if op == "?:" and len(args) == 3:
            return f"{_wrap(args[0])} ? {_wrap(args[1])} : {_wrap(args[2])}"
        if op == "[]" and len(args) in (2, 3):
            sel = ":".join(args[1:])
            return f"{_wrap(args[0])}[{sel}]"
        if op == "R{}" and len(args) == 2:
            return "{" + args[0] + "{" + args[1] + "}}"
        if op == "{}":
            return "{" + ", ".join(args) + "}"
        if len(args) == 2:
            return f"{_wrap(args[0])} {op} {_wrap(args[1])}"
        return f"{op}(" + ", ".join(args) + ")"
    return None


def _wrap(s: str) -> str:
    return s if re.fullmatch(r"[\w.$\[\]:]+", s) else f"({s})"


def term(guard: str, taken: bool) -> str:
    return _wrap(guard) if taken else f"!{_wrap(guard)}"
