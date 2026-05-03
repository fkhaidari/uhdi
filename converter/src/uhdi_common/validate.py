"""JSON Schema validation against schemas/*.schema.json."""
from __future__ import annotations

import json
import pathlib
import sys
from typing import Any, Dict, Iterator

# Sibling so package-data ships them in the wheel.
_SCHEMA_DIR = pathlib.Path(__file__).resolve().parent / "schemas"
_ROOT_SCHEMA_ID = "https://uhdi/document.schema.json"


def make_document_validator() -> Any:
    """Build Draft202012Validator with sibling schemas pre-registered.
    Imports lazily to keep uhdi_common import-cheap."""
    from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    if not _SCHEMA_DIR.is_dir():
        raise FileNotFoundError(
            f"schema directory not found at {_SCHEMA_DIR}; "
            f"expected schemas/ alongside uhdi_common/validate.py")

    store: Dict[str, Dict[str, Any]] = {}
    for path in _SCHEMA_DIR.glob("*.schema.json"):
        with path.open(encoding="utf-8") as f:
            schema = json.load(f)
        if "$id" not in schema:
            raise ValueError(f"{path}: missing required $id field")
        store[schema["$id"]] = schema

    if _ROOT_SCHEMA_ID not in store:
        raise FileNotFoundError(
            f"root schema {_ROOT_SCHEMA_ID!r} not found under {_SCHEMA_DIR}")

    registry = Registry().with_resources(
        (uri, Resource(contents=schema, specification=DRAFT202012))
        for uri, schema in store.items()
    )
    return Draft202012Validator(store[_ROOT_SCHEMA_ID], registry=registry)


def iter_errors(uhdi: Dict[str, Any]) -> Iterator[Any]:
    """Yield ValidationError instances sorted by path (stable across runs)."""
    validator = make_document_validator()
    return iter(sorted(validator.iter_errors(uhdi),
                       key=lambda e: list(e.absolute_path)))


def validate_or_exit(uhdi: Dict[str, Any], source: pathlib.Path) -> int:
    """CLI helper: print violations to stderr; 0 clean, 2 on violations."""
    errs = list(iter_errors(uhdi))
    if not errs:
        return 0
    print(f"{source}: {len(errs)} schema violation(s)", file=sys.stderr)
    for e in errs:
        path = "/".join(str(p) for p in e.absolute_path) or "<root>"
        print(f"  at {path}: {e.message}", file=sys.stderr)
    return 2
