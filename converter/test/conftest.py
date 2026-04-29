"""Shared pytest setup: adds src/ to sys.path (belt-and-suspenders for IDE runners that ignore pyproject's pythonpath)."""
from __future__ import annotations

import pathlib
import sys

_REPO = pathlib.Path(__file__).resolve().parent.parent
_SRC = str(_REPO / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
