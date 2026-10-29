"""Readers for the UHDI "v2 layout" (`"uhdi": "0.<minor>"`)."""
from __future__ import annotations

import re
from typing import Any, Dict


def is_v2(doc: Dict[str, Any]) -> bool:
    """Whether `doc` is in a layout these readers know: any minor of major
    0, since a minor only adds what a reader may ignore."""
    return re.fullmatch(r"0\.[0-9]+", str(doc.get("uhdi", ""))) is not None
