"""Unit tests for uhdi_common.context.BaseContext."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Set

import pytest
from uhdi_common.context import BaseContext, ConversionError


def _doc(**overrides):
    base = {
        "format": {"name": "uhdi", "version": "1.0"},
        "representations": {"chisel": {}, "verilog": {}},
        "roles": {"authoring": "chisel", "simulation": "verilog"},
        "top": ["Top"],
        "types": {},
        "expressions": {},
        "variables": {},
        "scopes": {},
    }
    base.update(overrides)
    return base


def test_from_uhdi_extracts_roles_from_document():
    ctx = BaseContext.from_uhdi(_doc(roles={"authoring": "scala",
                                            "simulation": "vhdl"}))
    assert ctx.authoring_repr == "scala"
    assert ctx.simulation_repr == "vhdl"


def test_from_uhdi_falls_back_to_defaults_when_roles_missing():
    ctx = BaseContext.from_uhdi(_doc(roles={}))
    assert ctx.authoring_repr == "chisel"
    assert ctx.simulation_repr == "verilog"


def test_from_uhdi_rejects_non_uhdi_document():
    bogus = {"format": {"name": "hgldd"}, "objects": []}
    with pytest.raises(ConversionError, match="not a uhdi document"):
        BaseContext.from_uhdi(bogus)


def test_from_uhdi_rejects_missing_format_block():
    with pytest.raises(ConversionError, match="not a uhdi document"):
        BaseContext.from_uhdi({"top": []})


def test_pool_accessors_default_to_empty_dict():
    """Getters never return None; callers iterate without a None-guard."""
    ctx = BaseContext.from_uhdi({"format": {"name": "uhdi"},
                                 "roles": {}})
    assert ctx.types == {}
    assert ctx.variables == {}
    assert ctx.scopes == {}
    assert ctx.expressions == {}
    assert ctx.representations == {}


def test_pool_accessor_treats_explicit_null_as_empty():
    """Some emitters write `"variables": null`; accessor must normalise to empty dict."""
    ctx = BaseContext.from_uhdi(_doc(variables=None))
    assert ctx.variables == {}


def test_pool_accessors_return_live_view():
    """Accessors return the underlying dict, not a copy (cheap repeated lookups during a walk)."""
    doc = _doc(variables={"v": {"name": "x"}})
    ctx = BaseContext.from_uhdi(doc)
    assert ctx.variables["v"]["name"] == "x"
    doc["variables"]["v"]["name"] = "y"
    assert ctx.variables["v"]["name"] == "y"


def test_subclass_can_add_dataclass_fields():
    """from_uhdi must forward **extra to the subclass constructor (HGLDD/hgdb extra state)."""

    @dataclass
    class _MyCtx(BaseContext):
        counter: int = 0
        seen: Set[str] = field(default_factory=set)

    ctx = _MyCtx.from_uhdi(_doc(), counter=7, seen={"a"})
    assert ctx.counter == 7
    assert ctx.seen == {"a"}
    assert ctx.uhdi["format"]["name"] == "uhdi"
    assert ctx.authoring_repr == "chisel"
