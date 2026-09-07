"""Unit tests for uhdi_common.refs."""
from __future__ import annotations

from uhdi_common.context import BaseContext
from uhdi_common.refs import (
    build_dotted_name_map,
    loc_column,
    loc_file_path,
    loc_line,
    resolve_authoring_name,
    resolve_sig_name,
    resolve_var_by_ref,
    root_scopes,
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
    if "top" in pools:
        doc["top"] = pools["top"]
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


def test_resolve_sig_name_falls_back_to_authoring_name(capsys=None):
    """Issue #21: post-DCE CIRCT EmitUHDI may emit guardRefs as authoring
    names rather than stable_ids. resolver must hop through `chisel.name`
    to land on the verilog sig_name without the caller knowing."""
    ctx = _ctx(variables={"var_GCD_io_en": {
        "representations": {"chisel": {"name": "io_en"},
                            "verilog": {"name": "io_en",
                                        "value": {"sigName": "io_en"}}},
    }})
    assert resolve_sig_name("io_en", ctx) == "io_en"


def test_resolve_sig_name_authoring_fallback_returns_dce_rewritten_sig():
    """When the authoring name maps to a var whose verilog sig was
    rewired by DCE (e.g. `q -> r`), the fallback returns the rewired
    sig, not the authoring token verbatim."""
    ctx = _ctx(variables={"var_q": {
        "representations": {"chisel": {"name": "q"},
                            "verilog": {"name": "q",
                                        "value": {"sigName": "r"}}},
    }})
    assert resolve_sig_name("q", ctx) == "r"


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


def test_resolve_authoring_name_falls_back_to_authoring_name():
    """Symmetric with resolve_sig_name's fallback: passing an authoring
    name resolves to itself when the pool has the matching variable."""
    ctx = _ctx(variables={"var_GCD_io_en": {
        "representations": {"chisel": {"name": "io_en"},
                            "verilog": {"name": "io_en"}},
    }})
    assert resolve_authoring_name("io_en", ctx) == "io_en"


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


def test_loc_file_path_none_when_index_negative():
    # Python's negative indexing would silently wrap to the last file.
    ctx = _ctx(representations={"chisel": {"files": ["a.scala", "b.scala"]}})
    assert loc_file_path({"file": -1}, "chisel", ctx) is None


def test_loc_file_path_none_when_repr_missing():
    """Undeclared repr yields None, not KeyError."""
    ctx = _ctx(representations={"chisel": {"files": ["a.scala"]}})
    assert loc_file_path({"file": 0}, "firrtl", ctx) is None


def test_loc_file_path_none_when_file_key_absent():
    """Missing `file` key must not silently default to files[0] (FU2.4)."""
    ctx = _ctx(representations={"chisel": {"files": ["a.scala", "b.scala"]}})
    assert loc_file_path({"beginLine": 5, "beginColumn": 3}, "chisel", ctx) is None


def test_loc_file_path_handles_string_index():
    """Emitter may write `file` as a string; int() coercion matches loc_line (FU2.5)."""
    ctx = _ctx(representations={"chisel": {"files": ["a.scala", "b.scala"]}})
    assert loc_file_path({"file": "1"}, "chisel", ctx) == "b.scala"
    assert loc_file_path({"file": "not-a-number"}, "chisel", ctx) is None


def test_loc_line_and_column_default_to_zero():
    """hgdb stores INTEGER NOT NULL, so missing fields must normalise to 0, not None."""
    assert loc_line(None) == 0
    assert loc_line({}) == 0
    assert loc_line({"beginLine": 42}) == 42
    assert loc_column({"beginColumn": 7}) == 7


def test_loc_line_handles_string_input():
    """Some emitters write line numbers as strings; int() coercion covers both."""
    assert loc_line({"beginLine": "12"}) == 12


# ---- build_dotted_name_map ------------------------------------------------


def _name(n):
    return {"representations": {"chisel": {"name": n}}}


def test_dotted_name_map_reconstructs_bundle_paths():
    """Synthetic subfields (`<io>__q`) recover `io_q` -> `io.q`."""
    ctx = _ctx(variables={
        "b": {**_name("io"), "bindKind": "node"},
        "b__q": {**_name("q"), "bindKind": "synthetic"},
        "b__rdy": {**_name("rdy"), "bindKind": "synthetic"},
        "io_q": {**_name("io_q"), "bindKind": "port"},
    })
    m = build_dotted_name_map(ctx)
    assert m == {"io_q": "io.q", "io_rdy": "io.rdy"}


def test_dotted_name_map_handles_nested_bundles():
    """Nested subfields chain: `io_sub_x` -> `io.sub.x`."""
    ctx = _ctx(variables={
        "b": {**_name("io"), "bindKind": "node"},
        "b__sub": {**_name("sub"), "bindKind": "synthetic"},
        "b__sub__x": {**_name("x"), "bindKind": "synthetic"},
    })
    m = build_dotted_name_map(ctx)
    assert m["io_sub_x"] == "io.sub.x"


def test_dotted_name_map_skips_scalars_and_literal_underscores():
    """No synthetic -> no entry; a scalar named `foo_bar` is left untouched."""
    ctx = _ctx(variables={
        "v": {**_name("foo_bar"), "bindKind": "node"},
    })
    assert build_dotted_name_map(ctx) == {}


# ---- root_scopes ------------------------------------------------------


def test_root_scopes_uses_declared_top_when_present():
    ctx = _ctx(top=["A", "B"], scopes={
        "A": {"kind": "module"}, "B": {"kind": "module"}})
    assert root_scopes(ctx) == ["A", "B"]


def test_root_scopes_derives_single_uninstantiated_module():
    """No `top`: the lone module scope no one instantiates is the root."""
    ctx = _ctx(scopes={"Top": {"kind": "module"}})
    assert root_scopes(ctx) == ["Top"]


def test_root_scopes_excludes_instantiated_modules():
    ctx = _ctx(scopes={
        "Top": {"kind": "module",
                "instantiates": [{"as": "l", "scopeRef": "Leaf"}]},
        "Leaf": {"kind": "module"},
    })
    assert root_scopes(ctx) == ["Top"]


def test_root_scopes_accepts_bare_id_instantiates_entries():
    ctx = _ctx(scopes={
        "Top": {"kind": "module", "instantiates": ["Leaf"]},
        "Leaf": {"kind": "module"},
    })
    assert root_scopes(ctx) == ["Top"]


def test_root_scopes_skips_non_module_kinds():
    ctx = _ctx(scopes={
        "Top": {"kind": "module"},
        "Ext": {"kind": "extmodule"},
    })
    assert root_scopes(ctx) == ["Top"]


def test_root_scopes_empty_declared_top_falls_back_to_derivation():
    ctx = _ctx(top=[], scopes={"Top": {"kind": "module"}})
    assert root_scopes(ctx) == ["Top"]
