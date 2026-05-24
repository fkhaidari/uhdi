"""Unit tests for the uhdi -> PDG (chiseltrace) converter internals."""
from __future__ import annotations

from typing import Any, Dict

import pytest
from uhdi_to_pdg import PDGConversionError
from uhdi_to_pdg import convert as pdg_convert
from uhdi_to_pdg.convert import (
    _BIND_TO_KIND,
    _collect_expr_vars,
    _collect_scopes,
    _Ctx,
    _expand_guard,
    _flatten_stmts,
    _module_path,
    _related_signal,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _doc(**overrides: Any) -> Dict[str, Any]:
    """Skeleton uhdi document. Override any pool by keyword."""
    base: Dict[str, Any] = {
        "format": {"name": "uhdi", "version": "1.0"},
        "representations": {
            "chisel":  {"kind": "source", "language": "Chisel", "files": ["X.scala"]},
            "verilog": {"kind": "hdl", "language": "SystemVerilog", "files": ["X.sv"]},
        },
        "roles": {"authoring": "chisel", "simulation": "verilog"},
        "top": ["X"],
        "types": {"u8": {"kind": "uint", "width": 8},
                  "bool": {"kind": "uint", "width": 1}},
        "expressions": {},
        "variables": {},
        "scopes": {"X": {"name": "X", "kind": "module",
                          "variableRefs": [], "body": []}},
    }
    base.update(overrides)
    return base


def _ctx(**overrides: Any) -> _Ctx:
    return _Ctx.from_uhdi(_doc(**overrides))


def _port(name: str, direction: str = "input",
          owner: str = "X", *, location: bool = True) -> Dict[str, Any]:
    repr_chisel: Dict[str, Any] = {"name": name}
    if location:
        repr_chisel["location"] = {"file": 0, "beginLine": 3, "beginColumn": 5}
    return {"typeRef": "u8", "bindKind": "port", "direction": direction,
            "ownerScopeRef": owner,
            "representations": {"chisel": repr_chisel}}


# ---------------------------------------------------------------------------
# _related_signal: bundle path split
# ---------------------------------------------------------------------------


def test_related_signal_splits_on_dot():
    ctx = _ctx()
    var = {"representations": {"chisel": {"name": "io.din"}}}
    assert _related_signal(var, ctx) == {"signalPath": "io", "fieldPath": ".din"}


def test_related_signal_splits_on_bracket():
    ctx = _ctx()
    var = {"representations": {"chisel": {"name": "mem[0]"}}}
    assert _related_signal(var, ctx) == {"signalPath": "mem", "fieldPath": "[0]"}


def test_related_signal_returns_none_for_flat_name():
    """Top-level signals without a `.` or `[` have no PDG-level fanout path."""
    ctx = _ctx()
    var = {"representations": {"chisel": {"name": "clock"}}}
    assert _related_signal(var, ctx) is None


# ---------------------------------------------------------------------------
# _collect_scopes / _module_path: DFS over instantiates
# ---------------------------------------------------------------------------


def test_collect_scopes_follows_instantiates():
    """Top instantiates Leaf -> both scopes reachable; modulePath threads
    through the instance label, not the bare scope id."""
    doc = _doc(
        top=["Top"],
        scopes={
            "Top": {"name": "Top", "variableRefs": [], "body": [],
                     "instantiates": [{"as": "l", "scopeRef": "Leaf"}]},
            "Leaf": {"name": "Leaf", "variableRefs": [], "body": []},
        })
    ctx = _Ctx.from_uhdi(doc)
    order = _collect_scopes(ctx)
    assert order == ["Top", "Leaf"]
    assert _module_path("Top", ctx) == ["Top"]
    assert _module_path("Leaf", ctx) == ["Top", "l"]


def test_collect_scopes_unknown_top_raises():
    doc = _doc(top=["Ghost"], scopes={"X": {"name": "X"}})
    ctx = _Ctx.from_uhdi(doc)
    with pytest.raises(PDGConversionError, match="unknown scope"):
        _collect_scopes(ctx)


def test_collect_scopes_breaks_instantiate_cycles():
    """A pair of mutually-instantiating scopes shouldn't recurse forever."""
    doc = _doc(
        top=["A"],
        scopes={
            "A": {"name": "A", "instantiates": [{"as": "b", "scopeRef": "B"}]},
            "B": {"name": "B", "instantiates": [{"as": "a", "scopeRef": "A"}]},
        })
    ctx = _Ctx.from_uhdi(doc)
    assert _collect_scopes(ctx) == ["A", "B"]


# ---------------------------------------------------------------------------
# _collect_expr_vars / _expand_guard: expression walk
# ---------------------------------------------------------------------------


def test_collect_expr_vars_flattens_nested_exprrefs():
    """`||` over two varRefs nested inside a `&&` exprRef: all 3 varRefs surface."""
    doc = _doc(
        expressions={
            "or":  {"opcode": "||",
                    "operands": [{"varRef": "a"}, {"varRef": "b"}]},
            "and": {"opcode": "&&",
                    "operands": [{"exprRef": "or"}, {"varRef": "c"}]},
        })
    ctx = _Ctx.from_uhdi(doc)
    acc: list[str] = []
    _collect_expr_vars(ctx.expressions["and"], ctx, acc, set())
    assert acc == ["a", "b", "c"]


def test_collect_expr_vars_raises_on_exprref_cycle():
    """Back-edge raises PDGConversionError; HGLDD's walk_expression does the
    same, so cyclic guards fail consistently across backends (FU2.6)."""
    doc = _doc(expressions={
        "loop": {"opcode": "id", "operands": [{"exprRef": "loop"}]}})
    ctx = _Ctx.from_uhdi(doc)
    acc: list[str] = []
    with pytest.raises(PDGConversionError, match="cycle in expression graph"):
        _collect_expr_vars(ctx.expressions["loop"], ctx, acc, set())


def test_collect_expr_vars_raises_on_mutual_cycle():
    """A -> B -> A back-edge also raises."""
    doc = _doc(expressions={
        "a": {"opcode": "id", "operands": [{"exprRef": "b"}]},
        "b": {"opcode": "id", "operands": [{"exprRef": "a"}]},
    })
    ctx = _Ctx.from_uhdi(doc)
    acc: list[str] = []
    with pytest.raises(PDGConversionError, match="cycle in expression graph"):
        _collect_expr_vars(ctx.expressions["a"], ctx, acc, set())


def test_expand_guard_handles_var_and_expr_refs():
    """A guardRef can resolve directly to a variable, or indirect through
    `expressions[]` to a fan-out over its operand vars."""
    doc = _doc(
        variables={"v": _port("v")},
        expressions={"or": {"opcode": "||",
                            "operands": [{"varRef": "v"}, {"varRef": "v"}]}},
    )
    ctx = _Ctx.from_uhdi(doc)
    assert _expand_guard("v", ctx) == ["v"]
    assert _expand_guard("or", ctx) == ["v", "v"]
    assert _expand_guard("", ctx) == []
    assert _expand_guard("ghost", ctx) == []


# ---------------------------------------------------------------------------
# _flatten_stmts: pre-order must match _walk_body's vertex-emit order
# ---------------------------------------------------------------------------


def test_flatten_stmts_skips_decl_and_none():
    """`decl` and `none` don't emit a statement vertex, so they must NOT
    appear in the flattened list (or the derivation pass's bookkeeping
    would mis-align with body_stmt_index)."""
    body = [
        {"kind": "decl",    "varRef": "x"},
        {"kind": "connect", "varRef": "y"},
        {"kind": "none"},
        {"kind": "block", "guardRef": "g", "body": [
            {"kind": "decl",    "varRef": "z"},
            {"kind": "connect", "varRef": "w"},
        ]},
    ]
    out: list[dict] = []
    _flatten_stmts(body, out)
    kinds = [s["kind"] for s in out]
    assert kinds == ["connect", "block", "connect"]


def test_flatten_stmts_includes_verification_statements():
    """assert/assume/cover lower to CF vertices (annotated), so they go in
    the flat list (and the CFG)."""
    body = [{"kind": "assert", "condRef": "c"},
            {"kind": "assume", "condRef": "c"},
            {"kind": "cover", "condRef": "c"}]
    out: list[dict] = []
    _flatten_stmts(body, out)
    assert [s["kind"] for s in out] == ["assert", "assume", "cover"]


# ---------------------------------------------------------------------------
# End-to-end shape invariants
# ---------------------------------------------------------------------------


def test_convert_emits_required_top_level_keys():
    """PDGSpec is `{vertices, edges, predicates, cfg}` -- all four must be
    present even on empty input (chiseltrace's Serde deserialiser is strict)."""
    out = pdg_convert(_doc())
    assert set(out.keys()) == {"vertices", "edges", "predicates", "cfg"}
    assert out["vertices"] == [] and out["edges"] == []
    assert out["predicates"] == [] and out["cfg"] == []


def test_convert_maps_bindkind_to_vertex_kind():
    """Table Sec.15.5.1: each uhdi bindKind takes a specific PDG kind."""
    variables = {
        "v_port": _port("p"),
        "v_wire": {"typeRef": "u8", "bindKind": "wire", "ownerScopeRef": "X",
                   "representations": {"chisel": {"name": "w"}}},
        "v_reg":  {"typeRef": "u8", "bindKind": "reg", "ownerScopeRef": "X",
                   "representations": {"chisel": {"name": "r"}}},
    }
    doc = _doc(
        variables=variables,
        scopes={"X": {"name": "X",
                       "variableRefs": list(variables.keys()),
                       "body": []}},
    )
    out = pdg_convert(doc)
    kinds = [v["kind"] for v in out["vertices"]]
    assert kinds == ["IO", "DataDefinition", "Definition"]


def test_convert_routes_probes_to_predicates():
    """Probes are NOT regular vertices: Sec.15.5.1 puts them in `predicates[]`,
    and the CFG references them by index there, not by vertex index."""
    variables = {
        "v_probe": {"typeRef": "bool", "bindKind": "probe", "ownerScopeRef": "X",
                    "representations": {"chisel": {"name": "p"}}},
        "v_target": {"typeRef": "u8", "bindKind": "reg", "ownerScopeRef": "X",
                     "representations": {"chisel": {"name": "r"}}},
    }
    body = [{"kind": "block", "guardRef": "v_probe", "body": [
        {"kind": "connect", "varRef": "v_target",
         "valueRef": {"constant": 0}}]}]
    doc = _doc(
        variables=variables,
        scopes={"X": {"name": "X", "variableRefs": list(variables.keys()),
                       "body": body}},
    )
    out = pdg_convert(doc)
    assert [v["kind"] for v in out["predicates"]] == ["DataDefinition"]
    # The block CFG entry must point predStmtRef at predicates[0], not at
    # a vertex (slicers index those two arrays differently).
    block_cfg = out["cfg"][0]
    assert block_cfg.get("predStmtRef") == 0


def test_convert_definition_clocked_bit_set_for_regs():
    """Sec.15.7: PDG retains the per-edge `clocked` bit; on Definition vertices
    we set it when the underlying bindKind is reg/mem."""
    doc = _doc(
        variables={"r": {"typeRef": "u8", "bindKind": "reg", "ownerScopeRef": "X",
                          "representations": {"chisel": {"name": "r"}}}},
        scopes={"X": {"name": "X", "variableRefs": ["r"], "body": []}},
    )
    out = pdg_convert(doc)
    assert out["vertices"][0]["clocked"] is True


def test_convert_drops_clock_and_reset_edges():
    """chiseltrace's PDGSpecEdgeKind enum has 4 variants; Sec.10 Clock/Reset
    edges aren't representable and must be silently filtered out (Sec.15.7)."""
    doc = _doc(
        variables={
            "clk": _port("clk"),
            "r":   {"typeRef": "u8", "bindKind": "reg", "ownerScopeRef": "X",
                    "representations": {"chisel": {"name": "r"}}},
        },
        scopes={"X": {"name": "X", "variableRefs": ["clk", "r"], "body": []}},
        dataflow={"edges": [
            {"from": {"varRef": "r"}, "to": {"varRef": "clk"}, "kind": "Clock"},
            {"from": {"varRef": "r"}, "to": {"varRef": "clk"}, "kind": "Reset"},
            {"from": {"varRef": "r"}, "to": {"varRef": "clk"}, "kind": "Data"},
        ]},
    )
    out = pdg_convert(doc)
    assert [e["kind"] for e in out["edges"]] == ["Data"]


def test_convert_require_dataflow_raises_when_section10_absent():
    """Per Sec.15.5.4: --require-dataflow turns "missing Sec.10" into a hard error
    instead of falling back to derivation (the alternative is a less precise
    edge set, which the caller may want to refuse)."""
    with pytest.raises(PDGConversionError, match="Sec.10"):
        pdg_convert(_doc(), require_dataflow=True)


def test_convert_uses_explicit_dataflow_when_present():
    """When the input carries Sec.10 directly, project it -- don't shadow with
    the derivation pass (the explicit graph is authoritative)."""
    variables = {
        "a": _port("a"), "b": _port("b", direction="output"),
    }
    body = [{"kind": "connect", "varRef": "b", "valueRef": {"varRef": "a"}}]
    doc = _doc(
        variables=variables,
        scopes={"X": {"name": "X", "variableRefs": ["a", "b"], "body": body}},
        dataflow={"edges": [
            {"from": {"varRef": "b"}, "to": {"varRef": "a"},
             "kind": "Data", "clocked": False},
        ]},
    )
    out = pdg_convert(doc)
    # The connect statement also emits a Connection vertex, but the edges
    # array contains exactly the one we declared (no derivation fan-out).
    assert len(out["edges"]) == 1
    assert out["edges"][0]["kind"] == "Data"


def test_convert_derives_data_decl_conditional_edges():
    """End-to-end Sec.15.5.4 derivation: one `block + connect` should yield
    one of each (Data to source, Declaration to target, Conditional to CF)."""
    variables = {
        "src": _port("src"),
        "dst": _port("dst", direction="output"),
        "g":   _port("g"),
    }
    body = [{"kind": "block", "guardRef": "g", "body": [
        {"kind": "connect", "varRef": "dst",
         "valueRef": {"varRef": "src"}}]}]
    doc = _doc(
        variables=variables,
        scopes={"X": {"name": "X",
                       "variableRefs": list(variables.keys()),
                       "body": body}},
    )
    out = pdg_convert(doc)
    kinds = sorted(e["kind"] for e in out["edges"])
    # CF gets a Data edge to the guard; connect gets Data/Declaration/Conditional.
    assert kinds == ["Conditional", "Data", "Data", "Declaration"]


# ---------------------------------------------------------------------------
# _BIND_TO_KIND coverage sanity
# ---------------------------------------------------------------------------


def test_bind_to_kind_covers_all_non_probe_bindkinds():
    """If Sec.6 adds a bindKind, this test fails until convert.py opts in;
    silent fallthrough to "skip the variable" used to lose mem cells."""
    assert set(_BIND_TO_KIND) == {"port", "wire", "node", "literal", "reg", "mem"}


# ---------------------------------------------------------------------------
# _resolve_predicate_index: exprRef-shaped guardRef (FU4.3)
# ---------------------------------------------------------------------------


def test_resolve_predicate_index_handles_exprref_shaped_guardref():
    """When guardRef is an exprRef id whose single operand is a probe varRef,
    the block CFG entry must get predStmtRef wired (FU4.3)."""
    variables = {
        "v_probe": {"typeRef": "bool", "bindKind": "probe", "ownerScopeRef": "X",
                    "representations": {"chisel": {"name": "p"}}},
        "v_target": {"typeRef": "u8", "bindKind": "reg", "ownerScopeRef": "X",
                     "representations": {"chisel": {"name": "r"}}},
    }
    # Expression "p_alias" wraps the probe variable in a single-operand exprRef.
    expressions = {
        "p_alias": {"opcode": "id", "operands": [{"varRef": "v_probe"}]},
    }
    body = [{"kind": "block", "guardRef": "p_alias", "body": [
        {"kind": "connect", "varRef": "v_target",
         "valueRef": {"constant": 0}}]}]
    doc = _doc(
        variables=variables,
        expressions=expressions,
        scopes={"X": {"name": "X", "variableRefs": list(variables.keys()),
                       "body": body}},
    )
    out = pdg_convert(doc)
    # The probe must end up in predicates[], not vertices[].
    assert [v["kind"] for v in out["predicates"]] == ["DataDefinition"]
    # The block CFG entry must reference predicates[0] via predStmtRef.
    block_cfg = out["cfg"][0]
    assert block_cfg.get("predStmtRef") == 0


# ---------------------------------------------------------------------------
# _derive_edges: assert/assume/cover (FU4.2)
# ---------------------------------------------------------------------------


def test_derive_edges_emits_data_for_assert_cond():
    """assert with condRef pointing at a varRef -> Data edge from CF vertex
    to the referenced variable's vertex (Sec.15.5.4 derivation, FU4.2)."""
    variables = {
        "v_in":     _port("in"),
        "v_result": {"typeRef": "u8", "bindKind": "wire", "ownerScopeRef": "X",
                     "representations": {"chisel": {"name": "result"}}},
    }
    body = [
        {"kind": "connect", "varRef": "v_result", "valueRef": {"varRef": "v_in"}},
        {"kind": "assert", "condRef": "v_in"},
    ]
    doc = _doc(
        variables=variables,
        scopes={"X": {"name": "X", "variableRefs": list(variables.keys()),
                       "body": body}},
    )
    out = pdg_convert(doc)
    # Locate the assert CF vertex: _controlflow_vertex prefixes annotation
    # into the `name` field, so look for "assert_cond_on_[...]".
    assert_cf_idx = next(
        i for i, v in enumerate(out["vertices"])
        if v.get("kind") == "ControlFlow" and v.get("name", "").startswith("assert_")
    )
    v_in_idx = next(
        i for i, v in enumerate(out["vertices"])
        if v.get("kind") == "IO"
    )
    data_edges = [
        e for e in out["edges"]
        if e["from"] == assert_cf_idx and e["to"] == v_in_idx and e["kind"] == "Data"
    ]
    assert len(data_edges) == 1


def test_derive_edges_emits_conditional_for_assert_under_block():
    """assert nested inside a block -> Conditional edge from assert CF vertex
    to the enclosing block's CF vertex (FU4.2)."""
    variables = {
        "v_guard": _port("guard"),
        "v_in":    _port("in"),
    }
    body = [{"kind": "block", "guardRef": "v_guard", "body": [
        {"kind": "assert", "condRef": "v_in"},
    ]}]
    doc = _doc(
        variables=variables,
        scopes={"X": {"name": "X", "variableRefs": list(variables.keys()),
                       "body": body}},
    )
    out = pdg_convert(doc)
    # block CF vertex has no annotation prefix in name.
    block_cf_idx = next(
        i for i, v in enumerate(out["vertices"])
        if v.get("kind") == "ControlFlow"
        and not v.get("name", "").startswith("assert_")
    )
    assert_cf_idx = next(
        i for i, v in enumerate(out["vertices"])
        if v.get("kind") == "ControlFlow" and v.get("name", "").startswith("assert_")
    )
    cond_edges = [
        e for e in out["edges"]
        if e["from"] == assert_cf_idx and e["to"] == block_cf_idx
        and e["kind"] == "Conditional"
    ]
    assert len(cond_edges) == 1


def test_derive_edges_assert_with_exprref_condition():
    """assert whose condRef is an exprRef whose operand is a varRef ->
    Data edge still emitted via _expand_guard fallthrough (FU4.2)."""
    variables = {
        "v_in": _port("in"),
    }
    expressions = {
        "in_nonzero": {"opcode": "!=", "operands": [{"varRef": "v_in"}, {"constant": 0}]},
    }
    body = [{"kind": "assert", "condRef": "in_nonzero"}]
    doc = _doc(
        variables=variables,
        expressions=expressions,
        scopes={"X": {"name": "X", "variableRefs": list(variables.keys()),
                       "body": body}},
    )
    out = pdg_convert(doc)
    assert_cf_idx = next(
        i for i, v in enumerate(out["vertices"])
        if v.get("kind") == "ControlFlow" and v.get("name", "").startswith("assert_")
    )
    v_in_idx = next(
        i for i, v in enumerate(out["vertices"])
        if v.get("kind") == "IO"
    )
    data_edges = [
        e for e in out["edges"]
        if e["from"] == assert_cf_idx and e["to"] == v_in_idx and e["kind"] == "Data"
    ]
    assert len(data_edges) == 1


def test_resolve_predicate_index_rejects_multi_probe_expr():
    """When the exprRef guardRef touches TWO probe variables, predStmtRef must
    stay unset -- no single predStmtRef slot can represent a multi-probe guard."""
    variables = {
        "v_probe1": {"typeRef": "bool", "bindKind": "probe", "ownerScopeRef": "X",
                     "representations": {"chisel": {"name": "p1"}}},
        "v_probe2": {"typeRef": "bool", "bindKind": "probe", "ownerScopeRef": "X",
                     "representations": {"chisel": {"name": "p2"}}},
        "v_target": {"typeRef": "u8", "bindKind": "reg", "ownerScopeRef": "X",
                     "representations": {"chisel": {"name": "r"}}},
    }
    expressions = {
        "both": {"opcode": "||",
                 "operands": [{"varRef": "v_probe1"}, {"varRef": "v_probe2"}]},
    }
    body = [{"kind": "block", "guardRef": "both", "body": [
        {"kind": "connect", "varRef": "v_target",
         "valueRef": {"constant": 0}}]}]
    doc = _doc(
        variables=variables,
        expressions=expressions,
        scopes={"X": {"name": "X", "variableRefs": list(variables.keys()),
                       "body": body}},
    )
    out = pdg_convert(doc)
    block_cfg = out["cfg"][0]
    assert block_cfg.get("predStmtRef") is None


# ---------------------------------------------------------------------------
# _endpoint_to_vertices / _project_explicit_edges: compound exprRef fan-out (FU4.8)
# ---------------------------------------------------------------------------


def test_project_explicit_edges_fans_out_compound_exprref_endpoint():
    """A Sec.10 edge whose `from` is an exprRef with two varRef operands must
    emit one Data edge per constituent varRef (FU4.8)."""
    variables = {
        "a":   _port("a"),
        "b":   _port("b"),
        "out": _port("out", direction="output"),
    }
    expressions = {
        "sum": {"opcode": "+", "operands": [{"varRef": "a"}, {"varRef": "b"}]},
    }
    doc = _doc(
        variables=variables,
        expressions=expressions,
        scopes={"X": {"name": "X", "variableRefs": list(variables.keys()),
                       "body": []}},
        dataflow={"edges": [
            {"from": {"exprRef": "sum"}, "to": {"varRef": "out"}, "kind": "Data"},
        ]},
    )
    out = pdg_convert(doc)
    data_edges = [e for e in out["edges"] if e["kind"] == "Data"]
    assert len(data_edges) == 2
    # IO vertices get name "input_<chisel-name>" / "output_<chisel-name>".
    a_idx = next(i for i, v in enumerate(out["vertices"])
                 if v.get("name") == "input_a")
    b_idx = next(i for i, v in enumerate(out["vertices"])
                 if v.get("name") == "input_b")
    out_idx = next(i for i, v in enumerate(out["vertices"])
                   if v.get("name") == "output_out")
    frm_set = {e["from"] for e in data_edges}
    to_set = {e["to"] for e in data_edges}
    assert frm_set == {a_idx, b_idx}
    assert to_set == {out_idx}


def test_project_explicit_edges_compound_exprref_dedupes_repeated_var():
    """`sum = a + a` must produce only one a->out edge, not two (FU4.8)."""
    variables = {
        "a":   _port("a"),
        "out": _port("out", direction="output"),
    }
    expressions = {
        "sum": {"opcode": "+", "operands": [{"varRef": "a"}, {"varRef": "a"}]},
    }
    doc = _doc(
        variables=variables,
        expressions=expressions,
        scopes={"X": {"name": "X", "variableRefs": list(variables.keys()),
                       "body": []}},
        dataflow={"edges": [
            {"from": {"exprRef": "sum"}, "to": {"varRef": "out"}, "kind": "Data"},
        ]},
    )
    out = pdg_convert(doc)
    data_edges = [e for e in out["edges"] if e["kind"] == "Data"]
    assert len(data_edges) == 1


def test_project_explicit_edges_skips_self_edge_after_fanout():
    """When an exprRef endpoint includes the same variable as the target,
    the self-edge (from == to) must be skipped (FU4.8)."""
    variables = {
        "a":   _port("a"),
        "b":   _port("b"),
    }
    expressions = {
        "sum": {"opcode": "+", "operands": [{"varRef": "a"}, {"varRef": "b"}]},
    }
    # Edge from {exprRef: sum} to {varRef: a} -- the 'a' constituent maps to
    # the same vertex as the 'to', producing a self-edge that must be dropped.
    doc = _doc(
        variables=variables,
        expressions=expressions,
        scopes={"X": {"name": "X", "variableRefs": list(variables.keys()),
                       "body": []}},
        dataflow={"edges": [
            {"from": {"exprRef": "sum"}, "to": {"varRef": "a"}, "kind": "Data"},
        ]},
    )
    out = pdg_convert(doc)
    data_edges = [e for e in out["edges"] if e["kind"] == "Data"]
    # Only b->a should survive; a->a (self-edge) must be dropped.
    assert len(data_edges) == 1
    a_idx = next(i for i, v in enumerate(out["vertices"]) if v.get("name") == "input_a")
    b_idx = next(i for i, v in enumerate(out["vertices"]) if v.get("name") == "input_b")
    assert data_edges[0]["from"] == b_idx
    assert data_edges[0]["to"] == a_idx


def test_project_explicit_edges_single_varref_endpoint_still_works():
    """Regression: single-varRef endpoint (the pre-existing path) continues
    to emit exactly one edge after the fan-out refactor (FU4.8)."""
    variables = {
        "a": _port("a"),
        "b": _port("b", direction="output"),
    }
    doc = _doc(
        variables=variables,
        scopes={"X": {"name": "X", "variableRefs": list(variables.keys()),
                       "body": []}},
        dataflow={"edges": [
            {"from": {"varRef": "a"}, "to": {"varRef": "b"}, "kind": "Data"},
        ]},
    )
    out = pdg_convert(doc)
    data_edges = [e for e in out["edges"] if e["kind"] == "Data"]
    assert len(data_edges) == 1
    assert data_edges[0]["kind"] == "Data"


# ---------------------------------------------------------------------------
# T11: falseBranch for when/otherwise pairs
# ---------------------------------------------------------------------------

def test_convert_emits_false_branch_for_negated_sibling():
    """when/otherwise: the negated sibling is absorbed into falseBranch of the
    first CF entry — not emitted as a separate top-level CFG record."""
    import json
    import pathlib
    fixture_path = (
        pathlib.Path(__file__).parent / "fixtures" / "uhdi" /
        "counter_with_otherwise.uhdi.json"
    )
    uhdi = json.loads(fixture_path.read_text())
    out = pdg_convert(uhdi)

    cfg = out["cfg"]

    # Find the CF entry for the when/otherwise block (guardRef = var_Counter_reset,
    # not negated). It must have a non-None falseBranch.
    cf_vertices = [v for v in out["vertices"] if v.get("kind") == "ControlFlow"]
    # There should be exactly one CF vertex (for the single when/otherwise pair).
    assert len(cf_vertices) == 1, f"expected 1 CF vertex, got {len(cf_vertices)}"

    # The cfg list must contain exactly one block-level entry (the when/otherwise
    # pair is one record), plus one connect entry for q := r.
    block_entries = [e for e in cfg if "trueBranch" in e or "falseBranch" in e]
    assert len(block_entries) == 1, (
        f"negated sibling must NOT appear as a separate top-level cfg entry; "
        f"got {len(block_entries)} block entries"
    )

    block_entry = block_entries[0]
    assert block_entry.get("falseBranch") is not None, (
        "falseBranch must be non-None for a when/otherwise pair"
    )
    assert len(block_entry["falseBranch"]) == 1, (
        "falseBranch should contain the connect from the otherwise body"
    )
