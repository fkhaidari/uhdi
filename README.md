# uhdi

Unified Hardware Debug Info -- reference implementation and thesis deliverables.

This repository hosts the out-of-tree pieces of the uhdi work, split
into two subprojects:

- **`converter/`** -- `uhdi-converter`: format spec, JSON schemas,
  `uhdi_common` shared base (BaseContext, refs, diff, validate,
  Backend registry, CLI scaffold), and the two backends
  `uhdi_to_hgldd` + `uhdi_to_hgdb`.
- **`bench/`** -- `uhdi-bench`: integration harness
  `Scala/Chisel → FIR → UHDI → projection → diff vs native`.  Test
  matrix per `(fixture × target)` for thesis chapter 5.
- **`docs/`** -- format specification (`uhdi-spec.md`), action plan
  (`uhdi-action-plan.md`), downstream-consumer roadmap
  (`consumer-roadmap.md`).
- **`tools/`** -- prebuilt toolchain Docker image
  (`ghcr.io/fkhaidari/uhdi-tools`); recipe for firtool x2 + chisel
  forks + hgdb stack, pinned via `tools/versions.env`.

The compiler side (CIRCT passes and `EmitUHDI.cpp`) lives in the sibling `circt/` fork on branch `fk-sc/uhdi-pool`, not here.

## Layout

```text
uhdi/
├── converter/       # uhdi-converter: format + projections
│   ├── pyproject.toml
│   ├── schemas/     # JSON Schemas extracted from the spec
│   ├── src/
│   │   ├── uhdi_common/
│   │   ├── uhdi_to_hgldd/
│   │   └── uhdi_to_hgdb/
│   └── test/        # unit + golden tests
├── bench/           # uhdi-bench: Scala -> FIR -> UHDI -> diff vs native
│   ├── pyproject.toml
│   ├── fixtures/    # Chisel sources
│   ├── src/uhdi_bench/
│   └── test/
├── docs/            # format spec + action plan
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
# Converter CLIs
uhdi-to-hgldd input.uhdi.json -o input.dd
uhdi-to-hgdb  input.uhdi.json -o input.db

# Tests
( cd converter && pytest )       # 92 tests, ~0.5s, no toolchain deps
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

See `converter/README.md` and `bench/README.md` for subproject-specific
docs (adding fixtures, registering new backends, toolchain config, etc.).
