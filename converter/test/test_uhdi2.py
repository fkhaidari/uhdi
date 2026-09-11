"""Tests for the tree-shaped UHDI prototype (`uhdi2`): upgrade, schema validation,
and v2 -> HGLDD conversion vs. the v1 path's goldens.

Every fixture's v2 -> HGLDD output is required to match the v1 golden
byte-for-byte. See `uhdi2/upgrade.py` and `uhdi2/to_hgldd.py` for how the
v2 shape (amended per the upgrade report to add `types[X].source`,
unflattened `Location`, and a `loc` on scalar/aggregate bindings) recovers
every field v1's HGLDD path emits, including `hdl_loc` and per-field
struct `source_lang_type_info`.
"""
from __future__ import annotations

import json
import pathlib
from typing import Any, Dict, List

import pytest
from uhdi2.downgrade import downgrade
from uhdi2.to_hgldd import convert as to_hgldd_v2
from uhdi2.upgrade import upgrade
from uhdi2.validate import iter_errors
from uhdi_common.diff import diff_dicts, format_deltas
from uhdi_to_hgldd.convert import convert as to_hgldd_v1

_REPO = pathlib.Path(__file__).resolve().parent.parent
_FIXTURES = _REPO / "test" / "fixtures" / "uhdi"
_FIXTURES_UHDI2 = _REPO / "test" / "fixtures" / "uhdi2"


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
    first = json.dumps(upgrade(doc_v1))
    second = json.dumps(upgrade(doc_v1))
    assert first == second


def test_alu_variables() -> None:
    doc_v1 = _load(_FIXTURES / "alu_member_refs.uhdi.json")
    doc_v2 = upgrade(doc_v1)
    alu = doc_v2["modules"]["Alu"]
    assert set(alu["variables"].keys()) == {"clock", "reset", "io", "res"}
    assert alu["variables"]["io"]["target"]["verilog"] == {
        "in.a": "io_in_a",
        "in.b": "io_in_b",
        "in.op": "io_in_op",
        "out": "io_out",
    }


def _iter_modules(modules: Dict[str, Any]):
    """Yield (key_or_None, module) for every top-level and nested-inline
    module/scope. Nested scopes (under `scopes`) have no key of their own."""
    for key, mod in modules.items():
        yield key, mod
        for nested in mod.get("scopes") or []:
            yield None, nested


@pytest.mark.parametrize("fixture", _fixture_paths(), ids=_stem)
def test_upgrade_drops_redundant_fields(fixture: pathlib.Path) -> None:
    """Audited-away fields never reappear: constant `format.layers`/
    `format.name`, `variables.*.role` (redundant with `"direction" in
    var`), top-level `modules.<key>.target.name` (redundant with the
    key), and a variable `source.name` equal to its own key (redundant
    with the key; only a name collision should ever populate it)."""
    doc_v1 = _load(fixture)
    doc_v2 = upgrade(doc_v1)

    assert "layers" not in doc_v2["format"]
    assert "name" not in doc_v2["format"]

    for key, mod in _iter_modules(doc_v2["modules"]):
        if key is not None:
            assert "name" not in (mod.get("target") or {})
        for var_key, var in (mod.get("variables") or {}).items():
            assert "role" not in var
            source_name = (var.get("source") or {}).get("name")
            assert source_name != var_key


@pytest.mark.parametrize("fixture", _fixture_paths(), ids=_stem)
def test_v2_to_hgldd_matches_v1(fixture: pathlib.Path) -> None:
    doc_v1 = _load(fixture)
    expected = to_hgldd_v1(doc_v1)
    actual = to_hgldd_v2(upgrade(doc_v1))

    deltas = diff_dicts(actual, expected)
    assert not deltas, format_deltas(deltas)


def test_type_pool_split_by_source_name() -> None:
    """A v1 ground type shared by two disagreeing source names (Chisel
    `Clock`/`Bool` reusing the "bool" ground type) splits into one
    types-pool entry per name -- matching the native emitter's own
    type-pool identity rule -- rather than picking one and dropping the
    other (see upgrade.py's module docstring)."""
    doc_v1 = _load(_FIXTURES / "alu_member_refs.uhdi.json")
    doc_v2 = upgrade(doc_v1)

    assert "bool" not in doc_v2["types"]
    assert doc_v2["types"]["bool_Clock"] == {"kind": "uint", "width": 1,
                                             "source": {"name": "Clock"}}
    assert doc_v2["types"]["bool_Bool"] == {"kind": "uint", "width": 1,
                                            "source": {"name": "Bool"}}

    alu = doc_v2["modules"]["Alu"]
    assert alu["variables"]["clock"]["typeRef"] == "bool_Clock"
    assert alu["variables"]["reset"]["typeRef"] == "bool_Bool"
    # No variable needs the old typeName escape hatch any more -- its own
    # typeRef already names the entry with the right source name.
    assert "typeName" not in alu["variables"]["clock"]["source"]
    assert "typeName" not in alu["variables"]["reset"]["source"]


def test_type_pool_split_rewrites_struct_members() -> None:
    """A struct member declared with a split typeRef is rewritten to the
    split entry matching that member's own occurrences (here: `ready`/
    `valid` are always plain `Bool`, never `Clock`, even though the
    "bool" ground type they share also has a `Clock`-derived root port
    elsewhere in the same module)."""
    doc_v1 = _load(_FIXTURES / "nested_bundle.uhdi.json")
    doc_v2 = upgrade(doc_v1)

    assert "bool" not in doc_v2["types"]
    cmd = doc_v2["types"]["NestedBundle_io_cmd"]
    members = {m["name"]: m["typeRef"] for m in cmd["members"]}
    assert members["ready"] == "bool_Bool"
    assert members["valid"] == "bool_Bool"


def test_module_source_params() -> None:
    """`Module.source.params` carries a parameterized module's own
    constructor params (e.g. Chisel's `width` on `Alu`/`Cpu`) the same
    way `types[X].source.params` does for a type -- there is no type
    pool for a module's own identity to share params through."""
    doc_v1 = _load(_FIXTURES_UHDI2 / "cpu_alu_instance.uhdi.json")
    doc_v2 = upgrade(doc_v1)

    assert doc_v2["modules"]["Cpu"]["source"]["params"] == [
        {"name": "width", "type": "Int", "value": "8"}]
    assert doc_v2["modules"]["Alu"]["source"]["params"] == [
        {"name": "width", "type": "Int", "value": "8"}]


def test_instance_bind() -> None:
    """A `bindKind: instance` variable (v1's "alu" port on "Cpu", typed
    by the instantiated module's own port-list struct "Cpu_alu") is
    folded into `instances["alu"].target.verilog` instead of becoming a
    `Module.variables` entry -- one entry per top-level port, the
    aggregate "io" port itself flattened one level down (see
    upgrade.py's `_instance_bind`)."""
    doc_v1 = _load(_FIXTURES_UHDI2 / "cpu_alu_instance.uhdi.json")
    doc_v2 = upgrade(doc_v1)

    cpu = doc_v2["modules"]["Cpu"]
    assert "alu" not in cpu["variables"]
    assert cpu["instances"]["alu"]["target"]["verilog"] == {
        "clock": "clock",
        "reset": "reset",
        "io": {"a": "io_a", "b": "io_b", "op": "io_op", "out": "_alu_io_out"},
    }


def test_instance_bind_downgrade_roundtrip() -> None:
    """downgrade() reconstructs the `bindKind: instance` variable
    `instances[as].target.verilog` came from (see downgrade.py's
    `_emit_instance_bind`), so hgdb/hgdb_json/pdg -- which only ever see
    a v2 document through `downgrade()` -- no longer silently lose a
    bound instance's ports. `upgrade(downgrade(upgrade(v1)))["modules"]`
    (target included) is an exact fixed point; the one documented gap is
    that the reconstruction mints a fresh, unreferenced-elsewhere struct
    typeRef for the instance itself (v2 keeps no record of v1's own key
    for it), so the round-tripped `types` pool gains that one extra entry
    relative to `upgrade(v1)` -- asserted here rather than left
    undetected (see downgrade.py's module docstring)."""
    doc_v1 = _load(_FIXTURES_UHDI2 / "cpu_alu_instance.uhdi.json")
    doc_v2 = upgrade(doc_v1)
    roundtripped = upgrade(downgrade(doc_v2))

    deltas = diff_dicts(roundtripped["modules"], doc_v2["modules"])
    assert not deltas, format_deltas(deltas)

    assert set(roundtripped["types"]) - set(doc_v2["types"]) == {"Alu#instance"}
    assert set(doc_v2["types"]) - set(roundtripped["types"]) == set()


def test_module_key_differs_from_source_name() -> None:
    """downgrade() and to_hgldd's module object both key off `source.name`
    (falling back to the `modules` key only when absent), independent of
    the `modules` key itself -- exercised because the native emitter's
    key is the Verilog module name while `source.name` is the authoring
    (Chisel) identity, and the two need not match."""
    doc_v2: Dict[str, Any] = {
        "format": {"version": "1.0"},
        "source": {"language": "Chisel", "files": ["Mod.scala"]},
        "target": {"language": "SystemVerilog", "files": ["ModKey.sv"]},
        "types": {},
        "modules": {
            "ModKey": {
                "source": {"name": "OtherName"},
                "variables": {},
            },
        },
    }
    assert not list(iter_errors(doc_v2))

    doc_v1 = downgrade(doc_v2)
    assert doc_v1["scopes"]["ModKey"]["representations"]["chisel"]["name"] == "OtherName"

    hgldd = to_hgldd_v2(doc_v2)
    mod_obj = next(o for o in hgldd["objects"] if o["kind"] == "module")
    assert mod_obj["obj_name"] == "OtherName"
    assert mod_obj["module_name"] == "ModKey"
