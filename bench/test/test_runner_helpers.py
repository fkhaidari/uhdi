"""Unit tests for runner helpers that don't need a toolchain."""
from __future__ import annotations

import os
import pathlib
import sys

import pytest
from uhdi_bench.runner import _pick_native_lib, _prepend_pythonpath


def test_prepend_pythonpath_keeps_user_value(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/user/site")
    out = _prepend_pythonpath(pathlib.Path("/a"), pathlib.Path("/b"))
    assert out == os.pathsep.join(["/a", "/b", "/user/site"])


def test_prepend_pythonpath_when_unset(monkeypatch):
    monkeypatch.delenv("PYTHONPATH", raising=False)
    out = _prepend_pythonpath(pathlib.Path("/a"), pathlib.Path("/b"))
    assert out == os.pathsep.join(["/a", "/b"])


def test_prepend_pythonpath_when_empty(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "")
    out = _prepend_pythonpath(pathlib.Path("/a"))
    assert out == "/a"


def _abi_tag() -> str:
    return f"cpython-{sys.version_info.major}{sys.version_info.minor}"


def _other_abi_tag() -> str:
    return f"cpython-{sys.version_info.major}{sys.version_info.minor + 1}"


def test_pick_native_lib_filters_mismatched_abi(tmp_path):
    build = tmp_path / "build"
    build.mkdir()
    (build / f"lib.linux-x86_64-{_other_abi_tag()}").mkdir()
    matching = build / f"lib.linux-x86_64-{_abi_tag()}"
    matching.mkdir()
    assert _pick_native_lib(tmp_path) == matching


def test_pick_native_lib_raises_when_no_match(tmp_path):
    build = tmp_path / "build"
    build.mkdir()
    (build / f"lib.linux-x86_64-{_other_abi_tag()}").mkdir()
    with pytest.raises(RuntimeError, match=_abi_tag()):
        _pick_native_lib(tmp_path)
