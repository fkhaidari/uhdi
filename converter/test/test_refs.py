"""Unit tests for uhdi_common.refs."""
from __future__ import annotations

from uhdi_common.context import BaseContext
from uhdi_common.refs import (
    loc_column,
    loc_file_path,
    loc_line,
    resolve_authoring_name,
    resolve_sig_name,
    resolve_var_by_ref,
)


def _ctx(**pools):
    """Mini context built directly (skips from_uhdi format-check) for unit-level access."""
    doc = {
        "format": {"name": "uhdi"},
        "representations": pools.get("representations", {
            "chisel":  {"files": ["src.scala"]},
            "verilog": {"files": ["out.sv"]},
        }),
        "variables": pools.get("variables", {}),
        "scopes": pools.get("scopes", {}),
        "expressions": pools.get("expressions", {}),
        "types": pools.get("types", {}),
    }
    return BaseContext(uhdi=doc)


# ---- resolve_sig_name -----------------------------------------------------


def test_resolve_sig_name_prefers_value_sigName_over_name():
    """verilog.value.sigName carries the actual wire after DCE; verilog.name is the chisel label."""
    ctx = _ctx(variables={"v": {
        "representations": {"verilog": {"name": "q",
                                        "value": {"sigName": "r"}}},
    }})
    assert resolve_sig_name("v", ctx) == "r"


def test_resolve_sig_name_falls_back_to_name_when_no_value():
    """Ports with no DCE rewrite have only verilog.name; resolver accepts it as sigName."""
    ctx = _ctx(variables={"v": {
        "representations": {"verilog": {"name": "io_in_a"}},
    }})
    assert resolve_sig_name("v", ctx) == "io_in_a"


def test_resolve_sig_name_returns_none_for_missing_var():
    """Unknown stable_id is None: hgdb falls back to raw token, hgldd skips the field."""
    ctx = _ctx()
    assert resolve_sig_name("nope", ctx) is None


def test_resolve_sig_name_returns_none_when_no_verilog_repr():
    """Variable with only chisel repr (lost in MaterializeDebugInfo DCE) has no sim-side name."""
    ctx = _ctx(variables={"v": {
        "representations": {"chisel": {"name": "q"}},
    }})
    assert resolve_sig_name("v", ctx) is None


def test_resolve_sig_name_honours_custom_simulation_repr():
    """roles.simulation override (e.g. "vhdl") must drive lookup, not hardcoded 'verilog'."""
    ctx = _ctx(variables={"v": {
        "representations": {"vhdl": {"name": "wire_q"},
                            "verilog": {"name": "ignore_me"}},
    }})
    ctx.simulation_repr = "vhdl"
    assert resolve_sig_name("v", ctx) == "wire_q"


# ---- resolve_authoring_name -----------------------------------------------


def test_resolve_authoring_name_returns_chisel_name():
    ctx = _ctx(variables={"v": {
        "representations": {"chisel": {"name": "io.in.a"}},
    }})
    assert resolve_authoring_name("v", ctx) == "io.in.a"


def test_resolve_authoring_name_none_when_var_missing():
    assert resolve_authoring_name("nope", _ctx()) is None


def test_resolve_authoring_name_none_when_no_authoring_repr():
    ctx = _ctx(variables={"v": {
        "representations": {"verilog": {"name": "io_in_a"}},
    }})
    assert resolve_authoring_name("v", ctx) is None


# ---- resolve_var_by_ref ---------------------------------------------------


def test_resolve_var_by_ref_direct_pool_key():
    ctx = _ctx(variables={"var_42": {"bindKind": "port"}})
    assert resolve_var_by_ref("var_42", ctx)["bindKind"] == "port"


def test_resolve_var_by_ref_authoring_name_fallback():
    """Body referring by chisel name -> resolver scans for representations.<authoring>.name."""
    ctx = _ctx(variables={
        "var_a": {"representations": {"chisel": {"name": "io_in_a"}},
                  "bindKind": "port"},
        "var_b": {"representations": {"chisel": {"name": "io_in_b"}}},
    })
    found = resolve_var_by_ref("io_in_a", ctx)
    assert found.get("bindKind") == "port"


def test_resolve_var_by_ref_skips_non_matching_before_match():
    """Fallback walks variables.values() in insertion order; must skip earlier non-matches."""
    ctx = _ctx(variables={
        "var_first":  {"representations": {"chisel": {"name": "alpha"}},
                       "bindKind": "wire"},
        "var_second": {"representations": {"chisel": {"name": "beta"}},
                       "bindKind": "port"},
    })
    found = resolve_var_by_ref("beta", ctx)
    assert found.get("bindKind") == "port"


def test_resolve_var_by_ref_empty_dict_on_miss():
    """Returning {} lets callers .get() without a None-guard."""
    assert resolve_var_by_ref("nope", _ctx()) == {}


def test_resolve_var_by_ref_empty_string_returns_empty():
    assert resolve_var_by_ref("", _ctx()) == {}


# ---- Location helpers ----------------------------------------------------


def test_loc_file_path_indexes_into_repr_files():
    ctx = _ctx(representations={
        "chisel": {"files": ["a.scala", "b.scala"]},
    })
    assert loc_file_path({"file": 0}, "chisel", ctx) == "a.scala"
    assert loc_file_path({"file": 1}, "chisel", ctx) == "b.scala"


def test_loc_file_path_none_when_missing_loc():
    assert loc_file_path(None, "chisel", _ctx()) is None
    assert loc_file_path({}, "chisel", _ctx()) is None


def test_loc_file_path_none_when_index_out_of_range():
    """Out-of-range index -> None; backends choose hard-error vs soft-warning."""
    ctx = _ctx(representations={"chisel": {"files": ["a.scala"]}})
    assert loc_file_path({"file": 5}, "chisel", ctx) is None


def test_loc_file_path_none_when_repr_missing():
    """Undeclared repr yields None, not KeyError."""
    ctx = _ctx(representations={"chisel": {"files": ["a.scala"]}})
    assert loc_file_path({"file": 0}, "firrtl", ctx) is None


def test_loc_line_and_column_default_to_zero():
    """hgdb stores INTEGER NOT NULL, so missing fields must normalise to 0, not None."""
    assert loc_line(None) == 0
    assert loc_line({}) == 0
    assert loc_line({"beginLine": 42}) == 42
    assert loc_column({"beginColumn": 7}) == 7


def test_loc_line_handles_string_input():
    """Some emitters write line numbers as strings; int() coercion covers both."""
    assert loc_line({"beginLine": "12"}) == 12
