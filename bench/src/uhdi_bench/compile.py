"""scala-cli wrapper: compile a Chisel fixture to FIR per pipeline.
Cached by sha256(source + pipeline-config) under bench/.cache/scala-fir/."""
from __future__ import annotations

import dataclasses
import hashlib
import os
import pathlib
import shutil
import subprocess
import sys
from typing import List, Optional


@dataclasses.dataclass(frozen=True)
class Pipeline:
    name: str
    scala_version: str
    chisel_dep: str
    plugin_dep: str
    repositories: tuple = ()


# Local-first defaults (publishLocal'd checkout). Override via env vars.
_TYWAVES = Pipeline(
    name="tywaves",
    scala_version="2.13.14",
    chisel_dep="org.chipsalliance::chisel:6.4.3-tywaves-SNAPSHOT",
    plugin_dep="org.chipsalliance:::chisel-plugin:6.4.3-tywaves-SNAPSHOT",
    repositories=("ivy2Local",),
)

# Shares tywaves SNAPSHOT; override via UHDI_BENCH_UHDI_CHISEL.
_UHDI = Pipeline(
    name="uhdi",
    scala_version="2.13.14",
    chisel_dep="org.chipsalliance::chisel:6.4.3-tywaves-SNAPSHOT",
    plugin_dep="org.chipsalliance:::chisel-plugin:6.4.3-tywaves-SNAPSHOT",
    repositories=("ivy2Local",),
)

_HGDB = Pipeline(
    name="hgdb",
    scala_version="2.13.14",
    chisel_dep="org.chipsalliance::chisel:6.4.0",
    plugin_dep="org.chipsalliance:::chisel-plugin:6.4.0",
)


def pipelines() -> List[Pipeline]:
    """All registered pipelines. Override via UHDI_BENCH_<NAME>_{CHISEL,PLUGIN}."""
    out: List[Pipeline] = []
    for default in (_TYWAVES, _UHDI, _HGDB):
        chisel_dep = os.environ.get(
            f"UHDI_BENCH_{default.name.upper()}_CHISEL", default.chisel_dep)
        plugin_dep = os.environ.get(
            f"UHDI_BENCH_{default.name.upper()}_PLUGIN", default.plugin_dep)
        out.append(dataclasses.replace(
            default, chisel_dep=chisel_dep, plugin_dep=plugin_dep))
    return out


def get(name: str) -> Pipeline:
    for p in pipelines():
        if p.name == name:
            return p
    raise KeyError(f"unknown pipeline {name!r}; have {[p.name for p in pipelines()]}")


_BENCH_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_CACHE = _BENCH_DIR / ".cache" / "scala-fir"


class CompileError(RuntimeError):
    pass


def _scala_cli() -> Optional[str]:
    return shutil.which("scala-cli")


def _bypass_coursier_mirror_env() -> dict:
    """Empty COURSIER_CONFIG_DIR -- avoids user-side mirrors that block
    offline resolution; forces fallback to cached Central artifacts."""
    empty = pathlib.Path("/tmp/uhdi-bench-empty-coursier-config")
    empty.mkdir(parents=True, exist_ok=True)
    return {"COURSIER_CONFIG_DIR": str(empty)}


def _cache_key(scala: pathlib.Path, pipeline: Pipeline) -> str:
    h = hashlib.sha256()
    h.update(scala.read_bytes())
    h.update(repr(dataclasses.astuple(pipeline)).encode("utf-8"))
    return h.hexdigest()[:16]


def compile_for(scala: pathlib.Path, pipeline: Pipeline) -> pathlib.Path:
    """Compile `scala` under `pipeline`, return path to cached .fir."""
    if not scala.is_file():
        raise FileNotFoundError(f"fixture not found: {scala}")
    cli = _scala_cli()
    if cli is None:
        raise RuntimeError("scala-cli not on PATH; install from "
                           "https://scala-cli.virtuslab.org/")

    digest = _cache_key(scala, pipeline)
    cached = _CACHE / f"{scala.stem}-{pipeline.name}-{digest}.fir"
    if cached.is_file():
        return cached
    cached.parent.mkdir(parents=True, exist_ok=True)

    cmd = [cli, "run", "--scala", pipeline.scala_version,
           "--dep", pipeline.chisel_dep,
           "--compiler-plugin", pipeline.plugin_dep,
           "--scala-option", "-Ymacro-annotations"]
    cmd += [f"--repository={r}" for r in pipeline.repositories]
    cmd.append(str(scala))

    env = {**os.environ, **_bypass_coursier_mirror_env()}
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=600, env=env)
    if proc.returncode != 0:
        raise CompileError(
            f"scala-cli exit {proc.returncode} for {scala.name} under "
            f"pipeline {pipeline.name!r}:\n"
            f"--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}")
    fir = proc.stdout
    if "FIRRTL" not in fir and "circuit " not in fir:
        raise CompileError(
            f"scala-cli succeeded but stdout doesn't look like FIRRTL "
            f"(first 200 chars: {fir[:200]!r}); did the fixture's Main "
            f"call ChiselStage.emitCHIRRTL?")
    cached.write_text(fir, encoding="utf-8")
    return cached


def main(argv: Optional[List[str]] = None) -> int:
    """`python -m uhdi_bench.compile <pipeline> <scala>` prints FIR to stdout."""
    import argparse
    p = argparse.ArgumentParser(
        description="scala-cli wrapper: compile a Chisel fixture to FIR per pipeline.")
    p.add_argument("pipeline", choices=[pl.name for pl in pipelines()])
    p.add_argument("scala", type=pathlib.Path)
    args = p.parse_args(argv)
    try:
        fir = compile_for(args.scala, get(args.pipeline))
    except (FileNotFoundError, RuntimeError, CompileError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    sys.stdout.write(fir.read_text(encoding="utf-8"))
    print(f"\n# cached at {fir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
