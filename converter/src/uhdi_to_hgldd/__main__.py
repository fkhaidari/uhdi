"""CLI entry point: `python -m uhdi_to_hgldd INPUT.uhdi.json [-o OUTPUT.dd]`.

All of the argparse + validate + write boilerplate lives in
`uhdi_common.cli`; this module is a thin shim that supplies the
backend instance.  See `uhdi_common.cli.main_for_backend` for the
flag contract.
"""
from __future__ import annotations

import sys

from uhdi_common.cli import main_for_backend

from .convert import HGLDDBackend


def main() -> int:
    return main_for_backend(HGLDDBackend())


if __name__ == "__main__":
    sys.exit(main())
