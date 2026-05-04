# UHDI demos

Five small Chisel projects that exercise the UHDI debug stack end-to-end:
Chisel design -> FIRRTL -> `firtool --emit-uhdi` -> UHDI JSON ->
`uhdi-to-hgldd` / `uhdi-to-hgdb` -> debug info that tywaves and hgdb consume.

Each subdirectory is a **standalone Chisel project** with its own
`build.mill`, `millw`, and `run.sh`. You can copy any one of them out of
this repository, drop it next to your own circuit, and use it as a
starter template (see [Use this in your own project](#use-this-in-your-own-project)).

Inside the repo, each demo's `run.sh` is a thin symlink to
[`demo/run.sh`](run.sh), a bash shim that locates `nu` (Nushell) and
dispatches to [`demo/run.nu`](run.nu) — the actual build / simulate /
download logic. Subcommands are positional: `./run.sh` (defaults to
`build`), `./run.sh simulate`, `./run.sh download-only`. When you copy
a demo out of the repo as a starter, replace the symlink with a
self-contained build script (mill + the converter CLIs) — the
in-repo shim only works under this checkout.

Each demo ships a Chisel testbench (`app/src/<Top>Sim.scala`) built on
[`chisel3.simulator`][chisel-sim] -- peek/poke from Scala, no hand-written
SystemVerilog harness. `./run.sh simulate` runs it end-to-end.

[chisel-sim]: https://www.chisel-lang.org/api/latest/chisel3/simulator/index.html

| Demo | Top module | Sim entrypoint | What it exercises |
|------|-----------|----------------|-------------------|
| `gcd/` | `GCD` | `GCDSim` | Plain UInt arithmetic, single module. The simplest end-to-end. |
| `fsm/` | `TrafficLight` | `TrafficLightSim` | `ChiselEnum` state. Tywaves should render the `state` register as `Red / RedYellow / Green / Yellow`, not as `2'b00 / 01 / 10 / 11`. |
| `fifo/` | `Fifo` | `FifoSim` | `Decoupled<UInt>` ports + `SyncReadMem`. Tywaves groups `valid / ready / bits` as a struct; the SyncReadMem appears as its own scope. |
| `pipeline/` | `Pipeline` | `PipelineSim` | 3-stage MAC with `MulStage` / `AddStage` as separate `Module`s. Hierarchy navigation in tywaves; hgdb steps a value across the pipeline registers cycle by cycle. |
| `bus/` | `MemController` | `MemControllerSim` | `Decoupled` carrying nested `Bundle`s (`Request{addr, data, write}` -> `Response{data, ok}`). Flexes nested-record rendering and a single-in-flight handshake. |

## Quick start

### Prerequisites
- Java 21+ (mill needs it)
- Python 3 (for the UHDI converters)
- Either Docker / podman (preferred -- pulls a prebuilt image with firtool)
  or `gh` CLI authenticated with a token that can read public releases
- Verilator (only for `./run.sh simulate` -- chisel3.simulator builds
  with it under the hood)

### Clone and install
```sh
git clone https://github.com/fkhaidari/uhdi
cd uhdi
tools/install.sh all --prefix ~/.local/uhdi-tools
```
That installs `firtool` (with `--emit-uhdi`), the hgdb python bindings,
the tywaves binary, and prints a JitPack snippet for the modified
Chisel artefact. Add the snippet exports to your shell profile.

### Run any demo
```sh
cd demo/gcd
./run.sh
```
The first run takes ~30 s on a warm cache (mill resolves Chisel from
JitPack, firtool runs through `--emit-uhdi`, and the two python
converters produce HGLDD/HGDB). Output:

| File | What it is |
|------|-----------|
| `design.uhdi.json` | UHDI debug-info document (single source of truth) |
| `design.dd` | HGLDD projection -- what tywaves reads |
| `design.db` | HGDB SQLite projection -- what the hgdb runtime reads |
| `<TopModule>.sv` | SystemVerilog firtool emitted alongside |

### Generate a waveform
```sh
./run.sh simulate
ls design.vcd
```
This invokes the demo's `<Top>Sim` entrypoint (e.g. `GCDSim`) -- a Scala
program built on `chisel3.simulator` that elaborates the DUT, runs
firtool with `--emit-uhdi --split-verilog`, drives Verilator with
peek/poke stimulus, and writes both `design.vcd` (waveform) and
`design.uhdi.json` (debug info, then re-projected to `.dd`/`.db`) into
the demo directory. The Chisel sim and `Main` go through the same
firtool, so the SystemVerilog signals in the VCD line up with the
HGLDD/HGDB symbols by construction.

## Open in tywaves

tywaves is rameloni's surfer fork that consumes a `.vcd` plus a
directory of HGLDD files (`.dd` here, written by `uhdi-to-hgldd`) and
renders the design with Chisel source types preserved.

### HGLDD-aware mode

Load the HGLDD directory and link it to the testbench-wrapped wave
hierarchy. `chisel3.simulator` wraps the DUT in a module called
`svsimTestbench`, so the `--extra-scopes` chain is `TOP svsimTestbench dut`:

```sh
tywaves design.vcd --hgldd-dir . --top-module GCD --extra-scopes TOP svsimTestbench dut
```

In this mode the `Scopes` panel shows `TOP / svsimTestbench / dut`
hierarchy and the `Variables` panel collapses bundle ports as
`GCD_io {a, b, en, q, rdy}` with each field carrying its real value
through the simulation. `./run.sh simulate` prints the exact command
for the demo's top module at the end of its output.

### Plain mode

If you don't need the type overlay, just open the waveform directly.
tywaves' built-in translators still give you signed / unsigned / hex /
binary / IEEE-754 views per signal, and the full
`TOP / svsimTestbench / dut / ...` hierarchy is browsable:

```sh
tywaves design.vcd
```

The same `design.dd` works regardless of which simulator produced the
`.vcd`. Verilator, iverilog, VCS, Xcelium -- UHDI was built so the type
layer lives independently of the simulator.

## Open in hgdb

`design.db` is UHDI's HGDB projection -- a SQLite symbol table that the
hgdb runtime consumes so you can set breakpoints, step, and inspect by
**Chisel source line**, not by Verilog signal name. Three workflows from
weakest to strongest, all reading the same `design.db`:

### A. Offline inspection -- `hgdb-db`

Upstream's CLI for poking at a symbol table without running anything.
Useful to confirm UHDI's projection looks the way `hgdb-firrtl`'s does.

```sh
hgdb-db design.db
hgdb> instance list
# [0]: GCD
hgdb> breakpoint where /abs/path/GCD.scala
# [0]: - id: 1  - filename: .../GCD.scala  - line: 17  - condition: 1
# [1]: - id: 2  - filename: .../GCD.scala  - line: 18  - condition: io_en
# ...
hgdb> exit
```

### B. Console session -- `hgdb-debugger`

Upstream's gdb-style console client ([Kuree/hgdb-debugger][hgdb-cli-repo]).
Connects to a running hgdb runtime over WebSocket. Replay-style works
without rebuilding the simulator: `hgdb-replay` reads `design.vcd` back
into a virtual simulator that hosts the debug server.

`tools/install.sh all` (or `tools/install.sh hgdb-cli`) handles setup:
it builds a CPython 3.12 venv at `<prefix>/cli-venv`, pip-installs
`hgdb-debugger` + deps, links the hgdb python bindings into it, and
exposes `<prefix>/bin/hgdb` alongside `firtool` and `tywaves`.  The
3.12 split is needed because the shipped `_hgdb.so` is built for
cpython-3.12 -- the workspace `.venv` is 3.11 and can't load it.

Two terminals:

```sh
# Terminal A -- replay server. Listens on ws://localhost:8888.
cd demo/gcd
./run.sh simulate         # writes design.vcd, only needed once
./run.sh debug-server     # foreground; Ctrl-C stops it
```

```sh
# Terminal B -- attach the upstream console client.
cd demo/gcd
./run.sh debug
# (hgdb) b /abs/path/GCD.scala:18
# (hgdb) c
# Breakpoint 2 at GCD.scala:18:9
# 18     x   := io.a
# (hgdb) p io_a
# '0x0030'
# (hgdb) p io_b
# '0x0012'
# (hgdb) c
# Breakpoint 2 at GCD.scala:18:9
# 18     x   := io.a
# (hgdb) exit
```

Commands are gdb-style: `b/break`, `c/continue`, `n/step-over`, `p/print`,
`set`, `watch`, `info`, `list`, plus reverse debugging (`step-back`,
`reverse-continue`/`rc`, `go <timestamp>`) -- the latter only meaningful
under `hgdb-replay`.

`hgdb-replay` and `hgdb-db` both come from the upstream [hgdb][hgdb-runtime]
repo. They're already on `$PATH` if you installed via pipx; otherwise
build them with `cmake --build build --target hgdb-replay-bin hgdb-db`.
`./run.sh debug-server` resolves `hgdb-replay` via `$HGDB_REPLAY`, then
`$PATH`, then a sibling `practice/hgdb/build/` checkout.

### C. VS Code session -- `hgdb-debug` extension

The author's [hgdb-debug][hgdb-cli-repo] also ships a VS Code extension
(`keyiz.hgdb-vscode` on the marketplace) -- the same WebSocket protocol
behind a real DAP-driven UI: breakpoints in the editor margin, locals
panel, watch, call stack.

`demo/.vscode/launch.json` is checked in with one config per demo
(`hgdb: gcd`, `hgdb: fsm`, ...). To use:

1. Open `demo/` as the workspace folder in VS Code.
2. In one terminal: `cd demo/gcd && ./run.sh debug-server` (server on :8888).
3. F5 -> pick `hgdb: gcd`. The extension connects, breakpoints set in
   `app/src/GCD.scala` will pause execution exactly like a normal
   debugger.

For a *live* (non-replay) session you'd link `libhgdb_vpi` into the
simulator instead -- same WebSocket protocol, so the same client attaches.

What matters for the UHDI thesis is that **both tywaves and hgdb
consume the same UHDI JSON** -- projected to `.dd` for tywaves and to
`.db` for hgdb. You don't maintain two separate debug-info builds.

[hgdb-runtime]: https://github.com/Kuree/hgdb
[hgdb-cli-repo]: https://github.com/Kuree/hgdb-debugger

## Use this in your own project

The standalone layout under each `demo/<name>/` is a working starter.
You can copy `demo/fsm/` (or any of them) anywhere on disk and use it
as the skeleton of your own Chisel project.

```text
my-chisel-project/
|-- build.mill           # mill module + chisel JitPack dep
|-- millw                # mill bootstrap (4-line shell wrapper)
|-- .mill-version
|-- app/
|   `-- src/
|       `-- MyTop.scala  # your Chisel design + Main object
`-- run.sh               # build, emit UHDI, convert to HGLDD/HGDB
```

Three things to adapt:

1. **Replace the source.** Drop your Chisel circuit into
   `app/src/MyTop.scala`. The `object Main` you keep should call
   `ChiselStage.emitSystemVerilog` with the same firtool flags so
   firtool emits UHDI alongside the SV:
   ```scala
   object Main extends App {
     val uhdi = "design.uhdi.json"
     ChiselStage.emitSystemVerilog(
       new MyTop,
       args = Array("--with-debug-intrinsics"),
       firtoolOpts = Array(
         "-g", "-O=debug",
         "--emit-uhdi", s"--uhdi-output-file=$uhdi",
         "-o", "MyTop.sv",
       ),
     )
   }
   ```
   `--with-debug-intrinsics` (Chisel) and `-g -O=debug` (firtool) are
   what carry source-level info from `MyTop.scala` all the way to
   `design.uhdi.json`. Drop them and tywaves falls back to flat signals.

2. **Update `run.sh`'s `UHDI_ROOT`.** In a copy that lives outside this
   repo, the converters have to be reachable. Two options:
   - Install the converter package: `pip install -e ./converter` from
     a clone of `fkhaidari/uhdi`, then drop the `PYTHONPATH=` prefix
     from the `python3 -m uhdi_to_hgldd` lines and just call
     `uhdi-to-hgldd design.uhdi.json -o design.dd`.
   - Or keep `PYTHONPATH=` and point `UHDI_ROOT` at the cloned repo.

3. **Bring your own testbench.** Each demo ships an
   `app/src/<Top>Sim.scala` entrypoint -- a `chisel3.simulator`-based
   driver that elaborates the DUT, peeks/pokes Scala-side, and writes
   `design.vcd`. Clone the file structure (declare `HasTestingDirectory`
   and `HasSimulator` implicits, pass `firtoolOpts` with `--emit-uhdi`,
   call `simulate(new MyTop, settings = ...)` with your stimulus, then
   copy `out/sim/workdir-verilator/trace.vcd` to `design.vcd`). The
   single firtool invocation gives you UHDI and SV that match the VCD's
   signal names by construction.

That's it. The `firtool --emit-uhdi` flag and the two python
converters are the only thing the UHDI stack adds on top of a normal
Chisel build.

## Troubleshooting

- **`firtool: command not found`** -- `run.sh` downloads it once into
  `.bin/firtool`. Re-run `./run.sh download-only` or check that
  `docker` / `gh` is on `PATH`.
- **`mill: Could not find or load main class Main`** -- sources have
  to live at `app/src/`, not `src/`. Mill's `object app` convention.
- **Verilator: `Cannot find include file: layers-...-Verification.sv`** --
  firtool emits Verilog tick-include directives for verification
  layers but inlines the bodies in the same file. The demo `run.sh`
  already passes `+define+layers_GCD_Verification_Assert`,
  `+define+layers_GCD_Verification_Assume`, and
  `+define+layers_GCD_Verification_Cover` to short-circuit them; if
  you adapt the script, keep those defines and substitute your own
  top module name for `GCD`.
- **`.dd` and `.db` differ between runs of the same source** -- they
  shouldn't. Both are deterministic projections of `design.uhdi.json`.
  If you see drift, file an issue with the input UHDI document.
- **tywaves shows the right variables but every value is `0`** -- two
  causes, both in the launch command:
  1. Verilator wraps the DUT in an extra `TOP` scope, and
     `chisel3.simulator` adds a further `svsimTestbench` wrapper, so
     the full path is `TOP/svsimTestbench/dut/...`. `--extra-scopes`
     must list all three: `--extra-scopes TOP svsimTestbench dut`.
  2. `--top-module FOO` triggers an internal bundle-pass that reads
     the positional `WAVE_FILE`, repacks Chisel struct ports back into
     their original shape, and writes a derived `<top>.vcd` next to
     it (e.g. `GCD.vcd`). That's the file actually rendered -- the
     log line `Applying startup command: LoadWaveformFile("GCD.vcd")`
     confirms it. The pass needs the scope path right (see #1) to
     copy values across; if you got #1 wrong, the derived file has
     the right hierarchy/widths but every value pinned at `0`.

  `./run.sh simulate` prints the right command verbatim at the end of
  its output -- copy that, don't hand-roll the flags.

