"""End-to-end pipeline tests with manifest-driven expected deltas."""
from __future__ import annotations

import pathlib

import pytest
from uhdi_bench.compile import CompileError, compile_for
from uhdi_bench.compile import get as get_pipeline
from uhdi_bench.manifest import CellExpectations, load_manifest
from uhdi_bench.runner import run_target
from uhdi_common.diff import diff_dicts, format_deltas

_BENCH = pathlib.Path(__file__).resolve().parent.parent
_FIXTURES = _BENCH / "fixtures"
_MANIFEST_PATH = _BENCH / "manifest.toml"


def _scala_fixtures() -> list[pathlib.Path]:
    return sorted(_FIXTURES.glob("*.scala"))


_TARGET_TO_PIPELINE = {
    "tywaves":     "tywaves",
    "hgdb_circt":  "hgdb",
    "hgdb_firrtl": "hgdb",
}

# Loaded once; missing entry -> empty CellExpectations (strict match).
_MANIFEST = load_manifest(_MANIFEST_PATH)


def _expectations_for(fixture_stem: str, target: str) -> CellExpectations:
    return _MANIFEST.get(
        (fixture_stem, target),
        CellExpectations(fixture=fixture_stem, target=target))


@pytest.mark.scala_cli
@pytest.mark.parametrize("target", sorted(_TARGET_TO_PIPELINE))
@pytest.mark.parametrize("scala", _scala_fixtures(),
                         ids=lambda p: p.stem)
def test_pipeline(scala: pathlib.Path, target: str, toolchain) -> None:
    pipeline_name = _TARGET_TO_PIPELINE[target]
    pipeline = get_pipeline(pipeline_name)
    try:
        fir = compile_for(scala, pipeline)
    except CompileError as e:
        pytest.skip(f"scala-cli failed for {scala.name} under "
                    f"{pipeline_name!r}: {e}")
    except RuntimeError as e:
        pytest.skip(str(e))

    try:
        ours, native = run_target(fir, target, toolchain)
    except RuntimeError as e:
        pytest.skip(f"{target} target not runnable: {e}")

    deltas = diff_dicts(ours, native, mode="superset")
    expectations = _expectations_for(scala.stem, target)
    matched, surprises, unused = expectations.classify(deltas)

    if surprises:
        pytest.fail(
            f"{scala.stem} -> {target}: {len(surprises)} unexpected "
            f"delta(s) (surprise -- regression OR new format-design "
            f"choice; either fix the converter or add to "
            f"bench/manifest.toml with a reason)\n"
            f"{format_deltas(surprises)}\n"
            f"\n{len(matched)} delta(s) matched manifest expectations.")

    if unused:
        # XPASS: tracked divergence resolved; force manifest update.
        bullets = "\n".join(
            f"  - path={exp.path or '(regex)'} "
            f"path_regex={exp.path_regex or '-'} "
            f"reason={exp.reason!r}"
            for exp in unused)
        pytest.fail(
            f"{scala.stem} -> {target}: {len(unused)} expected "
            f"delta(s) no longer occur (manifest stale -- gap closed, "
            f"drop these entries from bench/manifest.toml):\n"
            f"{bullets}")
