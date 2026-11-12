# Typed waves through UHDI

Compile Chisel with debug intrinsics, emit UHDI with CIRCT, convert it to HGLDD,
and load the result in Tywaves. Memory ports are supported; memory contents are
outside this check.

This series does not reunite source scopes split across modules by outlining.
Ordinary layer lowering is supported, but those split scopes may appear as
separate fragments in the debug hierarchy.

## Tools

Use the CIRCT, converter, tywaves-rs and GUI revisions in `versions.json`.
Build CIRCT with its documented LLVM/MLIR toolchain. Build the viewer against
the listed tywaves-rs revision. The GUI fix does not require disabling Spade.

Python must have the converter dependencies installed. The regression runner
also needs Verilator and a `tydump` binary built from `bench/tyview/tydump`
against the listed tywaves-rs revision. The expression check needs Icarus Verilog.
Set library search paths for your build if its shared libraries are not installed.

Until the viewer commits are published, build the helper with a local override:

```sh
cargo build --manifest-path bench/tyview/tydump/Cargo.toml \
  --config 'patch."https://github.com/rameloni/tywaves-rs".tywaves-rs.path="/path/to/tywaves-rs"'
```

This override updates the helper lockfile for the local checkout. Keep that
local change out of published dependency pins. The helper writes scope types
as well as variable metadata, which the hierarchy check requires.

## Your design

The checked Chisel version is `7.16.0+37-0422e52a-SNAPSHOT`, with the matching
compiler plugin. Pass `--with-experimental-debug-intrinsics` to
`ChiselStage.emitCHIRRTL`. Ordinary FIRRTL without source debug metadata cannot
recover Chisel types.

```sh
python3 bench/typed-waves/typed-waves.py compile design.fir \
  --firtool /path/to/firtool --out /tmp/design-debug
```

Simulate the Verilog in `/tmp/design-debug/verilog`, then open its VCD:

```sh
python3 bench/typed-waves/typed-waves.py view /tmp/design-debug \
  --viewer /path/to/surfer-tywaves --vcd /path/to/sim.vcd \
  --top MyTop --extra-scopes TOP MyTop
```

`--extra-scopes` must match the actual VCD hierarchy. The example is for the
Verilator testbench used here, not a required hierarchy for all simulators.

## Regression checks

```sh
python3 bench/typed-waves/run.py --firtool /path/to/firtool \
  --tydump /path/to/tydump --out /tmp/typed-waves-check
python3 bench/typed-waves/check_expressions.py \
  /path/to/tydump /tmp/typed-waves-expressions
```

The first command uses the committed FIRRTL snapshots. Add `--elaborate` to
regenerate them from the adjacent Scala sources with scala-cli and the pinned
Chisel dependency. Snapshot source locations record the original generation
path; they are not used to locate simulation signals.

`LayerHierarchy` checks 27 variables, source scope types, register values
against an independent state model, dedup, inlining and extracted layer binding.
`MemoryHierarchy` checks 18 variables, all six read/write memory ports and
packed Bundle values against the raw simulation trace. It does not validate
memory contents or undefined read behavior.
The expression check compares 15 expressions with Icarus, including 256-bit,
signed and four-state cases.

These checks do not use original Tywaves as an oracle. Original requires
`new ChiselStage(withDebug = true)` and firtool options
`-O=debug -g --emit-hgldd --split-verilog`. Earlier comparisons that omitted
these settings are not a valid baseline. Even with these settings, original
0.1.5 skips inferred-width values while materializing debug info, and original
tywaves-rs skips inline instance variables. Those are separate defects.
