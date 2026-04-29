"""Unit tests for hgdb_json (JSON) converter internals."""
from __future__ import annotations

from typing import Any

import pytest
from uhdi_to_hgdb_json import HGDBJsonConversionError
from uhdi_to_hgdb_json import convert as hgdb_json_convert
from uhdi_to_hgdb_json.convert import (
    _collect_body_refs,
    _Ctx,
    _inline_var_def,
    _module_filename,
    _stmt_to_entry,
)


def _ctx(**pools: Any) -> _Ctx:
    return _Ctx(uhdi={
        "format": {"name": "uhdi"},
        "representations": pools.get("representations", {
            "chisel":  {"files": ["src.scala"]},
            "verilog": {"files": ["out.sv"]},
        }),
        "variables": pools.get("variables", {}),
        "scopes": pools.get("scopes", {}),
        "expressions": pools.get("expressions", {}),
        "types": pools.get("types", {}),
    })


# ---- _inline_var_def -----------------------------------------------------


def test_inline_var_def_falls_back_to_authoring_name_when_no_verilog():
    # DCE'd output port: emit using chisel name as `value` (matches hgdb-circt pre-DCE).
    ctx = _ctx(variables={"v": {
        "representations": {"chisel": {"name": "q"}},
    }})
    out = _inline_var_def("v", ctx.variables["v"], ctx)
    assert out == {"name": "q", "value": "q", "rtl": True}


def test_inline_var_def_returns_none_when_no_names_resolve():
    ctx = _ctx(variables={"v": {"representations": {}}})
    # Empty stable_id + no names -> None.
    out = _inline_var_def("", {"representations": {}}, ctx)
    assert out is None


def test_inline_var_def_uses_verilog_value_when_available():
    ctx = _ctx(variables={"v": {
        "representations": {
            "chisel": {"name": "q"},
            "verilog": {"name": "wire_q", "value": {"sigName": "wire_q"}},
        },
    }})
    out = _inline_var_def("v", ctx.variables["v"], ctx)
    assert out["name"] == "q"
    assert out["value"] == "wire_q"


# ---- _stmt_to_entry ------------------------------------------------------


def test_stmt_to_entry_unknown_kind_returns_none():
    assert _stmt_to_entry({"kind": "assert"}, _ctx(), {}) is None


def test_stmt_to_entry_decl_without_pool_id_returns_none():
    out = _stmt_to_entry(
        {"kind": "decl", "varRef": "ghost"}, _ctx(), {})
    assert out is None


def test_stmt_to_entry_connect_uses_assign_type():
    out = _stmt_to_entry(
        {"kind": "connect", "varRef": "v",
         "locations": {"chisel": {"file": 0, "beginLine": 5,
                                  "beginColumn": 7}}},
        _ctx(), {"v": "0"})
    assert out["type"] == "assign"
    assert out["variable"] == "0"
    assert out["line"] == 5
    assert out["column"] == 7


def test_stmt_to_entry_omits_line_when_zero():
    out = _stmt_to_entry(
        {"kind": "decl", "varRef": "v"}, _ctx(), {"v": "0"})
    assert "line" not in out
    assert "column" not in out


def test_stmt_to_entry_block_with_negated_guard_wraps_in_paren_bang():
    ctx = _ctx(variables={"g": {
        "representations": {"verilog": {"name": "rst"}},
    }})
    out = _stmt_to_entry(
        {"kind": "block", "guardRef": "g", "negated": True,
         "body": []},
        ctx, {})
    assert out["condition"] == "!(rst)"


def test_stmt_to_entry_block_unnegated_guard_renders_plain():
    ctx = _ctx(variables={"g": {
        "representations": {"verilog": {"name": "en"}},
    }})
    out = _stmt_to_entry(
        {"kind": "block", "guardRef": "g", "body": []}, ctx, {})
    assert out["condition"] == "en"


def test_stmt_to_entry_block_unresolved_guard_falls_back_to_token():
    out = _stmt_to_entry(
        {"kind": "block", "guardRef": "ghost", "body": []},
        _ctx(), {})
    assert out["condition"] == "ghost"


def test_stmt_to_entry_block_filters_unknown_nested_kinds():
    out = _stmt_to_entry(
        {"kind": "block", "body": [
            {"kind": "assert"},
            {"kind": "decl", "varRef": "v"},
        ]},
        _ctx(), {"v": "0"})
    assert len(out["scope"]) == 1
    assert out["scope"][0]["type"] == "decl"


# ---- _collect_body_refs --------------------------------------------------


def test_collect_body_refs_walks_blocks_recursively():
    scopes = {
        "Top": {"body": [
            {"kind": "block", "body": [
                {"kind": "connect", "varRef": "v_inner"},
            ]},
            {"kind": "decl", "varRef": "v_outer"},
        ]},
    }
    refs = _collect_body_refs(scopes)
    assert refs == {"v_inner", "v_outer"}


def test_collect_body_refs_skips_unknown_kinds():
    scopes = {"Top": {"body": [
        {"kind": "event", "varRef": "v_x"},
        {"kind": "decl", "varRef": "v_y"},
    ]}}
    assert _collect_body_refs(scopes) == {"v_y"}


def test_collect_body_refs_skips_decl_without_varref():
    scopes = {"Top": {"body": [
        {"kind": "decl"},
        {"kind": "connect", "varRef": "v"},
    ]}}
    assert _collect_body_refs(scopes) == {"v"}


# ---- _module_filename ----------------------------------------------------


def test_module_filename_returns_first_resolvable():
    ctx = _ctx(representations={
        "chisel": {"files": ["A.scala", "B.scala"]},
        "verilog": {"files": ["A.sv"]},
    })
    scope = {"body": [
        {"kind": "decl", "varRef": "v"},
        {"kind": "decl", "varRef": "w",
         "locations": {"chisel": {"file": 1}}},
    ]}
    assert _module_filename(scope, ctx) == "B.scala"


def test_module_filename_returns_none_when_no_locations():
    ctx = _ctx()
    assert _module_filename({"body": []}, ctx) is None


# ---- error paths ---------------------------------------------------------


def test_convert_rejects_non_uhdi_format():
    with pytest.raises(HGDBJsonConversionError, match="not a uhdi"):
        hgdb_json_convert({"format": {"name": "hgldd"}})


def test_convert_rejects_unknown_top():
    with pytest.raises(HGDBJsonConversionError, match="Ghost"):
        hgdb_json_convert({
            "format": {"name": "uhdi", "version": "1.0"},
            "top": ["Ghost"],
            "scopes": {},
            "variables": {},
            "representations": {
                "chisel": {"files": []}, "verilog": {"files": []}},
            "roles": {"authoring": "chisel", "simulation": "verilog"},
        })


# ---- end-to-end shape checks --------------------------------------------


def _doc_skeleton():
    return {
        "format": {"name": "uhdi", "version": "1.0"},
        "representations": {
            "chisel": {"kind": "source", "files": ["X.scala"]},
            "verilog": {"kind": "hdl", "files": ["X.sv"]},
        },
        "roles": {"authoring": "chisel", "simulation": "verilog",
                  "canonical": "verilog"},
        "top": ["Top"],
        "types": {"u8": {"kind": "uint", "width": 8}},
        "expressions": {},
        "variables": {},
        "scopes": {"Top": {
            "name": "Top", "kind": "module",
            "representations": {"chisel": {"name": "Top"},
                                "verilog": {"name": "Top"}},
            "variableRefs": [],
            "body": [],
        }},
    }


def test_convert_top_field_is_string_when_singleton():
    # One top -> string; multiple -> list (matches hgdb-circt emitter).
    out = hgdb_json_convert(_doc_skeleton())
    assert isinstance(out["top"], str)
    assert out["top"] == "Top"


def test_convert_top_field_is_list_when_multiple():
    doc = _doc_skeleton()
    doc["top"] = ["Top", "Aux"]
    doc["scopes"]["Aux"] = {
        "name": "Aux", "kind": "module",
        "representations": {"chisel": {"name": "Aux"},
                            "verilog": {"name": "Aux"}},
        "variableRefs": [],
        "body": [],
    }
    out = hgdb_json_convert(doc)
    assert isinstance(out["top"], list)
    assert out["top"] == ["Top", "Aux"]


def test_convert_drops_non_dict_instance_entries():
    doc = _doc_skeleton()
    doc["scopes"]["Top"]["instantiates"] = ["bad-string-entry"]
    out = hgdb_json_convert(doc)
    top_module = next(m for m in out["table"] if m["name"] == "Top")
    assert "instances" not in top_module


def test_convert_skips_inline_vardef_when_no_names_resolve():
    doc = _doc_skeleton()
    doc["variables"][""] = {
        "typeRef": "u8", "bindKind": "port", "direction": "input",
        "ownerScopeRef": "Top",
        "representations": {},
    }
    doc["scopes"]["Top"]["variableRefs"] = [""]
    out = hgdb_json_convert(doc)
    top_module = next(m for m in out["table"] if m["name"] == "Top")
    assert top_module["variables"] == []


def test_convert_skips_variableref_pointing_at_nonexistent_var():
    doc = _doc_skeleton()
    doc["variables"]["v_real"] = {
        "typeRef": "u8", "bindKind": "wire",
        "ownerScopeRef": "Top",
        "representations": {
            "chisel": {"name": "real"},
            "verilog": {"name": "real", "value": {"sigName": "real"}},
        },
    }
    doc["scopes"]["Top"]["variableRefs"] = ["v_real", "v_ghost"]
    doc["scopes"]["Top"]["body"] = [
        {"kind": "decl", "varRef": "v_real",
         "locations": {"chisel": {"file": 0, "beginLine": 5}}},
    ]
    out = hgdb_json_convert(doc)
    top_module = next(m for m in out["table"] if m["name"] == "Top")
    real_present = any(v == "0" or
                       (isinstance(v, dict) and v.get("name") == "real")
                       for v in top_module["variables"])
    assert real_present


def test_convert_drops_pool_var_with_unresolvable_sig():
    doc = _doc_skeleton()
    doc["variables"]["v_unresolved"] = {
        "typeRef": "u8", "bindKind": "wire",
        "ownerScopeRef": "Top",
    }
    doc["scopes"]["Top"]["variableRefs"] = ["v_unresolved"]
    doc["scopes"]["Top"]["body"] = [
        {"kind": "decl", "varRef": "v_unresolved",
         "locations": {"chisel": {"file": 0, "beginLine": 5}}},
    ]
    out = hgdb_json_convert(doc)
    assert out["variables"] == []
    top_module = next(m for m in out["table"] if m["name"] == "Top")
    assert top_module["scope"] == []


def test_convert_module_entry_with_inline_port_and_pool_var():
    """Body-referenced port appears inline in module.variables[] AND
    in the global pool as a ref-string."""
    doc = _doc_skeleton()
    doc["variables"]["v_in"] = {
        "typeRef": "u8", "bindKind": "port", "direction": "input",
        "ownerScopeRef": "Top",
        "representations": {
            "chisel": {"name": "in"},
            "verilog": {"name": "in", "value": {"sigName": "in"}},
        },
    }
    doc["scopes"]["Top"]["variableRefs"] = ["v_in"]
    doc["scopes"]["Top"]["body"] = [
        {"kind": "connect", "varRef": "v_in",
         "valueRef": {"constant": 0},
         "locations": {"chisel": {"file": 0, "beginLine": 5}}},
    ]
    out = hgdb_json_convert(doc)
    assert len(out["variables"]) == 1
    assert out["variables"][0]["id"] == "0"
    top_module = next(m for m in out["table"] if m["name"] == "Top")
    has_inline = any(
        isinstance(v, dict) and v.get("name") == "in"
        for v in top_module["variables"])
    has_ref = any(v == "0" for v in top_module["variables"])
    # Ports come through inline only; non-port body-refs would also
    # appear as ref-strings.
    assert has_inline
    assert not has_ref
