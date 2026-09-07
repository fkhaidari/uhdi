"""Format dropped three redundant keys: variables.*.ownerScopeRef,
variables.*.representations.verilog.name, scopes.*.name. Every backend
must produce identical output whether or not a document carries them."""
from __future__ import annotations

import copy
import json
import pathlib
from typing import Any, Dict, List

import pytest
from uhdi_common.backend import discover, get
from uhdi_common.diff import diff_dicts, format_deltas

from test_golden import run_backend

_REPO = pathlib.Path(__file__).resolve().parent.parent
_FIXTURES = _REPO / "test" / "fixtures" / "uhdi"
_ALU_FIXTURE = _FIXTURES / "alu_member_refs.uhdi.json"

discover()


def _fixture_paths() -> List[pathlib.Path]:
    return sorted(_FIXTURES.glob("*.uhdi.json"))


def _strip_dropped_keys(uhdi: Dict[str, Any]) -> Dict[str, Any]:
    """Deep copy with the three dropped keys removed."""
    doc = copy.deepcopy(uhdi)
    for var in (doc.get("variables") or {}).values():
        var.pop("ownerScopeRef", None)
        verilog = (var.get("representations") or {}).get("verilog")
        if isinstance(verilog, dict):
            verilog.pop("name", None)
    for scope in (doc.get("scopes") or {}).values():
        scope.pop("name", None)
    return doc


def _lossy_verilog_vars(uhdi: Dict[str, Any]) -> List[str]:
    """Variable ids whose legacy verilog.name has no new-format equivalent:
    value is present but is neither `sigName` nor `constant` (an
    aggregate/composite value, e.g. `exprRef`). The format drop is lossy
    for these -- there is no single physical signal name to derive."""
    lossy = []
    for var_id, var in (uhdi.get("variables") or {}).items():
        verilog = (var.get("representations") or {}).get("verilog")
        if not isinstance(verilog, dict) or not verilog.get("name"):
            continue
        value = verilog.get("value")
        if isinstance(value, dict) and not ({"sigName", "constant"} & value.keys()):
            lossy.append(var_id)
    return lossy


def test_strip_actually_removes_the_dropped_keys():
    """Sanity check on the stripping helper itself, not the converters."""
    uhdi = json.loads(_ALU_FIXTURE.read_text(encoding="utf-8"))
    stripped = _strip_dropped_keys(uhdi)
    assert any("ownerScopeRef" in v for v in uhdi["variables"].values())
    assert not any("ownerScopeRef" in v for v in stripped["variables"].values())
    assert any("name" in s for s in uhdi["scopes"].values())
    assert not any("name" in s for s in stripped["scopes"].values())


@pytest.mark.parametrize("backend_name", ["hgldd", "hgdb", "hgdb_json", "pdg"])
@pytest.mark.parametrize("fixture", _fixture_paths(),
                         ids=lambda p: p.name.replace(".uhdi.json", ""))
def test_stripped_document_matches_original(backend_name: str,
                                             fixture: pathlib.Path) -> None:
    backend = get(backend_name)
    uhdi = json.loads(fixture.read_text(encoding="utf-8"))
    stripped = _strip_dropped_keys(uhdi)

    original = run_backend(backend, uhdi)
    from_stripped = run_backend(backend, stripped)

    deltas = diff_dicts(from_stripped, original)
    if deltas:
        lossy = _lossy_verilog_vars(uhdi)
        if lossy:
            pytest.xfail(
                f"{fixture.name} -> {backend_name}: legacy verilog.name for "
                f"aggregate-valued var(s) {lossy} has no new-format "
                f"equivalent (value is not sigName/constant) -- known lossy "
                f"case, not a converter bug")
        pytest.fail(
            f"{fixture.name} -> {backend_name} output for the stripped "
            f"document diverges from the original\n{format_deltas(deltas)}")
