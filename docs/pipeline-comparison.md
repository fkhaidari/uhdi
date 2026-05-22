# UHDI pipeline vs. native pipelines — structural & completeness comparison

This document compares the **UHDI pipeline** (`firtool --emit-uhdi` → Python
projector) against the **native** tool-specific pipelines it replaces, for the
three downstream debug formats: Tywaves HGLDD, hgdb (JSON + SQLite), and
ChiselTrace PDG. The focus is *structural* difference (how each format
organises the same information) and *completeness* difference (what information
each side carries, gains, or loses) — not line-level textual diff.

All claims below are backed by artefacts under `/tmp/bench-diff/` and
`/tmp/uhdi-verify/` produced on 2026-05-22 with the bumped toolchain
(circt `43a716db1`, chisel `f265718cc`, firtool LLVM 23.0.0git).

## 1. The two-producer model

Every downstream format has **two producers** targeting the *same* schema:

| Format | Native producer | UHDI producer | Directly comparable? |
|---|---|---|---|
| Tywaves HGLDD | `firtool --emit-hgldd` | `uhdi_to_hgldd` | yes — `firtool --emit-hgldd` vs `--emit-uhdi`→projector |
| hgdb JSON | hgdb-circt `firtool --hgdb` | `uhdi_to_hgdb_json` | yes |
| hgdb SQLite | hgdb-firrtl (Scala FIRRTL 1.x) | `uhdi_to_hgdb` | yes |
| ChiselTrace PDG | chisel plugin `BuildProgramDependencyGraph` (`ChiselStage(addChiselTrace=true)`) | `uhdi_to_pdg` | yes — both target `pdg_spec.rs` |

The bench harness (`bench/test/test_pipeline.py`) automates the first three as
`ours` vs `native` with per-cell expected-delta tracking in
`bench/manifest.toml`. PDG is added here manually because the bench has no
native PDG cell (historically "ChiselTrace is the sole PDG producer" — but the
chisel plugin *is* a second producer, so the comparison holds).

## 2. Structural difference (by design — not a loss)

The pipelines organise identical facts in deliberately different shapes:

| | UHDI (intermediate) | HGLDD | hgdb | PDG |
|---|---|---|---|---|
| Shape | normalised pools + `*Ref` indirection | denormalised nested `objects[]` | relational (9 ORM tables) | directed graph (`vertices`/`edges`/`predicates`/`cfg`) |
| Type info | shared `types` pool, keyed by id/FQN | inline `type_name` + `enum_defs` per object | per-variable rows | per-vertex `relatedSignal` |
| Identity | stable string ids + refs | array index position | integer PKs + FKs | vertex index |

UHDI's normalised form is the **superset carrier**: each native format is a
*projection* that flattens the pools into its own idiom. Differences of *shape*
between `ours` and `native` (e.g. nesting depth, pool ordering) are expected and
tracked as informational manifest entries, not regressions.

## 3. Comparison matrix (format × information dimension)

`N` = native captures it, `U` = UHDI pipeline captures it, `=` parity,
`▲` = UHDI richer, `▼` = UHDI poorer. Cells cite the worked example.

| Dimension | HGLDD (tywaves) | hgdb | PDG (chiseltrace) |
|---|---|---|---|
| source location (file:line:col) | `=` | `=` | `=` |
| HDL↔source name pairing | `=` (synth `<top>.sv`, §Counter) | `=` | n/a (source-only) |
| scalar/struct/vector types | `=` | `=` | `=` |
| **enum, single-module** | `=` | `=` | n/a |
| **enum, cross-module (shared)** | `▲` UHDI (§EnrichDemo) | `▲` | n/a |
| **generator / ctor params** | `▲` UHDI (§EnrichDemo) | n/a | n/a |
| Bundle `io` → `objects[]` | `▼` known gap (§4) | n/a | n/a |
| instance hierarchy / module path | `=` | `=` | `=` |
| control flow (when/switch) | n/a | `=`* | `=` |
| dataflow edges | n/a | `=`* | `▼` (§Trace) |
| **dynamic mem index** (probe/Index) | n/a | n/a | `▼` UHDI → `<complex>` (§Trace) |
| Vec scalarisation in graph | n/a | n/a | `▼` partial (§Trace) |
| pre-DCE vs post-DCE signals | `▼` post-DCE (§Counter) | `▼` post-DCE | `▼` post-DCE |

`*` complex when-chains currently emit empty hgdb rows on pending fixtures (§4).

## 4. Four categories of difference

Differences fall into four buckets — only the third is a true "UHDI is worse":

1. **By design (structural form).** Pool+ref vs denormalised vs relational vs
   graph (§2). Not a completeness loss.

2. **Pipeline-stage asymmetry: pre-DCE vs post-DCE.** Native hgdb/HGLDD tools
   walk FIRRTL *before* dead-code elimination and see intermediate signals;
   the UHDI pipeline runs *after* DCE. Concretely (Counter): the output port
   `q` survives as its own variable natively, but in post-DCE UHDI it is folded
   into backing wire `r` (`value=r`). This is the single largest, fully
   documented "completeness" delta and is justified per cell in
   `bench/manifest.toml`.

3. **Known converter gaps (UHDI poorer, fixable).**
   - `uhdi_to_hgldd` does not yet project Bundle-typed `io` into HGLDD
     `objects[]` (GCD/Fifo `missing /objects/*`).
   - Complex when/elsewhen chains emit empty hgdb breakpoint/assignment rows.
   - `uhdi_to_pdg` degrades a **dynamic memory index** to a `<complex>`
     predicate sentinel instead of native's probe + `Index` edges (§Trace).
   These fixtures are in bench `_PENDING_FIXTURES`; the gaps predate the
   toolchain bump.

4. **New richness from this toolchain bump (UHDI richer).** Cross-module shared
   enums and generator/constructor parameters now flow producer→consumer
   (§EnrichDemo). Native `firtool --emit-hgldd` does not carry these.

## 5. Worked example A — Counter (baseline parity)

`bench/fixtures/Counter.scala`, all three native targets. Artefacts:
`/tmp/bench-diff/Counter/<target>.{ours,native}.json`.

| Target | total deltas | unexpected | nature |
|---|---|---|---|
| tywaves | 1 | 0 | we synthesise `Counter.sv` for the HDL `file_info` slot; native leaves it `""` (issue #20, so Tywaves can locate the `.vcd`) |
| hgdb_circt | 3 | 0 | post-DCE `q→r`; cosmetic `generator: "uhdi"` vs `"circt"` (not consumed by runtime); nested table shape |
| hgdb_firrtl | 2 | 0 | post-DCE `q→r` (variable + generator_variable rows) |

**Conclusion:** outside the post-DCE asymmetry and one filename convention, the
UHDI projection is byte-equivalent to native. Zero unexpected deltas.

## 6. Worked example B — EnrichDemo (new richness, end-to-end)

`/tmp/uhdi-verify/EnrichDemo.scala`: two modules (`Top`, `Child`) sharing a
top-level `ChiselEnum AluOp`; `Top` parameterised (`width`, `depth`).

- **Cross-module shared enum.** Both `Top.io.op` and `Child.io.op` resolve to
  `typeRef:"AluOp"` (the enum type-pool entry) instead of a bare `uint2`. The
  borrowing module previously degraded to scalar; circt commit
  *propagate enumTypeName/enumFqn through dbg.subfield* fixes this. Reaches
  HGLDD as `enum_defs` + `enum_def_ref`.
- **Generator/ctor params.** `Top → params[{width,Int,8},{depth,Int,4}]`,
  `Child → params[{width,Int,8}]` flow into UHDI `sourceLangType.params` and,
  after this change's `uhdi_to_hgldd` follow-up, into Tywaves
  `source_lang_type_info.params`.

Native `firtool --emit-hgldd` carries **neither** — this information exists only
on the UHDI path.

## 7. Worked example C — Trace PDG (native plugin vs uhdi_to_pdg)

Identical `Trace` module (Reg`Vec(2)` + `when(io.wen)` + dynamic `mem(io.idx)`),
compiled two ways. Artefacts: `/tmp/uhdi-verify/pdg/Trace.{native,uhdi}.pdg.json`.

| | Native (chisel plugin) | UHDI (`uhdi_to_pdg`) |
|---|---|---|
| vertices / edges | 13 / 16 | 18 / 16 |
| predicates (probes) | **1** (`pred_io_wen`) | **0** |
| edge kinds | Data 8, Conditional 3, **Index 3**, Declaration 2 | Data 7, Conditional 4, Declaration 5 |
| Vec `mem` | scalarised → `mem.0`, `mem.1` (clocked `Definition`s) | single `mem` node + `connect_mem_0/1` |
| dynamic `mem(io.idx)` | probe insertion + `Index` edges (resolves each possible index) | `cond_on_[<complex>]` sentinel — index **not** resolved |
| IO / connection nodes | compact (IO 5, Conn 3) | verbose (IO 6, Conn 7); explicit clock/io/connect_* |

**Conclusion:** the native plugin models dynamic memory dataflow precisely
(probes + `Index` edges + scalarised Vec definitions), which `uhdi_to_pdg` does
not — it collapses the dynamic index into the `<complex>` predicate form
(spec §9.3 MVP). UHDI is, conversely, more explicit about IO/clock/connection
vertices. Closing the dynamic-index gap is the main PDG completeness follow-up.

## 8. Worked example D — HGLDD (native --emit-hgldd vs uhdi_to_hgldd)

Two regimes, because HGLDD source-language info has **two unrelated carriers**:
native `--emit-hgldd` reads the old `TywavesAnnotation` (tywaves chisel 6.4.3);
`uhdi_to_hgldd` reads the `circt_debug_*` intrinsics (uhdi chisel 7.1.1). Which
side is richer depends on which chisel produced the `.fir`.

**Regime 1 — tywaves-chisel `.fir` (Counter, bench tywaves target).** Native and
ours both carry `source_lang_type_info.type_name`; parity except the synthesised
`<top>.sv` filename (§5). But the new `firtool --emit-uhdi` **rejects** an
enum design's `EnumComponentAnnotation` from this fork (TrafficLight → exit 1),
so enum fixtures cannot traverse the UHDI side here at all.

**Regime 2 — uhdi-chisel `.fir` (EnrichDemo, same `.fir` into both emitters).**
Artefacts: `/tmp/hgldd-cmp/EnrichDemo.{native,ours}.hgldd.json`.

| | Native `--emit-hgldd` | Ours `uhdi_to_hgldd` |
|---|---|---|
| objects | 2 (`Child`, `Top`) | 3 (+ `Child_io` struct object) |
| `source_lang_type_info` | **none** | `type_name` on every object |
| generator/ctor params | **none** | `Top:[{width,8},{depth,4}]`, `Child:[{width,8}]` |
| `enum_defs` / `enum_def_ref` | **none** | present; `io.op → enum_def_ref:0` (`IO[AluOp]`) |

**Conclusion:** native `--emit-hgldd` does not understand the `circt_debug_*`
intrinsics, so on a uhdi-chisel `.fir` it emits **zero** source-language info —
no types, no enum, no params. The UHDI pipeline extracts the full set. This is
the end-to-end "richer debug info" win of the bump: cross-module enum typing and
generator parameters reach Tywaves only via UHDI. (Structural aside: ours also
projects the Bundle `io` of `Child` into its own `Child_io` struct object;
native flattens it into `port_vars`.)

## 9. Worked example E — hgdb (native hgdb-circt / hgdb-firrtl vs uhdi_to_hgdb)

Two native producers: modern hgdb-circt firtool (`--hgdb`, JSON) and legacy
hgdb-firrtl (Scala FIRRTL 1.x, SQLite). Both run on stock-chisel `.fir`
(pre-DCE), the UHDI side on post-DCE `--emit-uhdi`.

**JSON path (hgdb-circt, GCD).** Top-level structure is identical
(`generator`/`top`/`variables[]`/`table[]`, 3 variables, 1 module scope tree).
The difference is the *variable set*, driven by DCE stage:

| | Native (pre-DCE) | Ours (post-DCE) |
|---|---|---|
| variables | `io.rdy`, `io.q`, `busy` | `x`, `y`, `busy` |

Native exposes the **output ports** as named watch variables; UHDI exposes the
**backing registers** the ports were folded into. Same count, different debug
surface — native lets you watch `io.q`, UHDI lets you watch `x`. Plus the
cosmetic `generator: "uhdi"` vs `"circt"` (not consumed by the hgdb runtime).

**SQLite path (hgdb-firrtl, Counter, 9 ORM tables).** Here UHDI is **richer**:

| table | native | ours |
|---|---|---|
| context_variable | 0 | **30** |
| scope | 0 | 1 |
| annotation | 0 | 1 |
| breakpoint | 5 | 6 |
| variable / generator_variable / assignment / instance | 5 / 5 / 2 / 1 | 5 / 5 / 2 / 1 |

The legacy producer emits **no** `context_variable` rows (which variables are
live at each breakpoint scope) and no `scope` tree; `uhdi_to_hgdb` emits a full
30-row context mapping plus the scope/annotation rows. For a stepping debugger
this is a concrete completeness gain: UHDI knows per-breakpoint variable
visibility that hgdb-firrtl never recorded.

**Conclusion:** hgdb shows completeness moving in *both* directions — UHDI loses
pre-DCE port names (JSON path) but gains per-breakpoint context scoping (SQLite
path). Neither is a regression from the bump; both are inherent to operating on
post-DCE UHDI with a richer statement tree.

## 10. Summary: where UHDI gains and loses

- **UHDI gains** (vs native): cross-module enum typing + generator/ctor params
  reaching HGLDD (native `--emit-hgldd` extracts none of these from
  `circt_debug_*` intrinsics, §8); per-breakpoint `context_variable` scoping in
  hgdb SQLite (legacy producer emits zero, §9); a single normalised carrier
  feeding all three formats from one pass.
- **UHDI loses** (vs native): pre-DCE intermediate signals incl. output-port
  watch names (post-DCE asymmetry, inherent, §9); Bundle→`objects[]` projection
  on some designs (fixable gap); dynamic-mem-index precision in PDG (fixable
  gap, §7).
- **Parity** everywhere else: locations, scalar/struct/vector types,
  single-module enums, hierarchy, static control flow, hgdb top-level shape.

## 11. Reproduce

```sh
# HGLDD / hgdb (ours vs native), per fixture (tywaves/hgdb_circt/hgdb_firrtl):
FIRTOOL=../circt/build/bin/firtool .venv/bin/python -m uhdi_bench.dump_pair \
    bench/fixtures/Counter.scala -o /tmp/bench-diff
# -> code --diff /tmp/bench-diff/Counter/tywaves.{ours,native}.json

# HGLDD regime 2 — native --emit-hgldd vs uhdi_to_hgldd on the SAME uhdi-chisel
# .fir (shows native emits no source-lang info from circt_debug_* intrinsics):
#   runner._emit_native_hgldd(EnrichDemo.fir, wd, firtool)  vs
#   get('hgldd').convert(EnrichDemo.uhdi.json, None)
# hgdb SQLite 9-table inventory: run_target(fir, "hgdb_firrtl", tc) -> (ours, native)

# PDG native (chisel plugin) — emits pdg.json:
COURSIER_MIRRORS=/dev/null scala-cli run /tmp/uhdi-verify/pdg/TraceNative.scala
#   ChiselStage(withDebug=false, addChiselTrace=true), --target chirrtl

# PDG via UHDI:
COURSIER_MIRRORS=/dev/null scala-cli run /tmp/uhdi-verify/pdg/TraceUhdi.scala > t.fir
../circt/build/bin/firtool -g --emit-uhdi --uhdi-output-file=t.uhdi.json -o t.sv t.fir
.venv/bin/python -c "import json,sys; sys.path.insert(0,'converter/src'); \
  from uhdi_common.backend import discover,get; discover(); \
  json.dump(get('pdg').convert(json.load(open('t.uhdi.json')),None), open('t.pdg.json','w'))"
```
