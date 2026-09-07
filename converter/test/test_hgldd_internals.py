"""Unit tests for HGLDD converter internals."""
from __future__ import annotations

import pytest
from uhdi_to_hgldd import HGLDDConversionError
from uhdi_to_hgldd import convert as hgldd_convert
from uhdi_to_hgldd.convert import (
    _collect_aggregated_leaves,
    _Context,
    _expression_to_hgldd,
    _FileInfo,
    _first_vector_element_sig,
    _resolve_hdl_file_path,
    _source_lang_type,
    _topo_sorted_struct_ids,
    _type_description,
)

# ---- _FileInfo ----------------------------------------------------------


def test_fileinfo_dedup_returns_existing_index():
    fi = _FileInfo()
    a = fi.add_source("X.scala")
    b = fi.add_source("X.scala")
    assert a == b


def test_fileinfo_source_after_hdl_inserts_at_hdl_start_and_shifts():
    fi = _FileInfo()
    h_idx = fi.add_hdl("a.sv")
    assert fi.hdl_start == 0
    assert h_idx == 0

    src_idx = fi.add_source("Top.scala")
    assert src_idx == 0
    assert fi.ordered == ["Top.scala", "a.sv"]
    assert fi.index["a.sv"] == 1
    assert fi.hdl_start == 1


def test_fileinfo_hdl_after_source_appends():
    fi = _FileInfo()
    fi.add_source("Top.scala")
    h = fi.add_hdl("Top.sv")
    assert h == 1
    assert fi.hdl_start == 1


def test_fileinfo_source_insert_skips_shifting_earlier_sources():
    fi = _FileInfo()
    fi.add_source("a.scala")
    fi.add_hdl("b.sv")
    new = fi.add_source("c.scala")
    assert new == 1
    assert fi.index["a.scala"] == 0
    assert fi.index["c.scala"] == 1
    assert fi.index["b.sv"] == 2


def test_fileinfo_add_hdl_twice_keeps_hdl_start_unchanged():
    fi = _FileInfo()
    fi.add_hdl("a.sv")
    start_after_first = fi.hdl_start
    fi.add_hdl("b.sv")
    assert fi.hdl_start == start_after_first


def test_fileinfo_dedup_across_segments():
    fi = _FileInfo()
    src = fi.add_source("Top.scala")
    hdl = fi.add_hdl("Top.scala")
    assert src == hdl
    assert fi.ordered == ["Top.scala"]


# ---- _type_description --------------------------------------------------


def _ctx_with_types(types):
    return _Context(uhdi={
        "format": {"name": "uhdi"},
        "types": types,
    })


def test_type_description_unknown_type_falls_back_to_logic():
    ctx = _ctx_with_types({})
    assert _type_description("missing", ctx) == {"type_name": "logic"}


def test_type_description_uint_width_one_drops_packed_range():
    ctx = _ctx_with_types({"bool": {"kind": "uint", "width": 1}})
    assert _type_description("bool", ctx) == {"type_name": "logic"}


def test_type_description_uint_wide_includes_packed_range():
    ctx = _ctx_with_types({"u8": {"kind": "uint", "width": 8}})
    assert _type_description("u8", ctx) == {
        "type_name": "logic", "packed_range": [7, 0]}


def test_type_description_struct_uses_struct_id_as_name():
    ctx = _ctx_with_types({"S": {"kind": "struct", "members": []}})
    assert _type_description("S", ctx) == {"type_name": "S"}


def test_type_description_vector_flattens_to_unpacked_range():
    ctx = _ctx_with_types({
        "u8": {"kind": "uint", "width": 8},
        "vec_u8_4": {"kind": "vector", "elementRef": "u8", "size": 4},
    })
    out = _type_description("vec_u8_4", ctx)
    assert out["type_name"] == "logic"
    assert out["packed_range"] == [7, 0]
    assert out["unpacked_range"] == [3, 0]


def test_type_description_vector_zero_size_omits_unpacked_range():
    # Don't emit a meaningless `[-1, 0]` range.
    ctx = _ctx_with_types({
        "u8": {"kind": "uint", "width": 8},
        "v": {"kind": "vector", "elementRef": "u8", "size": 0},
    })
    out = _type_description("v", ctx)
    assert "unpacked_range" not in out


def test_type_description_unknown_kind_falls_back_to_logic():
    """A kind the resolver doesn't recognise (e.g. a future "fixed"
    type) defaults to plain logic so the converter doesn't crash on
    forward-compat documents."""
    ctx = _ctx_with_types({"odd": {"kind": "fixed", "width": 16}})
    assert _type_description("odd", ctx) == {"type_name": "logic"}


def test_type_description_nested_vector_emits_multi_dim_unpacked_range():
    """Vec(M, Vec(N, T)): outer dim first, inner dim last in flat list.

    Mirrors native HGLDD convention (EmitHGLDD::emitDims pushes each
    dim as [hi, lo] reversed, so outermost lands first in the array)."""
    ctx = _ctx_with_types({
        "u1": {"kind": "uint", "width": 1},
        "VInner": {"kind": "vector", "elementRef": "u1", "size": 8},
        "VV": {"kind": "vector", "elementRef": "VInner", "size": 4},
    })
    out = _type_description("VV", ctx)
    assert out == {"type_name": "logic", "unpacked_range": [3, 0, 7, 0]}


def test_type_description_three_dim_vector():
    """Vec(2, Vec(3, Vec(4, T))): outer-to-inner order in the flat list."""
    ctx = _ctx_with_types({
        "u1": {"kind": "uint", "width": 1},
        "V4":  {"kind": "vector", "elementRef": "u1", "size": 4},
        "V3":  {"kind": "vector", "elementRef": "V4", "size": 3},
        "VVV": {"kind": "vector", "elementRef": "V3", "size": 2},
    })
    out = _type_description("VVV", ctx)
    assert out == {"type_name": "logic", "unpacked_range": [1, 0, 2, 0, 3, 0]}


# ---- _expression_to_hgldd ----------------------------------------------


def _expr_ctx(expressions=None, variables=None):
    return _Context(uhdi={
        "format": {"name": "uhdi"},
        "expressions": expressions or {},
        "variables": variables or {},
    })


def test_expression_to_hgldd_non_dict_returns_empty():
    """Defensive against hand-edited fixtures that put a bare string
    where an operand dict is expected."""
    assert _expression_to_hgldd("oops", _expr_ctx()) == {}


def test_expression_to_hgldd_constant_with_width_renders_bit_vector():
    out = _expression_to_hgldd({"constant": 5, "width": 4}, _expr_ctx())
    assert out == {"bit_vector": "0101"}


def test_expression_to_hgldd_constant_no_width_renders_integer():
    out = _expression_to_hgldd({"constant": 42}, _expr_ctx())
    assert out == {"integer_num": 42}


def test_expression_to_hgldd_bitvector_passthrough():
    assert _expression_to_hgldd({"bitVector": "1010"}, _expr_ctx()) == {
        "bit_vector": "1010"}


def test_expression_to_hgldd_var_ref_resolves_to_sig_name():
    ctx = _expr_ctx(variables={"v": {
        "representations": {"verilog": {"name": "wire_q"}},
    }})
    assert _expression_to_hgldd({"varRef": "v"}, ctx) == {"sig_name": "wire_q"}


def test_expression_to_hgldd_var_ref_unresolved_returns_empty():
    assert _expression_to_hgldd({"varRef": "nope"}, _expr_ctx()) == {}


def test_expression_to_hgldd_expr_ref_resolves_via_pool():
    ctx = _expr_ctx(expressions={
        "e": {"opcode": "+", "operands": [{"constant": 1}, {"constant": 2}]},
    })
    out = _expression_to_hgldd({"exprRef": "e"}, ctx)
    assert out["opcode"] == "+"


def test_expression_to_hgldd_unknown_expr_ref_returns_empty():
    assert _expression_to_hgldd({"exprRef": "ghost"}, _expr_ctx()) == {}


def test_expression_to_hgldd_breaks_on_cycle():
    ctx = _expr_ctx(expressions={
        "a": {"opcode": "+", "operands": [{"exprRef": "b"}]},
        "b": {"opcode": "-", "operands": [{"exprRef": "a"}]},
    })
    with pytest.raises(HGLDDConversionError, match="cycle"):
        _expression_to_hgldd({"exprRef": "a"}, ctx)


def test_collect_aggregated_leaves_terminates_on_cycle():
    ctx = _Context.from_uhdi({
        "format": {"name": "uhdi"},
        "representations": {
            "chisel": {"files": []},
            "verilog": {"files": []},
        },
        "variables": {
            "v": {"representations": {
                "verilog": {"value": {"exprRef": "a"}}}},
        },
        "expressions": {
            "a": {"opcode": "+", "operands": [{"exprRef": "b"}]},
            "b": {"opcode": "-", "operands": [{"exprRef": "a"}]},
        },
        "scopes": {},
        "top": [],
    })
    assert _collect_aggregated_leaves(ctx) == set()


def test_expression_to_hgldd_inline_opcode_renders_opnode():
    out = _expression_to_hgldd({"opcode": "&", "operands": [
        {"sigName": "a"}, {"sigName": "b"}]}, _expr_ctx())
    assert out["opcode"] == "&"


def test_expression_to_hgldd_unknown_dict_shape_returns_empty():
    assert _expression_to_hgldd({"unrelated": 1}, _expr_ctx()) == {}


# ---- _topo_sorted_struct_ids -------------------------------------------


def test_topo_sort_skips_non_struct_types():
    ctx = _ctx_with_types({
        "u8": {"kind": "uint", "width": 8},
        "v": {"kind": "vector", "elementRef": "u8", "size": 2},
    })
    assert _topo_sorted_struct_ids(ctx) == []


def test_topo_sort_visits_inner_before_outer():
    ctx = _ctx_with_types({
        "Inner": {"kind": "struct", "members": [
            {"name": "a", "typeRef": "u8"}]},
        "Outer": {"kind": "struct", "members": [
            {"name": "i", "typeRef": "Inner"}]},
        "u8": {"kind": "uint", "width": 8},
    })
    order = _topo_sorted_struct_ids(ctx)
    assert order.index("Inner") < order.index("Outer")


def test_topo_sort_handles_member_without_typeref():
    ctx = _ctx_with_types({
        "S": {"kind": "struct", "members": [{"name": "x"}]},
    })
    assert _topo_sorted_struct_ids(ctx) == ["S"]


def test_topo_sort_skips_unknown_type_ref():
    ctx = _ctx_with_types({
        "S": {"kind": "struct", "members": [
            {"name": "x", "typeRef": "ghost"}]},
    })
    order = _topo_sorted_struct_ids(ctx)
    assert order == ["S"]
    assert "ghost" not in order


def test_topo_sort_handles_vector_without_elementref():
    ctx = _ctx_with_types({
        "S": {"kind": "struct", "members": [
            {"name": "v", "typeRef": "vec_no_elem"}]},
        "vec_no_elem": {"kind": "vector", "size": 4},
    })
    order = _topo_sorted_struct_ids(ctx)
    assert "S" in order


def test_topo_sort_walks_through_vector_to_inner_struct():
    ctx = _ctx_with_types({
        "u8": {"kind": "uint", "width": 8},
        "Inner": {"kind": "struct", "members": [
            {"name": "a", "typeRef": "u8"}]},
        "VecInner": {"kind": "vector", "elementRef": "Inner", "size": 4},
        "Outer": {"kind": "struct", "members": [
            {"name": "v", "typeRef": "VecInner"}]},
    })
    order = _topo_sorted_struct_ids(ctx)
    assert "VecInner" not in order
    assert order.index("Inner") < order.index("Outer")


def test_topo_sort_detects_vector_only_cycle():
    """Without seeding from vector roots, V1 -> V2 -> V1 cycles past
    detection and crashes _type_description with RecursionError."""
    ctx = _ctx_with_types({
        "V1": {"kind": "vector", "elementRef": "V2", "size": 4},
        "V2": {"kind": "vector", "elementRef": "V1", "size": 4},
    })
    with pytest.raises(HGLDDConversionError, match="cycle"):
        _topo_sorted_struct_ids(ctx)


# ---- _first_vector_element_sig -----------------------------------------


def test_first_vector_element_sig_extracts_first_leaf():
    ctx = _expr_ctx(expressions={
        "e": {"opcode": "'{",
              "operands": [{"sigName": "buf_0"}, {"sigName": "buf_1"}]},
    })
    assert _first_vector_element_sig({"exprRef": "e"}, ctx) == "buf_0"


def test_first_vector_element_sig_non_dict_returns_empty():
    assert _first_vector_element_sig("not-a-dict", _expr_ctx()) == ""


def test_first_vector_element_sig_no_operands_returns_empty():
    ctx = _expr_ctx(expressions={"e": {"opcode": "'{", "operands": []}})
    assert _first_vector_element_sig({"exprRef": "e"}, ctx) == ""


def test_first_vector_element_sig_no_expr_match_returns_empty():
    assert _first_vector_element_sig({"exprRef": "ghost"}, _expr_ctx()) == ""


def test_first_vector_element_sig_first_operand_not_sigName():
    ctx = _expr_ctx(expressions={
        "e": {"opcode": "'{",
              "operands": [{"constant": 0}, {"constant": 1}]},
    })
    assert _first_vector_element_sig({"exprRef": "e"}, ctx) == ""


# ---- _loc_to_hgldd edge cases -------------------------------------------


def test_convert_drops_loc_with_out_of_range_file_index():
    doc = _doc_skeleton()
    doc["types"]["u1"] = {"kind": "uint", "width": 1}
    doc["variables"]["v_x"] = {
        "typeRef": "u1", "bindKind": "wire",
        "ownerScopeRef": "Top",
        "representations": {
            "chisel": {"name": "x",
                       "location": {"file": 99, "beginLine": 5}},
            "verilog": {"name": "x", "value": {"sigName": "x"}},
        },
    }
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "module",
        "representations": {"chisel": {"name": "Top"},
                            "verilog": {"name": "Top"}},
        "variableRefs": ["v_x"],
    }
    out = hgldd_convert(doc)
    top_obj = next(o for o in out["objects"] if o.get("obj_name") == "Top")
    pv = next(pv for pv in top_obj["port_vars"] if pv["var_name"] == "x")
    assert "hgl_loc" not in pv


def test_convert_skips_variable_ref_pointing_at_missing_var():
    doc = _doc_skeleton()
    doc["types"]["u1"] = {"kind": "uint", "width": 1}
    doc["variables"]["v_real"] = {
        "typeRef": "u1", "bindKind": "wire",
        "ownerScopeRef": "Top",
        "representations": {
            "chisel": {"name": "real"},
            "verilog": {"name": "real", "value": {"sigName": "real"}},
        },
    }
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "module",
        "representations": {"chisel": {"name": "Top"},
                            "verilog": {"name": "Top"}},
        "variableRefs": ["v_real", "v_ghost"],
    }
    out = hgldd_convert(doc)
    top_obj = next(o for o in out["objects"] if o.get("obj_name") == "Top")
    names = [pv["var_name"] for pv in top_obj["port_vars"]]
    assert "real" in names
    assert "v_ghost" not in names


def test_convert_instance_child_with_no_location():
    doc = _doc_skeleton()
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "module",
        "representations": {"chisel": {"name": "Top"},
                            "verilog": {"name": "Top"}},
        "variableRefs": [],
        "instantiates": [{"as": "child", "scopeRef": "Sub"}],
    }
    doc["scopes"]["Sub"] = {
        "name": "Sub", "kind": "module",
        "representations": {"chisel": {"name": "Sub"},
                            "verilog": {"name": "Sub"}},
        "variableRefs": [],
    }
    out = hgldd_convert(doc)
    top_obj = next(o for o in out["objects"] if o.get("obj_name") == "Top")
    child = next(c for c in top_obj["children"] if c.get("name") == "child")
    assert "hgl_loc" not in child
    assert "hdl_loc" not in child


def test_convert_loc_with_only_file_no_line_fields():
    doc = _doc_skeleton()
    doc["types"]["u1"] = {"kind": "uint", "width": 1}
    doc["variables"]["v_x"] = {
        "typeRef": "u1", "bindKind": "wire",
        "ownerScopeRef": "Top",
        "representations": {
            "chisel": {"name": "x", "location": {"file": 0}},
            "verilog": {"name": "x", "value": {"sigName": "x"}},
        },
    }
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "module",
        "representations": {"chisel": {"name": "Top"},
                            "verilog": {"name": "Top"}},
        "variableRefs": ["v_x"],
    }
    out = hgldd_convert(doc)
    top_obj = next(o for o in out["objects"] if o.get("obj_name") == "Top")
    pv = next(pv for pv in top_obj["port_vars"] if pv["var_name"] == "x")
    assert pv["hgl_loc"] == {"file": 1}


def test_convert_variable_without_verilog_value_emits_no_value():
    doc = _doc_skeleton()
    doc["types"]["u8"] = {"kind": "uint", "width": 8}
    doc["variables"]["v_p"] = {
        "typeRef": "u8", "bindKind": "wire",
        "ownerScopeRef": "Top",
        "representations": {
            "chisel": {"name": "p"},
            "verilog": {"name": "p"},
        },
    }
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "module",
        "representations": {"chisel": {"name": "Top"},
                            "verilog": {"name": "Top"}},
        "variableRefs": ["v_p"],
    }
    out = hgldd_convert(doc)
    top_obj = next(o for o in out["objects"] if o.get("obj_name") == "Top")
    pv = next(pv for pv in top_obj["port_vars"] if pv["var_name"] == "p")
    assert "value" not in pv


def test_convert_variable_with_exprref_to_unknown_expr_emits_no_value():
    """Verilog value uses `exprRef` but the expression isn't in the
    pool -> rendered={} -> `if rendered:` falsy -> no `value` field."""
    doc = _doc_skeleton()
    doc["types"]["u8"] = {"kind": "uint", "width": 8}
    doc["variables"]["v_w"] = {
        "typeRef": "u8", "bindKind": "wire",
        "ownerScopeRef": "Top",
        "representations": {
            "chisel": {"name": "w"},
            "verilog": {"name": "w", "value": {"exprRef": "missing"}},
        },
    }
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "module",
        "representations": {"chisel": {"name": "Top"},
                            "verilog": {"name": "Top"}},
        "variableRefs": ["v_w"],
    }
    out = hgldd_convert(doc)
    top_obj = next(o for o in out["objects"] if o.get("obj_name") == "Top")
    pv = next(pv for pv in top_obj["port_vars"] if pv["var_name"] == "w")
    assert "value" not in pv


def test_convert_variable_with_unknown_hdl_value_shape_emits_no_value():
    """A hdl_value dict with neither sigName / exprRef / constant /
    bitVector keys is malformed -- the converter emits the var_name
    + type description but no `value` field, rather than crashing."""
    doc = _doc_skeleton()
    doc["types"]["u8"] = {"kind": "uint", "width": 8}
    doc["variables"]["v_w"] = {
        "typeRef": "u8", "bindKind": "wire",
        "ownerScopeRef": "Top",
        "representations": {
            "chisel": {"name": "w"},
            "verilog": {"name": "w", "value": {"unrecognised": 42}},
        },
    }
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "module",
        "representations": {"chisel": {"name": "Top"},
                            "verilog": {"name": "Top"}},
        "variableRefs": ["v_w"],
    }
    out = hgldd_convert(doc)
    top_obj = next(o for o in out["objects"] if o.get("obj_name") == "Top")
    pv = next(pv for pv in top_obj["port_vars"] if pv["var_name"] == "w")
    assert "value" not in pv


def test_convert_renders_vector_when_no_first_leaf_falls_back_to_chisel_name():
    """A Vec-typed variable whose materialised value has no first
    sigName leaf keeps the chisel-side name (`arr` rather than the
    nonexistent first leaf)."""
    doc = _doc_skeleton()
    doc["types"] = {
        "u8": {"kind": "uint", "width": 8},
        "v": {"kind": "vector", "elementRef": "u8", "size": 2},
    }
    doc["expressions"] = {
        # Empty pack -- no first leaf to extract.
        "pack": {"opcode": "'{", "operands": []},
    }
    doc["variables"]["v_arr"] = {
        "typeRef": "v", "bindKind": "wire",
        "ownerScopeRef": "Top",
        "representations": {
            "chisel": {"name": "arr"},
            "verilog": {"name": "arr", "value": {"exprRef": "pack"}},
        },
    }
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "module",
        "representations": {"chisel": {"name": "Top"},
                            "verilog": {"name": "Top"}},
        "variableRefs": ["v_arr"],
    }
    out = hgldd_convert(doc)
    top_obj = next(o for o in out["objects"] if o.get("obj_name") == "Top")
    names = [pv["var_name"] for pv in top_obj["port_vars"]]
    assert "arr" in names


# ---- end-to-end: vector / extmodule / output-port-without-hdl --------


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
        "types": {},
        "expressions": {},
        "variables": {},
        "scopes": {},
    }


def test_convert_handles_extmodule_scope():
    """An extmodule scope adds `isExtModule: 1` to the HGLDD object so
    Tywaves treats it as a black-box reference rather than a
    walk-into-able container."""
    doc = _doc_skeleton()
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "extmodule",
        "representations": {
            "chisel": {"name": "Top"},
            "verilog": {"name": "Top"},
        },
        "variableRefs": [],
    }
    out = hgldd_convert(doc)
    obj = next(o for o in out["objects"] if o.get("kind") == "module")
    assert obj.get("isExtModule") == 1


def test_convert_keeps_output_port_without_verilog_repr():
    """An output port with no verilog repr block must still appear in
    port_vars -- Bundle outputs whose firtool-UHDI emit happens to omit
    the verilog block (e.g. io_out of an aggregate IO) used to vanish.
    Keep them but emit no value/hdl_loc; the schema is correct, and the
    consumer can tell sim-binding is absent."""
    doc = _doc_skeleton()
    doc["types"]["u8"] = {"kind": "uint", "width": 8}
    doc["variables"]["v_q"] = {
        "typeRef": "u8", "bindKind": "port", "direction": "output",
        "ownerScopeRef": "Top",
        "representations": {
            "chisel": {"name": "q"},
            # No verilog repr.
        },
    }
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "module",
        "representations": {"chisel": {"name": "Top"},
                            "verilog": {"name": "Top"}},
        "variableRefs": ["v_q"],
    }
    out = hgldd_convert(doc)
    obj = next(o for o in out["objects"] if o.get("obj_name") == "Top")
    pv = next(p for p in obj["port_vars"] if p["var_name"] == "q")
    assert "value" not in pv
    assert "hdl_loc" not in pv


def test_convert_renames_vector_var_after_first_leaf():
    """For a Vec-typed variable rendered as `'{buf_0, buf_1}`, the
    HGLDD `var_name` becomes `buf_0` so Tywaves's path-lookup hits
    the actual VCD signal."""
    doc = _doc_skeleton()
    doc["types"] = {
        "u8": {"kind": "uint", "width": 8},
        "v": {"kind": "vector", "elementRef": "u8", "size": 2},
    }
    doc["expressions"] = {
        "pack": {"opcode": "'{", "operands": [
            {"sigName": "buf_0"}, {"sigName": "buf_1"}]},
    }
    doc["variables"]["v_arr"] = {
        "typeRef": "v", "bindKind": "wire",
        "ownerScopeRef": "Top",
        "representations": {
            "chisel": {"name": "arr"},
            "verilog": {"name": "arr", "value": {"exprRef": "pack"}},
        },
    }
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "module",
        "representations": {"chisel": {"name": "Top"},
                            "verilog": {"name": "Top"}},
        "variableRefs": ["v_arr"],
    }
    out = hgldd_convert(doc)
    obj = next(o for o in out["objects"] if o.get("obj_name") == "Top")
    names = [pv["var_name"] for pv in obj["port_vars"]]
    assert "buf_0" in names


def test_convert_renders_constant_value_with_known_width():
    """A constant-bound variable whose typeRef has a width emits a
    width-padded bit_vector value (matches HGLDD's literal shape)."""
    doc = _doc_skeleton()
    doc["types"]["u4"] = {"kind": "uint", "width": 4}
    doc["variables"]["v_lit"] = {
        "typeRef": "u4", "bindKind": "node",
        "ownerScopeRef": "Top",
        "representations": {
            "chisel": {"name": "lit"},
            "verilog": {"name": "lit", "value": {"constant": 5}},
        },
    }
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "module",
        "representations": {"chisel": {"name": "Top"},
                            "verilog": {"name": "Top"}},
        "variableRefs": ["v_lit"],
    }
    out = hgldd_convert(doc)
    obj = next(o for o in out["objects"] if o.get("obj_name") == "Top")
    pv = next(pv for pv in obj["port_vars"] if pv["var_name"] == "lit")
    assert pv["value"] == {"bit_vector": "0101"}


def test_convert_renders_constant_value_without_width_as_integer():
    """When the typeRef has no width, the constant value falls back
    to integer_num -- matches native HGLDD's open-width literal form."""
    doc = _doc_skeleton()
    # No type entry: width unknown -> integer_num path.
    doc["variables"]["v_lit"] = {
        "typeRef": "missing", "bindKind": "node",
        "ownerScopeRef": "Top",
        "representations": {
            "chisel": {"name": "lit"},
            "verilog": {"name": "lit", "value": {"constant": 7}},
        },
    }
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "module",
        "representations": {"chisel": {"name": "Top"},
                            "verilog": {"name": "Top"}},
        "variableRefs": ["v_lit"],
    }
    out = hgldd_convert(doc)
    obj = next(o for o in out["objects"] if o.get("obj_name") == "Top")
    pv = next(pv for pv in obj["port_vars"] if pv["var_name"] == "lit")
    assert pv["value"] == {"integer_num": 7}


def test_convert_passes_through_bitvector_value():
    """A wide constant (>64-bit) ships as a pre-formatted bitVector;
    HGLDD wraps it verbatim without re-deriving the binary string."""
    doc = _doc_skeleton()
    doc["variables"]["v_wide"] = {
        "typeRef": "missing", "bindKind": "node",
        "ownerScopeRef": "Top",
        "representations": {
            "chisel": {"name": "wide"},
            "verilog": {"name": "wide", "value": {"bitVector": "10101"}},
        },
    }
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "module",
        "representations": {"chisel": {"name": "Top"},
                            "verilog": {"name": "Top"}},
        "variableRefs": ["v_wide"],
    }
    out = hgldd_convert(doc)
    obj = next(o for o in out["objects"] if o.get("obj_name") == "Top")
    pv = next(pv for pv in obj["port_vars"] if pv["var_name"] == "wide")
    assert pv["value"] == {"bit_vector": "10101"}


# ---- inline scopes -----------------------------------------------------


def test_convert_emits_inline_child_under_container():
    """Inline scopes with `containerScopeRef = <module>` show up as
    children of that module's HGLDD object (kind != module, not a
    top-level entry)."""
    doc = _doc_skeleton()
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "module",
        "representations": {"chisel": {"name": "Top"},
                            "verilog": {"name": "Top"}},
        "variableRefs": [],
    }
    doc["scopes"]["when_block"] = {
        "name": "when_block", "kind": "inline",
        "containerScopeRef": "Top",
        "representations": {"chisel": {"name": "when_block"}},
        "variableRefs": [],
    }
    out = hgldd_convert(doc)
    top_obj = next(o for o in out["objects"] if o.get("obj_name") == "Top")
    assert any(c.get("name") == "when_block" for c in top_obj["children"])


def test_convert_uniquifies_inline_port_iterates_past_first_collision():
    """When the parent already has BOTH `x` and `x_0`, the inline
    scope's `x` lands at `x_1` -- the uniquify loop iterates once past
    the first candidate before finding a free slot.  Pin that loop
    iteration."""
    doc = _doc_skeleton()
    doc["types"]["u8"] = {"kind": "uint", "width": 8}
    # Parent has two vars whose names are `x` and `x_0`.
    for sid, name in [("v_top_x", "x"), ("v_top_x0", "x_0")]:
        doc["variables"][sid] = {
            "typeRef": "u8", "bindKind": "port", "direction": "input",
            "ownerScopeRef": "Top",
            "representations": {
                "chisel": {"name": name},
                "verilog": {"name": name, "value": {"sigName": name}},
            },
        }
    doc["variables"]["v_inline_x"] = {
        "typeRef": "u8", "bindKind": "wire",
        "ownerScopeRef": "inline",
        "representations": {
            "chisel": {"name": "x"},
            "verilog": {"name": "x", "value": {"sigName": "inline_x"}},
        },
    }
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "module",
        "representations": {"chisel": {"name": "Top"},
                            "verilog": {"name": "Top"}},
        "variableRefs": ["v_top_x", "v_top_x0"],
    }
    doc["scopes"]["inline"] = {
        "name": "inline", "kind": "inline",
        "containerScopeRef": "Top",
        "representations": {"chisel": {"name": "inline"}},
        "variableRefs": ["v_inline_x"],
    }
    out = hgldd_convert(doc)
    top_obj = next(o for o in out["objects"] if o.get("obj_name") == "Top")
    inline_child = next(c for c in top_obj["children"]
                        if c.get("name") == "inline")
    inline_names = [pv["var_name"] for pv in inline_child["port_vars"]]
    # Iterated past `x_0` to land at `x_1`.
    assert "x_1" in inline_names


def test_convert_uniquifies_inline_port_names_against_parent():
    """If an inline scope has a var with the same name as a port in
    its container, the inline copy gets a `_<N>` suffix (mirrors
    LowerToHW's uniquifier)."""
    doc = _doc_skeleton()
    doc["types"]["u8"] = {"kind": "uint", "width": 8}
    doc["variables"]["v_top_x"] = {
        "typeRef": "u8", "bindKind": "port", "direction": "input",
        "ownerScopeRef": "Top",
        "representations": {
            "chisel": {"name": "x"},
            "verilog": {"name": "x", "value": {"sigName": "x"}},
        },
    }
    doc["variables"]["v_inline_x"] = {
        "typeRef": "u8", "bindKind": "wire",
        "ownerScopeRef": "inline",
        "representations": {
            "chisel": {"name": "x"},
            "verilog": {"name": "x", "value": {"sigName": "inline_x"}},
        },
    }
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "module",
        "representations": {"chisel": {"name": "Top"},
                            "verilog": {"name": "Top"}},
        "variableRefs": ["v_top_x"],
    }
    doc["scopes"]["inline"] = {
        "name": "inline", "kind": "inline",
        "containerScopeRef": "Top",
        "representations": {"chisel": {"name": "inline"}},
        "variableRefs": ["v_inline_x"],
    }
    out = hgldd_convert(doc)
    top_obj = next(o for o in out["objects"] if o.get("obj_name") == "Top")
    inline_child = next(c for c in top_obj["children"]
                        if c.get("name") == "inline")
    inline_names = [pv["var_name"] for pv in inline_child["port_vars"]]
    # Top has `x`; inline's `x` collides and gets renamed.
    assert any(n.startswith("x_") for n in inline_names)


# ---- ownerScopeRef-based variable inclusion -----------------------------


def test_convert_inline_scope_with_unique_name_no_uniquify_collision():
    """An inline var whose name doesn't collide with any parent name
    keeps its name unchanged -- the uniquifier short-circuits.  This
    is the happy-path branch (no `_<N>` suffix appended)."""
    doc = _doc_skeleton()
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "module",
        "representations": {"chisel": {"name": "Top"},
                            "verilog": {"name": "Top"}},
        "variableRefs": [],
    }
    doc["types"]["u1"] = {"kind": "uint", "width": 1}
    doc["variables"]["v_inline_unique"] = {
        "typeRef": "u1", "bindKind": "wire",
        "ownerScopeRef": "inline",
        "representations": {
            "chisel": {"name": "unique_name"},
            "verilog": {"name": "unique_name",
                        "value": {"sigName": "unique_name"}},
        },
    }
    doc["scopes"]["inline"] = {
        "name": "inline", "kind": "inline",
        "containerScopeRef": "Top",
        "representations": {"chisel": {"name": "inline",
                                        "location": {"file": 0,
                                                      "beginLine": 5,
                                                      "beginColumn": 3}}},
        "variableRefs": ["v_inline_unique"],
    }
    out = hgldd_convert(doc)
    top_obj = next(o for o in out["objects"] if o.get("obj_name") == "Top")
    inline_child = next(c for c in top_obj["children"]
                        if c.get("name") == "inline")
    inline_names = [pv["var_name"] for pv in inline_child["port_vars"]]
    # No collision -> name preserved verbatim, no `_0` suffix.
    assert "unique_name" in inline_names
    # Inline scope's hgl_loc carries the chisel location through.
    assert "hgl_loc" in inline_child


def test_convert_inline_scope_keeps_output_port_without_verilog_repr():
    """Same as the module-level case but for inline scopes: an output
    port whose UHDI variable lacks a verilog repr stays in port_vars,
    just without value/hdl_loc."""
    doc = _doc_skeleton()
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "module",
        "representations": {"chisel": {"name": "Top"},
                            "verilog": {"name": "Top"}},
        "variableRefs": [],
    }
    doc["types"]["u8"] = {"kind": "uint", "width": 8}
    doc["variables"]["v_inline_output"] = {
        "typeRef": "u8", "bindKind": "port", "direction": "output",
        "ownerScopeRef": "inline",
        "representations": {
            "chisel": {"name": "inline_q"},
            # No verilog repr.
        },
    }
    doc["scopes"]["inline"] = {
        "name": "inline", "kind": "inline",
        "containerScopeRef": "Top",
        "representations": {"chisel": {"name": "inline"}},
        "variableRefs": ["v_inline_output"],
    }
    out = hgldd_convert(doc)
    top_obj = next(o for o in out["objects"] if o.get("obj_name") == "Top")
    inline_child = next(c for c in top_obj["children"]
                        if c.get("name") == "inline")
    pv = next(p for p in inline_child["port_vars"] if p["var_name"] == "inline_q")
    assert "value" not in pv
    assert "hdl_loc" not in pv


def test_convert_skips_port_whose_sig_is_aggregated_leaf():
    """A port whose verilog sigName is already a leaf of another
    variable's structured value (e.g. a bundle's `io_pack` exprRef
    referencing `io_in_a`) is dropped -- native HGLDD emits only the
    aggregate, and emitting both would duplicate the row in Tywaves."""
    doc = _doc_skeleton()
    doc["types"] = {
        "u8": {"kind": "uint", "width": 8},
        "InBundle": {"kind": "struct", "members": [
            {"name": "a", "typeRef": "u8"}]},
    }
    doc["expressions"] = {
        "io_pack": {"opcode": "'{",
                    "operands": [{"sigName": "io_in_a"}]},
    }
    # The aggregate variable -- its exprRef tree has sigName io_in_a.
    doc["variables"]["v_io"] = {
        "typeRef": "InBundle", "bindKind": "port", "direction": "input",
        "ownerScopeRef": "Top",
        "representations": {
            "chisel": {"name": "io"},
            "verilog": {"name": "io", "value": {"exprRef": "io_pack"}},
        },
    }
    # The flat decomposition port -- should be skipped (its sigName
    # io_in_a is already a leaf of the aggregate's tree).
    doc["variables"]["v_io_in_a"] = {
        "typeRef": "u8", "bindKind": "port", "direction": "input",
        "ownerScopeRef": "Top",
        "representations": {
            "chisel": {"name": "io_in_a"},
            "verilog": {"name": "io_in_a", "value": {"sigName": "io_in_a"}},
        },
    }
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "module",
        "representations": {"chisel": {"name": "Top"},
                            "verilog": {"name": "Top"}},
        "variableRefs": ["v_io", "v_io_in_a"],
    }
    out = hgldd_convert(doc)
    top_obj = next(o for o in out["objects"] if o.get("obj_name") == "Top")
    names = [pv["var_name"] for pv in top_obj["port_vars"]]
    # The aggregate stays; the flat port_var was skipped.
    assert "io" in names
    assert "io_in_a" not in names


def test_convert_dedups_variables_with_identical_var_name():
    """The pool may carry two distinct stable_ids whose chisel/verilog
    names coincide (audit fields differ).  Native HGLDD coalesces
    these into a single port_var; the dedup-by-name in _scope_object
    matches that."""
    doc = _doc_skeleton()
    doc["types"]["u8"] = {"kind": "uint", "width": 8}
    for sid in ("v_dup1", "v_dup2"):
        doc["variables"][sid] = {
            "typeRef": "u8", "bindKind": "wire",
            "ownerScopeRef": "Top",
            "representations": {
                "chisel": {"name": "shared_name"},
                "verilog": {"name": "shared_name",
                            "value": {"sigName": "shared_name"}},
            },
        }
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "module",
        "representations": {"chisel": {"name": "Top"},
                            "verilog": {"name": "Top"}},
        "variableRefs": ["v_dup1", "v_dup2"],
    }
    out = hgldd_convert(doc)
    top_obj = next(o for o in out["objects"] if o.get("obj_name") == "Top")
    names = [pv["var_name"] for pv in top_obj["port_vars"]]
    assert names.count("shared_name") == 1


def test_scope_picks_up_vars_via_ownerscoperef_fallback():
    """A var that's not in scope.variableRefs but whose ownerScopeRef
    points at this scope is still emitted -- this lets hand-built
    fixtures skip the variableRefs maintenance burden."""
    doc = _doc_skeleton()
    doc["types"]["u1"] = {"kind": "uint", "width": 1}
    doc["variables"]["v_orphan"] = {
        "typeRef": "u1", "bindKind": "wire",
        "ownerScopeRef": "Top",
        "representations": {
            "chisel": {"name": "orphan"},
            "verilog": {"name": "orphan", "value": {"sigName": "orphan"}},
        },
    }
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "module",
        "representations": {"chisel": {"name": "Top"},
                            "verilog": {"name": "Top"}},
        "variableRefs": [],  # Empty -- v_orphan picked up via ownerScopeRef.
    }
    out = hgldd_convert(doc)
    top_obj = next(o for o in out["objects"] if o.get("obj_name") == "Top")
    names = [pv["var_name"] for pv in top_obj["port_vars"]]
    assert "orphan" in names


# ---- hdl_file_index synthesis (issue #20) -----------------------------


def test_resolve_hdl_file_path_prefers_first_non_empty_simulation_file():
    ctx = _Context.from_uhdi({
        "format": {"name": "uhdi"},
        "representations": {
            "chisel": {"kind": "source", "files": ["X.scala"]},
            "verilog": {"kind": "hdl", "language": "SystemVerilog",
                        "files": ["X.sv", "Y.sv"]},
        },
        "scopes": {}, "top": [],
    })
    assert _resolve_hdl_file_path(ctx) == "X.sv"


def test_resolve_hdl_file_path_synthesizes_from_top_scope_when_files_blank():
    """firtool's --emit-uhdi can leave verilog.files=[''] -- the
    synthesized fallback uses the top scope's HDL name + .sv so
    Tywaves can derive `<top>.vcd` from it."""
    ctx = _Context.from_uhdi({
        "format": {"name": "uhdi"},
        "representations": {
            "chisel": {"kind": "source", "files": ["GCD.scala"]},
            "verilog": {"kind": "hdl", "language": "SystemVerilog",
                        "files": [""]},
        },
        "scopes": {"GCD": {
            "name": "GCD", "kind": "module",
            "representations": {"chisel": {"name": "GCD"},
                                "verilog": {"name": "GCD"}},
        }},
        "top": ["GCD"],
    })
    assert _resolve_hdl_file_path(ctx) == "GCD.sv"


def test_resolve_hdl_file_path_uses_verilog_extension_for_plain_verilog():
    ctx = _Context.from_uhdi({
        "format": {"name": "uhdi"},
        "representations": {
            "chisel": {"kind": "source", "files": ["Top.scala"]},
            "verilog": {"kind": "hdl", "language": "Verilog", "files": [""]},
        },
        "scopes": {"Top": {
            "name": "Top", "kind": "module",
            "representations": {"chisel": {"name": "Top"},
                                "verilog": {"name": "Top"}},
        }},
        "top": ["Top"],
    })
    assert _resolve_hdl_file_path(ctx) == "Top.v"


def test_resolve_hdl_file_path_returns_none_when_no_top_and_no_files():
    ctx = _Context.from_uhdi({
        "format": {"name": "uhdi"},
        "representations": {
            "chisel": {"kind": "source", "files": []},
            "verilog": {"kind": "hdl", "files": []},
        },
        "scopes": {}, "top": [],
    })
    assert _resolve_hdl_file_path(ctx) is None


def test_convert_hdl_file_index_points_at_synthesized_filename():
    """End-to-end shape of issue #20: a UHDI doc that mirrors
    firtool's --emit-uhdi output (verilog.files=['']) still produces
    a `file_info` entry that tywaves can use to find `<top>.vcd`."""
    doc = {
        "format": {"name": "uhdi"},
        "representations": {
            "chisel": {"kind": "source", "files": ["GCD.scala"]},
            "verilog": {"kind": "hdl", "language": "SystemVerilog",
                        "files": [""]},
        },
        "top": ["GCD"],
        "types": {"u8": {"kind": "uint", "width": 8}},
        "expressions": {},
        "variables": {
            "v_q": {
                "typeRef": "u8", "bindKind": "port", "direction": "output",
                "ownerScopeRef": "GCD",
                "representations": {
                    "chisel": {"name": "q"},
                    "verilog": {"name": "q", "value": {"sigName": "q"}},
                },
            },
        },
        "scopes": {"GCD": {
            "name": "GCD", "kind": "module",
            "representations": {
                "chisel": {"name": "GCD",
                           "location": {"file": 0, "beginLine": 1}},
                "verilog": {"name": "GCD",
                            "location": {"file": 0, "beginLine": 1}},
            },
            "variableRefs": ["v_q"],
        }},
    }
    out = hgldd_convert(doc)
    header = out["HGLDD"]
    # hdl_file_index is 1-indexed; the entry it points at is non-empty.
    hdl_idx = header["hdl_file_index"] - 1
    assert 0 <= hdl_idx < len(header["file_info"])
    assert header["file_info"][hdl_idx] == "GCD.sv"


def test_convert_hdl_file_index_kept_when_no_locations_at_all():
    """No scope carries any location, but the doc still has a top
    module -- `hdl_file_index` must point at a synthesized HDL filename
    rather than past-the-end. Tywaves reads this even when scopes lack
    individual `hdl_loc` entries."""
    doc = {
        "format": {"name": "uhdi"},
        "representations": {
            "chisel": {"kind": "source", "files": [""]},
            "verilog": {"kind": "hdl", "language": "SystemVerilog",
                        "files": [""]},
        },
        "top": ["Bare"],
        "types": {}, "expressions": {}, "variables": {},
        "scopes": {"Bare": {
            "name": "Bare", "kind": "module",
            "representations": {"chisel": {"name": "Bare"},
                                "verilog": {"name": "Bare"}},
        }},
    }
    out = hgldd_convert(doc)
    header = out["HGLDD"]
    hdl_idx = header["hdl_file_index"] - 1
    assert header["file_info"][hdl_idx] == "Bare.sv"


# ---- sourceLangType / enum_defs / enum_def_ref --------------------------
#
# These tests exercise the second-iteration UHDI -> HGLDD pipeline,
# covering metadata that surfaces in tywaves' waveform when the
# CIRCT-side EmitUHDI produces sourceLangType + dbg.enumdef entries.
# Fixtures mirror the Alu example in text/visual/experiments/alu and the
# rameloni Tywaves-Chisel HGLDD shape it's compared against.


def _alu_with_sourcelang_and_enum():
    """UHDI document approximating Alu after the producer-side update:
    parent aggregate `var_Alu_io` plus synthetic per-field Variables,
    an AluOp enum type, and sourceLangType strings on both the module
    scope and every chisel-repr of a Variable."""
    doc = _doc_skeleton()
    doc["top"] = ["Alu"]
    doc["types"]["uint2"] = {"kind": "uint", "width": 2}
    doc["types"]["uint8"] = {"kind": "uint", "width": 8}
    doc["types"]["bool"] = {"kind": "uint", "width": 1}
    doc["types"]["AluOp"] = {
        "kind": "enum",
        "underlyingTypeRef": "uint2",
        "variants": {"0": "ADD", "1": "SUB", "2": "AND", "3": "OR"},
    }
    doc["types"]["Alu_io_in"] = {
        "kind": "struct",
        "members": [
            {"name": "a", "typeRef": "uint8", "flipped": True},
            {"name": "b", "typeRef": "uint8", "flipped": True},
            {"name": "op", "typeRef": "AluOp", "flipped": True},
        ],
    }
    doc["types"]["Alu_io"] = {
        "kind": "struct",
        "members": [
            {"name": "in", "typeRef": "Alu_io_in"},
            {"name": "out", "typeRef": "uint8", "flipped": True},
        ],
    }
    # Parent aggregate Variable.
    doc["variables"]["var_io"] = {
        "typeRef": "Alu_io", "bindKind": "node", "ownerScopeRef": "Alu",
        "representations": {
            "chisel": {
                "name": "io",
                "sourceLangType": {"typeName": "IO[AnonymousBundle]"},
            },
            "verilog": {"value": {"exprRef": "io_expr"}},
        },
    }
    # Synthetic per-field leaves.
    doc["variables"]["var_io__in"] = {
        "typeRef": "Alu_io_in", "bindKind": "synthetic", "ownerScopeRef": "Alu",
        "representations": {"chisel": {
            "name": "in", "sourceLangType": {"typeName": "IO[Operands]"}}},
    }
    doc["variables"]["var_io__in__a"] = {
        "typeRef": "uint8", "bindKind": "synthetic", "ownerScopeRef": "Alu",
        "representations": {"chisel": {
            "name": "a", "sourceLangType": {"typeName": "IO[UInt<8>]"}}},
    }
    doc["variables"]["var_io__in__b"] = {
        "typeRef": "uint8", "bindKind": "synthetic", "ownerScopeRef": "Alu",
        "representations": {"chisel": {
            "name": "b", "sourceLangType": {"typeName": "IO[UInt<8>]"}}},
    }
    doc["variables"]["var_io__in__op"] = {
        "typeRef": "AluOp", "bindKind": "synthetic", "ownerScopeRef": "Alu",
        "representations": {"chisel": {
            "name": "op", "sourceLangType": {"typeName": "IO[AluOp]"}}},
    }
    doc["variables"]["var_io__out"] = {
        "typeRef": "uint8", "bindKind": "synthetic", "ownerScopeRef": "Alu",
        "representations": {"chisel": {
            "name": "out", "sourceLangType": {"typeName": "IO[UInt<8>]"}}},
    }
    # Top-level wire `res` to exercise sourceLangType on a non-aggregate
    # Variable (matches the Wire[UInt<8>] case in the rameloni golden).
    doc["variables"]["var_res"] = {
        "typeRef": "uint8", "bindKind": "wire", "ownerScopeRef": "Alu",
        "representations": {
            "chisel": {
                "name": "res",
                "sourceLangType": {"typeName": "Wire[UInt<8>]"},
            },
            "verilog": {"value": {"sigName": "res"}},
        },
    }
    doc["expressions"]["io_expr"] = {
        "opcode": "'{",
        "operands": [{"sigName": "io_in_a"}, {"sigName": "io_out"}],
    }
    doc["scopes"]["Alu"] = {
        "name": "Alu", "kind": "module",
        "representations": {
            "chisel": {"name": "Alu",
                       "sourceLangType": {"typeName": "Alu"}},
            "verilog": {"name": "Alu"},
        },
        "variableRefs": ["var_io", "var_io__in", "var_io__in__a",
                         "var_io__in__b", "var_io__in__op", "var_io__out",
                         "var_res"],
    }
    return doc


def _module_object(out):
    return next(o for o in out["objects"] if o.get("kind") == "module")


def _struct_object(out, obj_name):
    return next(o for o in out["objects"]
                if o.get("kind") == "struct" and o.get("obj_name") == obj_name)


def test_convert_emits_sourcelangtype_on_module_scope():
    out = hgldd_convert(_alu_with_sourcelang_and_enum())
    mod = _module_object(out)
    assert mod["source_lang_type_info"] == {"type_name": "Alu"}


def test_convert_emits_enum_defs_on_module():
    out = hgldd_convert(_alu_with_sourcelang_and_enum())
    mod = _module_object(out)
    # First (and only) enum referenced from this scope -> id 0.
    assert mod["enum_defs"] == {
        "0": {"0": "ADD", "1": "SUB", "2": "AND", "3": "OR"}
    }


def test_convert_emits_sourcelangtype_on_non_aggregate_variable():
    out = hgldd_convert(_alu_with_sourcelang_and_enum())
    mod = _module_object(out)
    res = next(pv for pv in mod["port_vars"] if pv["var_name"] == "res")
    assert res["source_lang_type_info"] == {"type_name": "Wire[UInt<8>]"}


def test_convert_skips_synthetic_variables_as_top_level_port_vars():
    """Synthetic per-field Variables surface only through the struct's
    port_vars, never as standalone HGLDD port_vars on the module."""
    out = hgldd_convert(_alu_with_sourcelang_and_enum())
    mod = _module_object(out)
    names = {pv["var_name"] for pv in mod["port_vars"]}
    assert names == {"io", "res"}


def test_convert_propagates_sourcelangtype_to_struct_members():
    out = hgldd_convert(_alu_with_sourcelang_and_enum())
    inner = _struct_object(out, "Alu_io_in")
    by_name = {pv["var_name"]: pv for pv in inner["port_vars"]}
    assert by_name["a"]["source_lang_type_info"] == {
        "type_name": "IO[UInt<8>]"}
    assert by_name["b"]["source_lang_type_info"] == {
        "type_name": "IO[UInt<8>]"}
    assert by_name["op"]["source_lang_type_info"] == {
        "type_name": "IO[AluOp]"}


def test_convert_sets_enum_def_ref_on_struct_member():
    out = hgldd_convert(_alu_with_sourcelang_and_enum())
    inner = _struct_object(out, "Alu_io_in")
    op_pv = next(pv for pv in inner["port_vars"] if pv["var_name"] == "op")
    assert op_pv["enum_def_ref"] == 0


def test_convert_propagates_sourcelangtype_to_nested_struct_member():
    """Outer struct's `in` member picks up IO[Operands] from the
    synthetic per-field Variable -- which itself wraps another struct."""
    out = hgldd_convert(_alu_with_sourcelang_and_enum())
    outer = _struct_object(out, "Alu_io")
    in_pv = next(pv for pv in outer["port_vars"] if pv["var_name"] == "in")
    assert in_pv["source_lang_type_info"] == {"type_name": "IO[Operands]"}


def test_convert_omits_enum_defs_when_no_enum_types_referenced():
    """Module without enum-typed Variables should not get an empty
    enum_defs object (mirrors rameloni: absent rather than {}).
    Regression guard for the pre-pass."""
    doc = _doc_skeleton()
    doc["types"]["uint8"] = {"kind": "uint", "width": 8}
    doc["variables"]["v"] = {
        "typeRef": "uint8", "bindKind": "wire", "ownerScopeRef": "Top",
        "representations": {"chisel": {"name": "v"},
                            "verilog": {"value": {"sigName": "v"}}},
    }
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "module",
        "representations": {"chisel": {"name": "Top"},
                            "verilog": {"name": "Top"}},
        "variableRefs": ["v"],
    }
    out = hgldd_convert(doc)
    mod = _module_object(out)
    assert "enum_defs" not in mod


def test_populate_enum_index_descends_vector_element():
    """Vec(N, Enum) port must register the element enum in enum_defs."""
    doc = _doc_skeleton()
    doc["top"] = ["Top"]
    doc["types"]["uint2"] = {"kind": "uint", "width": 2}
    doc["types"]["AluOp"] = {
        "kind": "enum",
        "underlyingTypeRef": "uint2",
        "variants": {"0": "ADD", "1": "SUB"},
    }
    doc["types"]["AluOpVec"] = {
        "kind": "vector", "elementRef": "AluOp", "size": 4,
    }
    doc["variables"]["var_ops"] = {
        "typeRef": "AluOpVec", "bindKind": "port",
        "direction": "input", "ownerScopeRef": "Top",
        "representations": {
            "chisel": {"name": "ops"},
            "verilog": {"value": {"sigName": "ops"}},
        },
    }
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "module",
        "representations": {"chisel": {"name": "Top"},
                            "verilog": {"name": "Top"}},
        "variableRefs": ["var_ops"],
    }
    out = hgldd_convert(doc)
    mod = _module_object(out)
    assert mod["enum_defs"] == {"0": {"0": "ADD", "1": "SUB"}}


def test_convert_omits_sourcelangtype_when_typename_missing():
    """A repr-record without `sourceLangType` (legacy / not-yet-updated
    UHDI) must not surface source_lang_type_info in HGLDD."""
    doc = _doc_skeleton()
    doc["types"]["uint8"] = {"kind": "uint", "width": 8}
    doc["variables"]["v"] = {
        "typeRef": "uint8", "bindKind": "wire", "ownerScopeRef": "Top",
        "representations": {"chisel": {"name": "v"},
                            "verilog": {"value": {"sigName": "v"}}},
    }
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "module",
        "representations": {"chisel": {"name": "Top"},
                            "verilog": {"name": "Top"}},
        "variableRefs": ["v"],
    }
    out = hgldd_convert(doc)
    mod = _module_object(out)
    assert "source_lang_type_info" not in mod
    pv = next(p for p in mod["port_vars"] if p["var_name"] == "v")
    assert "source_lang_type_info" not in pv


def test_type_description_enum_resolves_packed_range_from_underlying():
    """Enum-typed values must carry the storage width drawn from
    underlyingTypeRef. Width is otherwise unrecoverable from HGLDD
    output -- enum_defs payload omits the underlying type."""
    out = hgldd_convert(_alu_with_sourcelang_and_enum())
    inner = _struct_object(out, "Alu_io_in")
    op_pv = next(pv for pv in inner["port_vars"] if pv["var_name"] == "op")
    # AluOp's underlyingTypeRef is uint2 -> packed_range [1, 0].
    assert op_pv["packed_range"] == [1, 0]


def test_type_description_enum_one_bit_elides_packed_range():
    """1-bit enum elides packed_range, matching the uint/sint convention."""
    doc = _doc_skeleton()
    doc["top"] = ["Top"]
    doc["types"]["uint1"] = {"kind": "uint", "width": 1}
    doc["types"]["Toggle"] = {
        "kind": "enum", "underlyingTypeRef": "uint1",
        "variants": {"0": "OFF", "1": "ON"},
    }
    doc["variables"]["var_t"] = {
        "typeRef": "Toggle", "bindKind": "port",
        "direction": "input", "ownerScopeRef": "Top",
        "representations": {
            "chisel": {"name": "t"},
            "verilog": {"value": {"sigName": "t"}},
        },
    }
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "module",
        "representations": {"chisel": {"name": "Top"},
                            "verilog": {"name": "Top"}},
        "variableRefs": ["var_t"],
    }
    out = hgldd_convert(doc)
    mod = _module_object(out)
    pv = next(p for p in mod["port_vars"] if p["var_name"] == "t")
    assert "packed_range" not in pv
    assert pv["type_name"] == "logic"


def test_type_description_vec_of_enum_carries_element_width():
    """Vec(N, Enum) port: packed_range from the enum's underlying type,
    unpacked_range from the vector size."""
    doc = _doc_skeleton()
    doc["top"] = ["Top"]
    doc["types"]["uint2"] = {"kind": "uint", "width": 2}
    doc["types"]["AluOp"] = {
        "kind": "enum", "underlyingTypeRef": "uint2",
        "variants": {"0": "ADD", "1": "SUB"},
    }
    doc["types"]["AluOpVec"] = {
        "kind": "vector", "elementRef": "AluOp", "size": 4,
    }
    doc["variables"]["var_ops"] = {
        "typeRef": "AluOpVec", "bindKind": "port",
        "direction": "input", "ownerScopeRef": "Top",
        "representations": {
            "chisel": {"name": "ops"},
            "verilog": {"value": {"sigName": "ops"}},
        },
    }
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "module",
        "representations": {"chisel": {"name": "Top"},
                            "verilog": {"name": "Top"}},
        "variableRefs": ["var_ops"],
    }
    out = hgldd_convert(doc)
    mod = _module_object(out)
    pv = next(p for p in mod["port_vars"] if p["var_name"] == "ops")
    assert pv["packed_range"] == [1, 0]
    assert pv["unpacked_range"] == [3, 0]


def test_convert_global_enum_ids_stable_across_scopes_sharing_struct():
    """A struct shared between two modules must have a single
    enum_def_ref on its members that resolves to the same variants
    in every scope. Per-scope numbering would flip variants when the
    second module references the same enums in a different order."""
    doc = _doc_skeleton()
    doc["top"] = ["A", "B_mod"]
    doc["types"]["uint2"] = {"kind": "uint", "width": 2}
    doc["types"]["uint1"] = {"kind": "uint", "width": 1}
    doc["types"]["AluOp"] = {
        "kind": "enum", "underlyingTypeRef": "uint2",
        "variants": {"0": "ADD", "1": "SUB"},
    }
    doc["types"]["Mode"] = {
        "kind": "enum", "underlyingTypeRef": "uint1",
        "variants": {"0": "RUN", "1": "HALT"},
    }
    doc["types"]["B"] = {
        "kind": "struct",
        "members": [
            {"name": "op",   "typeRef": "AluOp"},
            {"name": "mode", "typeRef": "Mode"},
        ],
    }
    # scope A: AluOp encountered first (would get id 0 under old per-scope).
    doc["variables"]["a_bun"] = {
        "typeRef": "B", "bindKind": "node", "ownerScopeRef": "A",
        "representations": {"chisel": {"name": "bun"},
                            "verilog": {"value": {"sigName": "bun"}}},
    }
    doc["variables"]["a_bun__op"] = {
        "typeRef": "AluOp", "bindKind": "synthetic", "ownerScopeRef": "A",
        "representations": {"chisel": {"name": "op"}},
    }
    doc["variables"]["a_bun__mode"] = {
        "typeRef": "Mode", "bindKind": "synthetic", "ownerScopeRef": "A",
        "representations": {"chisel": {"name": "mode"}},
    }
    # scope B_mod: order reversed -- Mode encountered first.
    doc["variables"]["b_mode_wire"] = {
        "typeRef": "Mode", "bindKind": "wire", "ownerScopeRef": "B_mod",
        "representations": {"chisel": {"name": "m"},
                            "verilog": {"value": {"sigName": "m"}}},
    }
    doc["variables"]["b_bun"] = {
        "typeRef": "B", "bindKind": "node", "ownerScopeRef": "B_mod",
        "representations": {"chisel": {"name": "bun"},
                            "verilog": {"value": {"sigName": "bun2"}}},
    }
    doc["variables"]["b_bun__op"] = {
        "typeRef": "AluOp", "bindKind": "synthetic", "ownerScopeRef": "B_mod",
        "representations": {"chisel": {"name": "op"}},
    }
    doc["variables"]["b_bun__mode"] = {
        "typeRef": "Mode", "bindKind": "synthetic", "ownerScopeRef": "B_mod",
        "representations": {"chisel": {"name": "mode"}},
    }
    doc["scopes"]["A"] = {
        "name": "A", "kind": "module",
        "representations": {"chisel": {"name": "A"},
                            "verilog": {"name": "A"}},
        "variableRefs": ["a_bun", "a_bun__op", "a_bun__mode"],
    }
    doc["scopes"]["B_mod"] = {
        "name": "B_mod", "kind": "module",
        "representations": {"chisel": {"name": "B_mod"},
                            "verilog": {"name": "B_mod"}},
        "variableRefs": ["b_mode_wire", "b_bun", "b_bun__op", "b_bun__mode"],
    }
    out = hgldd_convert(doc)
    struct_b = _struct_object(out, "B")
    op_ref = next(pv for pv in struct_b["port_vars"]
                  if pv["var_name"] == "op")["enum_def_ref"]
    mode_ref = next(pv for pv in struct_b["port_vars"]
                    if pv["var_name"] == "mode")["enum_def_ref"]
    mod_a = next(o for o in out["objects"]
                 if o.get("kind") == "module" and o.get("obj_name") == "A")
    mod_b = next(o for o in out["objects"]
                 if o.get("kind") == "module" and o.get("obj_name") == "B_mod")
    # Both modules see the same variants under the shared struct's ref ids.
    assert mod_a["enum_defs"][str(op_ref)] == {"0": "ADD", "1": "SUB"}
    assert mod_b["enum_defs"][str(op_ref)] == {"0": "ADD", "1": "SUB"}
    assert mod_a["enum_defs"][str(mode_ref)] == {"0": "RUN", "1": "HALT"}
    assert mod_b["enum_defs"][str(mode_ref)] == {"0": "RUN", "1": "HALT"}


def test_loc_to_hgldd_drops_source_location_with_empty_file_path():
    """When representations.<src>.files contains an empty string and a
    Variable's location points at that entry, the converter must drop
    the location rather than insert '' into file_info."""
    doc = _doc_skeleton()
    doc["representations"]["chisel"]["files"] = [""]
    doc["representations"]["verilog"]["files"] = ["Top.sv"]
    doc["top"] = ["Top"]
    doc["types"]["uint8"] = {"kind": "uint", "width": 8}
    doc["variables"]["v"] = {
        "typeRef": "uint8", "bindKind": "wire", "ownerScopeRef": "Top",
        "representations": {
            "chisel": {
                "name": "v",
                "location": {"file": 0, "beginLine": 5},
            },
            "verilog": {"value": {"sigName": "v"}},
        },
    }
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "module",
        "representations": {"chisel": {"name": "Top"},
                            "verilog": {"name": "Top"}},
        "variableRefs": ["v"],
    }
    out = hgldd_convert(doc)
    assert "" not in out["HGLDD"]["file_info"]
    mod = _module_object(out)
    pv = next(p for p in mod["port_vars"] if p["var_name"] == "v")
    assert "hgl_loc" not in pv


def test_struct_member_enum_def_ref_emitted_without_synthetic_subfields():
    """Struct member's enum_def_ref must emit even when the producer
    did not emit synthetic per-field Variables (extmodule path)."""
    doc = _doc_skeleton()
    doc["top"] = ["Top"]
    doc["types"]["uint2"] = {"kind": "uint", "width": 2}
    doc["types"]["AluOp"] = {
        "kind": "enum", "underlyingTypeRef": "uint2",
        "variants": {"0": "ADD", "1": "SUB"},
    }
    doc["types"]["B"] = {
        "kind": "struct",
        "members": [{"name": "op", "typeRef": "AluOp"}],
    }
    # NO synthetic Variable for `op` -- just the aggregate.
    doc["variables"]["bun"] = {
        "typeRef": "B", "bindKind": "node", "ownerScopeRef": "Top",
        "representations": {
            "chisel": {"name": "bun"},
            "verilog": {"value": {"sigName": "bun"}},
        },
    }
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "module",
        "representations": {"chisel": {"name": "Top"},
                            "verilog": {"name": "Top"}},
        "variableRefs": ["bun"],
    }
    out = hgldd_convert(doc)
    struct_b = _struct_object(out, "B")
    op_pv = next(pv for pv in struct_b["port_vars"]
                 if pv["var_name"] == "op")
    assert "enum_def_ref" in op_pv
    # The enum is the only one referenced globally -> id 0.
    assert op_pv["enum_def_ref"] == 0
    # source_lang_type_info remains absent (no synthetic to source it).
    assert "source_lang_type_info" not in op_pv


# ---- hdl_file_index empty file_info regression (FU3.11) ----------------


def test_convert_omits_hdl_file_index_when_file_info_empty():
    """Degenerate input (top=[], no Variables with locations) must not
    emit hdl_file_index=1 against file_info=[]; the consumer would
    dereference past the end. Omit the key entirely."""
    doc = _doc_skeleton()
    doc["top"] = []
    doc["representations"]["chisel"]["files"] = []
    doc["representations"]["verilog"]["files"] = []
    out = hgldd_convert(doc)
    assert out["HGLDD"]["file_info"] == []
    assert "hdl_file_index" not in out["HGLDD"]


# ---- unknown HDL language guard (FU3.7) ---------------------------------


def test_resolve_hdl_file_path_raises_on_unknown_language():
    """Explicit but unrecognized HDL language must surface as a
    conversion error, not silently fall back to .sv."""
    doc = _doc_skeleton()
    doc["top"] = ["Top"]
    doc["representations"]["verilog"]["language"] = "VHDL"
    doc["representations"]["verilog"]["files"] = []
    doc["types"]["uint8"] = {"kind": "uint", "width": 8}
    doc["variables"]["v"] = {
        "typeRef": "uint8", "bindKind": "wire", "ownerScopeRef": "Top",
        "representations": {"chisel": {"name": "v"},
                            "verilog": {"value": {"sigName": "v"}}},
    }
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "module",
        "representations": {"chisel": {"name": "Top"},
                            "verilog": {"name": "Top"}},
        "variableRefs": ["v"],
    }
    with pytest.raises(HGLDDConversionError, match="unknown HDL language"):
        hgldd_convert(doc)


def test_resolve_hdl_file_path_keeps_sv_default_when_language_empty():
    """When language is absent/empty (current firtool --emit-uhdi),
    the .sv default still fires."""
    doc = _doc_skeleton()
    doc["top"] = ["Top"]
    doc["representations"]["verilog"].pop("language", None)
    doc["representations"]["verilog"]["files"] = []
    doc["types"]["uint8"] = {"kind": "uint", "width": 8}
    doc["variables"]["v"] = {
        "typeRef": "uint8", "bindKind": "wire", "ownerScopeRef": "Top",
        "representations": {"chisel": {"name": "v"},
                            "verilog": {"value": {"sigName": "v"}}},
    }
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "module",
        "representations": {"chisel": {"name": "Top"},
                            "verilog": {"name": "Top"}},
        "variableRefs": ["v"],
    }
    out = hgldd_convert(doc)
    assert "Top.sv" in out["HGLDD"]["file_info"]


# ---- _source_lang_type (sourceLangType -> source_lang_type_info) --------


def test_source_lang_type_none_when_no_repr_or_typename():
    assert _source_lang_type(None) is None
    assert _source_lang_type({}) is None
    assert _source_lang_type({"sourceLangType": {"params": []}}) is None


def test_source_lang_type_typename_only():
    assert _source_lang_type({"sourceLangType": {"typeName": "IO[Bundle]"}}) == {
        "type_name": "IO[Bundle]"}


def test_source_lang_type_projects_ctor_params_with_type_key():
    # UHDI `params[].typeName` maps onto Tywaves ConstructorParams `type`;
    # name/value pass through (HglddParser.scala SourceLangType.params).
    out = _source_lang_type({"sourceLangType": {
        "typeName": "Top",
        "params": [
            {"name": "width", "typeName": "Int", "value": "8"},
            {"name": "depth", "typeName": "Int", "value": "4"},
        ],
    }})
    assert out == {
        "type_name": "Top",
        "params": [
            {"name": "width", "type": "Int", "value": "8"},
            {"name": "depth", "type": "Int", "value": "4"},
        ],
    }


def test_source_lang_type_params_tolerate_partial_and_skip_malformed():
    out = _source_lang_type({"sourceLangType": {
        "typeName": "Q",
        "params": [
            {"name": "n"},                       # no typeName/value
            {"typeName": "Int", "value": "1"},   # no name -> skipped
            "garbage",                            # non-dict -> skipped
        ],
    }})
    assert out == {"type_name": "Q", "params": [{"name": "n"}]}


def test_source_lang_type_omits_empty_params_list():
    assert _source_lang_type({"sourceLangType": {
        "typeName": "T", "params": []}}) == {"type_name": "T"}


# ---- memberRefs aggregates (no expressions pool) -----------------------
# Producer-side change: an aggregate carries an ordered `memberRefs`
# list of child variable ids instead of an `exprRef` into a now-absent
# `expressions` pool. The HGLDD `value` is rebuilt from the children.


def _member_refs_doc():
    """Bundle `io = {in: {a, b}, out}` in the memberRefs shape, plus the
    flat Verilog ports that the aggregate's leaves alias."""
    doc = _doc_skeleton()
    del doc["roles"]
    del doc["expressions"]
    doc["types"] = {
        "u8": {"kind": "uint", "width": 8},
        "In": {"kind": "struct", "members": [
            {"name": "a", "typeRef": "u8"}, {"name": "b", "typeRef": "u8"}]},
        "Io": {"kind": "struct", "members": [
            {"name": "in", "typeRef": "In"}, {"name": "out", "typeRef": "u8"}]},
    }
    doc["variables"] = {
        "v_io": {
            "typeRef": "Io", "bindKind": "node", "ownerScopeRef": "Top",
            "memberRefs": ["v_io__in", "v_io__out"],
            "representations": {"chisel": {"name": "io"}, "verilog": {}},
        },
        "v_io__in": {
            "typeRef": "In", "bindKind": "synthetic", "ownerScopeRef": "Top",
            "memberRefs": ["v_io__in__a", "v_io__in__b"],
            "representations": {"chisel": {"name": "in"}, "verilog": {}},
        },
        "v_io__in__a": {
            "typeRef": "u8", "bindKind": "synthetic", "ownerScopeRef": "Top",
            "representations": {
                "chisel": {"name": "a"},
                "verilog": {"name": "io_in_a", "value": {"sigName": "io_in_a"}}},
        },
        "v_io__in__b": {
            "typeRef": "u8", "bindKind": "synthetic", "ownerScopeRef": "Top",
            "representations": {
                "chisel": {"name": "b"},
                "verilog": {"value": {"constant": 3}}},
        },
        "v_io__out": {
            "typeRef": "u8", "bindKind": "synthetic", "ownerScopeRef": "Top",
            "representations": {
                "chisel": {"name": "out"},
                "verilog": {"name": "io_out", "value": {"sigName": "io_out"}}},
        },
        "v_port_io_in_a": {
            "typeRef": "u8", "bindKind": "port", "direction": "input",
            "ownerScopeRef": "Top",
            "representations": {
                "chisel": {"name": "io_in_a"},
                "verilog": {"name": "io_in_a", "value": {"sigName": "io_in_a"}}},
        },
    }
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "module",
        "representations": {"chisel": {"name": "Top"},
                            "verilog": {"name": "Top"}},
        "variableRefs": list(doc["variables"]),
    }
    return doc


def _top_port_vars(doc):
    out = hgldd_convert(doc)
    top = next(o for o in out["objects"] if o.get("obj_name") == "Top")
    return {pv["var_name"]: pv for pv in top["port_vars"]}


def test_convert_member_refs_builds_nested_struct_literal():
    pvs = _top_port_vars(_member_refs_doc())
    assert pvs["io"]["value"] == {"opcode": "'{", "operands": [
        {"opcode": "'{", "operands": [
            {"sig_name": "io_in_a"}, {"bit_vector": "00000011"}]},
        {"sig_name": "io_out"},
    ]}


def test_convert_member_refs_skips_flat_port_aliased_by_leaf():
    """The flat `io_in_a` port is already a leaf of `io`'s value tree;
    native HGLDD emits only the aggregate."""
    pvs = _top_port_vars(_member_refs_doc())
    assert "io_in_a" not in pvs


def test_convert_member_refs_preserves_order():
    doc = _member_refs_doc()
    doc["variables"]["v_io"]["memberRefs"] = ["v_io__out", "v_io__in"]
    pvs = _top_port_vars(doc)
    assert pvs["io"]["value"]["operands"][0] == {"sig_name": "io_out"}


def test_convert_member_refs_unknown_member_renders_empty_operand():
    doc = _member_refs_doc()
    doc["variables"]["v_io"]["memberRefs"] = ["v_io__in", "ghost"]
    pvs = _top_port_vars(doc)
    assert pvs["io"]["value"]["operands"][1] == {}


def test_convert_member_refs_breaks_on_cycle():
    doc = _member_refs_doc()
    doc["variables"]["v_io__in"]["memberRefs"] = ["v_io"]
    with pytest.raises(HGLDDConversionError, match="cycle in memberRefs"):
        hgldd_convert(doc)


def test_convert_member_refs_names_vector_after_first_element():
    doc = _doc_skeleton()
    doc["types"] = {
        "u8": {"kind": "uint", "width": 8},
        "v": {"kind": "vector", "elementRef": "u8", "size": 2},
    }
    doc["variables"] = {
        "v_arr": {
            "typeRef": "v", "bindKind": "wire", "ownerScopeRef": "Top",
            "memberRefs": ["v_arr__0", "v_arr__1"],
            "representations": {"chisel": {"name": "arr"}, "verilog": {}},
        },
        "v_arr__0": {
            "typeRef": "u8", "bindKind": "synthetic", "ownerScopeRef": "Top",
            "representations": {
                "chisel": {"name": "0"},
                "verilog": {"value": {"sigName": "arr_0"}}},
        },
        "v_arr__1": {
            "typeRef": "u8", "bindKind": "synthetic", "ownerScopeRef": "Top",
            "representations": {
                "chisel": {"name": "1"},
                "verilog": {"value": {"sigName": "arr_1"}}},
        },
    }
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "module",
        "representations": {"chisel": {"name": "Top"},
                            "verilog": {"name": "Top"}},
        "variableRefs": list(doc["variables"]),
    }
    pvs = _top_port_vars(doc)
    assert "arr_0" in pvs
    assert pvs["arr_0"]["value"] == {"opcode": "'{", "operands": [
        {"sig_name": "arr_0"}, {"sig_name": "arr_1"}]}


def test_convert_alu_member_refs_fixture_matches_expression_shape():
    """firtool's memberRefs document for Alu yields the same `io` tree
    the old exprRef-based document produced."""
    import json
    import pathlib
    fixture = (pathlib.Path(__file__).parent / "fixtures" / "uhdi"
               / "alu_member_refs.uhdi.json")
    doc = json.loads(fixture.read_text(encoding="utf-8"))
    assert "expressions" not in doc and "roles" not in doc
    out = hgldd_convert(doc)
    alu = next(o for o in out["objects"] if o.get("obj_name") == "Alu")
    pvs = {pv["var_name"]: pv for pv in alu["port_vars"]}
    assert pvs["io"]["value"] == {"opcode": "'{", "operands": [
        {"opcode": "'{", "operands": [
            {"sig_name": "io_in_a"}, {"sig_name": "io_in_b"},
            {"sig_name": "io_in_op"}]},
        {"sig_name": "io_out"},
    ]}
    assert set(pvs) == {"clock", "reset", "io", "res"}
