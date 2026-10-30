"""Readers for the UHDI "v2 layout" (`"uhdi": "0.<minor>"`)."""
from __future__ import annotations

import re
from typing import Any, Dict

from uhdi_common.context import ConversionError


class UnsupportedVersion(ConversionError):
    pass


def is_v2(doc: Dict[str, Any]) -> bool:
    """Whether `doc` is in a layout these readers know: any minor of major
    0, since a minor only adds what a reader may ignore."""
    return re.fullmatch(r"0\.[0-9]+", str(doc.get("uhdi", ""))) is not None


def require_v2(doc: Dict[str, Any]) -> None:
    """Raise `UnsupportedVersion` unless `is_v2(doc)`, so a direct call
    with another version fails with a reason, not a KeyError."""
    if not is_v2(doc):
        raise UnsupportedVersion(
            f"unsupported document version {doc.get('uhdi')!r}: "
            f"these readers take \"uhdi\": \"0.<minor>\"")
