"""Unit tests for the shared expression-DAG walker."""
from __future__ import annotations

import pytest
from uhdi_common.context import BaseContext, ConversionError
from uhdi_common.expressions import walk


class _MyError(ConversionError):
    pass


def _ctx(expressions=None):
    return BaseContext.from_uhdi({
        "format": {"name": "uhdi"},
        "representations": {"chisel": {}, "verilog": {}},
        "expressions": expressions or {},
    })


def _passthrough(op):
    """Return the operand verbatim so tests can assert which terminal fired."""
    return ("terminal", op)


def _opnode_marker(op, seen):
    return ("opnode", op.get("opcode"), seen)


# ---- terminal dispatch ----------------------------------------------------


def test_walk_dispatches_non_dict_to_terminal():
    assert walk(42, _ctx(),
                on_terminal=_passthrough,
                on_opnode=_opnode_marker) == ("terminal", 42)


def test_walk_dispatches_unknown_dict_shape_to_terminal():
    op = {"unrelated": 1}
    assert walk(op, _ctx(),
                on_terminal=_passthrough,
                on_opnode=_opnode_marker) == ("terminal", op)


def test_walk_dispatches_sig_name_to_terminal():
    op = {"sigName": "wire"}
    assert walk(op, _ctx(),
                on_terminal=_passthrough,
                on_opnode=_opnode_marker) == ("terminal", op)


# ---- opnode dispatch -----------------------------------------------------


def test_walk_dispatches_opnode_with_empty_seen():
    op = {"opcode": "+", "operands": [{"sigName": "a"}]}
    assert walk(op, _ctx(),
                on_terminal=_passthrough,
                on_opnode=_opnode_marker) == ("opnode", "+", set())


# ---- exprRef resolution + cycle guard ------------------------------------


def test_walk_follows_expr_ref_into_pool():
    ctx = _ctx(expressions={
        "e": {"opcode": "+", "operands": [{"sigName": "a"}]},
    })
    out = walk({"exprRef": "e"}, ctx,
               on_terminal=_passthrough,
               on_opnode=_opnode_marker)
    # seen contains the followed ref by the time on_opnode fires.
    assert out == ("opnode", "+", {"e"})


def test_walk_unresolvable_expr_ref_routes_to_terminal():
    """Backends decide their own empty-shape fallback."""
    op = {"exprRef": "ghost"}
    assert walk(op, _ctx(),
                on_terminal=_passthrough,
                on_opnode=_opnode_marker) == ("terminal", op)


def test_walk_raises_default_exc_on_cycle():
    ctx = _ctx(expressions={
        "a": {"opcode": "+", "operands": [{"exprRef": "b"}]},
        "b": {"opcode": "-", "operands": [{"exprRef": "a"}]},
    })
    with pytest.raises(ConversionError, match="cycle"):
        # Walk just the structural part; the on_opnode here re-enters
        # walk on each operand, mirroring how real backends recurse.
        def on_opnode(op, seen):
            for o in op.get("operands") or []:
                walk(o, ctx, on_terminal=_passthrough,
                     on_opnode=on_opnode, seen=seen)
        walk({"exprRef": "a"}, ctx,
             on_terminal=_passthrough, on_opnode=on_opnode)


def test_walk_raises_backend_specific_exc_when_supplied():
    """exc_type lets each backend keep its public exception class."""
    ctx = _ctx(expressions={
        "a": {"opcode": "+", "operands": [{"exprRef": "a"}]},
    })

    def on_opnode(op, seen):
        for o in op.get("operands") or []:
            walk(o, ctx, on_terminal=_passthrough, on_opnode=on_opnode,
                 exc_type=_MyError, seen=seen)

    with pytest.raises(_MyError, match="cycle"):
        walk({"exprRef": "a"}, ctx,
             on_terminal=_passthrough, on_opnode=on_opnode,
             exc_type=_MyError)


def test_walk_seen_is_per_path_not_global():
    """Diamond: A -> B and A -> C, both ending at D.  Visiting A must NOT
    raise just because D was already seen on the sibling branch."""
    ctx = _ctx(expressions={
        "A": {"opcode": "and", "operands": [{"exprRef": "B"}, {"exprRef": "C"}]},
        "B": {"opcode": "id",  "operands": [{"exprRef": "D"}]},
        "C": {"opcode": "id",  "operands": [{"exprRef": "D"}]},
        "D": {"opcode": "leaf", "operands": []},
    })

    def on_opnode(op, seen):
        for o in op.get("operands") or []:
            walk(o, ctx, on_terminal=_passthrough,
                 on_opnode=on_opnode, seen=seen)
        return op.get("opcode")

    # Should resolve cleanly, no cycle error.
    walk({"exprRef": "A"}, ctx,
         on_terminal=_passthrough, on_opnode=on_opnode)
