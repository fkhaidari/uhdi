"""Tests for the UHDI 2.0 prototype (`uhdi2`): upgrade, schema validation,
and v2 -> HGLDD conversion vs. the v1 path's goldens.

Every fixture's v2 -> HGLDD output is expected to diverge from the v1
golden (marked `xfail(strict=True)`): v2's `Variable`/`Module.target`
carry no simulation-side location field at all, so `hdl_loc` is lost for
every fixture that has one, and structs additionally lose per-field
`source_lang_type_info` (sourced in v1 from `bindKind: synthetic`
Variables that v2 never materializes as records). See `uhdi2/to_hgldd.py`
for the full accounting. A fixture that unexpectedly matches (XPASS) means
that accounting is wrong and must be revisited.
"""
from __future__ import annotations

import json
import pathlib
from typing import Any, Dict, List

import pytest
from uhdi2.to_hgldd import convert as to_hgldd_v2
from uhdi2.upgrade import upgrade
from uhdi2.validate import iter_errors
from uhdi_common.diff import diff_dicts, format_deltas
from uhdi_to_hgldd.convert import convert as to_hgldd_v1

_REPO = pathlib.Path(__file__).resolve().parent.parent
_FIXTURES = _REPO / "test" / "fixtures" / "uhdi"

# fixture stem -> reason v2's HGLDD output diverges from the v1 golden.
_XFAIL_REASONS = {
    "alu_member_refs": ("hdl_loc lost everywhere; struct hgl_loc/source_lang_type_info "
                        "lost (both sourced from bindKind:synthetic subfield Variables, "
                        "which v2 never materializes as records)"),
    "bundle_io": "hdl_loc lost (v2 Variable/Module.target carry no simulation-side location)",
    "counter": "hdl_loc lost (v2 Variable/Module.target carry no simulation-side location)",
    "counter_with_otherwise": "hdl_loc lost (v2 Variable/Module.target carry no simulation-side location)",
    "leaf_and_top": "hdl_loc lost, plus instance hgl_loc/hdl_loc (v2 Instance has no location field)",
    "nested_bundle": ("hdl_loc lost everywhere; struct hgl_loc/source_lang_type_info "
                      "lost (both sourced from bindKind:synthetic subfield Variables, "
                      "which v2 never materializes as records)"),
    "vec_of_struct": ("hdl_loc lost everywhere; struct hgl_loc/source_lang_type_info/params "
                      "lost (all sourced from bindKind:synthetic subfield Variables, "
                      "which v2 never materializes as records)"),
    "with_assert": "hdl_loc lost (v2 Variable/Module.target carry no simulation-side location)",
    "with_expression": "hdl_loc lost (v2 Variable/Module.target carry no simulation-side location)",
}


def _fixture_paths() -> List[pathlib.Path]:
    return sorted(_FIXTURES.glob("*.uhdi.json"))


def _load(path: pathlib.Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _stem(path: pathlib.Path) -> str:
    return path.name.replace(".uhdi.json", "")


@pytest.mark.parametrize("fixture", _fixture_paths(), ids=_stem)
def test_upgrade_validates(fixture: pathlib.Path) -> None:
    doc_v1 = _load(fixture)
    doc_v2 = upgrade(doc_v1)
    errors = list(iter_errors(doc_v2))
    assert not errors, "\n".join(str(e) for e in errors)


@pytest.mark.parametrize("fixture", _fixture_paths(), ids=_stem)
def test_upgrade_idempotent_validation(fixture: pathlib.Path) -> None:
    """Re-validating an already-upgraded document (as an already-v2
    caller would receive it) still passes -- upgrade output is a stable
    fixed point of validation, not just valid once."""
    doc_v1 = _load(fixture)
    doc_v2 = upgrade(doc_v1)
    assert not list(iter_errors(doc_v2))
    # Round-trip through JSON (as a real consumer would) and re-validate.
    doc_v2_roundtrip = json.loads(json.dumps(doc_v2))
    assert not list(iter_errors(doc_v2_roundtrip))


@pytest.mark.parametrize("fixture", _fixture_paths(), ids=_stem)
def test_upgrade_deterministic(fixture: pathlib.Path) -> None:
    doc_v1 = _load(fixture)
    first = json.dumps(upgrade(doc_v1), sort_keys=True)
    second = json.dumps(upgrade(doc_v1), sort_keys=True)
    assert first == second


def test_alu_variables() -> None:
    doc_v1 = _load(_FIXTURES / "alu_member_refs.uhdi.json")
    doc_v2 = upgrade(doc_v1)
    alu = doc_v2["modules"]["Alu"]
    assert set(alu["variables"].keys()) == {"clock", "reset", "io", "res"}
    assert alu["variables"]["io"]["bind"]["verilog"] == {
        "in.a": "io_in_a",
        "in.b": "io_in_b",
        "in.op": "io_in_op",
        "out": "io_out",
    }


def _hgldd_params() -> List[Any]:
    params = []
    for fixture in _fixture_paths():
        stem = _stem(fixture)
        reason = _XFAIL_REASONS[stem]
        params.append(pytest.param(
            fixture, id=stem,
            marks=pytest.mark.xfail(reason=reason, strict=True)))
    return params


@pytest.mark.parametrize("fixture", _hgldd_params())
def test_v2_to_hgldd_matches_v1(fixture: pathlib.Path) -> None:
    doc_v1 = _load(fixture)
    expected = to_hgldd_v1(doc_v1)
    actual = to_hgldd_v2(upgrade(doc_v1))

    deltas = diff_dicts(actual, expected)
    assert not deltas, format_deltas(deltas)
