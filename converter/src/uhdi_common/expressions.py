"""Shared traversal of UHDI's expression DAG.

UHDI represents expressions as a pool keyed by stable id; an operand
carrying `exprRef: X` indirects into that pool.  Both backends walk the
same DAG, but transform it to different output shapes.  This module
factors out the structural invariants -- exprRef resolution and cycle
detection -- so backends supply only the transformation logic."""
from __future__ import annotations

from typing import Any, Callable, Optional, Set, Type

from .context import BaseContext, ConversionError


def walk(operand: Any, ctx: BaseContext, *,
         on_terminal: Callable[[Any], Any],
         on_opnode: Callable[[Any, Set[str]], Any],
         exc_type: Type[ConversionError] = ConversionError,
         seen: Optional[Set[str]] = None) -> Any:
    """Walk an expression operand DAG, dispatching to backend handlers.

    on_terminal(op) handles non-opnode shapes (sigName, constant, varRef,
    bitVector, anything unrecognized); backend dispatches on the keys it
    cares about.  on_opnode(op, seen) handles {opcode, operands} nodes;
    backend recurses into operands itself, threading `seen` so each path
    keeps its own ancestor set.

    exprRef indirections resolve here with cycle detection: a back-edge
    raises `exc_type` so backends keep their public exception class.  An
    unresolvable exprRef (target not in pool) routes to on_terminal so
    backends choose their own empty-shape fallback."""
    if not isinstance(operand, dict):
        return on_terminal(operand)
    if "exprRef" in operand:
        ref = operand["exprRef"]
        seen = set() if seen is None else seen
        if ref in seen:
            raise exc_type(f"cycle in expression graph at exprRef {ref!r}")
        target = ctx.expressions.get(ref)
        if target is None:
            return on_terminal(operand)
        return walk(target, ctx,
                    on_terminal=on_terminal, on_opnode=on_opnode,
                    exc_type=exc_type, seen=seen | {ref})
    if "opcode" in operand:
        return on_opnode(operand, seen if seen is not None else set())
    return on_terminal(operand)
