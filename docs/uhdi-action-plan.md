# uhdi in CIRCT: action plan

**Phase 1 (Tywaves) + Phase 2 (hgdb). Plan as a sequence of steps.**

---

## 0. Context and main thesis

The demonstration target is a medium-sized SoC (not RocketChip). The defense is scoped to Phase 1 + Phase 2 results.

Sanity-checks A1-A4 **passed**:

| # | Result | Consequence |
|---|---|---|
| A1 | [ok] InlineAnnotation -> `dbg.scope "l", "Leaf"` is created correctly | inline-tracking works without a pre-pass |
| A2 | [ok] LowerTypes preserves nested `dbg.struct` with flat SV ports | Tywaves bundle-view will receive correct data |
| A3 | [ok] Unused `observed` reg survived DCE | `dontTouch` is not needed |
| A4 | [ok] `dbg.variable` has attr-dict via assemblyFormat -- discardable attrs attach without dialect extension | primary path, not fallback |

All [C]-level risks from the first revision of the plan are resolved. Remaining: A13-A16 (see Sec.5), verified in Sec.1.

### Thesis statement for the defense

uhdi is a unified debug information format for hardware generators, built as a *superset* of existing formats (hgdb, HGLDD, PDG). The work demonstrates:

1. **Format** -- N-way representations, pool-based structure, layered optionality (spec).
2. **Infrastructure** -- two CIRCT passes and one emitter producing uhdi from Chisel designs.
3. **Independent projections** -- `uhdi-to-hgldd` and `uhdi-to-hgdb` as Python converters. Python is intentional: a reference implementation, explicitly decoupled from the compiler. The format is an independent entity, not an internal CIRCT representation.

### Why a pool-based format from scratch, rather than extending HGLDD

The alternative -- taking HGLDD as the base and adding `body[]`/`bp` keys for the hgdb use-case -- is shorter, but:

- the "unified format as superset" narrative blurs -- the format remains a hybrid.
- Phase 3+ foundations (temporal, provenance) become an addition bolted onto the HGLDD shape rather than a natural extension of the pool-based structure.
- the thesis defense is weaker: "implemented one projection on top of someone else's format" vs. "the format is an independent entity, two projections demonstrate that".

The pool-based path is heavier, but provides a solid foundation. A deliberate choice.

### What this means for the code

- Base branch -- `fk-sc/debug-info` (Chisel intrinsics + `dbg` dialect extension). We branch off from it; uhdi work starts with a clean slate.
- Minimum new passes: **two** in Phase 1, **one or two** in Phase 2.
- Minimum new dialect additions: attributes (Phase 1) + statement-ops with regions (Phase 2).
- **No Phase 3+ scaffolding**: representations are fixed as the pair `(chisel, verilog)`, status is not emitted, temporal/provenance is untouched.
- Emitter -- one file `EmitUHDI.cpp` with the `--emit-uhdi` flag.
- Converters (`uhdi-to-hgldd`, `uhdi-to-hgdb`) -- Python scripts, decoupled from the CIRCT build.

---

## 1. Pre-implementation: sanity-checks and baseline

A1-A4 are already done. Before starting implementation, we close four additional checks.

### 1.1 Check A13 -- discardable attrs survive the pipeline

Critical for both Phase 1 passes. MLIR normally preserves discardable attrs, but some CIRCT passes occasionally erase them manually.

Test:

- Set `uhdi.test_attr = "hello"` on `dbg.variable` at the FIRRTL level (manually in an MLIR fixture).
- Run the full pipeline to HW-dialect: `firtool --ir-hw test.fir`.
- Grep the output: the attr must be preserved on the corresponding `dbg.variable`.

**Plan B if they are lost:** register `uhdi.stable_id` and `uhdi.repr_entry` as **named attributes** in `DebugOps.td` -- 5-10 lines of tablegen adding fields to `dbg.variable` / `dbg.scope` / `dbg.struct` / `dbg.array`. MLIR guarantees survival of registered attrs through all standard passes because the op description explicitly declares them. FusedLoc as a fallback **must not be used**: location-merging passes rewrite more aggressively than op-level attrs.

### 1.2 Check A14 -- dbg dialect extensibility for region-ops

Phase 2 requires `dbg.scope_body` and `dbg.block` with regions. In the current `dbg` dialect (branch `fk-sc/uhdi`) -- only leaf ops.

Steps:

- Read `include/circt/Dialect/Debug/DebugOps.td` in your branch.
- Determine whether region-ops already exist or need to be added.

Implementation options:

- **Preferred:** add region-ops to the `dbg` dialect directly in your fork. You control the dialect, there are no upstream conflicts (this is a separate branch).
- **Fallback if less diff to dbg is desired:** create a separate `uhdi_body` dialect with the needed region-ops. More code, less interaction with existing `dbg` ops.

### 1.3 Check A15 -- Tywaves on HGLDD baseline

Baseline for M1 validation. We need to confirm that Tywaves starts at all and correctly parses HGLDD on our demo designs -- this is the reference point against which `uhdi-to-hgldd` output will be compared.

Steps:

- Take a demo design (GCD), build it via rameloni-chisel + rameloni-circt: `firtool -g --emit-hgldd --hgldd-source-prefix=...`.
- Open the resulting HGLDD in Tywaves.
- Confirm that signals, bundles, and hierarchy are displayed.

**Known pitfall:** without an explicit `--hgldd-source-prefix` the emitter produces `"file_info": [".."]` with a garbage file-index -- Tywaves will not load. Always pass the prefix.

**Plan B if it does not open after prefix:** investigate the specific cause (Tywaves expects a specific schema version / specific fields). This is a blocker-proxy for M1 -- Phase 1 cannot start because M1 validation through the Tywaves GUI will not pass.

### 1.4 Check A16 -- Chisel withDebug end of chain

The Chisel fork `fk-sc/debug-info` emits `circt_debug_*` intrinsics only when `withDebug` is enabled. Without this flag the emitter receives bare `MaterializeDebugInfo` output with no source-language info -> Tywaves will show flat signals, Phase 1 visually degrades.

Test:

- Take a demo design (GCD).
- Build it via ChiselStage with `withDebug` explicitly enabled.
- Confirm that the intermediate FIRRTL contains `circt_debug_var` intrinsics.
- Run through firtool and confirm that the UHDI JSON contains `source_lang_type_info`.

If `withDebug` is forgotten -- the defect will surface at M1 validation and force rebuilding the entire demo set.

---

## 2. Phase 1: pool-based uhdi + Tywaves

### 2.1 Architectural principle

The emitter is a **passive reader**. Debug information is not tracked, only read from existing CIRCT properties:

- `dbg.*` ops live through the entire pipeline (A13 -- confirmed in Sec.1.1).
- Inlining creates explicit `dbg.scope` (A1 [ok]).
- LowerTypes preserves `dbg.struct`/`dbg.array` (A2 [ok]).
- DCE does not remove values with dbg-uses (A3 [ok]).

Sequence:

1. Assign stable IDs to dbg-ops (init pass, Sec.2.2).
2. Snapshot final Verilog names (snapshot pass, Sec.2.3, parallel to ExportVerilog).
3. Serialize to pool-based JSON (emitter, Sec.2.4).
4. Convert to HGLDD (Python script, Sec.2.5).
5. Validate end-to-end through Tywaves (Sec.2.6).

### 2.2 Pass `firrtl-uhdi-init`

**Position:** after `MaterializeDebugInfo` / `LowerIntrinsics`. dbg ops are materialized by these passes -- there is nothing to annotate earlier.

**Action:** walk all `dbg.variable` / `dbg.scope` / `dbg.struct` / `dbg.array`. For each:

- Compute `stable_id = <kind>_<hash_prefix>_<counter>`:
  - `hash_prefix` = blake2b(name + type + scope-path), 8 hex chars.
  - `counter` resolves collisions within the same hash-prefix.
  - Stable across runs (required for diff-validation in Sec.2.6).
- Set attribute `uhdi.stable_id`.
- Populate attribute `uhdi.repr_entry` for the `"chisel"` key: name + source loc from SourceInfo.

**Tests:** `.mlir` FileCheck with 2-3 input-IR fixtures (leaf module; module with inlined scope; module with lowered bundle).

**Size:** ~50-100 LOC C++.

### 2.3 Pass `hw-uhdi-verilog-snapshot`

**Position:** parallel to ExportVerilog, not after. Operates on the same HW-dialect IR from which ExportVerilog emits Verilog.

**Name source:** `NameLoc "emitted"` and FusedLoc `"verilogLocations"` that `PrettifyVerilogNames` sets on HW ops before ExportVerilog. Integration reference -- `EmitHGLDDPass` in CIRCT, see `tools/firtool/firtool.cpp` (wiring for HGLDD).

**Action:** for each dbg-op with `uhdi.stable_id`, find the corresponding HW-op via the tracking chain, extract Verilog-name and source location from NameLoc/FusedLoc. Populate `uhdi.repr_entry` for the `"verilog"` key.

**If a dbg-op is not directly bound to an HW-op** (e.g., a pure `dbg.struct` over lowered scalars): recursively traverse operands to find a valid HW-ref.

**Tests:** `.mlir` FileCheck following the EmitHGLDD tests as a model.

**Size:** ~300-500 LOC C++.

### 2.4 Emitter `export-uhdi` (pool-based)

New file `lib/Target/DebugInfo/EmitUHDI.cpp`, flag `--emit-uhdi`. Wiring in `tools/firtool/firtool.cpp` following the existing `EmitHGLDDPass` as a model.

```cpp
struct UhdiEmitter {
  DenseMap<Type, std::string>       typePool;
  DenseMap<Operation*, std::string> exprPool;
  std::map<std::string, VarJson>    varPool;
  std::map<std::string, ScopeJson>  scopePool;

  LogicalResult run(ModuleOp top, raw_ostream &os);
};
```

**Stages:**

1. Fixed `representations` manifest: two entries `chisel` + `verilog`.
2. Walk `dbg.scope` -> `scopePool`:
   - no `scope` operand + `hw.module` -> `"module"`
   - no `scope` operand + `hw.module.extern` -> `"extmodule"`
   - with `scope` operand -> `"inline"`
3. Walk `dbg.variable` / `dbg.struct` / `dbg.array` -> `varPool` + `typePool`.
4. Dedup:
   - Types by structural equality.
   - Expressions by `(opcode, operands)` tuple.
5. Serialization via `llvm::json::OStream`.

**Simplification rules:**

- `status` is not emitted at all (implicit preserved per spec Sec.6.3).
- Expressions are inlined if uses_count == 1, named if >=2.
- Chunking is **not** implemented -- a medium SoC does not require it.
- CBOR is **not** implemented -- JSON is sufficient.
- Bundle in consolidated form (one variable with struct-type, per Sec.6.7) -- optimal for Tywaves.

**Tests:** integration -- run on 4 reference designs (see Sec.2.6), validate output against the spec schema via `jsonschema`.

**Size:** ~1000-1500 LOC C++. The main work of Phase 1.

### 2.5 CLI tool `uhdi-to-hgldd` (Python)

Straightforward field mapping per spec Sec.15.3.

Tasks:

- Load uhdi JSON, validate against schema (`jsonschema` library).
- Walk variables, generate HGLDD objects.
- Dedup struct types (HGLDD expects deduplicated structs).
- Packed/unpacked range conversion for vectors.

**Size:** ~600-900 LOC Python.

### 2.6 Phase 1 validation (M1)

Test design set:

| Design | Controls |
|---|---|
| GCD | Basic functionality, registers, simple whens |
| FIFO (~20 signals) | Memory, Vec, small control flow |
| SingleCycleCPU (educational RISC-V) | Hierarchy, bundles, parameterization |
| SoC with 2-3 modules + InlineInstance | `kind: "inline"` in uhdi scope tree |

**Metric:** Tywaves on **our** `uhdi-to-hgldd` output shows the same hierarchy/typed/values tree as on **native** HGLDD.

**Implementation:**

1. Visual diff in the Tywaves GUI -- 4 designs, quick sanity.
2. Python script canonical JSON diff: normalize both HGLDDs (sort keys, stable ID annotation, whitespace) and structural diff.
3. Additional metric for evaluation: pool-based compression -- count `(inline expressions / total)`, struct dedup rate, file size vs. naive-inline baseline. On SingleCycleCPU, 20-40% reduction is expected. Provides a numerical result for chapter 5.

**M1 closed:** all 4 designs display in Tywaves identically to native HGLDD (diff on canonicalized JSON is empty or discrepancies are explained).

---

## 3. Phase 2: uhdi + hgdb

### 3.1 What is added

- Capture-when pass for control flow and AND-reduced enable conditions.
- scope body -- statement tree inside the scope, surviving ExpandWhens.
- Breakpoint metadata (`enableRef` only).
- Python CLI tool `uhdi-to-hgdb`.

### 3.2 New dialect elements

**Ops** (inside dbg scope body region):

- `dbg.scope_body` -- region-containing op, one per `dbg.scope`.
- `dbg.block` -- region-op with `guardRef` attribute for when-nesting.
- `dbg.connect_stmt`, `dbg.decl_stmt` -- statement-ops.
- `dbg.assert_stmt`, `dbg.assume_stmt`, `dbg.cover_stmt` -- if the design contains verification.
- `dbg.expression` -- AST node with opcode and operands.

**Attribute:**

- `#dbg.bp` -- only the `enableRef` field. The rest (watchpoint, throttle, category, message) are not emitted.

The extension is made in your own branch. The form -- per the result of Sec.1.2: either directly in `dbg`, or in a separate `uhdi_body` dialect.

### 3.3 Pass `firrtl-uhdi-capture-when`

**Position:** between Inliner and ExpandWhens. Inliner runs before us (correctly handles `dbg.scope`), ExpandWhens runs after (destroys `firrtl.when`, but our `dbg.scope_body` is autonomous).

**Algorithm (pseudocode):**

```
walkRegion(region, condStack, intoRegion):
  for each op in region:
    match op:
      firrtl.when:
        guard = buildDbgExpr(op.condition)
        thenBlock = create dbg.block{guardRef=guard} in intoRegion
        walkRegion(op.thenRegion, condStack ++ [guard], thenBlock.body)
        if op.hasElse:
          notGuard = buildDbgExpr(not(op.condition))
          elseBlock = create dbg.block{guardRef=notGuard} in intoRegion
          walkRegion(op.elseRegion, condStack ++ [notGuard], elseBlock.body)
      firrtl.connect:
        enable = andReduce(condStack)
        create dbg.connect_stmt{
          varRef:   stableIdOf(op.dest),
          valueRef: buildDbgExpr(op.src),
          bp:       #dbg.bp{enableRef = stableIdOf(enable)}
        } in intoRegion
      ... (assert/assume/cover, declarations)
```

**Three critical subtleties:**

1. **`dbg.expression` references `dbg.variable`, not raw FIRRTL SSA.** ExpandWhens will later change everything in FIRRTL. If the condition is `firrtl.and %a, %b`, first look for `dbg.variable` on `%a` and `%b`; if none -- create a synthetic one via stable_id.

2. **Memoization of AND-reduction.** Content-addressable cache by sorted vector of operand stable_ids. At a minimum, you can start **without** memoization. On a medium SoC this is acceptable (~10K expr ops). Add it when actually needed.

3. **Autonomy from ExpandWhens.** `dbg.scope_body` must not have SSA dependencies on `firrtl.when`. ExpandWhens destroys the when-structure -- our region survives.

**Tests:** `.mlir` FileCheck -- flat when, when/else, nested when-in-when, when with connect to an aggregate.

**Size:** ~500-900 LOC C++.

**Plan B if the pass stalls:**

- Level A: do not support elsewhen chains -- only when/else. Covers 90% of patterns.
- Level B: do not implement memoization. Accept bloated IR.
- Level C: naive AND-reduce with inline AST in `bp` attribute, no exprPool. Emits duplicates, works.
- Level D: enter Emergency Sec.6.

### 3.4 Emitter extension

Added to `EmitUHDI.cpp`:

- Walk `dbg.scope_body` region -> uhdi `body` array. **Critical: pre-order, order is significant** (FIRRTL last-connect semantics).
- Serialization of `#dbg.bp` -> uhdi `bp` field (`enableRef` only).
- Handling of verification statements, if present.

**Size:** ~200-400 LOC on top of the existing emitter.

#### 3.4.1 Post-defense workstream: long-term `enableRef` shape

The current MVP (see spec Sec.9.3 MVP note) serializes `enableRef` as an `&`-joined predicate string (`var_a_id&!var_b_id`, sentinel `<complex>` for unresolvable leaves). This is a transitional form: the schema-typed `ExprOrVarRef` accepts it as a string without a pattern, and the `uhdi-to-hgdb` converter parses it inline in Sec.15.4.3 AND-reduction.

Long-term target: the emitter assembles the AND-reduced predicate as an `expressions`-pool entry (one `Conjunction` opcode with an array of operands) and writes `enableRef` as a single id resolved in the expressions pool. Steps when the time comes:

1. In `firrtl-uhdi-capture-when` hook: instead of string-concatenating predicates, materialize the AND-reduced tree as a set of `expressions` objects with `opcode: "&&"` (or `Conjunction` source-level opcode); leaves can still be `varRef` for the single-signal sample case.
2. In `EmitUHDI.cpp` `serializeEnable`: write `enableRef: "<expr_id>"` instead of the joined-string.
3. In `uhdi_to_hgdb/convert.py`: drop the branch that parses the `&`-joined string; read via the standard expression walk (`Sec.15.4.2` SV pretty-printer already handles `&&` opcode -> `(a) && (b)`).
4. Remove the sentinel `<complex>` exception from the Sec.13 linter (the spec already describes removal as cleanup when retiring the MVP).
5. Remove mention of the joined form from Sec.9.3 MVP note + Sec.7.4 ExprOrVarRef description + 0.9.2 changelog (or leave a historical note).

Out of scope for the defense: requires C++ MLIR work in `circt:fk-sc/uhdi-pool` (~1/2 day C++ + 1/2 day converter sync + regenerate fixtures). Deliberately deferred -- the schema accepts both variants simultaneously, the current emitter and projector are correct, rewriting does not block M1 or M2.

### 3.5 CLI tool `uhdi-to-hgdb` (Python)

Python, like `uhdi-to-hgldd`. Converters are decoupled from CIRCT, uniform tooling pattern.

Three components in order of complexity.

#### (A) SQLite schema population

| hgdb table | Source from uhdi |
|---|---|
| Instance | Recursive walk `scopes[*].instantiates[]`, fresh id per instance |
| Variable | variables pool, only those with a verilog-repr entry |
| Generator Variable | one row per (variable, instance) -- maps the authoring-language name to the bound HDL signal (see spec Sec.15.4.1); this is hgdb's name-index, not a literal-pool. |
| Scope Variable | variables with `ownerScopeRef == current scope` |
| Breakpoint | one row per `dbg.connect_stmt` per instance of the host scope |

We freeze a specific hgdb version (commit hash) -- we do not chase a moving target.

#### (B) Instance-path prefixing

Each `enable` string must use names in the context of the specific instance (`top.cpu.alu.io_en` instead of `io_en`). When serializing an expression, rename via the instance path.

#### (C) SV-string serializer

The main complexity of Phase 2. Requirements:

- Precedence-aware printing per SV LRM.
- Correct parentheses (minimal, not excessive).
- Special syntax: unary ops, `{N{x}}` replicate, `{a,b}` concat, ternary `?:`, reductions `&x` / `|x` / `^x`.

Skeleton:

```python
def print_expr(expr, parent_prec=0):
    my_prec = PREC[expr.opcode]
    body = format_by_opcode(expr)
    if my_prec < parent_prec:
        return f"({body})"
    return body
```

Unit tests: 50+ expression constructs, each roundtrip through Verilator `--lint-only`.

**Plan B:** always-parenthesize strategy `((a) + ((b) * (c)))`. Ugly output, always correct.

### 3.6 Phase 2 validation (M2)

Design set: 2-3 from Phase 1 + one special design with nested whens.

**Metric:** behavioral equivalence.

- Run simulation with the hgdb-VSCode plugin.
- Set a breakpoint on each source line.
- Record the trace `(cycle, breakpoint_id_triggered)`.
- Compare reference (stock hgdb-Chisel-plugin) with via-uhdi traces. They must match.

**If the stock hgdb emitter is unavailable:** behavioral check -- the debug session must subjectively behave as expected. Documented in chapter 5 as a methodology limitation.

**M2 closed:** hgdb-VSCode session on 2-3 designs through our toolchain triggers the same breakpoints as the reference (or behaves as expected if the reference is unavailable).

---

## 4. Thesis text

### 4.1 Structure

Six chapters. Approximately 60-80 pages total.

| Chapter | Order | Size |
|---|---|---|
| 1. Introduction (motivation, goals) | first, based on Sec.1 of the spec | ~5-8 p. |
| 2. Background (Chisel, FIRRTL, CIRCT, hgdb, Tywaves) | after intro | ~10-15 p. |
| 3. Format design (uhdi) | spec already written -- extract and rationale, written in parallel with the start of emitter implementation | ~15-20 p. |
| 4. Implementation (CIRCT passes, emitter, converters) | after passes and emitter exist | ~10-15 p. |
| 5. Evaluation (Tywaves + hgdb demo + limitations) | draft after M1, final after M2 | ~8-12 p. |
| 6. Conclusion, future work (Phase 3+ as outlook) | final | ~3-5 p. |

### 4.2 Details

- Chapter 3 (format) -- 70% already written in the uhdi spec. **Do not rewrite the spec**, cite it and focus on design decisions and their rationale. The spec goes as an appendix.
- Chapter 4 (implementation) -- describe **only what has been implemented**. Phase 3+ goes in future work.
- Chapter 5 (evaluation) -- Tywaves and hgdb-VSCode screenshots, trace comparison, discussion of limitations. Screenshots are taken when the code is stable; reshooting after code-freeze is not expected.
- Rejected alternatives (spec Appendix B) -- material for justifying design decisions in chapter 3.

### 4.3 Defense speaker note

One page with key talking points:

- **Problem:** fragmentation of debug formats (hgdb, HGLDD, PDG). None covers all use cases; lossy conversion between them.
- **Solution:** layered unified format, consumer selects the needed layers.
- **Demonstration:** one emitter from CIRCT, two independent Python projections work on the same document.
- **Contribution:** format (spec), two CIRCT passes, one pool-based emitter, two projection tools.
- **Limitations:** Phase 3+ (temporal, provenance) -- future work.

---

## 5. Assumptions summary

Levels: **[C]** critical -- failure = plan overhaul; **[I]** important -- failure = extra work; **[L]** weak -- local workaround.

| # | Assumption | Level | Status |
|---|---|---|---|
| A1 | InlineInstances creates explicit `dbg.scope` during inlining | C | [ok] confirmed |
| A2 | LowerTypes preserves `dbg.struct`/`dbg.array` | C | [ok] confirmed |
| A3 | `dbg.variable` structurally blocks DCE | C | [ok] confirmed |
| A4 | `dbg.variable` allows discardable attrs | I -> L | [ok] confirmed |
| A5 | Stable IDs are stable across compilation runs | I | Resolved via hash+counter |
| A6 | `capture-when` does not conflict with Inliner and other passes before ExpandWhens | I | Verified before Phase 2 |
| A7 | uhdi-attributes on dbg ops survive passes | I | Verified in Sec.1.1 (see A13) |
| A8 | FIRRTL SourceInfo is preserved through the pipeline | I | Verified in Sec.1 |
| A13 | Discardable attrs (`uhdi.*`) survive the full pipeline | I | Pre-sanity (Sec.1.1) |
| A14 | dbg dialect allows adding region-ops | I | Dialect is under our control in our fork; form -- Sec.1.2 |
| A15 | Tywaves correctly parses existing EmitUHDI output | L | Pre-sanity (Sec.1.3) |
| A16 | Chisel `withDebug` correctly triggers `circt_debug_*` intrinsics | I | Pre-sanity (Sec.1.4) |

---

## 6. Emergency: scope reduction tactics

In order of increasing cuts.

### 6.1 Entry conditions

- `firrtl-uhdi-init` does not work on GCD after reasonable debugging.
- M1 is not closed after implementing the emitter + `uhdi-to-hgldd`.
- capture-when pass stalls for several iterations with partial rollback to Plan B levels A-C (Sec.3.3).
- Phase 2 emitter extension or SV-serializer hit a wall.

### 6.2 Level 1 -- soft simplifications

- Do not add memoization to capture-when.
- SV-string serializer: always-parenthesize.
- Do not support verification statements (assert/assume/cover) -- remove from test designs.
- `uhdi-to-hgldd`: drop enum support, demo designs do not use ChiselEnum.

### 6.3 Level 2 -- demo reduction

- Reduce the Phase 2 test set to GCD + one simple design with a single when.
- Remove the SoC design with InlineInstance. Inline demonstration only in Phase 1.
- Phase 2 evaluation: not a full behavioral trace comparison, but screenshots of a working session.

### 6.4 Level 3 -- defense on Phase 1 only

- Phase 2 code -- present as work-in-progress in chapter 4.
- Chapter 5 evaluation covers Phase 1 only (Tywaves).
- Chapter 6 future work: Phase 2 completion, Phase 3+ temporal/provenance.
- The thesis is reformulated: *"the format is designed and partially implemented; the Tywaves-projection implementation demonstrates the practicality of the pool-based architecture; the hgdb-projection is the next immediate step"*.

This is not a failure. The uhdi spec by itself is a strong thesis contribution. The Phase 1 demo validates it.

---

## 7. Defense checklist

### 7.1 Code

- [ ] `firrtl-uhdi-init` built in CIRCT, passes unit tests
- [ ] `hw-uhdi-verilog-snapshot` built, passes unit tests
- [ ] `export-uhdi` (pool-based, `EmitUHDI.cpp`) generates valid JSON per schema on GCD/FIFO/SingleCycleCPU
- [ ] `uhdi-to-hgldd` (Python) generates HGLDD that opens in Tywaves
- [ ] `firrtl-uhdi-capture-when` works on the nested when test *(if Phase 2 is included in the defense)*
- [ ] `uhdi-to-hgdb` (Python) creates SQLite that the hgdb-VSCode plugin opens *(if Phase 2)*
- [ ] Repository pushed with README, build instructions, and an example

### 7.2 Text

- [ ] All 6 chapters written, at least one self-read pass done
- [ ] uhdi spec attached as appendix
- [ ] Tywaves / hgdb-VSCode screenshots in chapter 5
- [ ] Bibliography: hgdb paper, Tywaves paper, CIRCT docs, FIRRTL paper
- [ ] PDF built, checked for typos

### 7.3 Defense

- [ ] Slides (15-20)
- [ ] Tywaves video demo (30-60 seconds)
- [ ] hgdb-VSCode video demo *(if Phase 2)*
- [ ] Speaker notes (Sec.4.3)
- [ ] Rehearsed aloud -- at least 2 times
- [ ] Answers to obvious questions:
  - "Why not extend HGLDD instead of creating a new format?"
  - "Why no provenance in the current version?"
  - "Why Python converters, not CIRCT-native?"
  - "Why no comparison with DWARF?"

---

## 8. First action

1. Run the four sanity-checks from Sec.1 (A13 / A14 / A15 / A16).
2. Create the repository skeleton (`src/`, `test/`, `docs/`, `scripts/`).
3. Compile CIRCT with debug symbols (`-DCMAKE_BUILD_TYPE=RelWithDebInfo`). Without this, exploratory experiments are painful.
4. Create an Overleaf/Word thesis template with chapter headings and a TOC outline. The structure must be in place before the first page of text.
5. After Sec.1, start Phase 1 strictly in order: Sec.2.2 -> Sec.2.3 -> Sec.2.4 -> Sec.2.5 -> Sec.2.6.
6. After M1 is closed -- Sec.3 in order: Sec.3.2 -> Sec.3.3 -> Sec.3.4 -> Sec.3.5 -> Sec.3.6.

**The main thing:** do not try to code everything "correctly" from the first attempt. A working end-to-end chain is more important than a beautiful architecture. The first goal is to get any pool-based JSON out of CIRCT; everything else is iterative.

---

### Removed from format (deferred)

- **Temporal layer**, **Provenance layer**: previously specified as Sec.11 and Sec.12 in `docs/uhdi-spec.md`; removed in changelog 0.9.3. No emitter or consumer existed. Reinstate from git history (`git log --diff-filter=D -- docs/uhdi-spec.md`) when a first emitter/consumer ships.

---

*-- end of document --*

---

### Removed from format (deferred to future)

- **Sec.11 Temporal layer**, **Sec.12 Provenance layer**: previously specified in uhdi-spec.md; removed because no emitter/consumer existed. Reinstate from git history (`git log --diff-filter=D -- docs/uhdi-spec.md`) when a first emitter or consumer ships.
