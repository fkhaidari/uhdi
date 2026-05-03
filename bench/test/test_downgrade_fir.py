"""Unit tests for the FIRRTL 4.x -> 1.x downgrader."""
from __future__ import annotations

import pytest
from uhdi_bench._downgrade_fir import DowngradeError, downgrade


def test_strips_firrtl_version_header():
    out = downgrade("FIRRTL version 4.0.0\nmodule M :\n")
    assert "FIRRTL version" not in out


def test_drops_public_modifier():
    assert "module M :" in downgrade("public module M :\n")
    assert "extmodule E :" in downgrade("public extmodule E :\n")


def test_unsupported_construct_raises_downgrade_error():
    # SystemExit would propagate past pytest since bench runs in-process.
    with pytest.raises(DowngradeError, match="intrinsic"):
        downgrade("    intrinsic foo : UInt<8>\n")


def test_banned_token_in_comment_does_not_trigger():
    out = downgrade("    ; intrinsic was removed in 1.x\n")
    assert "intrinsic was removed" in out


def test_banned_token_substring_does_not_trigger():
    out = downgrade("    node x = redefine\n")
    assert "redefine" in out


def test_connect_lowered_to_arrow():
    out = downgrade("    connect a, b\n")
    assert "a <= b" in out
