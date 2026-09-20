"""The `x-chisel` object of a UHDI `source`.

A UHDI document describes hardware. What only Chisel knows about an entity
sits one level down, under `x-chisel`: a variable's `binding`, and the
`typeName` and `params` of a type, a module or an instance. A reader that
does not know the frontend skips the object whole.
"""
from __future__ import annotations

from typing import Any, Dict, Iterator, Optional

EXT_KEY = "x-chisel"


def ext(source: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The extension object of `source`, empty when it has none."""
    return (source or {}).get(EXT_KEY) or {}


def put_ext(source: Dict[str, Any], key: str, value: Any) -> None:
    """Record `key` in `source`'s extension object, creating it."""
    source.setdefault(EXT_KEY, {})[key] = value


def _instances(scope: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    """The instances of a module and of every scope nested in it."""
    yield from (scope.get("instances") or {}).values()
    for nested in scope.get("scopes") or []:
        yield from _instances(nested)


def class_ext_by_module(doc_v2: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """`moduleRef` -> the `x-chisel` record its instantiation sites agree on.

    The record rides on the instance, because deduplication may merge two
    source classes into one module. A module-keyed format -- v1, HGLDD --
    has one slot per definition, so it can only take a record every
    instance names; where they disagree, the module gets none.
    """
    agreed: Dict[str, Dict[str, Any]] = {}
    split: set[str] = set()
    for module in (doc_v2.get("modules") or {}).values():
        for inst in _instances(module):
            ref = inst.get("moduleRef")
            if not ref:
                continue
            record = ext(inst.get("source"))
            if ref not in agreed:
                agreed[ref] = record
            elif agreed[ref] != record:
                split.add(ref)
    return {ref: rec for ref, rec in agreed.items() if rec and ref not in split}
