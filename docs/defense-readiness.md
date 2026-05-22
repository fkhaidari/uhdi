# uhdi defense readiness

Single-file synthesis after the 0.9.2 audit (`docs/uhdi-spec.md` Appendix A,
2026-05-15). Defense is in ~3 weeks. Intent: rehearse the Q&A bank, work the
risk register down, smoke-test the three deck-referenced demo paths, file the
documentation gaps separately.

Sources: `docs/uhdi-spec.md` (§14 open questions, §15 projections, Appendix B.9-B.11),
`docs/uhdi-action-plan.md` (§3.4.1, §4.3, §5, §6, §7), `bench/README.md`
(Pending fixtures), and the deck index at
`/home/farid/thesis/text/visual/decks/defense/deck.js` (slide `solution/counter-demo.html`).

---

## 1. Reviewer Q&A bank

### 1.1 "Why not extend HGLDD instead of creating a new format?"

HGLDD imposes a fixed `hgl_loc`/`hdl_loc` pair (Appendix B.7,
`docs/uhdi-spec.md:3064-3066`) and a flat `objects`-representation that is
incompatible with pool-based dedup (Appendix B.1, `docs/uhdi-spec.md:3026-3033`).
If we had layered `body[]`/`bp`/`dataflow` on top of an HGLDD document, the
result would be a hybrid: the HGLDD consumer would have to be treated as
privileged, and the format's independence from consumers — which is the foundation
of §1.2 "superset of three legacy formats" — collapses. The full argument is in
Appendix B.9 (`docs/uhdi-spec.md:3072-3078`) and in `docs/uhdi-action-plan.md:30-38`.
This is visible from §15: HGLDD is one of three projections on equal footing with
hgdb and PDG, none of which has special status (`docs/uhdi-spec.md:2730-3002`).

### 1.2 "Why no dataflow in the current version? What happened to provenance?"

§10 dataflow is fully specified in the spec but not implemented in the emitter.
This is a deliberate scope cut, recorded in `docs/uhdi-action-plan.md:45`
("no Phase 3+ groundwork"): pool-based JSON and three projectors (HGLDD / hgdb /
PDG) demonstrate the format; the dataflow graph adds nothing to that demonstration
and requires separate producer instrumentation in CIRCT. The honest answer: this
is future work; the spec lays the foundation, implementation is the next iteration
after Phase 2.

Provenance (historically §12) has been removed entirely from the spec in 0.9.3 —
no emitter produced the corresponding pool, no consumer read it, so the layer was
cut along with temporal (§11) and reserved in git history until a first real
producer or consumer appears. See the changelog entry "0.9.3 (2026-05-21)" in
`docs/uhdi-spec.md`.

### 1.3 "Why Python converters instead of CIRCT-native?"

If the projections (uhdi → HGLDD/hgdb/PDG) lived inside CIRCT as C++ MLIR
passes, the boundary between "uhdi as a format" and "uhdi as CIRCT's internal IR"
would blur. The Python projectors read the same JSON that any external tool reads —
this demonstrates the format's portability. The full argument is in Appendix B.10
(`docs/uhdi-spec.md:3080-3086`). C++ MLIR code does exist — it is the producer
side: `EmitUHDI.cpp` + two passes `firrtl-uhdi-init` / `hw-uhdi-verilog-snapshot`
(see `docs/uhdi-action-plan.md:46-47`); this split (emitter in C++, projectors in
Python) is a structural reflection of the IR/format split.

### 1.4 "Why no comparison with DWARF?"

Domain mismatch: DWARF assumes PC-driven execution, software-style lexical scopes,
and registers; in hardware there is no PC, there are multiple concurrently live
clock domains, and "variables" are `bindKind: "port"/"reg"/"wire"/...` (§6.2) —
categories that DWARF does not have. Additionally, none of the three real consumers
(Tywaves, hgdb, ChiselTrace) reads DWARF — three new integrations would have to be
built from scratch. The full argument is in Appendix B.11
(`docs/uhdi-spec.md:3088-3094`). Proposing a hardware-DWARF extension would
create a fourth silo, which directly contradicts the §1.1 problem statement.

### 1.5 "What is `<complex>` in `enableRef`, and is the `&`-joined string a hack?"

This is the transitional MVP form of §9.3, explicitly marked as transitional in the
spec (`docs/uhdi-spec.md:1512-1514`) and in action plan §3.4.1
(`docs/uhdi-action-plan.md:318-330`). The current emitter serializes the
AND-reduced predicate as an `&`-separated string of stable_ids with `!` for
negation and the literal `<complex>` for unresolvable leaves; the schema type
`ExprOrVarRef` accepts a string without a pattern, so the form is schema-legal.
The long-term target is to materialize the AND-reduction as an entry in the
`expressions` pool and write `enableRef` as a single id resolved there. This
requires C++ MLIR work in `circt:fk-sc/uhdi-pool` (~½ day emitter + ½ day
converter sync + fixture regeneration) and is deliberately deferred: the rewrite
does not block M1 or M2, and the schema accepts both forms simultaneously. The
linter (§13, `docs/uhdi-spec.md:2646+`) excludes the MVP form from the
every-`*Ref`-resolves rule.

### 1.6 "What is `representations`, and why an N-way map instead of an HGL/HDL pair?"

`representations` (§3.2, `docs/uhdi-spec.md:281-289`) is a map declaring all IR
levels tracked by the document, with arbitrary string keys and
`kind: "source"|"ir"|"hdl"`. Every `Location.file` is an index into `files[]` of
a specific representation. Why N-way: the CIRCT pipeline has 4–5 meaningful IR
levels (Chisel → High FIRRTL → Low FIRRTL → HW dialect → SystemVerilog), and for
compiler debug-info any of them can be relevant. The HGLDD-style dual pair
(`hgl_loc`/`hdl_loc`) is a special case of an N-way map (one `source`, one `hdl`);
semantic mapping roles are handled via §3.3
`roles.authoring`/`simulation`/`canonical`. Full justification in Appendix B.7
(`docs/uhdi-spec.md:3064-3066`).

### 1.7 "What is the advantage of uhdi → hgdb projection over SystemVerilog DPI / cosimulation?"

DPI is a runtime FFI between an SV simulator and C++ code; it does not provide a
**symbol table** (signal name in the source → signal name in Verilog) needed to
debug **against lines of original Chisel**. hgdb operates on top of SQLite tables
`Instance`/`Variable`/`Breakpoint`/`Generator Variable` (§15.4.1,
`docs/uhdi-spec.md:2801-2811`); the uhdi emitter populates these tables from
pool-based JSON. DPI and hgdb solve different problems: DPI is about calls from/to
the simulator, hgdb is about a human debugging UI on top of SV simulation with
mapping back to source terms. The uhdi format adds two advantages over using hgdb
directly: (a) a single source of truth for multiple consumers (the same JSON
generates HGLDD for Tywaves), and (b) expressions as ASTs rather than strings, so
they can be normalized / analyzed programmatically (Appendix B.8,
`docs/uhdi-spec.md:3068-3070`).

### 1.8 "Why pool-based instead of nested everywhere?"

Pure nested (everything inline, hgdb-max-style) does no dedup: a single shared
`Bundle` type referenced by 50 ports is duplicated 50 times — this does not scale
to RocketChip-class designs. Pure flat + integer indices (PDG-max-style) forces
every consumer to rebuild the hierarchy by traversal — overkill for 80% of use
cases (interactive debug, waveform). Pool-based JSON with named refs is the
compromise: O(1) dict lookup, dedup works, hierarchy does not need to be
reconstructed. A size benchmark expects 20–40% compression on SingleCycleCPU vs
a naive-inline baseline (`docs/uhdi-action-plan.md:230`); this is the numerical
result for chapter 5 evaluation. Full argument in Appendix B.1
(`docs/uhdi-spec.md:3026-3033`).

### 1.9 "Who actually reads the JSON Schema?"

Three consumers at present: (a) `uhdi_common.validate.UhdiValidator`, which
validates every input in the Python projectors before mapping — this runs in the
production path `uhdi-to-hgldd` / `uhdi-to-hgdb` / `uhdi-to-pdg`; (b) the bench
(`bench/test/test_pipeline.py`) runs schema validation as part of each
`(fixture × target)` cell before the structural diff; (c) `docs/uhdi-spec.md`
quotes schema snippet subsets in every §N.4 so that the spec and schema files
stay in sync (audits 0.9.1 / 0.9.2 were specifically about closing divergences,
see changelog `docs/uhdi-spec.md:3017-3018`). The schema is not for downstream
HGLDD/hgdb consumers: their input format after projection is uhdi-agnostic. The
schema is needed by the emitter author and by any new projector author to test
without a full CIRCT build.

### 1.10 "Can uhdi be reconstructed from an existing HGLDD/hgdb document?"

Reverse projection (HGLDD/hgdb/PDG → uhdi) is a separate task not specified in
§15: ingestion requires auxiliary inputs (VCD, FIRRTL dump) for type-width recovery
in two out of three cases (§15.1, `docs/uhdi-spec.md:2738`). §15.6 formalizes the
round-trip contract: `X → uhdi → X` is an invariant for regression tests;
`uhdi → X → uhdi` is not, because the projection is lossy (the legacy format lacks
fields that uhdi stores). Ingestion is future work whose main challenge is type
recovery, not format mapping.

---

## 2. Risk register

| # | Risk | Severity | Mitigation | Owner / Status |
|---|------|----------|------------|----------------|
| R1 | **Deck slide `solution/counter-demo.html` (deck.js:406-410) references "Demo on Counter", but `demo/counter/` does not exist** — `Counter` is only a bench fixture (`bench/fixtures/Counter.scala`), not a self-contained `./run.sh` demo. | **P0** | Decide: either use `demo/gcd/` as "GCD-demo" and fix the deck, or clone the `demo/gcd/` structure around Counter (5–10 minutes). | author / open |
| R2 | `uhdi_to_hgldd` does not project `io`-Bundle scopes into HGLDD `objects[]`; native firtool emits one entry per Bundle field, ours emits nothing (`bench/README.md:137-140`). Affects `GCD-tywaves`, `Fifo-tywaves`, `TrafficLight-tywaves`. If the tywaves demo at defense goes through GCD/Fifo/FSM, the port-view in Tywaves will be thinner than the native baseline. | **P0** | The bench skip-list in `_PENDING_FIXTURES` (`bench/test/test_pipeline.py:48`) masks this in CI. Open the demo in Tywaves manually **before** generating screenshots and record the delta in chapter 5 as a known limitation, or defer Bundle projection to Phase 2.5. | author / open |
| R3 | `uhdi_to_hgdb` emits **zero rows** for multi-arm `when`/`elsewhen` chains; Counter (single `when`) works (`bench/README.md:141-143`). GCD contains a loop with elsewhen, FSM has a `switch`. `demo/gcd/design.db` exists (~40 KB) but its contents for multi-arm branches have not been verified. | **P0** | Run `hgdb-db demo/gcd/design.db` + `breakpoint where /abs/path/GCD.scala` by hand; if breakpoint rows are absent — either simplify the demo to a single-when scenario, or openly acknowledge the gap in chapter 5. The README hgdb session in `demo/README.md:151-159` must **reflect actual current output**, not an old screenshot. | author / open |
| R4 | `uhdi_to_hgldd` enum projection (`source_lang_type_info` / `enum_def_ref`) is partially implemented but not stress-tested on real FSMs with `ChiselEnum` + `switch` (`bench/README.md:144-147`). Affects `demo/fsm/` (TrafficLight) — the only demo with ChiselEnum. | **P1** | Verify that Tywaves renders `state` as `Red/RedYellow/Green/Yellow` rather than `2'b00/.../11` (`demo/README.md:30`). If not — mention as known limitation. | author / open |
| R5 | §9.3 MVP `&`-joined predicate string + sentinel `<complex>` — transitional, schema-legal, but may look like a hack under deep probing by a reviewer. Does not block M1/M2. | P1 | Answer Q1.5 is prepared; action plan §3.4.1 (`docs/uhdi-action-plan.md:318-330`) formalizes the post-defense rewrite. | author / answered |
| R6 | Sanity checks **A13-A16** are listed as "pre-sanity" in action plan §1.1-1.4, but the status table §0 (`docs/uhdi-action-plan.md:13-19`) shows ✅ only for A1-A4. If A15 (Tywaves on HGLDD baseline) and A16 (Chisel `withDebug` → `circt_debug_*` intrinsics) fail, Phase 1 demo silently degrades. | P1 | Re-run A13-A16 on the current checkout (A13 trivial, A14 already reflected by the emitter's existence, A15/A16 require tywaves + scala-cli locally). Update the §0 table. | author / unverified |
| R7 | Assumptions A5-A8 in §5 (`docs/uhdi-action-plan.md:432-441`) are listed as "being verified" / "being resolved": A5 stable IDs deterministic, A6 capture-when conflict-free, A7 uhdi-attrs survive pipeline (≡A13), A8 SourceInfo preserved. Without an explicit green flag they all carry P2 risk. | P2 | A5 → bench `manifest.toml` shows determinism by hash; A6 → emitter works on 5 demos without failures — build the argument in evaluation; A7 ≡ A13; A8 → SourceInfo is used in the Tywaves screenshot. | author / inferentially-passed |
| R8 | Bench `_PENDING_FIXTURES` skip-list (`bench/test/test_pipeline.py:48`) + `manifest.toml` semantics: "manifest stale: gap closed" fail (`bench/manifest.toml:20`). When a projector fix lands and the skip is removed, every `expected.*` for that fixture that **is no longer needed** will fail CI. Not a risk for the defense, but a risk for post-defense commits. | P2 | Documented in README; when removing from the skip-list, run `pytest bench/test -k <Name>` and clean up the manifest. | author / future |
| R9 | §3.4.1 post-defense workstream (long-term `enableRef` shape) — fork at `circt:fk-sc/uhdi-pool` remains open. Does not block the defense, but a reviewer may ask "will this actually get closed?". | P2 | Q1.5 explains; action plan §3.4.1 explicitly scopes the work: ~1 day C++ + fixture regeneration. | author / scheduled |
| R10 | `tools/install.sh` depends on GitHub Releases at `fkhaidari/uhdi`; the Yadro corporate network sometimes drops HTTPS to github.com without VPN (see global CLAUDE.md). If the defense demo runs outside the Yadro network — low risk; inside Yadro — VPN required. | P2 | Pre-defense pull via VPN and install `~/.local/uhdi-tools/` on the defense laptop. Alternative: docker image `ghcr.io/fkhaidari/uhdi-tools:b683085ef03e5ba2` (`tools/docker/image-tag.txt`). | author / mechanical |
| R11 | ChiselTrace demo path: deck slides `overview/chiseltrace*.html` exist (deck.js:146-155), but **there is no self-contained demo** in `demo/`. `uhdi_to_pdg/` is uncommitted (git status), fixtures are under `converter/test/fixtures/expected/pdg/`. End-to-end for PDG goes through bench fixtures, not through `./run.sh`. | P1 | If the ChiselTrace slide needs a "here's us running it" — build a minimal path via bench-fixture Counter + ChiselTrace GUI, or acknowledge that the slide shows only the projection (uhdi → PDG JSON) without runtime visualization. | author / open |
| R12 | `scripts/demo.sh` (mentioned in MEMORY.md as an entry point) **does not exist** in the repo root. The real entry point is `demo/<name>/run.sh` → `demo/run.nu`. | P2 | Fix MEMORY.md or add `scripts/demo.sh` as a symlink/wrapper. Does not block the defense. | author / cosmetic |

**Total:** P0 = 3 (R1, R2, R3); P1 = 4 (R4, R5, R6, R11); P2 = 5.

---

## 3. Demo readiness

`demo/<name>/run.sh` in all five cases is a symbolic link to the shared
`demo/run.sh` (bash shim that locates `nu` and invokes `demo/run.nu` with a
positional subcommand). The flag `--with-experimental-debug-intrinsics` is used
everywhere in the Chisel source (`demo/*/app/src/*.scala`, verified by `grep -rn`);
the old `--with-debug-intrinsics` appears nowhere.

| Demo | `run.sh` exists | Flag current | What it shows | Linked to a slide | Smoke status |
|------|----------------|--------------|-----------------|-----------------------|--------------|
| `demo/gcd/` | ✅ symlink → `demo/run.sh` | ✅ `--with-experimental-debug-intrinsics` | UInt arithmetic, single module, simplest end-to-end. README hgdb console session (`demo/README.md:151-205`) uses GCD. | **Likely hgdb-slide proxy** (deck slide is titled "Demo on Counter", but the actual demo repo has GCD as the most polished path). Also suitable for the Tywaves slide set. | **MUST WORK.** `design.db` exists (40 KB, May 8); contents for multi-arm `when` branches **not verified** — see R3. Run `./run.sh build` + `hgdb-db design.db` by hand. |
| `demo/fsm/` | ✅ symlink | ✅ verified | `ChiselEnum`-FSM (TrafficLight); Tywaves should show state as `Red/RedYellow/Green/Yellow` (`demo/README.md:30`). | Tywaves enum-rendering — potential tywaves slide. | Enum projection stress-test **not run** — R4. Run `./run.sh simulate` + tywaves manually. |
| `demo/fifo/` | ✅ symlink | ✅ verified | `Decoupled<UInt>` + `SyncReadMem`; Bundle ports collapse into Tywaves struct view. | Tywaves bundle-rendering — potential tywaves slide. | Bundle projection in HGLDD broken (R2) — `io_*` fields may not appear in `objects[]`. |
| `demo/pipeline/` | ✅ symlink | ✅ verified | 3-stage MAC + two sub-Modules; hierarchy navigation in Tywaves; hgdb step across pipeline registers. | Potential hgdb-slide or Tywaves-hierarchy slide. | Single-when only inside stages — hgdb path should work without multi-arm gap. Smoke manually. |
| `demo/bus/` | ✅ symlink | ✅ verified | `Decoupled` of nested `Bundle` (`Request{addr,data,write}` → `Response{data,ok}`). | Nested-record stress; not explicitly in the deck. | Bundle projection broken (R2) — two levels of nesting worsen the gap. |

**Deck bindings (deck.js → demo):**

- `overview/tywaves-usage.html` (slides 80-95) — overview slides with no hard
  binding to a specific `demo/<name>/`; screenshots can be pre-captured.
- `overview/hgdb-usage.html` (slides 116-143) — overview hgdb on the GCD session
  from the README, also not runtime-dependent.
- `overview/chiseltrace-usage.html` (146-155) — no demo repo (see R11).
- `solution/counter-demo.html` (406-410) — **final demonstration**, the slide
  specifically about a live run. **This is where R1 bites.**

**Pre-defense MUST-WORK path (minimal):**

1. `cd demo/gcd && ./run.sh build` — generates `design.uhdi.json`,
   `design.dd`, `design.db`. Must complete without errors.
2. `./run.sh simulate` — generates `design.vcd`. Verilator must be in PATH.
3. `tywaves design.vcd --hgldd-dir . --top-module GCD --extra-scopes
   TOP svsimTestbench dut` — GUI opens, hierarchy is visible, types are
   recognized.
4. `./run.sh debug-server` + `./run.sh debug` in two terminals — hgdb
   console session with breakpoints on lines of `GCD.scala`.

If all 4 steps are green on GCD, the hgdb+tywaves slides are defensible.
ChiselTrace is a separate matter (R11).

---

## 4. Documentation gaps surfaced this pass

A quick scan of the first ~50 lines of the key markdown files. Each entry is a
real inconsistency, not a hypothetical one.

- **`README.md:73`** (`uv pip install ... --no-config`) — a working path, but
  it does not mention that `--index-url` + `--no-config` are needed **only** in
  the Yadro corporate network. For an external reader this is unnecessary noise;
  worth clarifying.
- **`docs/uhdi-action-plan.md:13-19`** — the table shows ✅ only for A1-A4.
  A13-A16 are described in §1.1-1.4 as "being verified", but in the final table
  §5 (`docs/uhdi-action-plan.md:432-445`) their status is `Pre-sanity (§1.x)`.
  **Run A13-A16 and mark ✅ or unresolved** before the defense — otherwise a
  reviewer will point out the gap. See R6.
- **`docs/uhdi-action-plan.md:308-316`** (§3.4 Emitter extension) and
  **§3.4.1** (`docs/uhdi-action-plan.md:318-330`) — added by the current audit.
  The body of §3.4 describes the MVP, §3.4.1 describes the long-term shape.
  They are in the same section, but §3.4 does not say "the current implementation
  is the MVP, see §3.4.1". Worth adding a one-liner cross-reference.
- **`docs/uhdi-action-plan.md:511-515`** (§7.3 anticipated questions) — four
  questions are listed but **no answers are given in the action plan**. This file
  (`docs/defense-readiness.md` §1) closes them. Worth adding a cross-reference
  from §7.3 to here.
- **`bench/README.md:130-152`** "Pending fixtures" — the section is current
  (dated 2026-05-15). But the 3 P0/P1 risks from here (R2/R3/R4) did not
  make it into action plan §5 "Summary of assumptions". Worth adding them there
  or noting that the risk register lives in `defense-readiness.md`.
- **`tools/README.md:3`** references `ghcr.io/fkhaidari/uhdi-tools:<tag>`
  without explicitly stating `b683085ef03e5ba2` (the current tag from
  `tools/docker/image-tag.txt`). This is by design (`<tag>` placeholder), but
  `bench/README.md:69` uses the tag via `$(cat ../tools/docker/image-tag.txt)`.
  Verify manually that the image on ghcr.io is actually tagged `b683085ef03e5ba2`.
- **`demo/README.md:151-205`** — the README hgdb session for GCD shows working
  breakpoints. **Does this reflect current behavior or an old state?** If the GCD
  multi-arm when-gap (R3) cuts off breakpoints, the README diverges from the
  current state. Verify manually and either re-capture the output or simplify the
  demo to a single-when path.
- **`MEMORY.md`** (private: global) mentions `scripts/demo.sh` — the path does
  not exist, see R12.

---

## 5. Pre-defense punch list

In dependency and date order (T-0 = defense day, ~21 days from 2026-05-15).

1. **T-21 → T-18: Close P0 risks R1/R2/R3 (demo semantic readiness).**
   1. Fix the deck reference in `solution/counter-demo.html`: either point it to
      GCD as "Demo on GCD", or build a minimal counter-demo as a clone of the
      gcd structure. Record the decision in `docs/uhdi-action-plan.md` §3.6 / §7.
   2. Run `demo/gcd/run.sh build` + `hgdb-db design.db` by hand → confirm that
      breakpoint rows exist for all `:=`-lines of GCD.scala. If the multi-arm
      gap breaks everything — either patch uhdi_to_hgdb (priority), or simplify
      GCD to single-when.
   3. Open `design.dd` in Tywaves for GCD/Fifo/FSM → decide which of the three
      stays in the defense-demo set. Bundle gap R2 may narrow the choice to Fifo
      or FSM, which have less dependency on Bundle objects[].

2. **T-18 → T-14: A13-A16 sanity checks (R6).**
   1. A13: `uhdi.test_attr` on `dbg.variable` → grep the post-firtool output
      (`docs/uhdi-action-plan.md:56-66`).
   2. A14: confirm that `dbg.scope_body`/`dbg.block` already exist in our
      fk-sc/uhdi-pool branch (de facto yes, since the emitter works).
   3. A15: Tywaves on native HGLDD from GCD — does it open at all.
   4. A16: `withDebug` is present in Chisel sims
      (`demo/*/app/src/*Sim.scala`) — `--with-experimental-debug-intrinsics`
      verified by grep.
   - Mark ✅ in `docs/uhdi-action-plan.md:13-19`.

3. **T-14 → T-10: ChiselTrace path (R11).**
   1. Run `uhdi-to-pdg bench/.cache/.../Counter*.uhdi.json` → obtain
      `Counter.pdg.json`.
   2. Open in ChiselTrace GUI. If it works — capture a static screenshot for
      `overview/chiseltrace-usage.html`. If not — reframe the slide as
      "projection works, GUI integration is future work".

4. **T-10 → T-7: Documentation final pass.**
   1. README.md `--no-config` clarification (gap §4 item 1).
   2. Action plan §0 ✅-table updated (gap §4 item 2).
   3. Action plan §3.4 cross-reference to §3.4.1 (gap §4 item 3).
   4. Action plan §7.3 cross-reference to defense-readiness.md §1 (gap §4 item 4).
   5. bench/README.md Pending fixtures cross-reference to risk register
      (gap §4 item 5).
   6. tools/README.md image tag sanity check (gap §4 item 6).
   7. demo/README.md hgdb session reviewed against current output (gap §4 item 7).

5. **T-7 → T-3: Slides + speaker notes.**
   1. Speaker note for action plan §4.3 (`docs/uhdi-action-plan.md:416-424`) —
      finalize.
   2. 4 "obvious" questions (§7.3) → answers from §1 of this document.
   3. Bonus questions (Q1.5-Q1.10) — mentally rehearse aloud.
   4. Risk register — every P0 closed or explicitly explained in a speaker note
      as "known limitation, mitigation X".

6. **T-3 → T-1: Rehearse aloud at least 2 times.** Action plan §7.3 last bullet
   requires this. Record on phone, listen back.

7. **T-1: Defense laptop checklist.**
   1. `~/.local/uhdi-tools/` installed (`tools/install.sh all`), VPN to
      Yadro if on corp network (R10).
   2. `cd demo/gcd && ./run.sh build && ./run.sh simulate` — green.
   3. Tywaves + hgdb-replay + hgdb-debugger all in PATH.
   4. Video fallbacks (30-60s tywaves + 30-60s hgdb) recorded in case of
      network / GUI failure on the projector.

8. **T-0: Defense.** Run-of-show: open demo/gcd/.bin/, trigger
   `./run.sh simulate` 30 seconds before the slide, switch to the tywaves
   window. If anything crashes — switch to video.

---

*— end of document —*
