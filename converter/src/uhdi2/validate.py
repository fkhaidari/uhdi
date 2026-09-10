"""JSON Schema validation for tree-shaped UHDI documents."""
from __future__ import annotations

import pathlib
from typing import Iterator

import jsonschema

_SCHEMA_PATH = pathlib.Path(__file__).parent / "schema" / "uhdi-1.0.schema.json"


def _load_validator() -> jsonschema.protocols.Validator:
    import json
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    return validator_cls(schema)


def iter_errors(doc_v2: dict) -> Iterator[jsonschema.exceptions.ValidationError]:
    """Yield schema violations, if any."""
    return _load_validator().iter_errors(doc_v2)


def is_valid(doc_v2: dict) -> bool:
    return _load_validator().is_valid(doc_v2)
