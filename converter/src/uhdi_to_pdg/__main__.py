"""CLI entry: `uhdi-to-pdg INPUT.uhdi.json [-o OUT.pdg.json]
                  [--require-dataflow | --derive-dataflow]`.

Adds two PDG-specific flags on top of the shared scaffold (uhdi-spec.md
Sec.15.5.4):

  --require-dataflow   fail if Sec.10 is absent in the input
  --derive-dataflow    synthesise a best-effort graph from Sec.5/Sec.7
                       (default; same as omitting both flags)

Mutually exclusive."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from uhdi_common.validate import validate_or_exit

from .convert import PDGConversionError, convert


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="uhdi-to-pdg",
        description="Convert a uhdi document to PDG (chiseltrace) JSON.")
    p.add_argument("input", type=pathlib.Path,
                   help="Path to the uhdi JSON document.")
    p.add_argument("-o", "--output", type=pathlib.Path,
                   help="Output path. Defaults to stdout.")
    p.add_argument("--validate", action="store_true",
                   help="Schema-validate input before converting.")
    p.add_argument("--quiet", action="store_true",
                   help="Suppress success message (requires --output).")
    df = p.add_mutually_exclusive_group()
    df.add_argument("--require-dataflow", action="store_true",
                    help="Error if input lacks Sec.10 dataflow.")
    df.add_argument("--derive-dataflow", action="store_true",
                    help="Synthesise edges from Sec.5/Sec.7 when Sec.10 is absent "
                         "(default).")
    args = p.parse_args(argv)

    try:
        with args.input.open(encoding="utf-8") as f:
            uhdi = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: cannot read {args.input}: {e}", file=sys.stderr)
        return 2

    if args.validate:
        try:
            rc = validate_or_exit(uhdi, args.input)
        except ImportError as e:
            print(f"error: --validate needs jsonschema + referencing "
                  f"installed ({e})", file=sys.stderr)
            return 2
        if rc != 0:
            return rc

    try:
        result = convert(uhdi, require_dataflow=args.require_dataflow)
    except PDGConversionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    text = json.dumps(result, indent=2) + "\n"
    if args.output is not None:
        try:
            args.output.write_text(text, encoding="utf-8")
        except OSError as e:
            print(f"error: cannot write {args.output}: {e}", file=sys.stderr)
            return 2
        if not args.quiet:
            print(f"wrote {args.output}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
