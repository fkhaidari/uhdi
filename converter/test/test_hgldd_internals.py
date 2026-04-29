"""Unit tests for HGLDD converter internals."""
from __future__ import annotations

from uhdi_to_hgldd import convert as hgldd_convert
from uhdi_to_hgldd.convert import (
    _Context,
    _expression_to_hgldd,
    _FileInfo,
    _first_vector_element_sig,
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


def test_convert_skips_output_port_without_verilog_repr():
    """An output port with no verilog repr block (DCE'd in our flow)
    must not appear in port_vars -- otherwise the HGLDD output diverges
    from native, which only emits what survives DCE."""
    doc = _doc_skeleton()
    doc["types"]["u8"] = {"kind": "uint", "width": 8}
    doc["variables"]["v_q"] = {
        "typeRef": "u8", "bindKind": "port", "direction": "output",
        "ownerScopeRef": "Top",
        "representations": {
            "chisel": {"name": "q"},
            # No verilog repr -- DCE collapsed this output.
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
    names = [pv["var_name"] for pv in obj["port_vars"]]
    assert "q" not in names


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


def test_convert_inline_scope_skips_dce_output_port():
    """An inline scope can also contain a DCE'd output port (no
    verilog repr); the inline emitter must use the same skip rule
    as the parent so the inline port_vars stay shape-aligned with
    native."""
    doc = _doc_skeleton()
    doc["scopes"]["Top"] = {
        "name": "Top", "kind": "module",
        "representations": {"chisel": {"name": "Top"},
                            "verilog": {"name": "Top"}},
        "variableRefs": [],
    }
    doc["types"]["u8"] = {"kind": "uint", "width": 8}
    doc["variables"]["v_dce_output"] = {
        "typeRef": "u8", "bindKind": "port", "direction": "output",
        "ownerScopeRef": "inline",
        "representations": {
            "chisel": {"name": "dce_q"},
            # No verilog repr -- DCE collapsed it.
        },
    }
    doc["scopes"]["inline"] = {
        "name": "inline", "kind": "inline",
        "containerScopeRef": "Top",
        "representations": {"chisel": {"name": "inline"}},
        "variableRefs": ["v_dce_output"],
    }
    out = hgldd_convert(doc)
    top_obj = next(o for o in out["objects"] if o.get("obj_name") == "Top")
    inline_child = next(c for c in top_obj["children"]
                        if c.get("name") == "inline")
    inline_names = [pv["var_name"] for pv in inline_child["port_vars"]]
    # The DCE'd port was skipped (returned None from
    # _variable_to_port_var) and never landed in port_vars.
    assert "dce_q" not in inline_names


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
