# UHDI demos

Five small Chisel projects that exercise the UHDI debug stack end-to-end:
Chisel design → FIRRTL → `firtool --emit-uhdi` → UHDI JSON →
`uhdi-to-hgldd` / `uhdi-to-hgdb` → debug info that tywaves and hgdb consume.

Each subdirectory is a **standalone Chisel project** with its own
`build.mill`, `millw`, and `run.sh`. You can copy any one of them out of
this repository, drop it next to your own circuit, and use it as a
starter template (see [Use this in your own project](#use-this-in-your-own-project)).

| Demo | Top module | What it exercises |
|------|-----------|-------------------|
| `gcd/` | `GCD` | Plain UInt arithmetic, single module. The simplest end-to-end. Ships a testbench (`tb.sv`) for `--simulate`. |
| `fsm/` | `TrafficLight` | `ChiselEnum` state. Tywaves should render the `state` register as `Red / RedYellow / Green / Yellow`, not as `2'b00 / 01 / 10 / 11`. |
| `fifo/` | `Fifo` | `Decoupled<UInt>` ports + `SyncReadMem`. Tywaves groups `valid / ready / bits` as a struct; the SyncReadMem appears as its own scope. |
| `pipeline/` | `Pipeline` | 3-stage MAC with `MulStage` / `AddStage` as separate `Module`s. Hierarchy navigation in tywaves; hgdb steps a value across the pipeline registers cycle by cycle. |
| `bus/` | `MemController` | `Decoupled` carrying nested `Bundle`s (`Request{addr, data, write}` → `Response{data, ok}`). Flexes nested-record rendering and a single-in-flight handshake. |

## Quick start

### Prerequisites
- Java 21+ (mill needs it)
- Python 3 (for the UHDI converters)
- Either Docker / podman (preferred — pulls a prebuilt image with firtool)
  or `gh` CLI authenticated with a token that can read public releases
- Verilator (only for `./run.sh --simulate`)

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
| `design.dd` | HGLDD projection — what tywaves reads |
| `design.db` | HGDB SQLite projection — what the hgdb runtime reads |
| `<TopModule>.sv` | SystemVerilog firtool emitted alongside |

### Generate a waveform
```sh
./run.sh --simulate           # gcd only out of the box; see TBs below
ls design.vcd
```

## Open in tywaves

tywaves takes the `.dd` (type info from UHDI) and a `.vcd` (raw signals
from any simulator) and shows you the design **with Chisel-source-level
types preserved**:

```sh
tywaves design.dd design.vcd
```

What you get that a flat waveform viewer can't show you:
- `ChiselEnum` registers as `Red / Green / ...`, not raw bits
  (try the `fsm/` demo).
- `Decoupled[T]` and other `Bundle`s as collapsible structs with
  `valid / ready / bits` grouped together (try `fifo/` and `bus/`).
- `Module` hierarchy — `Pipeline / mul / add` instead of one flat
  signal soup (try `pipeline/`).

The same `design.dd` works regardless of which simulator produced the
`.vcd`. Verilator, iverilog, VCS, Xcelium — UHDI was built so the type
layer lives independently of the simulator.

## Open in hgdb

The hgdb runtime reads `design.db` (UHDI's HGDB projection) plus your
simulator's trace and lets you set breakpoints, step, and inspect by
**Chisel source line**, not Verilog signal name.

```sh
# Add the bundled python bindings to your shell:
export HGDB_PY="$HOME/.local/uhdi-tools/lib/hgdb/bindings/python"
export PYTHONPATH="$HGDB_PY:$HGDB_PY/build/lib.linux-x86_64-cpython-312:$PYTHONPATH"

# In one terminal: launch your simulation with the hgdb VPI shim
#   (verilator: run with --vpi and pre-load libhgdb_vpi)
# In another terminal: attach the debugger to design.db.
python -m hgdb design.db
```

The exact "attach the debugger" command depends on your setup; see
the [hgdb runtime documentation][hgdb-runtime] for the protocol details.
What matters here is that **both tywaves and hgdb consume the same UHDI
JSON** — it's projected to `.dd` for tywaves and to `.db` for hgdb.
You don't have to maintain two separate debug-info builds.

[hgdb-runtime]: https://github.com/fkhaidari/hgdb

## Use this in your own project

The standalone layout under each `demo/<name>/` is a working starter.
You can copy `demo/fsm/` (or any of them) anywhere on disk and use it
as the skeleton of your own Chisel project.

```text
my-chisel-project/
├── build.mill           # mill module + chisel JitPack dep
├── millw                # mill bootstrap (4-line shell wrapper)
├── .mill-version
├── app/
│   └── src/
│       └── MyTop.scala  # your Chisel design + Main object
└── run.sh               # build, emit UHDI, convert to HGLDD/HGDB
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

3. **Bring your own testbench.** Each demo's `tb.sv` is hand-written
   SystemVerilog because it's the most portable form for a one-shot
   demo (verilator builds it directly). For a real project you'll
   typically write tests in Chisel via `chisel3.simulator` or
   `chiseltest` — both produce a `.vcd` that tywaves consumes
   together with the `design.dd` you just generated.

That's it. The `firtool --emit-uhdi` flag and the two python
converters are the only thing the UHDI stack adds on top of a normal
Chisel build.

## Troubleshooting

- **`firtool: command not found`** — `run.sh` downloads it once into
  `.bin/firtool`. Re-run `./run.sh --download-only` or check that
  `docker` / `gh` is on `PATH`.
- **`mill: Could not find or load main class Main`** — sources have
  to live at `app/src/`, not `src/`. Mill's `object app` convention.
- **Verilator: `Cannot find include file: layers-…-Verification.sv`** —
  firtool emits `\`include` directives for verification layers but
  inlines the bodies. The demo `run.sh` already passes
  `+define+layers_<Top>_Verification_*` to short-circuit the includes;
  if you adapt the script, keep those defines.
- **`.dd` and `.db` differ between runs of the same source** — they
  shouldn't. Both are deterministic projections of `design.uhdi.json`.
  If you see drift, file an issue with the input UHDI document.
