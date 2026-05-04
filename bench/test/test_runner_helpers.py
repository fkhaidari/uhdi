"""Unit tests for runner helpers that don't need a toolchain."""
from __future__ import annotations

import os
import pathlib

from uhdi_bench.runner import _prepend_pythonpath


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
