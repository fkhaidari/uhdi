"""CLI entry: `python -m uhdi_to_hgdb INPUT.uhdi.json -o OUTPUT.db`.

All of the argparse + validate + write boilerplate lives in
`uhdi_common.cli`; this module is a thin shim that supplies the
backend instance.  See `uhdi_common.cli.main_for_backend` for the
flag contract.
"""
from __future__ import annotations

import sys

from uhdi_common.cli import main_for_backend

from .convert import HGDBBackend


def main() -> int:
    return main_for_backend(HGDBBackend())


if __name__ == "__main__":
    sys.exit(main())
