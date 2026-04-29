"""Pytest setup for the bench: sys.path, toolchain fixture, scala-cli/firtool skip-marking."""
from __future__ import annotations

import pathlib
import shutil
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parent.parent
_SRC = str(_REPO / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


@pytest.fixture(scope="session")
def toolchain():
    """Resolve binary paths once per session; reused across (fixture, target) cells."""
    from uhdi_bench.runner import discover_toolchain
    tc = discover_toolchain()
    if not tc.firtool.is_file():
        pytest.skip(f"firtool not found at {tc.firtool}; set FIRTOOL=...")
    return tc


def pytest_collection_modifyitems(config, items):
    """Skip scala_cli-marked tests with a clear reason rather than crashing in subprocess.run."""
    if shutil.which("scala-cli") is None:
        skip = pytest.mark.skip(
            reason="scala-cli not on PATH; install from "
                   "https://scala-cli.virtuslab.org/")
        for item in items:
            if "scala_cli" in item.keywords:
                item.add_marker(skip)
