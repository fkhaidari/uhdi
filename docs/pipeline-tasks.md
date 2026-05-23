# Pipeline tasks for chisel -> circt -> uhdi (from torture fixture runs)

Document derived from running three torture fixtures (`bench/fixtures/{Hgdb,Tywaves,ChiselTrace}Torture.scala`)
via both paths: **native** (firtool `--emit-hgldd` / hgdb-firrtl / chiseltrace plugin) and
**UHDI + converter** (`firtool --emit-uhdi` -> `uhdi_to_{hgldd,hgdb,pdg}`).
Date: 2026-05-23 (updated 2026-05-23). CIRCT pin `43a716db1`, chisel uhdi-fork `7.1.1+210-c6faff5e`, chiseltrace chisel-fork `6.4.3-tywaves-chiseltrace-SNAPSHOT`.

**Experimental verification 2026-05-23:** all three torture fixtures ran through `bench/compare_fixture.py` with 0 surprises — HgdbTorture/hgdb_firrtl (46/46), TywavesTorture/tywaves (9/9), ChiselTraceTorture/pdg (246/246). Open tasks T2, T3, T4, T8, T10, T11, T13, G1-G7 confirmed via live pipeline run; no regressions detected.

Each task is tagged with the layer where it should be fixed:
- **[CIRCT]** -- bug/gap in CIRCT passes (EmitUHDI etc.); information does not reach the UHDI document.
- **[CONV]** -- gap in the Python converter; information is present in UHDI but lost/distorted during projection.
- **[SPEC]** -- the UHDI schema cannot express what is needed; requires a spec extension.
- **[ENV]** -- toolchain/environment limitation (not a pipeline defect).

Priority: **P0** breaks the pipeline (no output), **P1** loses data, **P2** cosmetic/edge case.

---

## Tool summary

| Tool | Native baseline | UHDI side | Verdict |
|---|---|---|---|
| **Tywaves** (HGLDD) | rameloni firtool `-g --emit-hgldd` (rameloni-chisel compiled with `withDebug=true`): 10 objects, enum_defs, **43 SLT**, Vec[Bundle] scalarised (bvec_0/1/2/3) | converter: 6 objects, enum_defs, ctor-params, 43 SLT | SLT/params **at parity**; native richer in Vec[Bundle] scalarisation (T8); ours richer in `file_info[1]` (issue #20) |
| **hgdb** (SQLite) | hgdb-firrtl: 53 bp, 3 instance, 29 var, 22 assignment; hgdb-circt hangs (E2) | converter: 69 bp, 3 instance, 31 var, 28 assignment, scope/context_var/annotation tables | ours **richer** than native (more bp, scope, context_var, annotation); 46 deltas classified as G1-G7 (CIRCT naming + CONV dotted-name gaps); **live runtime verified 2026-05-23** (GCD demo: bp set, hit, variables readable) |
| **ChiselTrace** (PDG) | chiseltrace plugin: 76 vertices, 129 edges (Data 63 / Declaration 35 / Conditional 20 / **Index 11**), 4 probe predicates, Vec scalarisation (`mem.0`/`mem.1`/`regs.0..3`) | converter: 102 vertices, 77 edges (Data + Declaration + Conditional; **no Index**), 0 predicates, aggregates kept | UHDI path **runs**; 5 [OK] cases at parity; 3 [CIRCT] gaps (T2): probe/Index/scalarisation absent |

---

## P1 -- data loss

### T2 [CIRCT] Probe insertion / Index edges / Vec scalarisation for PDG are absent

The native chiseltrace plugin on the same design produces (smoke test, 39 KB pdg.json):
- **11 Index edges** -- linking the dynamic index to the probe signal;
- **4 probe predicates** (`pred_io_en`, `pred_io_we`, `pred_io_cond`, `stage.pred_io_en`);
- **Vec scalarisation**: `mem` -> `connect_mem.0` / `connect_mem.1` with `relatedSignal.fieldPath`.

Probes are inserted in `insertVectorProbes()`/`insertMuxProbes()` **before** emission (on FIRRTL).
`firtool --emit-uhdi` does not produce them -> the UHDI document contains no probe signals or Index edges;
the converter physically cannot reconstruct them. This is a **producer-gap**, not a converter issue.

**Fix:** a new CIRCT pass that emits probe metadata into UHDI (Sec.10 dataflow + Sec.15.5.1).
Dynamic Vec index (SSA dominance) is resolved on CIRCT pin `43a716db1` -- T2 can be tackled directly.

### T3 [CIRCT] Compound guard collapses into `_GEN`

`when(io.a && io.b && !io.c)` in `HgdbTorture` -> hgdb condition `!reset && _GEN & (io_c ^ 1)`.
The sub-expression `io.a && io.b` is folded by CIRCT into an intermediate node `_GEN`, and the converter
emits the raw token `_GEN` (1 breakpoint out of 69). The rest of the structure (`!io_c`, reset, AND) round-trips correctly.

**Note:** the old `<complex>`-gap on this pin is **closed** -- CIRCT names sub-terms (`expr_*`/`var_*`),
a three-level guard stack resolves fully (`!reset && io_sel && io_in != 0 && reg_0 < io_in`),
hex literals too (`io_opcode == 10`). Only the `_GEN` folding remains.

**Fix:** EmitUHDI -- expand `_GEN` nodes back into named sub-expressions,
or add `_GEN` to the expressions pool with its own definition so the converter can unfold it.

### T4 [CIRCT] Non-standard clock is not annotated

`HgdbTorture.Sub` runs on an explicit `myClk` (via `withClock`). The hgdb UHDI side emits
the `clock` annotation only for `HgdbTorture.clock` (top), but **not** for `sub0/sub1.myClk`.
The hgdb heuristic `_CLOCK_NAMES` ({clk,clock,...}) will not find `myClk` -> live-debug of sub-instances
will not get a posedge callback.

**Fix:** EmitUHDI -- emit the clock role for all clock-typed signals, not only by name;
the converter already knows how to carry this over into the annotation table.

### T5 [CIRCT] Native PDG loses `mem.read` via IsInvalid

Even native chiseltrace on `mem.read(io.rdAddr)` warns:
```
Unsupported statement encountered during PDG building
IsInvalid(@[ChiselTraceTorture.scala 87:22], Reference(_io_dout_WIRE, UnknownType))
```
The plugin skips the statement. This is a native-side gap: the SyncReadMem read port is not represented in the PDG.

**Fix:** verify whether UHDI emits the memory read port in Sec.7 body as a connect (UHDI PDG now exists).
If so, this is a potential advantage over native; needs empirical confirmation.

---

## P2 -- converter / cosmetic

### T11 [CONV] `falseBranch` always `null` in PDG CFG

`_walk_body` in `uhdi_to_pdg/convert.py` never populates `falseBranch` (always `None`, line 344).
UHDI §7 supports `.otherwise` as a second `block` with `negated: true` on the same `guardRef`.
GraphBuilder in chiseltrace (`graphbuilder.rs:351`) does process `false_branch` when present.

**Impact:** designs with `when/otherwise` produce a PDG where the else-branch is silently dropped.
Dynamic slicing will miss activations that go through the false branch.

**Fix:** `uhdi_to_pdg/_walk_body` -- detect consecutive `block` statements sharing the same
`guardRef` where the second has `negated: true`; emit the second as `falseBranch` of the first
CFG entry. Scope is purely the Python converter; no CIRCT change needed.

### T12 [CONV] `modulePath` uses first parent only (multi-instance PDG lookup broken)

`_module_path` in `uhdi_to_pdg/convert.py` follows only the first entry in `ctx.parents[scope_id]`
(line 129). When a module is instantiated more than once, every vertex gets the path of the first
instantiation. ChiselTrace's `find_var` in `graphbuilder.rs` uses `modulePath` to locate probe
signals in the VCD; a wrong path causes `VariableNotFoundError` at runtime.

**Impact:** multi-instance designs (e.g. `pipeline` demo with two stage sub-modules) will fail
the ChiselTrace VCD probe lookup for the second (and later) instances.

**Fix:** `uhdi_to_pdg` -- either (a) emit one vertex copy per instantiation path (mirrors how
chiseltrace's native plugin handles multi-instance), or (b) document as a known limitation scoped
to single-instantiation designs (sufficient for all current demo fixtures). Low priority while
demo fixtures are single-instance.

### T13 [SPEC] §15.5.4 promises Index edges from `--derive-dataflow` but implementation cannot deliver

Spec §15.5 (line 2226 of `uhdi-spec.md`) states:
> «The derivation walks Sec.5 expression trees and Sec.7 body connects to synthesise
> `Data` / `Conditional` / `Index` / `Declaration` edges»

This is a false promise: Index edges require probe-variable metadata (`bindKind: "probe"`) that
firtool does not yet emit (T2). Without probes in the UHDI document the derivation pass has no
basis to construct Index edges or per-cell conditions on Connection vertices. The current
`_derive_edges` in `convert.py` generates only Data / Declaration / Conditional.

**Fix (spec):** rewrite §15.5.4 to state that Index edge derivation requires either (a) §10
dataflow in the input, or (b) probe metadata from the CIRCT pass (T2). Until T2 lands, derivation
is limited to Data / Declaration / Conditional. Mark as known limitation rather than a planned feature.

**Fix (code, blocked on T2):** once CIRCT emits probe vars, extend `_derive_edges` to detect
memory-port connect patterns and emit Index edges with per-value conditions.

### T6 [CONV] Delayed context_variable not emitted

`HgdbTorture.Sub` holds a 3-level shift history `reg` (hist0/hist1/hist2). hgdb supports
`context_variable.type=1` with `depth>1` (value N cycles ago), but the converter stores history registers
as ordinary signals (type=0). Loss: the debugger will not show "value of reg 2 cycles ago".

**Fix:** `uhdi_to_hgdb` -- detect RegNext chains and emit delayed context_variable.
A [SPEC] annotation in UHDI about delay depth may also be needed.

### T8 [CONV] Vec[Bundle] not scalarised into per-element struct objects

`TywavesTorture.io.bvec: Vec(4, new Inner)` -- the rameloni native baseline produces 4 separate
struct objects (`TywavesTorture_io_bvec_0` / `_1` / `_2` / `_3` + a parent `TywavesTorture_io_bvec`).
The UHDI converter emits only `TywavesTorture_io_packet_inner` (the nested Bundle) but not the
Vec[Bundle] element objects. Result: tywaves cannot index into individual bvec elements.

Confirmed: ours=6 objects, native=10 objects; 4 missing are the Vec element structs.

**Fix:** `uhdi_to_hgldd` -- detect Vec-of-struct types and emit one struct object per element,
mirroring how `_first_vector_element_sig` names element 0.

### T10 [CIRCT] Constant operands in expressions lack `width` → `integer_num` instead of `bit_vector`

Discovered on `TrafficLight` fixture (2026-05-23). In the expression `state != 0` (enum comparison),
firtool `--emit-uhdi` emits the constant operand as `{"constant": 0}` with no `width` field.
The converter `_terminal_to_hgldd` falls back to `{"integer_num": 0}` because `width` is absent.
Native firtool `--emit-hgldd` emits `{"bit_vector": "00"}` (2-bit zero, matching the enum width).

Root cause: firtool knows the FIRRTL type of every expression operand at emission time but does not
record `width` on constant nodes in expression context. The `width` field is already written in
value-binding context (`hdlValue`); the expression operand path is an omission.

**Fix:** EmitUHDI (C++) -- when emitting a constant operand inside an expression, write
`"width": <bitwidth>` alongside `"constant"`. The converter already handles it at `convert.py:266`
(the `w > 0` branch); no converter change needed.

---

## What round-trips without loss ([OK], confirmed empirically)

- **Tywaves**: nested Bundle (6 struct objects), enum_defs + enum_def_ref, ctor-params,
  multi-dimensional Vec, `source_lang_type_info` on module and port_vars (43 entries each side --
  **at parity** since 2026-05-23: native pipeline now uses `new ChiselStage(withDebug=true)` →
  TywavesAnnotation in FIR → rameloni firtool `-g` → SLT).
  Remaining gap: Vec[Bundle] scalarisation (T8); native emits 4 element struct objects that ours lacks.
  Minor cosmetic divergences: `params` key `"type"` (ours) vs `"typeName"` (native) -- both accepted by
  tywaves-rs via serde alias; `file_info[0]` leading `/`; `file_info[1]` empty vs `<Top>.sv`.
- **hgdb**: 3-level guard stack, hex literals, dotted names of nested bundles (`io.data.x`),
  instance hierarchy (3 instances: top + sub0 + sub1), top-clock annotation,
  multi-site data-breakpoint (both writes to `acc` produce an assignment row).
- **ChiselTrace PDG** (5 [OK] cases confirmed 2026-05-23): combinational chain (Data edges a→b→c),
  nested when/elsewhen/otherwise (3-level Conditional edges), register with reset (`clocked=true`),
  assert statement (ControlFlow vertex), submodule instantiation (cross-module
  Data edges). All 246 deltas vs native are known CIRCT gaps (T2) documented in `bench/manifest.toml`.

---

## Environment limitations ([ENV], not pipeline defects)

- **E2**: hgdb-circt native (`firtool --hgdb`) hangs for >300 s on `HgdbTorture` (LLVM-16 legacy fork).
  Skipped; hgdb-firrtl is the baseline (hgdb-firrtl native rebuilt for python-3.12 on 2026-05-23).

---

## hgdb-firrtl vs UHDI comparison (empirical, 2026-05-23)

`compare_fixture.py fixtures/HgdbTorture.scala --targets hgdb_firrtl` produced
46 deltas, all `missing` (native entries not found in ours). UHDI side is **richer** in every table
(more bp, scope, context_var, annotation); the deltas arise from CIRCT naming differences and two
CONV gaps:

### G1 [CIRCT] Intermediate breakpoints absent (lines 80-82, sub instances, 12 bp)

FIRRTL 1.x emits one breakpoint per statement *operand* (column points to the LHS token, col=18),
giving additional bp at the assignment target position. CIRCT emits one bp per statement (col=27).
FIRRTL 1.x also expands `_T`/`_T_1` intermediate nodes back into the full boolean expression in
the condition string; CIRCT preserves the node names.

**Impact:** a debugger using the native baseline would pause at col=18 (token start); ours pauses
at col=27 (expression end). Semantically the same line, different column.

### G2 [CIRCT] `!reset && 1` simplified to `!reset` (lines 91-93, 6 bp)

CIRCT constant-folds `x && 1` → `x` in guard expressions; FIRRTL 1.x preserves the literal `1`.
Result: 6 breakpoints with condition `!reset && 1` in native are not matched by ours (`!reset`).

**Impact:** cosmetic. Both conditions are logically equivalent; debugger behaviour is identical.

### G3 [CIRCT] Extra intermediate breakpoints at line 125 (3 bp)

Native emits 4 bp at line 125 (col=13, 21, 24, 31); ours emits only col=31 (the outermost
expression). The extra bp at col=13/21/24 cover sub-expressions of the same compound statement.

**Impact:** same as G1 -- column-level granularity difference.

### G4 [CIRCT] Condition token name mismatch for compound guard (lines 126/131/132, 3 bp)

This is the T3 gap seen from the native side: FIRRTL 1.x names the compound-guard intermediate
node `_T_2`/`_T_3`; CIRCT names it `_GEN`. The logical condition is identical; the identifier
differs between the two frontends. Additionally, ours expands `io.opcode == 10` (hex literal)
while native writes `_T_3`.

**Impact:** debugger condition string differs but evaluates identically at runtime.

### G5 [CIRCT] Vec register RTL name: `reg_` (native) vs `reg_0` (ours, 4 entries)

FIRRTL 1.x names the first Vec element `reg_` (no index suffix); CIRCT names it `reg_0`.
Affects `variable` and `generator_variable` tables (2 entries each).

**Impact:** if the debugger looks up the variable by RTL name, `reg_` vs `reg_0` will mismatch.
A one-time rename in the converter would align the two.

### G6 [CONV] assignment.value uses UHDI dotted names vs hgdb-firrtl flat names

The converter writes `variable_value` from UHDI directly into `assignment.value`. UHDI uses
dotted Chisel names (`io.out`, `io.q`, `sub0.io_in`); hgdb-firrtl uses flat RTL names
(`io_out`, `io_q`, `sub0.io.in`). The naming conventions are inverted for `io.*` vs `sub.*`.

**Impact:** a debugger resolving the assignment target by value string may fail to match the RTL
signal if it uses the hgdb-firrtl convention.

**Fix:** `uhdi_to_hgdb` -- normalise `assignment.value` to flat RTL convention (replace `.` with
`_` for port paths, keep `subname.io.field` for cross-module references).

### G7 [CONV] assignment breakpoint_key mismatches due to G4

The `breakpoint_key` in the assignment table is derived from the breakpoint condition string.
When ours writes `_GEN` (G4) and native writes `_T_2`, the key strings differ and the superset
check cannot find the matching native assignment row. Affects `acc` assignments at L126/132.

**Fix:** resolves automatically if G4 (T3) is fixed in CIRCT.

---

## hgdb runtime verification (live test, 2026-05-23)

`hgdb-replay` + `HGDBClient` (Python websocket client) used against `demo/gcd/` to confirm
the uhdi→hgdb pipeline produces a working debug database.

**Setup:** `design.db` generated via `uhdi_to_hgdb design.uhdi.json -o design.db`;
`hgdb-replay design.vcd --port 8888` (debug.db picked up from cwd); client connects via `ws://localhost:8888`.

**Results:**

| Check | Result |
|---|---|
| Server sees correct source file | `GCD.scala` ✓ |
| `set_breakpoint(GCD.scala, 20)` | `success` ✓ |
| Conditional bp hit (`!reset && io_en`) | time=35, column=10 ✓ |
| Variables readable at hit | `io_a=0x30`, `io_b=0x12`, `io_en=1`, `reset=0`, `x=1`, `y=1`, `busy=4`, `io_rdy=1` ✓ |

The conditional breakpoint on line 20 (`!reset && io_en`) fired at simulation time 35 with correct
variable values in both `local` and `generator` frames. The uhdi→hgdb path is **functionally
equivalent** to the native hgdb-firrtl path for interactive debugging.

**Known remaining gaps (not blocking runtime):**

- `context_var` = 0: intermediate Wire/Node variables not visible in frame view (same limitation as hgdb-firrtl)
- `Fifo` fixture: `invalidate` op unsupported by both hgdb-circt and hgdb-firrtl -- environment issue, not a pipeline defect
- `TrafficLight` fixture: pending (Bundle/complex-when, risk register)
- `HgdbTorture` live run: not yet verified (only static diff done); sub-module clock (T4) and `_GEN` condition (T3/G4) may affect sub-instance bp

---

## Prioritization for the pipeline

```
T2  (P1, CIRCT) --> probe insertion / Index edges / Vec scalarisation in PDG
T3  (P1, CIRCT) --> accurate hgdb conditions (compound guard _GEN / G4)
T4  (P1, CIRCT) --> multi-clock live-debug (non-standard clock annotation)
T5  (P1)        --> UHDI > native for mem.read (IsInvalid gap in native)
T11 (P1, CONV)  --> PDG falseBranch always null (when/otherwise else-branch dropped)
T13 (P1, SPEC)  --> §15.5.4 false promise: Index derivation blocked on T2; spec must document the limitation
T6  (P2, CONV)  --> hgdb delayed context_variable (RegNext chains)
T8  (P2, CONV)  --> tywaves Vec[Bundle] element struct objects
T10 (P2, CIRCT) --> constant operand width missing → integer_num vs bit_vector
T12 (P2, CONV)  --> PDG modulePath multi-instance (VCD probe lookup broken; low pri while demos are single-instance)
G1  (P2, CIRCT) --> intermediate bp column granularity (col=18 vs col=27)
G2  (P2, CIRCT) --> `!reset && 1` vs `!reset` condition simplification
G3  (P2, CIRCT) --> extra sub-expression bp at L125
G5  (P2, CIRCT) --> Vec register RTL name `reg_` vs `reg_0`
G6  (P2, CONV)  --> assignment.value dotted vs flat name convention
G7  (P2, CONV)  --> assignment breakpoint_key mismatch (derived from G4/T3)
```

T2 is the main remaining PDG gap: probe/Index edge synthesis requires a new CIRCT pass.
G6 is the only actionable CONV fix independent of CIRCT; the rest resolve if T3/G4 is fixed.
