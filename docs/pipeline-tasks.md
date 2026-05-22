# Pipeline tasks for chisel → circt → uhdi (from torture fixture runs)

Document derived from running three torture fixtures (`bench/fixtures/{Hgdb,Tywaves,ChiselTrace}Torture.scala`)
via both paths: **native** (firtool `--emit-hgldd` / hgdb-firrtl / chiseltrace plugin) and
**UHDI + converter** (`firtool --emit-uhdi` → `uhdi_to_{hgldd,hgdb,pdg}`).
Date: 2026-05-23. CIRCT pin `43a716db1`, chisel uhdi-fork `7.1.1+210-c6faff5e`.

Each task is tagged with the layer where it should be fixed:
- **[CIRCT]** — bug/gap in CIRCT passes (EmitUHDI etc.); information does not reach the UHDI document.
- **[CONV]** — gap in the Python converter; information is present in UHDI but lost/distorted during projection.
- **[SPEC]** — the UHDI schema cannot express what is needed; requires a spec extension.
- **[ENV]** — toolchain/environment limitation (not a pipeline defect).

Priority: **P0** breaks the pipeline (no output), **P1** loses data, **P2** cosmetic/edge case.

---

## Tool summary

| Tool | Native baseline | UHDI side | Verdict |
|---|---|---|---|
| **Tywaves** (HGLDD) | firtool `--emit-hgldd`: 2 objects, 0 `source_lang_type_info` | converter: 6 objects, 29 SLT, enum_defs, ctor-params | UHDI **richer** than native |
| **hgdb** (SQLite) | hgdb-firrtl unavailable ([ENV]), hgdb-circt hangs | converter: 69 bp, 3 instance, dotted names, clock annotation, 28 assignment | UHDI side complete; native unavailable in this environment |
| **ChiselTrace** (PDG) | chiseltrace plugin: 76 vertices, 129 edges (Data 63 / Declaration 35 / Conditional 20 / **Index 11**), 4 probe predicates, Vec scalarisation (`mem.0`/`mem.1`) | **firtool --emit-uhdi crashes** (P0 CIRCT bug) | UHDI path **does not run** on dynamic index |

---

## P0 — blockers (no output)

### T1 [CIRCT] EmitUHDI breaks on dynamic Vec index (SSA dominance)

`firtool -g --emit-uhdi` on `ChiselTraceTorture` (uhdi-fork fir) exits with code 1:

```
error: operand #1 does not dominate this use
note: see current operation: %81 = "dbg.expression"(%9, %85)
      <{name = "__uhdi_expr_ChiselTraceTorture_0", opcode = "==", ...}>
note: operand defined here (op in a child region)
    regs(io.sel) := io.din          // ChiselTraceTorture.scala:101
```

The `dbg.expression` for the dynamic Vec-register index (`regs(io.sel)`) is generated inside
a child region (when-block), but the operand is defined outside — an SSA dominance violation.
The entire UHDI document is not emitted → PDG/HGLDD/hgdb cannot be obtained for such a design.

**Fix:** EmitUHDI pass (CIRCT) — lift `dbg.expression` into a dominating scope or
materialize operands locally. Until fixed, any design with a dynamic Vec-write fails UHDI.

---

## P1 — data loss

### T2 [CIRCT] Probe insertion / Index edges / Vec scalarisation for PDG are absent

The native chiseltrace plugin on the same design produces (smoke test, 39 KB pdg.json):
- **11 Index edges** — linking the dynamic index to the probe signal;
- **4 probe predicates** (`pred_io_en`, `pred_io_we`, `pred_io_cond`, `stage.pred_io_en`);
- **Vec scalarisation**: `mem` → `connect_mem.0` / `connect_mem.1` with `relatedSignal.fieldPath`.

Probes are inserted in `insertVectorProbes()`/`insertMuxProbes()` **before** emission (on FIRRTL).
`firtool --emit-uhdi` does not produce them → the UHDI document contains no probe signals or Index edges;
the converter physically cannot reconstruct them. This is a **producer-gap**, not a converter issue.

**Fix:** a new CIRCT pass that emits probe metadata into UHDI (§10 dataflow + §15.5.1).
Depends on T1 (without it, designs with dynamic index never reach emission at all).

### T3 [CIRCT] Compound guard collapses into `_GEN`

`when(io.a && io.b && !io.c)` in `HgdbTorture` → hgdb condition `!reset && _GEN & (io_c ^ 1)`.
The sub-expression `io.a && io.b` is folded by CIRCT into an intermediate node `_GEN`, and the converter
emits the raw token `_GEN` (1 breakpoint out of 69). The rest of the structure (`!io_c`, reset, AND) round-trips correctly.

**Note:** the old `<complex>`-gap on this pin is **closed** — CIRCT names sub-terms (`expr_*`/`var_*`),
a three-level guard stack resolves fully (`!reset && io_sel && io_in != 0 && reg_0 < io_in`),
hex literals too (`io_opcode == 10`). Only the `_GEN` folding remains.

**Fix:** EmitUHDI — expand `_GEN` nodes back into named sub-expressions,
or add `_GEN` to the expressions pool with its own definition so the converter can unfold it.

### T4 [CIRCT] Non-standard clock is not annotated

`HgdbTorture.Sub` runs on an explicit `myClk` (via `withClock`). The hgdb UHDI side emits
the `clock` annotation only for `HgdbTorture.clock` (top), but **not** for `sub0/sub1.myClk`.
The hgdb heuristic `_CLOCK_NAMES` ({clk,clock,...}) will not find `myClk` → live-debug of sub-instances
will not get a posedge callback.

**Fix:** EmitUHDI — emit the clock role for all clock-typed signals, not only by name;
the converter already knows how to carry this over into the annotation table.

### T5 [CIRCT] Native PDG loses `mem.read` via IsInvalid

Even native chiseltrace on `mem.read(io.rdAddr)` warns:
```
Unsupported statement encountered during PDG building
IsInvalid(@[ChiselTraceTorture.scala 87:22], Reference(_io_dout_WIRE, UnknownType))
```
The plugin skips the statement. This is a native-side gap: the SyncReadMem read port is not represented in the PDG.

**Fix:** after fixing T1 — verify whether UHDI emits the memory read port in §7 body as a connect.
If so, this is a potential advantage over native, but until T1 is fixed (UHDI PDG does not exist)
this is a hypothesis, not a conclusion.

---

## P2 — converter / cosmetic

### T6 [CONV] Delayed context_variable not emitted

`HgdbTorture.Sub` holds a 3-level shift history `reg` (hist0/hist1/hist2). hgdb supports
`context_variable.type=1` with `depth>1` (value N cycles ago), but the converter stores history registers
as ordinary signals (type=0). Loss: the debugger will not show "value of reg 2 cycles ago".

**Fix:** `uhdi_to_hgdb` — detect RegNext chains and emit delayed context_variable.
A [SPEC] annotation in UHDI about delay depth may also be needed.

### ~~T7~~ [OK] Multi-site data-breakpoint — round-trips correctly

Verified: `acc` (two when-branches) produces **both** assignment rows —
`acc @bp14 L129` and `acc @bp18 L135`. A watch on `acc` catches both writes. **Not a gap.**

---

## What round-trips without loss ([OK], confirmed empirically)

- **Tywaves**: nested Bundle (6 struct objects), enum_defs + enum_def_ref, ctor-params
  (29 `source_lang_type_info` vs 0 from native), multi-dimensional Vec, Vec[Bundle].
  The UHDI projection is **strictly richer** than native `--emit-hgldd` on the uhdi-fork fir.
- **hgdb**: 3-level guard stack, hex literals, dotted names of nested bundles (`io.data.x`),
  instance hierarchy (3 instances: top + sub0 + sub1), top-clock annotation,
  multi-site data-breakpoint (both writes to `acc` produce an assignment row — see ~~T7~~).

---

## Environment limitations ([ENV], not pipeline defects)

- **E1**: hgdb-firrtl native unavailable — `_hgdb.so` was built against cpython-3.11, but the environment
  has only python-3.12 (ABI mismatch). A native hgdb baseline for comparison cannot be obtained without
  rebuilding the bindings. The UHDI side of hgdb is fully functional.
  *Resolution:* rebuild bindings against 3.12 (`python3.12 setup.py build_ext` in
  `hgdb/bindings/python`) — a separate task requiring a toolchain in the environment.
- **E2**: hgdb-circt native (`firtool --hgdb`) hangs for >300 s on `HgdbTorture` (LLVM-16 legacy fork).
  Skipped; use hgdb-firrtl as baseline (after fixing E1).

---

## Prioritization for the pipeline

```
T1 (P0, CIRCT) ── unblocks ──► T2 (P1, CIRCT) ──► full PDG round-trip
T3 (P1, CIRCT) ─────────────► accurate hgdb conditions
T4 (P1, CIRCT) ─────────────► multi-clock live-debug
T5 (P1)        ─────────────► UHDI > native for mem.read
T6 (P2, CONV)  ─────────────► hgdb delayed context_variable
```

T1 is the root: without it, designs with dynamic index do not pass emission, and T2 cannot be verified end-to-end.
