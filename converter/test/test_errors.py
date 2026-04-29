"""Converter failure-mode tests."""
from __future__ import annotations

import pytest
from uhdi_to_hgdb import HGDBConversionError
from uhdi_to_hgdb import convert as hgdb_convert
from uhdi_to_hgldd import HGLDDConversionError
from uhdi_to_hgldd import convert as hgldd_convert


def _doc(**overrides):
    base = {
        "format": {"name": "uhdi", "version": "1.0"},
        "representations": {
            "chisel": {"kind": "source", "language": "Chisel",
                       "files": ["X.scala"]},
            "verilog": {"kind": "hdl", "language": "SystemVerilog",
                        "files": ["X.sv"]},
        },
        "roles": {"authoring": "chisel", "simulation": "verilog",
                  "canonical": "verilog"},
        "top": ["Top"],
        "types": {},
        "expressions": {},
        "variables": {},
        "scopes": {"Top": {"name": "Top", "kind": "module",
                           "representations": {"chisel": {"name": "Top"},
                                                "verilog": {"name": "Top"}},
                           "variableRefs": []}},
    }
    base.update(overrides)
    return base


# ---- shared "is this a uhdi document at all" path -----------------------


def test_hgldd_rejects_non_uhdi_format():
    bogus = {"format": {"name": "hgldd"}}
    with pytest.raises(HGLDDConversionError, match="not a uhdi document"):
        hgldd_convert(bogus)


def test_hgdb_rejects_non_uhdi_format(tmp_path):
    """Verify exception fires before the output file is created."""
    out = tmp_path / "out.db"
    with pytest.raises(HGDBConversionError, match="not a uhdi document"):
        hgdb_convert({"format": {"name": "hgldd"}}, out)
    assert not out.exists()


def test_hgldd_rejects_missing_format_block():
    with pytest.raises(HGLDDConversionError, match="not a uhdi document"):
        hgldd_convert({"top": ["X"]})


def test_hgdb_rejects_missing_format_block(tmp_path):
    with pytest.raises(HGDBConversionError, match="not a uhdi document"):
        hgdb_convert({"top": ["X"]}, tmp_path / "out.db")


# ---- top -> unknown scope ------------------------------------------------


def test_hgldd_rejects_top_pointing_at_unknown_scope():
    """A stale reference is an emitter / hand-edit bug, not silent empty output."""
    doc = _doc(top=["Ghost"])
    with pytest.raises(HGLDDConversionError, match="Ghost"):
        hgldd_convert(doc)


def test_hgdb_rejects_top_pointing_at_unknown_scope(tmp_path):
    doc = _doc(top=["Ghost"])
    out = tmp_path / "out.db"
    with pytest.raises(HGDBConversionError, match="Ghost"):
        hgdb_convert(doc, out)
    # Atomic-write: writes go to .tmp, renamed on commit; failure leaves no file.
    assert not out.exists()


# ---- HGDB instantiation cycles -------------------------------------------


def test_hgdb_detects_instantiation_cycle(tmp_path):
    """A self/transitive instantiation cycle would walk forever; _instance_rows raises."""
    doc = _doc(scopes={
        "Top": {
            "name": "Top", "kind": "module",
            "representations": {"chisel": {"name": "Top"},
                                "verilog": {"name": "Top"}},
            "variableRefs": [],
            "instantiates": [{"as": "child", "scopeRef": "Sub"}],
        },
        "Sub": {
            "name": "Sub", "kind": "module",
            "representations": {"chisel": {"name": "Sub"},
                                "verilog": {"name": "Sub"}},
            "variableRefs": [],
            "instantiates": [{"as": "back", "scopeRef": "Top"}],
        },
    })
    with pytest.raises(HGDBConversionError, match="instantiation cycle"):
        hgdb_convert(doc, tmp_path / "out.db")


# ---- HGLDD cyclic struct types -------------------------------------------


def test_hgldd_detects_cyclic_struct_types():
    """Back-edges must be reported, else Tywaves/Surfer fail at type-name resolution."""
    doc = _doc(types={
        "Outer": {"kind": "struct", "members": [
            {"name": "inner", "typeRef": "Inner"},
        ]},
        "Inner": {"kind": "struct", "members": [
            {"name": "back", "typeRef": "Outer"},
        ]},
    }, variables={
        "var_x": {"typeRef": "Outer", "bindKind": "node",
                  "ownerScopeRef": "Top",
                  "representations": {
                      "chisel": {"name": "x", "location": {"file": 0,
                                                           "beginLine": 1}},
                  }},
    })
    doc["scopes"]["Top"]["variableRefs"] = ["var_x"]
    with pytest.raises(HGLDDConversionError, match="cycle in type pool"):
        hgldd_convert(doc)


# ---- HGDB atomic write ---------------------------------------------------


def test_hgdb_does_not_clobber_existing_output_on_failure(tmp_path):
    """Writes go to .tmp, os.replace on commit; pre-existing output survives a failure."""
    out = tmp_path / "design.db"
    out.write_bytes(b"YESTERDAYS-BYTES")

    doc = _doc(top=["Ghost"])
    with pytest.raises(HGDBConversionError):
        hgdb_convert(doc, out)

    assert out.read_bytes() == b"YESTERDAYS-BYTES"
    assert not (tmp_path / "design.db.tmp").exists()
