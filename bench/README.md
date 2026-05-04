# uhdi-bench

Integration harness: `Scala/Chisel` source → `FIR` → `UHDI` →
projection → structural diff against the native reference.

This is the chapter-5 deliverable for the thesis: a numeric matrix
showing where the uhdi-derived projections converge with native
firtool / hgdb-circt outputs and where they diverge (with reasons).

## Targets

Three reference emitters are diffed against the UHDI projections:

| Target | Native reference | UHDI-side projection (Backend) |
|--------|------------------|--------------------------------|
| `tywaves` | `firtool --emit-hgldd` (uhdi-side firtool) | `uhdi_to_hgldd` (HGLDD dict) |
| `hgdb_circt` | hgdb-circt `firtool --hgdb=<file>` (JSON) | `uhdi_to_hgdb_json` (JSON dict) |
| `hgdb_firrtl` | legacy Scala FIRRTL 1.x `hgdb-firrtl.jar` + `toml2hgdb` (SQLite) | `uhdi_to_hgdb` (SQLite, `canonical_dump` for diff) |

## What runs

For each `(fixture × target)` cell:

1. **Scala → FIR** -- `bench/fixtures/<Name>.scala` is compiled by
   `scala-cli` against the target's Chisel fork (rameloni for
   tywaves, Farid's fork for uhdi, stock 6.4.0 for hgdb).  Result is
   cached under `bench/.cache/scala-fir/<stem>-<pipeline>-<digest>.fir`.
2. **FIR → UHDI** -- the cached `.fir` is fed to `firtool --emit-uhdi`.
3. **UHDI → projection** -- the matching `uhdi_common` Backend
   converts the UHDI document to its target format (see table above).
4. **FIR → native reference** -- the matching native emitter runs on
   the same `.fir` (or its FIRRTL-1.x downgrade for `hgdb_firrtl`).
5. **Structural diff** -- `uhdi_common.diff_dicts` walks both sides
   element-by-element; `manifest.toml` lists per-cell allowed deltas
   (post-DCE divergences, etc.). Any unexpected delta fails the cell
   with the exact JSON-pointer path that drifted; an expectation that
   matched zero real deltas fails as "manifest stale: gap closed".

## Toolchains

Bench reads paths from env vars, then falls back to a baked
`/opt/...` layout (provided by the `ghcr.io/fkhaidari/uhdi-tools`
image), then to the sibling-checkout layout (Farid's local
arrangement).

| Tool | Env var | Image default | Sibling fallback |
|------|---------|---------------|------------------|
| `firtool` (uhdi + tywaves) | `FIRTOOL` | `/opt/circt/bin/firtool` | `../../circt/build/bin/firtool` |
| `firtool` (hgdb-circt) | `HGDB_CIRCT_FIRTOOL` | `/opt/hgdb-circt/bin/firtool` | `../../hgdb-circt/build/bin/firtool` |
| `hgdb-firrtl.jar` | `HGDB_FIRRTL_JAR` | `/opt/hgdb-firrtl/bin/hgdb-firrtl.jar` | `../../hgdb-firrtl/bin/hgdb-firrtl.jar` |
| hgdb python bindings | `HGDB_PY` | `/opt/hgdb/bindings/python` | `../../hgdb/bindings/python` |
| `scala-cli` | (PATH) | preinstalled in image | -- |

Cells whose toolchain isn't found auto-skip with a clear reason --
no cryptic FileNotFoundError tracebacks.

### Quick start: Docker image (no local builds needed)

```sh
TAG=$(cat ../tools/docker/image-tag.txt)
docker run --rm -v "$PWD/..":/work -w /work \
    ghcr.io/fkhaidari/uhdi-tools:$TAG \
    bash -c 'pip install --break-system-packages -e ./converter -e "./bench[dev]" \
             && cd bench && pytest -v'
```

The image bakes both Chisel forks `mill publishLocal`'d into
`/opt/ivy2-local/`, symlinked at container start to
`$HOME/.ivy2/local/` so `scala-cli`'s `--repository ivy2Local` finds
them transparently. See `tools/README.md` for image build / bump
procedures.

### Chisel forks

Each pipeline assumes the matching Chisel checkout has been
`mill publishLocal`'d to `~/.ivy2/local/`:

| Pipeline | Chisel artifact | Origin |
|----------|----------------|--------|
| `tywaves` | `org.chipsalliance::chisel:6.4.3-tywaves-SNAPSHOT` | `practice/rameloni-chisel` |
| `uhdi` | (same SNAPSHOT until Farid mints a separate version) | `practice/chisel` (debug intrinsics) |
| `hgdb` | `org.chipsalliance::chisel:6.4.0` | Maven Central / coursier cache |

Override either with env vars:

```sh
UHDI_BENCH_TYWAVES_CHISEL=org.chipsalliance::chisel:6.5.0 pytest
UHDI_BENCH_TYWAVES_PLUGIN=org.chipsalliance:::chisel-plugin:6.5.0 pytest
```

The compile step bypasses any user-side `coursier mirror.properties`
(via `COURSIER_CONFIG_DIR=/tmp/uhdi-bench-empty-coursier-config`) so
a corp artifactory being unreachable doesn't block local runs.

## Running

```sh
# Local: from bench/
pytest                                # all cells
pytest -k Counter-tywaves             # one cell
UHDI_BENCH_KEEP_WORKDIR=1 pytest      # leave workdirs at /tmp/uhdi-bench-*

# Smoke a single Scala fixture without pytest
python -m uhdi_bench.compile tywaves fixtures/Counter.scala
```

Cells skip cleanly when scala-cli or firtool are absent -- so on a
fresh checkout, `pytest` is green-by-default and only flips to
"actually running" once the toolchains are wired.

## Adding a fixture

1. Drop `bench/fixtures/<Name>.scala` -- a Chisel `Module` plus a
   `Main` calling `ChiselStage.emitCHIRRTL(...)`.  No `//> using`
   directives: `compile.py` injects the dep set per pipeline.
2. `pytest -k <Name>` to confirm the new fixture compiles and the
   cells run end-to-end.
3. Adjust expectations in `manifest.toml` if specific (fixture × target)
   cells are expected to diverge.

## Adding a target

Bench iterates over the `uhdi_common` Backend registry, so adding a
new converter (say `uhdi_to_pdg`) plus an entry in `_TARGET_TO_PIPELINE`
in `test_pipeline.py` is enough to surface it as new test cells.
