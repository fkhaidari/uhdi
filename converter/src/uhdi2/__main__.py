"""`uhdi2` CLI: upgrade / validate / to-hgldd subcommands."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Optional

from uhdi_common.context import ConversionError

from .to_hgldd import convert as to_hgldd_convert
from .upgrade import upgrade as upgrade_doc
from .validate import iter_errors


def _read_json(path: pathlib.Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _write(result: dict, output: Optional[pathlib.Path]) -> None:
    text = json.dumps(result, indent=2) + "\n"
    if output is not None:
        output.write_text(text, encoding="utf-8")
        print(f"wrote {output}")
    else:
        sys.stdout.write(text)


def _cmd_upgrade(args: argparse.Namespace) -> int:
    doc_v1 = _read_json(args.input)
    try:
        doc_v2 = upgrade_doc(doc_v1)
    except ConversionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    _write(doc_v2, args.output)
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    doc_v2 = _read_json(args.input)
    errors = list(iter_errors(doc_v2))
    if not errors:
        print(f"{args.input}: valid")
        return 0
    for e in errors:
        loc = "/".join(str(p) for p in e.absolute_path) or "<root>"
        print(f"error: {loc}: {e.message}", file=sys.stderr)
    return 2


def _cmd_to_hgldd(args: argparse.Namespace) -> int:
    doc_v2 = _read_json(args.input)
    try:
        result = to_hgldd_convert(doc_v2)
    except ConversionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    _write(result, args.output)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="uhdi2", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_up = sub.add_parser("upgrade", help="Upgrade a UHDI 1.0 document to UHDI 2.0.")
    p_up.add_argument("input", type=pathlib.Path)
    p_up.add_argument("-o", "--output", type=pathlib.Path)
    p_up.set_defaults(func=_cmd_upgrade)

    p_val = sub.add_parser("validate", help="Schema-validate a UHDI 2.0 document.")
    p_val.add_argument("input", type=pathlib.Path)
    p_val.set_defaults(func=_cmd_validate)

    p_hg = sub.add_parser("to-hgldd", help="Convert a UHDI 2.0 document to HGLDD.")
    p_hg.add_argument("input", type=pathlib.Path)
    p_hg.add_argument("-o", "--output", type=pathlib.Path)
    p_hg.set_defaults(func=_cmd_to_hgldd)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
