"""JSON Schema validation against schemas/*.schema.json."""
from __future__ import annotations

import functools
import json
import pathlib
import sys
from typing import Any, Dict, Iterator, List, Tuple

# Sibling so package-data ships them in the wheel.
_SCHEMA_DIR = pathlib.Path(__file__).resolve().parent / "schemas"
_ROOT_SCHEMA_ID = "https://uhdi/document.schema.json"


def make_document_validator() -> Any:
    """Build Draft202012Validator with sibling schemas pre-registered.
    Imports lazily to keep uhdi_common import-cheap. Schema parsing is
    cached; the validator itself is built fresh on each call."""
    from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
    registry, root_schema = _load_schemas(_SCHEMA_DIR, _ROOT_SCHEMA_ID)
    return Draft202012Validator(root_schema, registry=registry)


@functools.lru_cache(maxsize=4)
def _load_schemas(schema_dir: pathlib.Path,
                  root_id: str) -> Tuple[Any, Dict[str, Any]]:
    # schema_dir is part of the cache key so monkey-patching `_SCHEMA_DIR`
    # in tests bypasses the cache.
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    if not schema_dir.is_dir():
        raise FileNotFoundError(
            f"schema directory not found at {schema_dir}; "
            f"expected schemas/ alongside uhdi_common/validate.py")

    store: Dict[str, Dict[str, Any]] = {}
    seen: Dict[str, pathlib.Path] = {}
    for path in schema_dir.glob("*.schema.json"):
        with path.open(encoding="utf-8") as f:
            schema = json.load(f)
        if "$id" not in schema:
            raise ValueError(f"{path}: missing required $id field")
        sid = schema["$id"]
        if sid in seen:
            raise ValueError(
                f"duplicate $id {sid!r}: {path} vs {seen[sid]}")
        seen[sid] = path
        store[sid] = schema

    if root_id not in store:
        raise FileNotFoundError(
            f"root schema {root_id!r} not found under {schema_dir}")

    registry = Registry().with_resources(
        (uri, Resource(contents=schema, specification=DRAFT202012))
        for uri, schema in store.items()
    )
    return registry, store[root_id]


def iter_errors(uhdi: Dict[str, Any]) -> Iterator[Any]:
    """Yield ValidationError instances sorted by path (stable across runs)."""
    validator = make_document_validator()
    return iter(sorted(validator.iter_errors(uhdi),
                       key=lambda e: list(e.absolute_path)))


def validate_or_exit(uhdi: Dict[str, Any], source: pathlib.Path) -> int:
    """CLI helper: print violations to stderr; 0 clean, 2 on violations.

    Dangling refs surface as warnings only -- partial pools are common in
    emitter intermediates, and converters cope via fallback resolution."""
    for ref_err in referential_errors(uhdi):
        print(f"{source}: warning: dangling ref: {ref_err}", file=sys.stderr)

    for rep_err in representations_errors(uhdi):
        print(f"{source}: warning: unknown representation: {rep_err}",
              file=sys.stderr)

    for enum_err in enum_width_errors(uhdi):
        print(f"{source}: warning: enum invariant: {enum_err}",
              file=sys.stderr)

    errs = list(iter_errors(uhdi))
    if not errs:
        return 0
    print(f"{source}: {len(errs)} schema violation(s)", file=sys.stderr)
    for e in errs:
        path = "/".join(str(p) for p in e.absolute_path) or "<root>"
        print(f"  at {path}: {e.message}", file=sys.stderr)
    return 2


# Reference key -> acceptable pools.  enable/guard/matchRef are polymorphic:
# emitters put either an expression id or a bare variable id there.
_REF_TO_POOLS: Dict[str, Tuple[str, ...]] = {
    "typeRef":            ("types",),
    "elementRef":         ("types",),
    "underlyingTypeRef":  ("types",),
    "varRef":             ("variables",),
    "exprRef":            ("expressions",),
    "condRef":            ("expressions",),
    "scopeRef":           ("scopes",),
    "ownerScopeRef":      ("scopes",),
    "containerScopeRef":  ("scopes",),
    "guardRef":           ("expressions", "variables"),
    "enableRef":          ("expressions", "variables"),
    "matchRef":           ("expressions", "variables"),
}


def representations_errors(uhdi: Dict[str, Any]) -> List[str]:
    """Diagnostics for per-entity representations keys not declared at top level.

    Spec §6.6 invariant 3 and §7.6 invariant 9 require per-entity
    `representations` keys to be a subset of top-level `representations`."""
    top_keys = set((uhdi.get("representations") or {}).keys())
    errs: List[str] = []

    for var_id, var in (uhdi.get("variables") or {}).items():
        if not isinstance(var, dict):
            continue
        for key in (var.get("representations") or {}):
            if key not in top_keys:
                errs.append(
                    f"variables.{var_id}.representations[{key!r}] "
                    f"(not in document representations)")

    for scope_id, scope in (uhdi.get("scopes") or {}).items():
        if not isinstance(scope, dict):
            continue
        for key in (scope.get("representations") or {}):
            if key not in top_keys:
                errs.append(
                    f"scopes.{scope_id}.representations[{key!r}] "
                    f"(not in document representations)")

    return sorted(errs)


def enum_width_errors(uhdi: Dict[str, Any]) -> List[str]:
    """Diagnostics for enum variant keys that overflow underlyingTypeRef width.

    Spec §4.4 invariants 3 (underlying must be ground integer) and 4
    (variant keys must fit width)."""
    types = uhdi.get("types") or {}
    errs: List[str] = []

    for type_id, t in types.items():
        if not isinstance(t, dict) or t.get("kind") != "enum":
            continue
        underlying_ref = t.get("underlyingTypeRef")
        if not isinstance(underlying_ref, str):
            continue
        underlying = types.get(underlying_ref)
        if not isinstance(underlying, dict):
            continue
        kind = underlying.get("kind")
        if kind not in ("uint", "sint"):
            errs.append(
                f"types.{type_id}.underlyingTypeRef -> "
                f"types[{underlying_ref!r}] is kind={kind!r}, "
                f"must be uint or sint")
            continue
        width = underlying.get("width")
        if not isinstance(width, int) or width < 0:
            continue
        if kind == "uint":
            lo = 0
            hi = (1 << width) - 1 if width > 0 else 0
        else:
            if width == 0:
                continue
            half = 1 << (width - 1)
            lo, hi = -half, half - 1

        for key in (t.get("variants") or {}):
            try:
                k = int(key)
            except (TypeError, ValueError):
                continue
            if not (lo <= k <= hi):
                errs.append(
                    f"types.{type_id}.variants[{key!r}] = {k} "
                    f"out of range [{lo}, {hi}] for "
                    f"underlying {kind}<{width}>")

    return sorted(errs)


def referential_errors(uhdi: Dict[str, Any]) -> List[str]:
    """Diagnostics for every dangling cross-pool reference; [] if closed."""
    pool_names = {p for ps in _REF_TO_POOLS.values() for p in ps}
    pools = {name: (uhdi.get(name) or {}) for name in pool_names}
    errs: List[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                here = f"{path}.{k}" if path else k
                if k in _REF_TO_POOLS and isinstance(v, str):
                    candidates = _REF_TO_POOLS[k]
                    if not any(v in pools[p] for p in candidates):
                        joined = "|".join(candidates)
                        errs.append(
                            f"{here} -> {joined}[{v!r}] (not in pool)")
                else:
                    walk(v, here)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]")

    # top[] is a bare list of scope ids -- not caught by the dict-key walk.
    for i, sid in enumerate(uhdi.get("top") or []):
        if isinstance(sid, str) and sid not in pools["scopes"]:
            errs.append(f"top[{i}] -> scopes[{sid!r}] (not in pool)")

    for name in ("types", "variables", "expressions", "scopes", "dataflow"):
        walk(uhdi.get(name) or {}, name)

    return sorted(errs)
