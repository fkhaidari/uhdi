"""Key the `types` of a v2 document by name.

The document lists its types and refers to one by its index. HGLDD names
every struct, so the converters key each aggregate by the name native
EmitHGLDD gives it: `<Verilog module>_<variable>[_<field>]...` of the first
variable that reaches it, an element taking the name of its vector. Types
hinted to the same name keep it only if they have no source name; the others
append theirs. Any other type is keyed by its index, which no hint can take.
"""
from __future__ import annotations

import copy
import re
from collections import Counter
from typing import Any, Dict, List

_AGGREGATE = ("struct", "vector")


def _sanitize(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z_]", "_", name)


def named_types(doc: Dict[str, Any]) -> Dict[str, Any]:
    """A copy of `doc` whose `types` is a name -> type object and whose
    type references are those names."""
    types: List[Dict[str, Any]] = doc.get("types") or []
    hints: Dict[int, str] = {}
    order: List[int] = []

    def hint(index: int, name: str) -> None:
        t = types[index]
        kind = t.get("kind")
        if kind == "struct":
            for f in t.get("fields") or []:
                if types[f["type"]].get("kind") in _AGGREGATE:
                    hint(f["type"], f"{name}_{f['name']}")
        elif kind == "vector" and types[t["elem"]].get("kind") in _AGGREGATE:
            hint(t["elem"], name)
        if index not in hints:
            hints[index] = name
            order.append(index)

    for b in doc.get("bindings", []):
        svars = (doc.get("original", {}).get(b["original"]) or {}).get("vars") or {}
        for vname, var in svars.items():
            index = var.get("type")
            if isinstance(index, int) and types[index].get("kind") in _AGGREGATE:
                hint(index, f"{b['generated']}_{vname}")
    unhinted = Counter()
    for index, t in enumerate(types):
        if t.get("kind") in _AGGREGATE and index not in hints:
            prefix = "struct_" if t["kind"] == "struct" else "array_"
            hints[index] = f"{prefix}{unhinted[prefix]}"
            unhinted[prefix] += 1
            order.append(index)

    group = Counter(hints.values())
    taken = set()
    keys: Dict[int, str] = {}

    def claim(index: int, want: str) -> None:
        key, n = want, 2
        while key in taken:
            key, n = f"{want}_{n}", n + 1
        taken.add(key)
        keys[index] = key

    for index in order:
        if group[hints[index]] == 1 or not types[index].get("name"):
            claim(index, hints[index])
    for index in order:
        if index not in keys:
            claim(index, f"{hints[index]}_{_sanitize(types[index]['name'])}")
    for index in range(len(types)):
        keys.setdefault(index, f"#{index}")

    out = copy.copy(doc)
    named: Dict[str, Any] = {}
    for index, t in enumerate(types):
        t = copy.deepcopy(t)
        for f in t.get("fields") or []:
            f["type"] = keys[f["type"]]
        for ref in ("elem", "base"):
            if ref in t:
                t[ref] = keys[t[ref]]
        named[keys[index]] = t
    out["types"] = dict(sorted(named.items()))
    out["original"] = copy.deepcopy(doc.get("original", {}))
    for module in out["original"].values():
        for var in (module.get("vars") or {}).values():
            if isinstance(var.get("type"), int):
                var["type"] = keys[var["type"]]
    return out
