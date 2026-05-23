#!/usr/bin/env python3
"""Compare one .scala fixture across all pipeline targets.

Usage:
    python compare_fixture.py <fixture.scala> [--targets tywaves,hgdb_circt,...] [-o OUTDIR]

Prints a markdown matrix (target × status) and writes ours/native JSON pairs
to OUTDIR (default: /tmp/uhdi-compare-<stem>/) for `code --diff`.

Targets: tywaves, hgdb_circt, hgdb_firrtl, pdg
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import traceback

# Make sure both bench and converter packages are importable when run directly.
_BENCH = pathlib.Path(__file__).resolve().parent
_REPO  = _BENCH.parent
sys.path.insert(0, str(_BENCH / "src"))
sys.path.insert(0, str(_REPO / "converter" / "src"))

from uhdi_bench.compile import CompileError, compile_for, get as get_pipeline
from uhdi_bench.manifest import CellExpectations, load_manifest
from uhdi_bench.runner import Toolchain, discover_toolchain, run_target
from uhdi_common.diff import Delta, diff_dicts, format_deltas

_MANIFEST_PATH = _BENCH / "manifest.toml"

_TARGET_TO_PIPELINE: dict[str, str] = {
    "tywaves":     "uhdi",   # ours=uhdi->hgldd; native uses tywaves pipeline+rameloni firtool
    "hgdb_circt":  "hgdb",
    "hgdb_firrtl": "hgdb",
    "pdg":         "uhdi",
}

_ALL_TARGETS = list(_TARGET_TO_PIPELINE)

# ANSI colours (stripped when not a tty).
def _c(code: str, text: str) -> str:
    if sys.stdout.isatty():
        return f"\033[{code}m{text}\033[0m"
    return text

_GREEN  = lambda t: _c("32", t)
_RED    = lambda t: _c("31", t)
_YELLOW = lambda t: _c("33", t)
_GREY   = lambda t: _c("90", t)


def _write_json(path: pathlib.Path, obj: object) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def run_one(
    scala: pathlib.Path,
    target: str,
    toolchain: Toolchain,
    outdir: pathlib.Path,
    manifest: dict,
) -> dict:
    """Run one (scala, target) cell. Returns a result dict."""
    pipeline_name = _TARGET_TO_PIPELINE[target]
    result: dict = {"target": target, "pipeline": pipeline_name, "status": "?"}

    try:
        pipeline = get_pipeline(pipeline_name)
        fir = compile_for(scala, pipeline)
    except CompileError as e:
        result["status"] = "skip"
        result["reason"] = f"scala-cli compile failed: {e}"
        return result
    except RuntimeError as e:
        result["status"] = "skip"
        result["reason"] = str(e)
        return result

    # tywaves target: native --emit-hgldd needs tywaves-pipeline FIR (rameloni firtool).
    # fir is already uhdi-pipeline (from _TARGET_TO_PIPELINE["tywaves"]="uhdi").
    tywaves_fir = None
    if target == "tywaves":
        try:
            tywaves_fir = compile_for(scala, get_pipeline("tywaves"))
        except (CompileError, RuntimeError) as e:
            result["status"] = "skip"
            result["reason"] = f"tywaves pipeline compile failed (needed for native side): {e}"
            return result

    try:
        ours, native = run_target(fir, target, toolchain,
                                  scala_path=scala if target == "pdg" else None,
                                  tywaves_fir=tywaves_fir)
    except RuntimeError as e:
        result["status"] = "skip"
        result["reason"] = str(e)
        return result
    except Exception:
        result["status"] = "error"
        result["reason"] = traceback.format_exc(limit=5)
        return result

    # Write JSON pair for code --diff.
    ours_path   = outdir / f"{target}.ours.json"
    native_path = outdir / f"{target}.native.json"
    _write_json(ours_path, ours)
    _write_json(native_path, native)
    result["ours_path"]   = str(ours_path)
    result["native_path"] = str(native_path)

    deltas: list[Delta] = diff_dicts(ours, native, mode="superset")
    result["delta_count"] = len(deltas)

    cell_key = (scala.stem, target)
    expectations: CellExpectations = manifest.get(
        cell_key,
        CellExpectations(fixture=scala.stem, target=target),
    )
    matched, surprises, unused = expectations.classify(deltas)

    result["matched"]   = len(matched)
    result["surprises"] = len(surprises)
    result["unused"]    = len(unused)
    result["deltas"]    = format_deltas(deltas) if deltas else ""

    if surprises:
        result["status"] = "fail-surprise"
        result["surprise_details"] = format_deltas(surprises)
    elif unused:
        result["status"] = "fail-stale"
        result["unused_details"] = [
            f"path={e.path or '(regex)'} regex={e.path_regex or '-'} reason={e.reason!r}"
            for e in unused
        ]
    else:
        result["status"] = "pass"

    return result


def print_matrix(stem: str, results: list[dict]) -> None:
    print(f"\n## {stem}\n")
    col_w = max(len(r["target"]) for r in results)
    header = f"{'target':<{col_w}}  status           deltas  matched  surprises"
    print(header)
    print("-" * len(header))
    for r in results:
        status = r["status"]
        if status == "pass":
            status_str = _GREEN(f"{'pass':<16}")
        elif status.startswith("fail"):
            status_str = _RED(f"{status:<16}")
        elif status == "skip":
            status_str = _YELLOW(f"{'skip':<16}")
        else:
            status_str = _GREY(f"{status:<16}")

        deltas   = r.get("delta_count", "-")
        matched  = r.get("matched",     "-")
        surprises = r.get("surprises",  "-")
        print(f"{r['target']:<{col_w}}  {status_str}  {deltas!s:>6}  {matched!s:>7}  {surprises!s:>9}")

    print()

    # Details for failures.
    for r in results:
        if r["status"] == "fail-surprise":
            print(f"### {r['target']}: unexpected deltas")
            print(r.get("surprise_details", ""))
        elif r["status"] == "fail-stale":
            print(f"### {r['target']}: stale manifest entries (gap closed)")
            for line in r.get("unused_details", []):
                print(f"  - {line}")
        elif r["status"] == "skip":
            print(_GREY(f"[skip] {r['target']}: {r.get('reason', '')}"))
        elif r["status"] == "error":
            print(_RED(f"[error] {r['target']}:\n{r.get('reason', '')}"))

    # code --diff hints.
    diffable = [r for r in results if "ours_path" in r]
    if diffable:
        print("\n### JSON pairs for code --diff")
        for r in diffable:
            print(f"  code --diff {r['ours_path']} {r['native_path']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scala", type=pathlib.Path, help=".scala fixture path")
    ap.add_argument("--targets", default=",".join(_ALL_TARGETS),
                    help=f"comma-separated targets (default: all). choices: {_ALL_TARGETS}")
    ap.add_argument("-o", "--outdir", type=pathlib.Path, default=None,
                    help="directory for ours/native JSON (default: /tmp/uhdi-compare-<stem>/)")
    ap.add_argument("--firtool",         default=None, help="override FIRTOOL binary")
    ap.add_argument("--hgdb-circt",      default=None, help="override HGDB_CIRCT_FIRTOOL")
    ap.add_argument("--hgdb-firrtl-jar", default=None, help="override HGDB_FIRRTL_JAR")
    ap.add_argument("--hgdb-py",         default=None, help="override HGDB_PY")
    args = ap.parse_args(argv)

    scala: pathlib.Path = args.scala.resolve()
    if not scala.is_file():
        print(f"error: {scala} not found", file=sys.stderr)
        return 1

    targets = [t.strip() for t in args.targets.split(",") if t.strip()]
    unknown = [t for t in targets if t not in _ALL_TARGETS]
    if unknown:
        print(f"error: unknown targets: {unknown}; choices: {_ALL_TARGETS}",
              file=sys.stderr)
        return 1

    outdir = args.outdir or pathlib.Path(f"/tmp/uhdi-compare-{scala.stem}/")
    outdir.mkdir(parents=True, exist_ok=True)

    # Apply CLI overrides via env vars that runner.discover_toolchain() reads.
    import os
    if args.firtool:         os.environ["FIRTOOL"]             = args.firtool
    if args.hgdb_circt:      os.environ["HGDB_CIRCT_FIRTOOL"]  = args.hgdb_circt
    if args.hgdb_firrtl_jar: os.environ["HGDB_FIRRTL_JAR"]     = args.hgdb_firrtl_jar
    if args.hgdb_py:         os.environ["HGDB_PY"]              = args.hgdb_py

    toolchain = discover_toolchain()
    manifest  = load_manifest(_MANIFEST_PATH)

    print(f"Fixture : {scala}")
    print(f"Targets : {targets}")
    print(f"Outdir  : {outdir}")
    print(f"firtool : {toolchain.firtool}")

    results = []
    for target in targets:
        print(f"\n[{target}] running...", end="", flush=True)
        r = run_one(scala, target, toolchain, outdir, manifest)
        results.append(r)
        status = r["status"]
        print(f" {status}")

    print_matrix(scala.stem, results)

    # Summary exit code: 0 if all pass or skip, 1 if any fail/error.
    bad = [r for r in results if r["status"] not in ("pass", "skip")]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
