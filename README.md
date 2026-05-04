# uhdi

Unified Hardware Debug Info -- reference implementation and thesis deliverables.

This repository hosts the out-of-tree pieces of the uhdi work:

- **`converter/`** -- `uhdi-converter`: `uhdi_common` shared base
  (BaseContext, refs, diff, validate, Backend registry, CLI scaffold)
  plus three backends: `uhdi_to_hgldd`, `uhdi_to_hgdb` (SQLite), and
  `uhdi_to_hgdb_json` (the JSON shape hgdb-circt's `firtool --hgdb`
  emits). JSON Schemas extracted from the spec ship with `uhdi_common`.
- **`bench/`** -- `uhdi-bench`: integration harness
  `Scala/Chisel → FIR → UHDI → projection → diff vs native`. Test
  matrix per `(fixture × target)` over targets `tywaves`,
  `hgdb_circt`, `hgdb_firrtl` for thesis chapter 5.
- **`demo/`** -- five standalone Chisel projects (`gcd`, `fsm`,
  `fifo`, `pipeline`, `bus`) wired end-to-end through
  `firtool --emit-uhdi` + the converters. Copy any one out and use
  it as a starter template; see [`demo/README.md`](demo/README.md).
- **`docs/`** -- format specification (`uhdi-spec.md`), action plan
  (`uhdi-action-plan.md`), downstream-consumer roadmap
  (`consumer-roadmap.md`).
- **`tools/`** -- consumer-side installer (`install.sh` /
  `install.nu`) that pulls firtool, hgdb-py, and tywaves from a
  GitHub Release; bench-side toolchain Docker image
  (`ghcr.io/fkhaidari/uhdi-tools`) recipe pinned via
  `tools/versions.env`; per-component release scripts in
  `tools/release/`.

The compiler side (CIRCT passes and `EmitUHDI.cpp`) lives in the sibling `circt/` fork on branch `fk-sc/uhdi-pool`, not here.

## Layout

```text
uhdi/
├── converter/       # uhdi-converter: format + projections
│   ├── pyproject.toml
│   ├── src/
│   │   ├── uhdi_common/
│   │   │   └── schemas/        # JSON Schemas (sec.3-12 of the spec)
│   │   ├── uhdi_to_hgldd/      # tywaves projection
│   │   ├── uhdi_to_hgdb/       # hgdb SQLite projection
│   │   └── uhdi_to_hgdb_json/  # hgdb JSON projection
│   └── test/                   # unit + golden tests (~300 tests, no toolchain)
├── bench/           # uhdi-bench: Scala -> FIR -> UHDI -> diff vs native
│   ├── pyproject.toml
│   ├── manifest.toml           # per-fixture allowed deltas
│   ├── fixtures/               # Chisel sources
│   ├── src/uhdi_bench/
│   └── test/
├── demo/            # standalone Chisel demos (gcd, fsm, fifo, pipeline, bus)
├── docs/            # format spec + action plan
├── tools/           # installer + uhdi-tools image recipe + release scripts
├── pyproject.toml   # workspace root: ruff / mypy / coverage configs
└── .github/workflows/ci.yml
```

Each subproject has its own `pyproject.toml` (with [project], deps,
pytest config); the root holds shared linter / type / coverage
configuration so rules are uniform across the codebase.

## Setup

```sh
# Install both packages editable in one venv:
uv venv .venv && source .venv/bin/activate
uv pip install -e ./converter -e ./bench --index-url https://pypi.org/simple --no-config
```

`--no-config` / `--index-url` bypass any site-wide artifactory proxy.

## Quick reference

```sh
# Converter CLIs (the first two ship as console scripts; the third
# runs as a module since hgdb-circt's JSON shape is bench-only today).
uhdi-to-hgldd                 input.uhdi.json -o input.dd
uhdi-to-hgdb                  input.uhdi.json -o input.db
python -m uhdi_to_hgdb_json   input.uhdi.json -o input.json

# Tests
( cd converter && pytest )       # ~300 tests, no toolchain deps
( cd bench     && pytest )       # bench cells, skip if toolchains absent

# Run the bench with prebuilt toolchains (no local builds needed)
TAG=$(cat tools/docker/image-tag.txt)
docker run --rm -v "$PWD":/work -w /work \
    ghcr.io/fkhaidari/uhdi-tools:$TAG \
    bash -c 'pip install --break-system-packages -e ./converter -e "./bench[dev]" \
             && cd bench && pytest -v'

# Lint / type / coverage (from workspace root)
ruff check converter/src converter/test bench/src bench/test
mypy converter/src/uhdi_common
coverage run --branch -m pytest converter/test/ bench/test/ && coverage report
```

See [`bench/README.md`](bench/README.md), [`demo/README.md`](demo/README.md),
and [`tools/README.md`](tools/README.md) for subproject-specific docs
(adding fixtures, registering new backends, toolchain config, demo walkthrough).
