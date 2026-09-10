"""Round-trip: every v1->v2->v1 document (`downgrade(upgrade(v1))`) still
drives hgdb/hgdb_json/pdg correctly. HGLDD is excluded here -- in
production it never goes through `downgrade()` at all (it keeps its own
native v2 path); its v2 correctness is covered by
`test_uhdi2_to_hgldd.py::test_v2_to_hgldd_matches_v1`.

A downgraded document can diverge from the golden for two very different
reasons, and this test tells them apart before judging a mismatch:

  1. v2's schema has no slot for `scope.body`, the `expressions` pool, or
     top-level `dataflow` at all (see `uhdi2/downgrade.py`'s module
     docstring) -- structurally unrecoverable, not a converter bug. Isolated
     by comparing against the *same backend* run on a body/dataflow-stripped
     v1 document: if that matches, the golden divergence is exactly this
     gap.
  2. Anything left over after that is a real bug, unless it's one of the
     specific, already-documented gaps below -- those get a targeted xfail
     with a citation, everything else fails the test."""
from __future__ import annotations

import copy
import json
import pathlib
from typing import Any, Dict, List

import pytest
from uhdi_common.backend import discover, get
from uhdi_common.diff import diff_dicts, format_deltas
from uhdi2.downgrade import downgrade
from uhdi2.upgrade import upgrade

from test_golden import run_backend

_REPO = pathlib.Path(__file__).resolve().parent.parent
_FIXTURES = _REPO / "test" / "fixtures" / "uhdi"
_EXPECTED = _REPO / "test" / "fixtures" / "expected"

discover()

_ROUNDTRIP_BACKENDS = ("hgdb", "hgdb_json", "pdg")

# Fixture/backend pairs where an aggregate leaf had independent RTL port
# identity in v1 (its own duplicate-port record beside the synthetic
# member tree) -- v2 cannot tell this apart from a leaf that only ever
# existed through an inline expression chain (see downgrade.py's module
# docstring). Costs hgdb/hgdb_json extra alias rows and pdg IO vertices.
_LEAF_PORT_IDENTITY_GAP = {
    ("alu_member_refs", "hgdb"), ("alu_member_refs", "hgdb_json"),
    ("alu_member_refs", "pdg"),
    ("nested_bundle", "hgdb"), ("nested_bundle", "hgdb_json"),
    ("nested_bundle", "pdg"),
}

# A declared (non-port) variable's original v1 bindKind (reg/mem/probe/...)
# is only recoverable from its Chisel `Binding[X]` source type name; when
# that's absent (a bare `reg` with no recorded Chisel type), downgrade.py
# falls back to a generic "wire", which costs pdg the Definition-vs-
# DataDefinition vertex-kind split.
_REG_BINDKIND_GAP = {
    ("counter", "pdg"), ("counter_with_otherwise", "pdg"),
}

# The pre-existing, pre-authorized gap from test_format_drop.py: an
# aggregate variable's legacy `verilog.name` has no v2 equivalent when its
# value isn't a scalar sigName/constant (bundle_io's io.in resolves via an
# expression chain). v2 never carries this name at all, so it reappears
# here for the same reason.
_AGGREGATE_NAME_LOSS_GAP = {
    ("bundle_io", "hgdb_json"),
}


def _fixture_paths() -> List[pathlib.Path]:
    return sorted(_FIXTURES.glob("*.uhdi.json"))


def _strip_body(uhdi: Dict[str, Any]) -> Dict[str, Any]:
    doc = copy.deepcopy(uhdi)
    for scope in (doc.get("scopes") or {}).values():
        if isinstance(scope, dict):
            scope.pop("body", None)
    doc.pop("dataflow", None)
    return doc


def _expected_path(backend_name: str, fixture: pathlib.Path) -> pathlib.Path:
    stem = fixture.name.replace(".uhdi.json", "")
    ext = get(backend_name).output_extension
    return _EXPECTED / backend_name / f"{stem}.{ext}.json"


@pytest.mark.parametrize("backend_name", _ROUNDTRIP_BACKENDS)
@pytest.mark.parametrize("fixture", _fixture_paths(),
                         ids=lambda p: p.name.replace(".uhdi.json", ""))
def test_downgraded_v2_matches_golden(backend_name: str,
                                       fixture: pathlib.Path) -> None:
    stem = fixture.name.replace(".uhdi.json", "")
    backend = get(backend_name)
    v1 = json.loads(fixture.read_text(encoding="utf-8"))
    v1b = downgrade(upgrade(v1))

    actual = run_backend(backend, v1b)
    expected = json.loads(_expected_path(backend_name, fixture)
                          .read_text(encoding="utf-8"))
    deltas = diff_dicts(actual, expected)
    if not deltas:
        return

    stripped_actual = run_backend(backend, _strip_body(v1))
    residual = diff_dicts(actual, stripped_actual)
    if not residual:
        pytest.xfail(
            f"{fixture.name} -> {backend_name}: v2 has no slot for "
            f"scope.body/the expressions pool/top-level dataflow "
            f"(schema-confirmed) -- downgrade(upgrade(v1)) output here "
            f"matches what {backend_name} produces on the same v1 with "
            f"body/dataflow stripped, so the divergence from golden is "
            f"exactly that structural gap, not a converter bug")

    key = (stem, backend_name)
    if key in _LEAF_PORT_IDENTITY_GAP:
        pytest.xfail(
            f"{fixture.name} -> {backend_name}: v1 encodes an aggregate "
            f"leaf's independent RTL port identity two different, "
            f"v2-indistinguishable ways (a per-leaf duplicate-port record "
            f"vs. an inline expression chain with no such record) -- v2's "
            f"ModuleTarget/StructMember carry no port-list or per-member "
            f"direction to tell them apart, so downgrade.py never "
            f"speculatively reconstructs either; costs this fixture real "
            f"{backend_name} rows/vertices")
    if key in _REG_BINDKIND_GAP:
        pytest.xfail(
            f"{fixture.name} -> {backend_name}: this reg's original v1 "
            f"bindKind has no recorded Chisel sourceLangType to recover it "
            f"from, so downgrade.py falls back to a generic \"wire\" -- "
            f"costs pdg's Definition-vs-DataDefinition vertex-kind split")
    if key in _AGGREGATE_NAME_LOSS_GAP:
        pytest.xfail(
            f"{fixture.name} -> {backend_name}: legacy verilog.name for "
            f"an aggregate-valued var has no v2 equivalent (value resolves "
            f"through an expression chain, not a scalar sigName/constant) "
            f"-- known lossy case, same reason as "
            f"test_format_drop.py's bundle_io-hgdb_json xfail")

    pytest.fail(
        f"{fixture.name} -> {backend_name}: downgrade(upgrade(v1)) output "
        f"diverges from golden for a reason other than body/dataflow loss "
        f"or a documented gap\n{format_deltas(residual)}")


@pytest.mark.parametrize("fixture", _fixture_paths(),
                         ids=lambda p: p.name.replace(".uhdi.json", ""))
def test_downgrade_upgrade_fixed_point(fixture: pathlib.Path) -> None:
    """upgrade(downgrade(upgrade(v1))) == upgrade(v1): downgrade() must not
    lose anything upgrade() itself would have kept."""
    v1 = json.loads(fixture.read_text(encoding="utf-8"))
    v2 = upgrade(v1)
    v2_roundtripped = upgrade(downgrade(v2))
    deltas = diff_dicts(v2_roundtripped, v2)
    assert not deltas, (
        f"{fixture.name}: upgrade(downgrade(upgrade(v1))) != upgrade(v1)\n"
        f"{format_deltas(deltas)}")
