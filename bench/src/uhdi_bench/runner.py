"""End-to-end pipeline runner: FIR -> (UHDI -> projection) vs (native).

Glue between scala-cli compile output and the structural diff. For one
(fixture, target) cell: run firtool --emit-uhdi + native emitter, route
uhdi through the matching backend, return the comparable pair."""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Any, Dict, Tuple


@dataclass(frozen=True)
class Toolchain:
    """Resolved binary paths for one bench invocation."""
    firtool: pathlib.Path
    hgdb_circt_firtool: pathlib.Path | None = None
    hgdb_firrtl_jar: pathlib.Path | None = None
    hgdb_python: pathlib.Path | None = None  # bindings/python root


def _bench_repo() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent.parent


def _prepend_pythonpath(*entries: pathlib.Path) -> str:
    """Build PYTHONPATH with our entries first, user's existing prefix kept.

    native_lib (built `hgdb` w/ _hgdb.so) must precede source-only
    `hgdb_python/hgdb/`; the toml-shim before either."""
    parts = [str(e) for e in entries]
    if existing := os.environ.get("PYTHONPATH"):
        parts.append(existing)
    return os.pathsep.join(parts)


def _pick_native_lib(hgdb_python: pathlib.Path) -> pathlib.Path:
    """Pick `build/lib.*` matching the running interpreter's ABI tag.

    _hgdb.so is built for one minor; filesystem iteration order would
    otherwise pick a stale build at random when two `lib.*` coexist."""
    abi_tag = f"cpython-{sys.version_info.major}{sys.version_info.minor}"
    build = hgdb_python / "build"
    candidates = sorted(d for d in build.glob("lib.*") if abi_tag in d.name)
    if candidates:
        return candidates[0]
    present = sorted(d.name for d in build.glob("lib.*"))
    raise RuntimeError(
        f"hgdb python bindings under {build} have no lib.* matching "
        f"{abi_tag} (running {sys.executable}); present: "
        f"{present or '<none>'} -- did `python setup.py build_ext` run "
        f"under this interpreter?")


def discover_toolchain() -> Toolchain:
    """Resolve env var -> /opt baked path -> sibling-checkout path.

    Env overrides: FIRTOOL (required), HGDB_CIRCT_FIRTOOL, HGDB_FIRRTL_JAR,
    HGDB_PY (hgdb python bindings root)."""
    practice = _bench_repo().parent.parent

    if (env := os.environ.get("FIRTOOL")):
        firtool = pathlib.Path(env)
    elif (opt := pathlib.Path("/opt/circt/bin/firtool")).is_file():
        firtool = opt
    else:
        firtool = practice / "circt" / "build" / "bin" / "firtool"

    hgdb_circt_path: pathlib.Path | None = None
    if (env := os.environ.get("HGDB_CIRCT_FIRTOOL")):
        hgdb_circt_path = pathlib.Path(env)
    elif (opt := pathlib.Path("/opt/hgdb-circt/bin/firtool")).is_file():
        hgdb_circt_path = opt
    else:
        candidate = practice / "hgdb-circt" / "build" / "bin" / "firtool"
        if candidate.is_file():
            hgdb_circt_path = candidate

    hgdb_firrtl_jar: pathlib.Path | None = None
    if (env := os.environ.get("HGDB_FIRRTL_JAR")):
        hgdb_firrtl_jar = pathlib.Path(env)
    elif (opt := pathlib.Path(
            "/opt/hgdb-firrtl/bin/hgdb-firrtl.jar")).is_file():
        hgdb_firrtl_jar = opt
    else:
        candidate = practice / "hgdb-firrtl" / "bin" / "hgdb-firrtl.jar"
        if candidate.is_file():
            hgdb_firrtl_jar = candidate

    hgdb_py: pathlib.Path | None = None
    if (env := os.environ.get("HGDB_PY")):
        hgdb_py = pathlib.Path(env)
    else:
        # Need both toml2hgdb and the prebuilt C extension; partial
        # layouts would fail later with a less actionable error.
        for candidate in (
                pathlib.Path("/opt/hgdb/bindings/python"),
                practice / "hgdb" / "bindings" / "python"):
            if (candidate / "scripts" / "toml2hgdb").is_file() and \
                    (candidate / "hgdb").is_dir() and \
                    any((candidate / "build").glob("lib.*")):
                hgdb_py = candidate
                break

    return Toolchain(
        firtool=firtool,
        hgdb_circt_firtool=hgdb_circt_path,
        hgdb_firrtl_jar=hgdb_firrtl_jar,
        hgdb_python=hgdb_py,
    )


def _emit_uhdi(fir: pathlib.Path, workdir: pathlib.Path,
               firtool: pathlib.Path) -> Dict[str, Any]:
    # No --uhdi-source-prefix: chisel-emitted @[<path>] are already the
    # absolute Scala paths; setting a prefix would mangle them.
    uhdi = workdir / f"{fir.stem}.uhdi.json"
    sv = workdir / f"{fir.stem}.sv"
    proc = subprocess.run(
        [str(firtool), "-g", "--emit-uhdi",
         f"--uhdi-output-file={uhdi}",
         "-o", str(sv), str(fir)],
        capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(
            f"firtool --emit-uhdi exit {proc.returncode} on {fir.name}:\n"
            f"--- stderr ---\n{proc.stderr}")
    return json.loads(uhdi.read_text(encoding="utf-8"))


def _emit_native_hgldd(fir: pathlib.Path, workdir: pathlib.Path,
                       firtool: pathlib.Path) -> Dict[str, Any]:
    """Native HGLDD via firtool. Single-file mode embeds .dd JSON in
    the SV stream after a marker; we extract everything past it."""
    sv = workdir / f"{fir.stem}.ref.sv"
    proc = subprocess.run(
        [str(firtool), "-g", "--emit-hgldd",
         "-o", str(sv), str(fir)],
        capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(
            f"firtool --emit-hgldd exit {proc.returncode} on {fir.name}:\n"
            f"--- stderr ---\n{proc.stderr}")
    text = sv.read_text(encoding="utf-8")
    marker = "// ----- 8<"
    body_start = None
    for i, line in enumerate(text.splitlines(keepends=True)):
        if line.startswith(marker) and ".dd" in line:
            body_start = i + 1
            break
    if body_start is None:
        raise RuntimeError(
            f"firtool did not embed an HGLDD block in {sv.name}")
    return json.loads("".join(text.splitlines(keepends=True)[body_start:]))


def _emit_native_hgdb_circt(fir: pathlib.Path, workdir: pathlib.Path,
                            firtool: pathlib.Path) -> Dict[str, Any]:
    """Native hgdb table via hgdb-circt's `firtool --hgdb=<file>`.

    hgdb-circt is a pre-regreset LLVM-16 fork, so we feed it downgraded
    FIR. Output is JSON (different schema from hgdb-firrtl SQLite)."""
    from . import _downgrade_fir

    legacy = workdir / f"{fir.stem}.circt-input.fir"
    legacy.write_text(
        _downgrade_fir.downgrade(fir.read_text(encoding="utf-8")),
        encoding="utf-8")

    db = workdir / f"{fir.stem}.circt.db"
    sv = workdir / f"{fir.stem}.circt.sv"
    proc = subprocess.run(
        [str(firtool), f"--hgdb={db}",
         "-o", str(sv), str(legacy)],
        capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(
            f"hgdb-circt firtool --hgdb exit {proc.returncode} on "
            f"{legacy.name}:\n--- stderr ---\n{proc.stderr}")
    if not db.is_file():
        # ExportHGDBPass exits 0 without producing the .db on plain
        # fixtures (Counter etc.) -- upstream gap in HWDebugBuilder.
        # Caller turns this RuntimeError into pytest.skip.
        raise RuntimeError(
            f"hgdb-circt firtool succeeded but produced no {db.name}")
    return json.loads(db.read_text(encoding="utf-8"))


def _emit_native_pdg(scala_path: pathlib.Path, workdir: pathlib.Path,
                    scala_cli_bin: str) -> Dict[str, Any]:
    """Native PDG via chiseltrace-fork ChiselStage (addChiselTrace=True).

    Unlike hgldd/hgdb emitters that take a pre-compiled .fir, PDG requires
    its own Scala compilation: the chiseltrace plugin injects PDG annotations
    during ChiselStage and emits pdg.json directly -- firtool is not involved.
    The fixture must define `object PdgMain extends App` that calls
    ChiselStage(withDebug=False, addChiselTrace=True).execute(...)."""
    from .compile import _bypass_coursier_mirror_env

    cmd = [
        scala_cli_bin,
        "run",
        "--scala", "2.13.14",
        "--dep", "org.chipsalliance::chisel:6.4.3-tywaves-chiseltrace-SNAPSHOT",
        "--compiler-plugin",
        "org.chipsalliance:::chisel-plugin:6.4.3-tywaves-chiseltrace-SNAPSHOT",
        "--scala-option", "-Ymacro-annotations",
        "--repository=ivy2Local",
        # --main-class is a scala-cli option, so it must precede the source
        # file; after `--` it would be passed to the program (the fixture
        # has both Main and PdgMain, so scala-cli errors on ambiguity).
        "--main-class", "PdgMain",
        str(scala_path),
        "--",
        str(workdir),
    ]
    env = {**os.environ, **_bypass_coursier_mirror_env()}
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env)
    if proc.returncode != 0:
        raise RuntimeError(
            f"scala-cli PDG exit {proc.returncode} for {scala_path.name}:\n"
            f"--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}")
    pdg_file = workdir / "pdg.json"
    if not pdg_file.is_file():
        raise RuntimeError(
            f"scala-cli succeeded but pdg.json not found in {workdir}; "
            f"did PdgMain write to args(0)?")
    return json.loads(pdg_file.read_text(encoding="utf-8"))


def _emit_native_hgdb_firrtl(fir: pathlib.Path, workdir: pathlib.Path,
                             jar: pathlib.Path,
                             hgdb_python: pathlib.Path) -> Dict[str, Any]:
    """Native hgdb SQLite via Scala-FIRRTL 1.x (downgrade -> jar -> toml2hgdb)."""
    from . import _downgrade_fir

    # Stage 1: downgrade 4.x -> 1.x.
    legacy = workdir / f"{fir.stem}.legacy.fir"
    legacy.write_text(
        _downgrade_fir.downgrade(fir.read_text(encoding="utf-8")),
        encoding="utf-8")

    # Stage 2: hgdb-firrtl jar -> toml.
    toml_path = workdir / f"{fir.stem}.legacy.toml"
    proc = subprocess.run(
        ["java", "-cp", str(jar), "firrtl.stage.FirrtlMain",
         "--custom-transforms", "hgdb.CollectSourceNamesTransform",
         "-i", str(legacy), "-X", "verilog",
         "-o", str(workdir / fir.stem), "-td", str(workdir),
         "--hgdb-toml", str(toml_path)],
        capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(
            f"hgdb-firrtl FirrtlMain exit {proc.returncode} on "
            f"{legacy.name}:\n"
            f"--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}")

    # Stage 3: toml2hgdb. hgdb's bindings expect PyPI's `toml`; drop a
    # stdlib-only shim re-exporting tomllib so offline hosts work.
    shim = workdir / "_toml_shim"
    shim.mkdir()
    (shim / "toml.py").write_text(
        "import tomllib\n"
        "def load(fp):\n"
        "    if hasattr(fp, 'read'):\n"
        "        d = fp.read()\n"
        "        return tomllib.loads(d.decode() if isinstance(d, bytes) else d)\n"
        "    with open(fp, 'rb') as f: return tomllib.load(f)\n"
        "def loads(s): return tomllib.loads(s)\n",
        encoding="utf-8")

    db = workdir / f"{fir.stem}.firrtl.db"
    native_lib = _pick_native_lib(hgdb_python)
    env = dict(os.environ,
               PYTHONPATH=_prepend_pythonpath(shim, native_lib, hgdb_python))
    # Use pytest's interpreter -- _hgdb.so is ABI-tagged for one minor
    # version; system /usr/bin/python3 may not match.
    proc = subprocess.run(
        [sys.executable,
         str(hgdb_python / "scripts" / "toml2hgdb"),
         str(toml_path), str(db)],
        env=env, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(
            f"toml2hgdb exit {proc.returncode}:\n"
            f"--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}")
    if not db.is_file():
        raise RuntimeError(f"toml2hgdb did not create {db}")
    from uhdi_to_hgdb.dump import canonical_dump
    return canonical_dump(db)


def _canonical_hgldd(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Same canonicalisation the converter golden tests apply: drop
    file_info / hdl_file_index churn, sort objects by obj_name."""
    drop_top = {"file_info", "hdl_file_index"}
    loc_neighbours = {"begin_line", "end_line",
                      "begin_column", "end_column"}

    def is_loc(d: dict) -> bool:
        return any(k in d for k in loc_neighbours)

    def walk(obj: Any, top: bool = True) -> Any:
        if isinstance(obj, dict):
            drop = set(drop_top) if top else set()
            if is_loc(obj):
                drop.add("file")
            out = {k: walk(v, False) for k, v in sorted(obj.items())
                   if k not in drop}
            if top and "objects" in out and isinstance(out["objects"], list):
                out["objects"] = sorted(
                    out["objects"],
                    key=lambda o: o.get("obj_name", "")
                    if isinstance(o, dict) else "")
            return out
        if isinstance(obj, list):
            return [walk(x, False) for x in obj]
        return obj
    return walk(doc)


def run_target(fir: pathlib.Path, target: str,
               toolchain: Toolchain,
               scala_path: pathlib.Path | None = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Run one (fixture, target) cell. Returns (uhdi_derived, native);
    caller does the structural diff."""
    from uhdi_common.backend import discover, get

    discover()
    workdir = pathlib.Path(tempfile.mkdtemp(prefix=f"uhdi-bench-{fir.stem}-"))
    try:
        uhdi_doc = _emit_uhdi(fir, workdir, toolchain.firtool)
        if target == "tywaves":
            backend = get("hgldd")
            ours = _canonical_hgldd(backend.convert(uhdi_doc, None))
            native = _canonical_hgldd(
                _emit_native_hgldd(fir, workdir, toolchain.firtool))
            return ours, native
        if target == "hgdb_circt":
            if toolchain.hgdb_circt_firtool is None:
                raise RuntimeError(
                    "hgdb_circt target needs HGDB_CIRCT_FIRTOOL "
                    "(or sibling hgdb-circt/build/bin/firtool)")
            backend = get("hgdb_json")
            ours = backend.convert(uhdi_doc, None)
            native = _emit_native_hgdb_circt(
                fir, workdir, toolchain.hgdb_circt_firtool)
            return ours, native
        if target == "hgdb_firrtl":
            if toolchain.hgdb_firrtl_jar is None:
                raise RuntimeError(
                    "hgdb_firrtl target needs HGDB_FIRRTL_JAR "
                    "(or sibling hgdb-firrtl/bin/hgdb-firrtl.jar)")
            if toolchain.hgdb_python is None:
                raise RuntimeError(
                    "hgdb_firrtl target needs HGDB_PY pointing at "
                    "hgdb/bindings/python (with built _hgdb extension)")
            backend = get("hgdb")
            db_out = workdir / f"{fir.stem}.ours.db"
            backend.convert(uhdi_doc, db_out)
            ours = backend.canonical_dump(db_out)
            native = _emit_native_hgdb_firrtl(
                fir, workdir, toolchain.hgdb_firrtl_jar,
                toolchain.hgdb_python)
            return ours, native
        if target == "pdg":
            # Native reference is chiseltrace (a chisel fork) compiled
            # directly from the .scala source -- firtool is not involved.
            # The PdgMain entry point lives in a sidecar `<Name>Native.scala`
            # (the chiseltrace fork's ChiselStage API differs from the uhdi
            # fork's, so PdgMain can't share the main fixture file). The UHDI
            # side still uses `fir`/`uhdi_doc` from the uhdi pipeline above.
            if scala_path is None:
                raise RuntimeError(
                    "pdg target requires scala_path (pass the .scala fixture "
                    "path to run_target)")
            sidecar = scala_path.with_name(f"{scala_path.stem}Native.scala")
            if not sidecar.is_file():
                raise RuntimeError(
                    f"pdg target needs a native sidecar {sidecar.name} next to "
                    f"{scala_path.name} (defines `object PdgMain` on the "
                    f"chiseltrace fork); not found")
            cli = shutil.which("scala-cli")
            if cli is None:
                raise RuntimeError(
                    "scala-cli not on PATH; install from "
                    "https://scala-cli.virtuslab.org/")
            backend = get("pdg")
            ours = backend.convert(uhdi_doc, None)
            native = _emit_native_pdg(sidecar, workdir, cli)
            return ours, native
        raise ValueError(
            f"unknown target {target!r}; have tywaves, hgdb_circt, "
            f"hgdb_firrtl, pdg")
    finally:
        if not os.environ.get("UHDI_BENCH_KEEP_WORKDIR"):
            shutil.rmtree(workdir, ignore_errors=True)
