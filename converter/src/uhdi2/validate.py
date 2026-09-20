"""JSON Schema validation for tree-shaped UHDI documents.

Two schemas, one document. The core describes hardware and knows nothing
about any frontend; it accepts an `x-<vendor>` key on any record without
looking inside. The companion gives those bodies a shape, so an extension
is a specified thing rather than a blob. A document is valid when it
satisfies both.
"""
from __future__ import annotations

import pathlib
from typing import Iterator, List

import jsonschema

_SCHEMA_DIR = pathlib.Path(__file__).parent / "schema"
_CORE_PATH = _SCHEMA_DIR / "uhdi-core-1.0.schema.json"
_CHISEL_PATH = _SCHEMA_DIR / "uhdi-chisel-1.0.schema.json"


def _load_validator(path: pathlib.Path) -> jsonschema.protocols.Validator:
    import json
    schema = json.loads(path.read_text(encoding="utf-8"))
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    return validator_cls(schema)


def _validators() -> List[jsonschema.protocols.Validator]:
    return [_load_validator(_CORE_PATH), _load_validator(_CHISEL_PATH)]


def iter_errors(doc_v2: dict) -> Iterator[jsonschema.exceptions.ValidationError]:
    """Yield schema violations, if any."""
    for validator in _validators():
        yield from validator.iter_errors(doc_v2)


def is_valid(doc_v2: dict) -> bool:
    return all(v.is_valid(doc_v2) for v in _validators())
