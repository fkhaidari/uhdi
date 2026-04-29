"""Dump (ours, native) pairs for one fixture as JSON files for `code --diff`."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from . import compile as compile_mod
from .runner import discover_toolchain, run_target

# Mirrors test_pipeline._TARGET_TO_PIPELINE (kept here so this CLI is pytest-free).
_TARGET_TO_PIPELINE = {
    "tywaves":     "tywaves",
    "hgdb_circt":  "hgdb",
    "hgdb_firrtl": "hgdb",
}

_DEFAULT_OUT = pathlib.Path("/tmp/bench-diff")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Dump (ours, native) pairs for one fixture as JSON files.")
    p.add_argument("scala", type=pathlib.Path,
                   help="path to bench/fixtures/<Name>.scala")
    p.add_argument("--target", choices=sorted(_TARGET_TO_PIPELINE),
                   help="dump just this target (default: all 3)")
    p.add_argument("-o", "--out", type=pathlib.Path, default=_DEFAULT_OUT,
                   help=f"output dir (default: {_DEFAULT_OUT})")
    args = p.parse_args(argv)

    if not args.scala.is_file():
        print(f"fixture not found: {args.scala}", file=sys.stderr)
        return 2

    targets = ([args.target] if args.target
               else sorted(_TARGET_TO_PIPELINE))
    tc = discover_toolchain()
    out_root = args.out / args.scala.stem
    out_root.mkdir(parents=True, exist_ok=True)

    rc = 0
    for target in targets:
        pipeline = compile_mod.get(_TARGET_TO_PIPELINE[target])
        try:
            fir = compile_mod.compile_for(args.scala, pipeline)
            ours, native = run_target(fir, target, tc)
        except (RuntimeError, compile_mod.CompileError) as e:
            print(f"{target:14} skip: {e}", file=sys.stderr)
            rc = 1
            continue
        fmt = lambda d: json.dumps(d, indent=2, sort_keys=True)
        ours_path = out_root / f"{target}.ours.json"
        native_path = out_root / f"{target}.native.json"
        ours_path.write_text(fmt(ours))
        native_path.write_text(fmt(native))
        print(f"{target:14}  code --diff {ours_path} {native_path}")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
