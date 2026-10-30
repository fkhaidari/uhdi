"""JSON Schema validation for UHDI documents.

A document with `modules` at the top is the older tree shape (`upgrade` /
`downgrade`) and is checked against the 1.0 core and Chisel schemas. Any
other document is the current layout: it must satisfy the core schema, and
the `x-chisel` companion when `profiles` lists `x-chisel`. The core accepts
an `x-<vendor>` key without looking inside; the companion gives that body
a shape.
"""
from __future__ import annotations

import json
import pathlib
from typing import Any, Iterator, List

import jsonschema

_SCHEMA_DIR = pathlib.Path(__file__).parent / "schema"
_CORE_PATH = _SCHEMA_DIR / "uhdi-2.1-draft.schema.json"
_X_CHISEL_PATH = _SCHEMA_DIR / "uhdi-x-chisel.schema.json"
# The tree shape, kept for `uhdi2 upgrade` / `downgrade` output.
_TREE_CORE_PATH = _SCHEMA_DIR / "uhdi-core-1.0.schema.json"
_TREE_CHISEL_PATH = _SCHEMA_DIR / "uhdi-chisel-1.0.schema.json"


def _load_validator(path: pathlib.Path) -> jsonschema.protocols.Validator:
    schema = json.loads(path.read_text(encoding="utf-8"))
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    return validator_cls(schema)


def _validators(doc: Any) -> List[jsonschema.protocols.Validator]:
    if isinstance(doc, dict) and "modules" in doc:
        return [_load_validator(_TREE_CORE_PATH),
                _load_validator(_TREE_CHISEL_PATH)]
    paths = [_CORE_PATH]
    profiles = doc.get("profiles") if isinstance(doc, dict) else None
    if isinstance(profiles, list) and "x-chisel" in profiles:
        paths.append(_X_CHISEL_PATH)
    return [_load_validator(p) for p in paths]


def iter_errors(doc: Any) -> Iterator[jsonschema.exceptions.ValidationError]:
    """Yield schema violations, if any."""
    for validator in _validators(doc):
        yield from validator.iter_errors(doc)


def is_valid(doc: Any) -> bool:
    return all(v.is_valid(doc) for v in _validators(doc))
