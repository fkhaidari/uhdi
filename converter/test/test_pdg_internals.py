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


def test_collect_expr_vars_terminates_on_exprref_cycle():
    """Self-referencing exprRef would loop forever without the seen-set guard."""
    doc = _doc(expressions={
        "loop": {"opcode": "id", "operands": [{"exprRef": "loop"}]}})
    ctx = _Ctx.from_uhdi(doc)
    acc: list[str] = []
    _collect_expr_vars(ctx.expressions["loop"], ctx, acc, set())
    assert acc == []


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
    """Table §15.5.1: each uhdi bindKind takes a specific PDG kind."""
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
    """Probes are NOT regular vertices: §15.5.1 puts them in `predicates[]`,
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
    """§15.7: PDG retains the per-edge `clocked` bit; on Definition vertices
    we set it when the underlying bindKind is reg/mem."""
    doc = _doc(
        variables={"r": {"typeRef": "u8", "bindKind": "reg", "ownerScopeRef": "X",
                          "representations": {"chisel": {"name": "r"}}}},
        scopes={"X": {"name": "X", "variableRefs": ["r"], "body": []}},
    )
    out = pdg_convert(doc)
    assert out["vertices"][0]["clocked"] is True


def test_convert_drops_clock_and_reset_edges():
    """chiseltrace's PDGSpecEdgeKind enum has 4 variants; §10 Clock/Reset
    edges aren't representable and must be silently filtered out (§15.7)."""
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
    """Per §15.5.4: --require-dataflow turns "missing §10" into a hard error
    instead of falling back to derivation (the alternative is a less precise
    edge set, which the caller may want to refuse)."""
    with pytest.raises(PDGConversionError, match="§10"):
        pdg_convert(_doc(), require_dataflow=True)


def test_convert_uses_explicit_dataflow_when_present():
    """When the input carries §10 directly, project it -- don't shadow with
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
    """End-to-end §15.5.4 derivation: one `block + connect` should yield
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
    """If §6 adds a bindKind, this test fails until convert.py opts in;
    silent fallthrough to "skip the variable" used to lose mem cells."""
    assert set(_BIND_TO_KIND) == {"port", "wire", "node", "literal", "reg", "mem"}
