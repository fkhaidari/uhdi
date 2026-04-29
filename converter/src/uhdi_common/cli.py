"""Shared CLI scaffolding for `python -m uhdi_to_<target>`."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Optional

from .backend import Backend
from .context import ConversionError
from .validate import validate_or_exit


def main_for_backend(backend: Backend, argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog=f"python -m uhdi_to_{backend.name}",
        description=(backend.description
                     or f"Convert a uhdi document to {backend.name}."))
    parser.add_argument(
        "input", type=pathlib.Path,
        help="Path to the uhdi JSON document.")
    parser.add_argument(
        "-o", "--output", type=pathlib.Path,
        required=backend.binary_output,
        help="Output path." +
             (" (Required for binary backends.)" if backend.binary_output
              else " Defaults to stdout."))
    parser.add_argument(
        "--validate", action="store_true",
        help="Schema-validate input before converting. Exits 2 on violations.")
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress success message (requires --output).")
    args = parser.parse_args(argv)

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
        result = backend.convert(uhdi, args.output)
    except ConversionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if backend.binary_output:
        if not args.quiet and args.output is not None:
            print(f"wrote {args.output}")
        return 0

    if result is None:
        print(f"error: {backend.name} backend returned None for a "
              f"text-output conversion", file=sys.stderr)
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
