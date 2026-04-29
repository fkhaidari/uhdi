"""CLI entry: `python -m uhdi_to_hgdb_json INPUT.uhdi.json [-o OUT.json]`.

Thin shim around `uhdi_common.cli.main_for_backend`; same flag
contract as the other converters (--validate, --quiet, -o)."""
from __future__ import annotations

import sys

from uhdi_common.cli import main_for_backend

from .convert import HGDBJsonBackend


def main() -> int:
    return main_for_backend(HGDBJsonBackend())


if __name__ == "__main__":
    sys.exit(main())
