"""Ids are opaque: unique within their pool, no structure a consumer may
rely on (spec). Renaming every variable/scope/type id -- and rewriting
every reference to them -- must not change any backend's output, other
than fields that literally echo the id string itself."""
from __future__ import annotations

import copy
import json
import pathlib
import secrets
from typing import Any, Dict, List, Tuple

import pytest
from uhdi_common.backend import discover, get
from uhdi_common.diff import diff_dicts, format_deltas
from uhdi_common.validate import _REF_TO_POOLS

from test_golden import run_backend

_REPO = pathlib.Path(__file__).resolve().parent.parent
_FIXTURES = _REPO / "test" / "fixtures" / "uhdi"

discover()

# `_REF_TO_POOLS` (validate.py) omits `variableRefs` (checked separately by
# `variable_ownership_errors`) and the top-level `top` list; both hold ids
# from the `scopes`/`variables` pools and must be rewritten too. It also
# declares `condRef` as expressions-only, but uhdi_to_pdg's own comment
# ("condRef may be a varRef or exprRef") and the `with_assert` fixture
# (`condRef: "var_in"`) show it is polymorphic like guard/enable/match --
# widen it here so a bare variable id in `condRef` gets rewritten too.
_ALL_REF_TO_POOLS: Dict[str, Tuple[str, ...]] = {
    **_REF_TO_POOLS,
    "variableRefs": ("variables",),
    "top": ("scopes",),
    "condRef": ("expressions", "variables"),
}

# Pools whose keys are ids in scope for the opacity guarantee.
_RENAMED_POOLS = ("types", "variables", "scopes")


def _fixture_paths() -> List[pathlib.Path]:
    return sorted(_FIXTURES.glob("*.uhdi.json"))


def _random_id_map(ids) -> Dict[str, str]:
    """Old id -> a same-shape-for-every-id, content-free replacement.
    Independent random hex per id: no correlation to original order or
    spelling, so nothing downstream can rely on id structure."""
    return {old: f"id{secrets.token_hex(8)}" for old in ids}


def _rewrite_ref(value: str, maps: Dict[str, Dict[str, str]],
                 pools: Tuple[str, ...]) -> str:
    """Resolve `value` against the first candidate pool's map that has it.
    Falls through unchanged for expression ids (not renamed here) and for
    the bare authoring names some polymorphic ref fields also accept."""
    for pool in pools:
        renamed = maps.get(pool, {}).get(value)
        if renamed is not None:
            return renamed
    return value


def _rename_ids(uhdi: Dict[str, Any]) -> Dict[str, Any]:
    """Deep copy of `uhdi` with every types/variables/scopes id replaced,
    and every reference to them (per `_ALL_REF_TO_POOLS`) rewritten to
    match. Mirrors validate.py's own referential walk so nothing it
    considers a ref field is missed."""
    doc = copy.deepcopy(uhdi)
    maps = {pool: _random_id_map((doc.get(pool) or {}).keys())
            for pool in _RENAMED_POOLS}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k in _ALL_REF_TO_POOLS and isinstance(v, str):
                    node[k] = _rewrite_ref(v, maps, _ALL_REF_TO_POOLS[k])
                elif k in _ALL_REF_TO_POOLS and isinstance(v, list):
                    node[k] = [_rewrite_ref(item, maps, _ALL_REF_TO_POOLS[k])
                               if isinstance(item, str) else item
                               for item in v]
                else:
                    walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(doc)

    for pool in _RENAMED_POOLS:
        if pool in doc:
            doc[pool] = {maps[pool].get(old, old): rec
                         for old, rec in doc[pool].items()}

    return doc, maps


def _map_ids_back(value: Dict[str, Any], maps: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
    """Undo the renaming inside converter output: a backend that echoes an
    id verbatim (e.g. HGLDD names a struct type object after its own
    `typeRef`) will carry the new id in its output; substitute the
    original id back in so the comparison isolates real behavior changes
    from expected id-text differences."""
    text = json.dumps(value)
    for pool_map in maps.values():
        for old, new in pool_map.items():
            text = text.replace(new, old)
    return json.loads(text)


@pytest.mark.parametrize("backend_name", ["hgldd", "hgdb", "hgdb_json", "pdg"])
@pytest.mark.parametrize("fixture", _fixture_paths(),
                         ids=lambda p: p.name.replace(".uhdi.json", ""))
def test_backend_output_is_invariant_to_id_renaming(backend_name: str,
                                                     fixture: pathlib.Path) -> None:
    backend = get(backend_name)
    uhdi = json.loads(fixture.read_text(encoding="utf-8"))
    renamed, maps = _rename_ids(uhdi)

    original = run_backend(backend, uhdi)
    actual = _map_ids_back(run_backend(backend, renamed), maps)

    deltas = diff_dicts(actual, original)
    if deltas:
        pytest.fail(
            f"{fixture.name} -> {backend_name}: output changed under "
            f"id renaming (after mapping ids back), meaning the "
            f"converter relies on id shape/spelling rather than treating "
            f"ids as opaque\n{format_deltas(deltas)}")
