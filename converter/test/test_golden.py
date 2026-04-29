"""Golden-file tests for every registered backend."""
from __future__ import annotations

import json
import pathlib
import tempfile
from typing import Any, Dict, List

import pytest
from uhdi_common.backend import Backend, all_backends, discover, get
from uhdi_common.diff import diff_dicts, format_deltas

_REPO = pathlib.Path(__file__).resolve().parent.parent
_FIXTURES = _REPO / "test" / "fixtures" / "uhdi"
_EXPECTED = _REPO / "test" / "fixtures" / "expected"


# Discover at import time so parametrize() at collection sees all backends.
discover()


def _fixture_paths() -> List[pathlib.Path]:
    return sorted(_FIXTURES.glob("*.uhdi.json"))


def _backend_names() -> List[str]:
    return [b.name for b in all_backends()]


def _expected_path(backend: Backend, fixture: pathlib.Path) -> pathlib.Path:
    """Golden path: expected/<backend>/<stem>.<output_extension>.json."""
    stem = fixture.name.replace(".uhdi.json", "")
    return _EXPECTED / backend.name / f"{stem}.{backend.output_extension}.json"


def run_backend(backend: Backend,
                uhdi: Dict[str, Any]) -> Dict[str, Any]:
    """Convert and return a comparable dict; binary backends round-trip via canonical_dump()."""
    if backend.binary_output:
        with tempfile.NamedTemporaryFile(suffix=f".{backend.output_extension}",
                                         delete=False) as tmp:
            tmp_path = pathlib.Path(tmp.name)
        try:
            backend.convert(uhdi, tmp_path)
            dumped = backend.canonical_dump(tmp_path)
            if dumped is None:
                raise NotImplementedError(
                    f"backend {backend.name!r} declares binary_output=True "
                    f"but did not override canonical_dump(); the golden "
                    f"harness has nothing comparable to assert against")
            return dumped
        finally:
            tmp_path.unlink(missing_ok=True)
    return backend.convert(uhdi, None)


# ---- the test ------------------------------------------------------------


@pytest.mark.parametrize("backend_name", _backend_names())
@pytest.mark.parametrize("fixture", _fixture_paths(),
                         ids=lambda p: p.name.replace(".uhdi.json", ""))
def test_golden(backend_name: str, fixture: pathlib.Path) -> None:
    backend = get(backend_name)
    expected_path = _expected_path(backend, fixture)
    if not expected_path.is_file():
        pytest.fail(
            f"no expected file at {expected_path.relative_to(_REPO)}\n"
            f"seed it with: python3 test/update_expected.py "
            f"{backend_name} {fixture.relative_to(_REPO)}")

    uhdi = json.loads(fixture.read_text(encoding="utf-8"))
    actual = run_backend(backend, uhdi)
    expected = json.loads(expected_path.read_text(encoding="utf-8"))

    deltas = diff_dicts(actual, expected)
    if deltas:
        pytest.fail(
            f"{fixture.name} -> {backend.name} diverges from "
            f"{expected_path.relative_to(_REPO)}\n"
            f"{format_deltas(deltas)}\n"
            f"\nto re-seed (after manual review of the change):\n"
            f"  python3 test/update_expected.py {backend_name} "
            f"{fixture.relative_to(_REPO)}")
