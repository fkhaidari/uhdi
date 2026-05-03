"""Unit tests for hgdb (SQLite) converter internals."""
from __future__ import annotations

import sqlite3
from typing import Any

import pytest
from uhdi_to_hgdb import HGDBConversionError
from uhdi_to_hgdb import convert as hgdb_convert
from uhdi_to_hgdb.convert import (
    HGDBBackend,
    _Ctx,
    _emit_unresolved_warnings,
    _filename_for,
    _render_expression,
    _render_operand,
    _serialize_enable,
    _walk_body,
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


# ---- _filename_for -------------------------------------------------------


def test_filename_for_empty_loc_returns_empty():
    assert _filename_for(None, _ctx()) == ""
    assert _filename_for({}, _ctx()) == ""


def test_filename_for_returns_authoring_repr_path_verbatim():
    # Path verbatim (no basename reduction) to match hgdb-firrtl.
    ctx = _ctx(representations={
        "chisel": {"files": ["src/main/scala/Counter.scala"]},
        "verilog": {"files": ["build/Counter.sv"]},
    })
    assert _filename_for({"file": 0}, ctx) == "src/main/scala/Counter.scala"


def test_filename_for_out_of_range_file_index_bumps_audit_counter():
    ctx = _ctx(representations={
        "chisel": {"files": ["a.scala"]},
        "verilog": {"files": ["a.sv"]},
    })
    assert _filename_for({"file": 99}, ctx) == ""
    assert ctx.unresolved_locs == 1


# ---- _emit_unresolved_warnings -------------------------------------------


def test_emit_warnings_quiet_when_no_unresolved(capsys):
    _emit_unresolved_warnings(_ctx())
    captured = capsys.readouterr()
    assert captured.err == ""


def test_emit_warnings_summarises_unresolved_sig_refs(capsys):
    ctx = _ctx()
    ctx.unresolved_sig_refs.update({"a", "b", "c"})
    _emit_unresolved_warnings(ctx)
    err = capsys.readouterr().err
    assert "3 stable_id(s)" in err
    assert "a" in err
    assert "b" in err


def test_emit_warnings_truncates_after_five_with_more_count(capsys):
    ctx = _ctx()
    ctx.unresolved_sig_refs.update({f"sig_{i}" for i in range(10)})
    _emit_unresolved_warnings(ctx)
    err = capsys.readouterr().err
    assert "10 stable_id" in err
    assert "+5 more" in err


def test_emit_warnings_reports_unresolved_locs(capsys):
    ctx = _ctx()
    ctx.unresolved_locs = 4
    _emit_unresolved_warnings(ctx)
    err = capsys.readouterr().err
    assert "4 statement location(s)" in err


# ---- _render_operand -----------------------------------------------------


def test_render_operand_non_dict_returns_empty():
    assert _render_operand("oops", 0, _ctx()) == ""


def test_render_operand_sig_name_passthrough():
    assert _render_operand({"sigName": "wire"}, 0, _ctx()) == "wire"


def test_render_operand_constant_with_width_uses_sv_literal():
    assert _render_operand({"constant": 5, "width": 4}, 0, _ctx()) == "4'd5"


def test_render_operand_constant_no_width_emits_decimal_int():
    assert _render_operand({"constant": 42}, 0, _ctx()) == "42"


@pytest.mark.parametrize("const,width,expected", [
    (-3, 4, "4'd13"),
    (-1, 8, "8'd255"),
    (16, 4, "4'd0"),
    (-128, 8, "8'd128"),
])
def test_render_operand_constant_masks_to_width(const, width, expected):
    assert (_render_operand({"constant": const, "width": width}, 0, _ctx())
            == expected)


def test_render_operand_negative_constant_no_width():
    assert _render_operand({"constant": -3}, 0, _ctx()) == "-3"


def test_render_operand_bitvector_emits_sv_b_literal():
    assert _render_operand({"bitVector": "1010"}, 0, _ctx()) == "4'b1010"


def test_render_operand_var_ref_resolves_to_sig_name():
    ctx = _ctx(variables={"v": {
        "representations": {"verilog": {"name": "wire_q"}},
    }})
    assert _render_operand({"varRef": "v"}, 0, ctx) == "wire_q"


def test_render_operand_var_ref_unresolved_falls_back_to_token():
    ctx = _ctx()
    out = _render_operand({"varRef": "ghost"}, 0, ctx)
    assert out == "ghost"
    assert "ghost" in ctx.unresolved_sig_refs


def test_render_operand_expr_ref_resolves_via_pool():
    ctx = _ctx(expressions={
        "e": {"opcode": "+",
              "operands": [{"sigName": "a"}, {"sigName": "b"}]},
    })
    assert _render_operand({"exprRef": "e"}, 0, ctx) == "a + b"


def test_render_operand_unknown_expr_ref_returns_empty():
    assert _render_operand({"exprRef": "ghost"}, 0, _ctx()) == ""


def test_render_operand_breaks_on_cyclic_expr_ref():
    ctx = _ctx(expressions={
        "a": {"opcode": "+", "operands": [{"exprRef": "b"}]},
        "b": {"opcode": "-", "operands": [{"exprRef": "a"}]},
    })
    with pytest.raises(HGDBConversionError, match="cycle"):
        _render_operand({"exprRef": "a"}, 0, ctx)


def test_render_operand_inline_opcode_dispatches_to_render_expression():
    out = _render_operand({"opcode": "+",
                           "operands": [{"sigName": "a"}, {"sigName": "b"}]},
                          0, _ctx())
    assert out == "a + b"


def test_render_operand_unknown_dict_shape_returns_empty():
    assert _render_operand({"foo": 1}, 0, _ctx()) == ""


# ---- _render_expression --------------------------------------------------


def test_render_expression_non_dict_returns_empty():
    assert _render_expression("oops", 0, _ctx()) == ""


def test_render_expression_ternary_renders_question_colon():
    expr = {"opcode": "?:", "operands": [
        {"sigName": "c"}, {"sigName": "t"}, {"sigName": "f"}]}
    assert _render_expression(expr, 0, _ctx()) == "(c ? t : f)"


def test_render_expression_unary_not_no_paren():
    # `!sig` not `!(sig)` -- match hgdb-firrtl shape.
    expr = {"opcode": "!", "operands": [{"sigName": "reset"}]}
    assert _render_expression(expr, 0, _ctx()) == "!reset"


def test_render_expression_unary_neg_renders_minus():
    expr = {"opcode": "neg", "operands": [{"sigName": "x"}]}
    assert _render_expression(expr, 0, _ctx()) == "-x"


def test_render_expression_unary_bitwise_not():
    expr = {"opcode": "~", "operands": [{"sigName": "x"}]}
    assert _render_expression(expr, 0, _ctx()) == "~x"


@pytest.mark.parametrize("opcode,sym", [("andr", "&"), ("orr", "|"),
                                         ("xorr", "^")])
def test_render_expression_reduction_operators(opcode, sym):
    expr = {"opcode": opcode, "operands": [{"sigName": "x"}]}
    assert _render_expression(expr, 0, _ctx()) == f"({sym}x)"


def test_render_expression_concat_braces():
    expr = {"opcode": "{}", "operands": [
        {"sigName": "a"}, {"sigName": "b"}, {"sigName": "c"}]}
    assert _render_expression(expr, 0, _ctx()) == "({a, b, c})"


def test_render_expression_replication_braces():
    expr = {"opcode": "R{}", "operands": [
        {"sigName": "x"}, {"constant": 4}]}
    assert _render_expression(expr, 0, _ctx()) == "({4{x}})"


def test_render_expression_binary_uses_opcode_as_infix():
    expr = {"opcode": "&&", "operands": [
        {"sigName": "a"}, {"sigName": "b"}]}
    assert _render_expression(expr, 0, _ctx()) == "a && b"


def test_render_expression_unknown_opcode_with_three_operands_uses_call_form():
    # Non-binary arity falls to `op(args)` form (already self-bracketed).
    expr = {"opcode": "future_op", "operands": [
        {"sigName": "a"}, {"sigName": "b"}, {"sigName": "c"}]}
    assert _render_expression(expr, 0, _ctx()) == "future_op(a, b, c)"


def test_render_expression_parenthesises_low_prec_under_high_prec():
    # `||` (prec 1) under `&&` (prec 2) -> `(a || b) && c`.
    expr = {"opcode": "&&", "operands": [
        {"opcode": "||", "operands": [
            {"sigName": "a"}, {"sigName": "b"}]},
        {"sigName": "c"},
    ]}
    out = _render_expression(expr, 0, _ctx())
    assert "(a || b)" in out


# ---- _serialize_enable ---------------------------------------------------


def test_serialize_enable_empty_returns_empty():
    assert _serialize_enable("", _ctx()) == ""
    assert _serialize_enable(None, _ctx()) == ""


def test_serialize_enable_drops_empty_split_tokens():
    # `a&&b` splits to ["a","","b"]; empty middle dropped silently.
    ctx = _ctx(variables={
        "a": {"representations": {"verilog": {"name": "sigA"}}},
        "b": {"representations": {"verilog": {"name": "sigB"}}},
    })
    assert _serialize_enable("a&&b", ctx) == "sigA && sigB"


def test_serialize_enable_drops_complex_sentinel():
    assert _serialize_enable("<complex>", _ctx()) == ""


def test_serialize_enable_drops_bare_negation():
    assert _serialize_enable("!", _ctx()) == ""


def test_serialize_enable_resolves_var_token_to_sig_name():
    ctx = _ctx(variables={"v": {
        "representations": {"verilog": {"name": "wire_en"}},
    }})
    assert _serialize_enable("v", ctx) == "wire_en"


def test_serialize_enable_negated_var_token():
    ctx = _ctx(variables={"v": {
        "representations": {"verilog": {"name": "reset"}},
    }})
    assert _serialize_enable("!v", ctx) == "!reset"


def test_serialize_enable_expression_token_renders_op_tree():
    ctx = _ctx(expressions={
        "e": {"opcode": "&&",
              "operands": [{"sigName": "a"}, {"sigName": "b"}]},
    })
    assert _serialize_enable("e", ctx) == "a && b"


def test_serialize_enable_negated_expression_wraps_in_parens():
    # `!` over op-tree needs explicit parens to bind to the whole subtree.
    ctx = _ctx(expressions={
        "e": {"opcode": "&&",
              "operands": [{"sigName": "a"}, {"sigName": "b"}]},
    })
    assert _serialize_enable("!e", ctx) == "!(a && b)"


def test_serialize_enable_joins_multiple_tokens_with_andand():
    ctx = _ctx(variables={
        "v1": {"representations": {"verilog": {"name": "en1"}}},
        "v2": {"representations": {"verilog": {"name": "en2"}}},
    })
    assert _serialize_enable("v1&v2", ctx) == "en1 && en2"


# ---- _walk_body edge cases ----------------------------------------------


def test_walk_body_skips_statement_without_filename(tmp_path):
    ctx = _ctx(variables={
        "v": {"representations": {"verilog": {"name": "wire"}},
              "ownerScopeRef": "Top"},
    })
    bps = []
    scope_bps = []
    asgs = []
    body = [{"kind": "connect", "varRef": "v",
             "valueRef": {"sigName": "wire"}}]
    _walk_body(body, ctx, instance_id=1, out_bps=bps,
               out_scope_bps=scope_bps, out_assignments=asgs)
    assert bps == []


def test_walk_body_skips_self_connect():
    ctx = _ctx()
    bps = []
    scope_bps = []
    asgs = []
    body = [{"kind": "connect", "varRef": "v",
             "valueRef": {"varRef": "v"},
             "locations": {"chisel": {"file": 0, "beginLine": 5}}}]
    _walk_body(body, ctx, instance_id=1, out_bps=bps,
               out_scope_bps=scope_bps, out_assignments=asgs)
    assert bps == []


def test_walk_body_negated_block_token_propagates():
    ctx = _ctx(variables={
        "guard": {"representations": {"verilog": {"name": "rst"}}},
        "v": {"representations": {"verilog": {"name": "wire"}}},
    })
    bps = []
    scope_bps = []
    asgs = []
    body = [{
        "kind": "block",
        "guardRef": "guard",
        "negated": True,
        "body": [{
            "kind": "decl", "varRef": "v",
            "locations": {"chisel": {"file": 0, "beginLine": 10}},
        }],
    }]
    _walk_body(body, ctx, instance_id=1, out_bps=bps,
               out_scope_bps=scope_bps, out_assignments=asgs)
    assert any(row[3] == 10 for row in bps)  # decl line


def test_walk_body_connect_with_empty_varref_skips_assignment():
    ctx = _ctx()
    bps = []
    scope_bps = []
    asgs = []
    # valueRef has len>1 so _is_self_connect doesn't fire.
    body = [{"kind": "connect",
             "valueRef": {"constant": 1, "width": 8},
             "locations": {"chisel": {"file": 0, "beginLine": 5}}}]
    _walk_body(body, ctx, instance_id=1, out_bps=bps,
               out_scope_bps=scope_bps, out_assignments=asgs)
    assert len(bps) == 1
    assert asgs == []


def test_walk_body_emits_assignment_row_for_connect():
    ctx = _ctx(variables={
        "v": {"representations": {
                  "chisel": {"name": "x"},
                  "verilog": {"name": "x", "value": {"sigName": "x"}}}},
    })
    bps = []
    scope_bps = []
    asgs = []
    body = [{"kind": "connect", "varRef": "v",
             "valueRef": {"constant": 1},
             "locations": {"chisel": {"file": 0, "beginLine": 5}}}]
    _walk_body(body, ctx, instance_id=1, out_bps=bps,
               out_scope_bps=scope_bps, out_assignments=asgs)
    assert len(asgs) == 1
    name, value, bp_id, cond, scope_id = asgs[0]
    assert name == value == "x"
    assert bp_id == bps[0][0]


# ---- end-to-end edge cases ---------------------------------------------


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


def test_convert_cleans_up_stale_tmp_file_before_writing(tmp_path):
    out = tmp_path / "design.db"
    stale = tmp_path / "design.db.tmp"
    stale.write_bytes(b"not a sqlite file")
    hgdb_convert(_doc_skeleton(), out)
    assert not stale.exists()
    assert out.is_file()


def test_convert_skips_decl_breakpoint_for_port():
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
    doc["scopes"]["Top"]["body"] = [{
        "kind": "decl", "varRef": "v_in",
        "locations": {"chisel": {"file": 0, "beginLine": 4}},
    }]
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "x.db"
        hgdb_convert(doc, db_path)
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT COUNT(*) FROM breakpoint").fetchone()[0]
        conn.close()
    assert rows == 0


def test_hgdb_backend_requires_output():
    backend = HGDBBackend()
    with pytest.raises(HGDBConversionError, match="requires an output"):
        backend.convert(_doc_skeleton(), output=None)


def test_convert_handles_instantiation_to_unknown_scope(tmp_path):
    doc = _doc_skeleton()
    doc["scopes"]["Top"]["instantiates"] = [
        {"as": "ghost", "scopeRef": "GhostScope"},
    ]
    out = tmp_path / "x.db"
    hgdb_convert(doc, out)
    conn = sqlite3.connect(str(out))
    rows = conn.execute("SELECT name FROM instance").fetchall()
    conn.close()
    names = [r[0] for r in rows]
    assert names == ["Top"]


def test_convert_dedups_variableref_with_same_chisel_name(tmp_path):
    """Two variables sharing chisel name: first wins. Matches native
    hgdb-firrtl's pre-DCE shape (it sees one `q`)."""
    doc = _doc_skeleton()
    doc["variables"]["v_q1"] = {
        "typeRef": "u8", "bindKind": "wire",
        "ownerScopeRef": "Top",
        "representations": {
            "chisel": {"name": "q"},
            "verilog": {"name": "q", "value": {"sigName": "q"}},
        },
    }
    doc["variables"]["v_q2"] = {  # same chisel name -> dedup
        "typeRef": "u8", "bindKind": "wire",
        "ownerScopeRef": "Top",
        "representations": {
            "chisel": {"name": "q"},
            "verilog": {"name": "q", "value": {"sigName": "q_dup"}},
        },
    }
    doc["scopes"]["Top"]["variableRefs"] = ["v_q1", "v_q2"]
    out = tmp_path / "x.db"
    hgdb_convert(doc, out)
    conn = sqlite3.connect(str(out))
    names = [r[0] for r in conn.execute(
        "SELECT name FROM generator_variable").fetchall()]
    conn.close()
    assert names.count("q") == 1


def test_convert_skips_pool_entry_for_unknown_var_id(tmp_path):
    doc = _doc_skeleton()
    doc["scopes"]["Top"]["variableRefs"] = ["v_real", "v_ghost"]
    doc["variables"]["v_real"] = {
        "typeRef": "u8", "bindKind": "wire",
        "ownerScopeRef": "Top",
        "representations": {
            "chisel": {"name": "real"},
            "verilog": {"name": "real", "value": {"sigName": "real"}},
        },
    }
    out = tmp_path / "x.db"
    hgdb_convert(doc, out)  # no raise
    conn = sqlite3.connect(str(out))
    names = [r[0] for r in conn.execute(
        "SELECT name FROM generator_variable").fetchall()]
    conn.close()
    assert names == ["real"]


def test_convert_emits_row_when_src_name_resolves_empty(tmp_path):
    # Empty src bypasses dedup cache but still produces a row.
    doc = _doc_skeleton()
    doc["variables"][""] = {  # empty stable_id (technically valid JSON)
        "typeRef": "u8", "bindKind": "wire",
        "ownerScopeRef": "Top",
        "representations": {
            "verilog": {"name": "anon", "value": {"sigName": "anon"}},
        },
    }
    doc["scopes"]["Top"]["variableRefs"] = [""]
    out = tmp_path / "x.db"
    hgdb_convert(doc, out)
    conn = sqlite3.connect(str(out))
    rows = conn.execute(
        "SELECT name FROM generator_variable").fetchall()
    conn.close()
    assert ("",) in rows


def test_convert_skips_dce_output_port_with_no_chisel_name(tmp_path):
    # No verilog repr + no chisel name -> nothing to use; row dropped.
    doc = _doc_skeleton()
    doc["variables"]["v_x"] = {
        "typeRef": "u8", "bindKind": "port", "direction": "output",
        "ownerScopeRef": "Top",
    }
    doc["scopes"]["Top"]["variableRefs"] = ["v_x"]
    out = tmp_path / "x.db"
    hgdb_convert(doc, out)
    conn = sqlite3.connect(str(out))
    rows = conn.execute(
        "SELECT name FROM generator_variable").fetchall()
    conn.close()
    assert rows == []


def test_convert_emits_output_port_via_chisel_fallback(tmp_path):
    # DCE'd output (no verilog repr): fall back to chisel name as sig.
    doc = _doc_skeleton()
    doc["variables"]["v_q"] = {
        "typeRef": "u8", "bindKind": "port", "direction": "output",
        "ownerScopeRef": "Top",
        "representations": {
            "chisel": {"name": "q"},
        },
    }
    doc["scopes"]["Top"]["variableRefs"] = ["v_q"]
    out = tmp_path / "x.db"
    hgdb_convert(doc, out)
    conn = sqlite3.connect(str(out))
    names = [r[0] for r in conn.execute(
        "SELECT name FROM generator_variable").fetchall()]
    conn.close()
    assert "q" in names


def test_convert_handles_top_with_none_value(tmp_path):
    # `top` referencing a None-valued scope: skip silently, empty DB.
    doc = _doc_skeleton()
    doc["scopes"]["Top"] = None
    out = tmp_path / "x.db"
    hgdb_convert(doc, out)
    conn = sqlite3.connect(str(out))
    rows = conn.execute("SELECT COUNT(*) FROM instance").fetchone()[0]
    conn.close()
    assert rows == 0


def test_convert_emits_unresolved_warnings_at_end(capsys, tmp_path):
    # Unresolved enableRef tokens warn AFTER DB write; file still valid.
    doc = _doc_skeleton()
    doc["variables"]["v_w"] = {
        "typeRef": "u8", "bindKind": "wire",
        "ownerScopeRef": "Top",
        "representations": {
            "chisel": {"name": "w"},
            "verilog": {"name": "w", "value": {"sigName": "w"}},
        },
    }
    doc["scopes"]["Top"]["variableRefs"] = ["v_w"]
    doc["scopes"]["Top"]["body"] = [{
        "kind": "connect", "varRef": "v_w",
        "valueRef": {"constant": 1},
        "locations": {"chisel": {"file": 0, "beginLine": 5}},
        "bp": {"enableRef": "ghost_token"},  # unresolved
    }]
    hgdb_convert(doc, tmp_path / "out.db")
    err = capsys.readouterr().err
    assert "ghost_token" in err
