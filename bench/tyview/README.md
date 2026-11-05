# tyview: what tywaves shows, ours vs native

An end-to-end check of the debug info on the path a user sees:
Chisel → firtool → HGLDD → Verilator → tywaves-rs. For each fixture it
builds two Verilogs from one `.scala` file and compares, per source
variable, what tywaves shows over the same simulation.

```
fixture.scala ─┬─ new Chisel ─ firtool -g --emit-uhdi ─ converter (hgldd) ─┐
               │                                                           ├─ Verilator ─ tydump ─ compare
               └─ rameloni Chisel ─ firtool-type-dbg-info --emit-hgldd ────┘
```

`tydump` builds the typed view with tywaves-rs, the library inside
surfer-tywaves, and rewrites the VCD the same way the viewer does.

## Run

```sh
cargo build --release --manifest-path bench/tyview/tydump/Cargo.toml
FIRTOOL=<circt with the Debug-op stack>/bin/firtool \
  converter/.venv/bin/python bench/tyview/run.py            # all fixtures
  converter/.venv/bin/python bench/tyview/run.py HierInline # one fixture
```

The exit code is 1 if any fixture fails to build or shows a
`OURS-BROKEN` or `VALUE-DIFF` line. Work files go to
`bench/.cache/tyview/<fixture>/{ours,native}`.

## Toolchain

| Piece | Where it comes from | Override |
|---|---|---|
| firtool (ours) | circt branch with the Debug-op stack | `FIRTOOL` (required) |
| Chisel (ours) | `7.16.0+37-0422e52a-SNAPSHOT`, `publishLocal` of the chisel branch with the handle-tree intrinsics | `UHDI_BENCH_UHDI_CHISEL`, `UHDI_BENCH_UHDI_PLUGIN` |
| firtool (native) | `firtool-type-dbg-info-0.1.5` from the rameloni/circt release `v0.1.5-tywaves-SNAPSHOT` | `TYWAVES_FIRTOOL` |
| Chisel (native) | rameloni/chisel `v6.4.3-tywaves-SNAPSHOT`, `publishLocal` | `UHDI_BENCH_TYWAVES_CHISEL`, `UHDI_BENCH_TYWAVES_PLUGIN` |
| tywaves-rs | rameloni/tywaves-rs main, pinned in `tydump/Cargo.toml` | `[patch]` in `.cargo/config.toml` |
| Verilator | 5.020 was used; needs `--trace-underscore` | `PATH` |
| tydump | `tydump/` | `TYDUMP` |

Both Chisel builds resolve from `~/.ivy2/local`. Behind a Maven mirror
that does not carry them, set `COURSIER_MIRRORS=/dev/null`.

## Reading the report

One header per fixture: `native N vars, ours M` and counts. Then one
line per variable that is not a plain match:

| Line | Meaning |
|---|---|
| (none) | both show the variable with the same values at every step |
| `same values ... type: ours=... native=...` | values match, the type name differs |
| `VALUE-DIFF` | both show it, values differ at the given time |
| `OURS-BROKEN` | native shows it, ours has a leaf with no signal |
| `NATIVE-BROKEN` | ours shows it, native does not |
| `BOTH-BROKEN` | neither shows it |
| `ONLY-NATIVE` / `ONLY-OURS` | the variable exists on one side only; `shown=` says whether its values resolve |

`ONLY-*` lines do not fail the run. Read them: some are expected (native
lists the ports of a memory's `_ext` module), some are gaps.

Upstream tywaves-rs skips the variables of an inlined module on both
sides: an HGLDD instance without a module name gives an empty scope. To
see them, build `tydump` against a tywaves-rs with that fixed (via
`[patch]`).

The stimulus is fixed: inputs get hashed pseudo-random values for 40
cycles, `reset` is high for the first two.
