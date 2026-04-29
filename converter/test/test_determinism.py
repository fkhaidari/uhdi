"""Determinism tests: same input -> same output, every run."""
from __future__ import annotations

import copy
import json
import pathlib
import tempfile

import pytest
from uhdi_common.backend import all_backends, discover, get

_FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "uhdi"


discover()


def _convert_to_dict(backend, uhdi):
    """Duplicated from the golden harness so this test stays standalone."""
    if backend.binary_output:
        with tempfile.NamedTemporaryFile(suffix=f".{backend.output_extension}",
                                         delete=False) as tmp:
            tmp_path = pathlib.Path(tmp.name)
        try:
            backend.convert(uhdi, tmp_path)
            return backend.canonical_dump(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)
    return backend.convert(uhdi, None)


@pytest.mark.parametrize("backend_name",
                         [b.name for b in all_backends()])
@pytest.mark.parametrize("fixture",
                         sorted(_FIXTURES.glob("*.uhdi.json")),
                         ids=lambda p: p.name.replace(".uhdi.json", ""))
def test_convert_is_deterministic(backend_name, fixture):
    backend = get(backend_name)
    doc = json.loads(fixture.read_text(encoding="utf-8"))

    first = _convert_to_dict(backend, copy.deepcopy(doc))
    second = _convert_to_dict(backend, copy.deepcopy(doc))
    assert first == second


@pytest.mark.parametrize("backend_name",
                         [b.name for b in all_backends()])
@pytest.mark.parametrize("fixture",
                         sorted(_FIXTURES.glob("*.uhdi.json")),
                         ids=lambda p: p.name.replace(".uhdi.json", ""))
def test_convert_does_not_mutate_input(backend_name, fixture):
    """convert() must treat the uhdi document as immutable (idempotency, multi-backend reuse)."""
    backend = get(backend_name)
    doc = json.loads(fixture.read_text(encoding="utf-8"))
    snapshot = copy.deepcopy(doc)

    _convert_to_dict(backend, doc)
    assert doc == snapshot, "convert() mutated the input document"
