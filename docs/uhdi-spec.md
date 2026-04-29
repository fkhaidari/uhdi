# uhdi: Unified Hardware Debug Info -- Specification

**Status:** Draft
**Version:** 0.9
**Last updated:** 2026-04-22

This document specifies a unified JSON-based format for source-level debug information across hardware generator compilation pipelines (Chisel -> FIRRTL -> CIRCT -> Verilog, and comparable flows).

---

## 1. Motivation and Scope

### 1.1 Problem

Hardware generator workflows lose source-level debug fidelity at multiple points in the compilation pipeline (Chisel elaboration -> FIRRTL -> CIRCT lowering -> Verilog). Three open-source formats attempt to preserve this information, each taking a different approach:

#### hgdb (Hardware Generator Debugger, DAC '22)

- **Model:** scope tree + breakpoints.
- **Primary unit:** breakpoint (`type: decl | assign | none | block`).
- **Optimized for:** interactive GDB-like debugging.
- **Key features:** global variable dictionary with ID-based dedup; module definitions with nested instance hierarchy; conditional scope blocks with string `condition` fields; SSA-derived enable conditions on breakpoints; `indices` for array/regfile unrolling; `type: "delay"` + `depth` for FIFO-based history access; per-scope `filename`, per-breakpoint `line/column`.
- **Weakness:** conditions stored as strings (not ASTs), making them opaque to programmatic analysis; no value reconstruction for optimized-away variables; minimal type information (just `rtl: bool`).

#### Chisel trace / PDG (Program Dependency Graph)

- **Model:** typed graph (vertices + edges) + explicit CFG.
- **Primary unit:** vertex (`kind: IO | Definition | DataDefinition | ControlFlow | Connection`).
- **Optimized for:** dynamic slicing, root-cause analysis.
- **Key features:** four typed edge kinds (Data, Conditional, Index, Declaration); probe signals as first-class vertices for dynamic-index conditions; separate `predicates` list for when-condition probes; CFG with `stmtRef`/`predStmtRef` preserving FIRRTL last-connect semantics; dynamic writes (`mem(io.idx) := io.din`) expanded into N conditional connects; `clocked` flag on both vertices and edges; `isChiselStatement` distinguishing user-written from compiler-synthetic nodes.
- **Weakness:** no control-flow nesting in scope-tree sense (everything is graph); no type information beyond naming convention; doesn't preserve information usable by a GDB-like debugger.

#### HGLDD (CIRCT Debug Dialect emitter output)

- **Model:** object tree + expression trees.
- **Primary unit:** object (`kind: struct | module`).
- **Optimized for:** type-aware waveform viewing (Tywaves and Synopsys Verdi alpha).
- **Key features:** deduplicated `file_info` with explicit HGL/HDL boundary; struct definitions uniqued by JSON content; dual `hgl_loc`/`hdl_loc` per entity; **expression trees** for value reconstruction (opcodes `+`, `-`, `*`, `/`, `%`, `&`, `|`, `^`, `~`, `<<`, `>>`, `>>>`, `==`, `!=`, `===`, `!==`, `==?`, `!=?`, `<`, `>`, `<=`, `>=`, `{}`, `R{}`, `[]`, `?:`, `'{`); `packed_range`/`unpacked_range` for multi-dimensional arrays; `isExtModule` marker; instance renaming via `name` vs `hdl_obj_name`. Tywaves extensions add `dbg.enumdef`, `dbg.subfield`, `dbg.moduleinfo`.
- **Weakness:** no control-flow representation at all (static snapshot format); no breakpoint semantics; fixed HGL/HDL pair of locations (cannot track intermediate IR levels); no dataflow edges.

### 1.2 Why a unified format

No single format covers the union of use cases, and conversion between them is lossy in both directions:

- hgdb -> HGLDD loses breakpoint enables and conditional scope nesting.
- HGLDD -> hgdb loses expression-tree reconstruction.
- PDG -> either loses dataflow typing.

The goal of `uhdi` is a format whose **layered structure** accommodates all three use cases simultaneously, emitted once by the compiler and consumed selectively by each tool. A GDB-like debugger uses `scopes` + `variables` + `§9` breakpoints; a waveform viewer adds `types` + `expressions`; a slicer adds `§10` dataflow.

### 1.3 Non-goals

The format deliberately does **not** store:

- Runtime signal traces (VCD-style time-series values) -- those live in separate trace files.
- RTL netlists -- this is metadata *about* a netlist, not a replacement.
- Simulator-specific state (VPI handles, thread IDs, breakpoint state).
- Coverage, assertion, or verification result data.
- Synthesis/PnR metadata.

### 1.4 Design principles

1. **Flat ID-keyed pools for shared entities**, inline structures for unique-per-site data. Rationale: shared entities (types, expressions) repeat hundreds of times in large designs; inlining bloats documents and prevents dedup.
2. **Reference by ID through named fields** (`typeRef`, `varRef`, etc.) -- no prefix encoding, no JSON paths. Field name encodes the target pool. Rationale: O(1) resolution, no parsing, orthogonal to physical layout.
3. **Layered optionality** -- consumers pick the layers they need. Required core: `scopes`. Commonly useful: `types`, `expressions`, `variables`. Optional: `dataflow`, `temporal`, `provenance`.
4. **N-way representations** via top-level `representations` map with arbitrary string keys, not a fixed HGL/HDL pair. Rationale: CIRCT has 4-5 meaningful IR levels; dual pair is a special case.
5. **Honest status reporting** -- `preserved | reconstructed | lost` tells consumers what actually happened to each variable after compiler passes. Rationale: filling a gap none of the existing formats address.
6. **Strict validation** -- `additionalProperties: false` everywhere; unknown fields are errors, not silently ignored. Rationale: catches typos and drift at parse time, not runtime.

---

## 2. Information Categories

### 2.1 Coverage in existing formats

Prior work distributes debug information across thirteen orthogonal axes. No single existing format covers more than seven of them.

| # | Category | hgdb | PDG | HGLDD |
| --- | --- | :---: | :---: | :---: |
| 1 | Source location (file/line/col) | ✓ scope + bp | ✓ per vertex | ✓ `hgl_loc` |
| 2 | HDL location (generated code) | partial | ✗ | ✓ `hdl_loc` |
| 3 | Module hierarchy | ✓ instances | ✓ `modulePath` | ✓ children |
| 4 | Source ↔ RTL signal mapping | ✓ `variable.value` | ✓ `assignsTo` + `relatedSignal` | ✓ `value.sig_name` |
| 5 | Value reconstruction (post-opt) | ✗ | ✗ | ✓ **primary** |
| 6 | Type information | minimal (`rtl` bool) | none | ✓ rich |
| 7 | Control flow | ✓ nested blocks | ✓ CF vertices + edges | ✗ |
| 8 | Breakpoint / stepping | ✓ **primary** | implicit | implicit |
| 9 | Dataflow / dependencies | ✗ | ✓ **primary** | implicit via operands |
| 10 | Temporal / clocking | ✓ `delay` | ✓ `clocked` + `assignDelay` | ✗ |
| 11 | Arrays / memory indexing | ✓ `indices` | ✓ unrolled | ✓ `unpacked_range` |
| 12 | Probe instrumentation | ✗ (has `target`) | ✓ **primary** | ✗ |
| 13 | Metadata | `attributes` | -- | `HGLDD.version` |

### 2.2 Intersections

All three formats share the following common denominator:

- **Source location tuple** (file, line, column) -- stored differently: hgdb at scope + breakpoint level, PDG per vertex, HGLDD with dual hgl_loc/hdl_loc.
- **Module hierarchy** with definition ≠ instance distinction.
- **Minimal source-to-RTL name mapping** -- some way to translate a source-level name to an RTL signal.

Pairwise overlaps:

- **hgdb ∩ PDG:** both encode control flow, but with incompatible models (hgdb structural blocks with string conditions; PDG control-flow vertices plus explicit CFG).
- **hgdb ∩ HGLDD:** both use nested scope-like containers (`scope/block` vs `children`).
- **PDG ∩ HGLDD:** both handle bundle/vector fields via path notation.

### 2.3 Unique features per format

Features **only hgdb** has:

- Enable condition as a string derived from SSA condition stack.
- Delay FIFO (`type: "delay"`, `depth: N`) for history access.
- Global variable dictionary with deduplication by ID.
- `reorder` flag notifying the debugger that entries may need resorting by line.
- `target` for attaching a watchpoint to a signal without full variable mapping.

Features **only PDG** has:

- Typed edges (Data / Conditional / Index / Declaration) forming the basis for dynamic slicing.
- Probe signals as first-class vertices, split into two lists (regular vs when-predicates).
- Explicit CFG preserving FIRRTL last-connect semantics via `stmtRef`/`predStmtRef`.
- Dynamic-write expansion into N conditional connects per possible index value.
- `isChiselStatement` flag distinguishing user-written from synthetic nodes.

Features **only HGLDD** has:

- Expression trees with opcodes permitting value reconstruction after dead-code elimination.
- Separate struct definitions referenced by name.
- Inline scopes preserving debug info for modules that disappeared via inlining.
- Explicit instance renaming (`hdl_obj_name` vs `name`) for `hw.verilogName` attribute support.
- Packed vs unpacked ranges with correct multi-dimensional semantics.
- `isExtModule` marker for external bodies.

### 2.4 Gaps none of the three formats addresses

1. **Unified control-flow model.** hgdb's string conditions are not programmatically analyzable; PDG's CF vertices are not portable to a hgdb-style scope model. A single normalized representation (AST) from which both views can be derived is absent.

2. **Data-loss semantics.** HGLDD's expression trees solve value reconstruction, but no format declares *what happened* to each variable (preserved / reconstructed / lost). Consumers can't distinguish "debugger couldn't find the signal" from "compiler optimized it away".

3. **Multi-stage location mapping.** HGLDD gives HGL↔HDL; hgdb uses a two-pass High/Low FIRRTL approach internally but loses intermediate levels in the final table. Neither tracks all four to five meaningful IR levels in CIRCT pipelines.

4. **Provenance.** No format logs which compiler pass generated a synthetic signal. This is crucial for debugging the compiler itself and for tracing optimized outputs back to source constructs.

5. **Unified memory/array model.** Three different approaches exist (hgdb compact `indices`; PDG eager per-cell vertex expansion; HGLDD hybrid pattern + range). No canonical form exists to derive all three from.

### 2.5 Required vs optional categories in `uhdi`

The format addresses the gaps above via a layered structure. Seven categories are required for minimal usefulness; four more are optional for consumers that need them.

#### Required

1. **Source ↔ IR ↔ HDL mapping** -- per-entity location data across multiple representations (axis 1+2 unified).
2. **Module hierarchy** -- definitions vs instances, parameterized monomorphization (axis 3).
3. **Variables** -- source-level names with type, binding, status, and value recovery (axes 4+5 unified, plus data-loss semantics from §2.4 gap 2).
4. **Types** *(required for type-aware consumers, recommended otherwise)* -- ground integer, clock/reset, struct, vector, enum (axis 6).
5. **Expressions** -- opcode trees for reconstructing values of optimized-away variables (axis 5).
6. **Conditions** -- boolean ASTs (stored in the expressions pool with `uint<1>` result) used as guards, breakpoint enables, and formal assumptions (axis 7, filling §2.4 gap 1).
7. **Scope body** -- statement tree within each scope, preserving source structure and FIRRTL last-connect order.

#### Optional

8. **Dataflow graph** -- typed edges (Data/Conditional/Index/Declaration) for slicing (axis 9).
9. **Temporal info** -- clock domains, reset info, history FIFO depths (axis 10).
10. **Breakpoint metadata** -- inline on statements and scopes; covers steppability, dynamic enable conditions, priority, watchpoints, throttling, categorization, and entry/exit breakpoints (axis 8, see §9).
11. **Provenance** -- origin pass and derivation chain for synthetic entities (filling §2.4 gap 4).

---

## 3. Top-Level Document Structure

A `uhdi` document is a JSON object with the following top-level shape:

```jsonc
{
  "format":          { "name": "uhdi", "version": "1.0" },
  "producer":        { /* optional: name, version, timestamp */ },

  "representations": { /* required: repr-key -> { kind, language, files } */ },
  "roles":           { /* optional: authoring, simulation, canonical */ },
  "top":             [ /* required: array of scope refs */ ],

  "types":           { /* optional pool */ },
  "expressions":     { /* optional pool */ },
  "variables":       { /* optional pool */ },
  "scopes":          { /* required pool */ },

  "dataflow":        { /* optional; see §10 */ },
  "dataflowChunks":  [ /* optional; relative paths to per-scope chunks, see §10.8 */ ],
  "temporal":        { /* optional; see §11 */ },
  "provenance":      { /* optional; see §12 */ },
  "attributes":      { /* optional free-form metadata */ }
}
```

### 3.1 JSON Schema

```jsonc
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://uhdi/document.schema.json",

  "$defs": {
    "ScopeRef": { "type": "string" },

    "Representation": {
      "type": "object",
      "required": ["kind"],
      "additionalProperties": false,
      "properties": {
        "kind":     { "enum": ["source", "ir", "hdl"] },
        "language": { "type": "string" },
        "dialect":  { "type": "string" },
        "files":    { "type": "array", "items": { "type": "string" } }
      }
    },

    "Roles": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "authoring":  { "type": "string" },
        "simulation": { "type": "string" },
        "canonical":  { "type": "string" }
      }
    },

    "Format": {
      "type": "object",
      "required": ["name", "version"],
      "additionalProperties": false,
      "properties": {
        "name":    { "const": "uhdi" },
        "version": { "type": "string", "pattern": "^[0-9]+\\.[0-9]+$" }
      }
    },

    "Producer": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "name":      { "type": "string" },
        "version":   { "type": "string" },
        "timestamp": { "type": "string", "format": "date-time" }
      }
    }
  },

  "type": "object",
  "required": ["format", "representations", "top", "scopes"],
  "additionalProperties": false,

  "properties": {
    "format":          { "$ref": "#/$defs/Format" },
    "producer":        { "$ref": "#/$defs/Producer" },
    "representations": {
      "type": "object",
      "minProperties": 1,
      "additionalProperties": { "$ref": "#/$defs/Representation" }
    },
    "roles":       { "$ref": "#/$defs/Roles" },
    "top":         {
      "type": "array",
      "items": { "$ref": "#/$defs/ScopeRef" },
      "minItems": 1
    },
    "types":       { "$ref": "https://uhdi/types.schema.json" },
    "expressions": { "$ref": "https://uhdi/expressions.schema.json" },
    "variables":   { "$ref": "https://uhdi/variables.schema.json" },
    "scopes":      { "$ref": "https://uhdi/scopes.schema.json" },

    "dataflow":       { "$ref": "https://uhdi/dataflow.schema.json" },
    "dataflowChunks": { "type": "array", "items": { "type": "string" } },
    "temporal":       { "$ref": "https://uhdi/temporal.schema.json" },
    "provenance":     { "$ref": "https://uhdi/provenance.schema.json" },
    "attributes":     { "type": "object", "additionalProperties": true }
  }
}
```

### 3.2 Representations

The `representations` map declares all IR levels tracked by the document. Keys are arbitrary strings chosen by the producer. Every `Location.file` elsewhere in the document is an index into the `files` array of exactly one representation.

**Kinds:**

- `source` -- human-authored language (Chisel, Spade, PyMTL).
- `ir` -- compiler intermediate (FIRRTL, HW dialect).
- `hdl` -- emitted target (Verilog, VHDL, SystemVerilog).

### 3.3 Roles

Optional binding of representation keys to semantic roles:

- `authoring` -- where the human wrote code (used for IDE breakpoints).
- `simulation` -- what the simulator runs (used for `sigName` resolution).
- `canonical` -- default repr for unqualified references.

### 3.4 Top

Array of scope IDs that are roots of the module hierarchy. Typically one element (`Top` or `SoC`), but multiple entries are valid (e.g., design + testbench).

### 3.5 Document-level invariants

1. All `roles.*` values reference existing `representations` keys.
2. Every entry in `top` references an existing scope.
3. Every `Location.file` value is a valid index into the `files` array of the representation that owns the Location.
4. Variable `ownerScopeRef` and scope `variableRefs` are consistent (bi-directional).
5. No instantiation cycles between scopes.
6. Unreachable expressions are allowed but should generate a warning.

---

## 4. Types Pool

### 4.1 Grammar

Five type kinds:

1. **Ground integer** -- `uint` or `sint` with a width.
2. **Ground clock/reset** -- `clock`, `reset`, `asyncreset`, `analog`.
3. **Struct** -- ordered named members with optional `flipped` for bidirectional bundles.
4. **Vector** -- homogeneous element type with a size.
5. **Enum** -- underlying integer type plus variant-to-name mapping.

Ground types use abstract names only; `logic` and other HDL-specific names are emitter concerns, not format concerns.

### 4.2 JSON Schema

```jsonc
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://uhdi/types.schema.json",

  "$defs": {
    "TypeRef": { "type": "string" },

    "GroundInt": {
      "type": "object",
      "required": ["kind", "width"],
      "additionalProperties": false,
      "properties": {
        "kind":  { "enum": ["uint", "sint"] },
        "width": { "type": "integer", "minimum": 0 }
      }
    },

    "GroundClock": {
      "type": "object",
      "required": ["kind"],
      "additionalProperties": false,
      "properties": {
        "kind":  { "enum": ["clock", "reset", "asyncreset", "analog"] },
        "width": { "type": "integer", "minimum": 0 }
      }
    },

    "StructMember": {
      "type": "object",
      "required": ["name", "typeRef"],
      "additionalProperties": false,
      "properties": {
        "name":    { "type": "string" },
        "typeRef": { "$ref": "#/$defs/TypeRef" },
        "flipped": { "type": "boolean", "default": false }
      }
    },

    "Struct": {
      "type": "object",
      "required": ["kind", "members"],
      "additionalProperties": false,
      "properties": {
        "kind":    { "const": "struct" },
        "members": { "type": "array", "items": { "$ref": "#/$defs/StructMember" } }
      }
    },

    "Vector": {
      "type": "object",
      "required": ["kind", "elementRef", "size"],
      "additionalProperties": false,
      "properties": {
        "kind":       { "const": "vector" },
        "elementRef": { "$ref": "#/$defs/TypeRef" },
        "size":       { "type": "integer", "minimum": 0 }
      }
    },

    "Enum": {
      "type": "object",
      "required": ["kind", "underlyingTypeRef", "variants"],
      "additionalProperties": false,
      "properties": {
        "kind":              { "const": "enum" },
        "underlyingTypeRef": { "$ref": "#/$defs/TypeRef" },
        "variants": {
          "type": "object",
          "patternProperties": { "^-?[0-9]+$": { "type": "string" } },
          "additionalProperties": false
        }
      }
    },

    "Type": {
      "oneOf": [
        { "$ref": "#/$defs/GroundInt" },
        { "$ref": "#/$defs/GroundClock" },
        { "$ref": "#/$defs/Struct" },
        { "$ref": "#/$defs/Vector" },
        { "$ref": "#/$defs/Enum" }
      ]
    }
  },

  "type": "object",
  "additionalProperties": { "$ref": "#/$defs/Type" }
}
```

### 4.3 Examples

```jsonc
"types": {
  "uint8":    { "kind": "uint",  "width": 8 },
  "sint16":   { "kind": "sint",  "width": 16 },
  "bool":     { "kind": "uint",  "width": 1 },

  "clk":      { "kind": "clock" },
  "rst_sync": { "kind": "reset" },

  "CounterIO": {
    "kind": "struct",
    "members": [
      { "name": "en",    "typeRef": "bool" },
      { "name": "count", "typeRef": "uint8", "flipped": true }
    ]
  },

  "RegFile": {
    "kind": "vector",
    "elementRef": "uint8",
    "size": 32
  },

  "uint2": { "kind": "uint", "width": 2 },
  "FSMState": {
    "kind": "enum",
    "underlyingTypeRef": "uint2",
    "variants": { "0": "IDLE", "1": "RUN", "2": "DONE" }
  }
}
```

### 4.4 Type-level invariants

1. All `typeRef`, `elementRef`, `underlyingTypeRef` values reference existing types.
2. No cycles in struct membership or vector elements.
3. Enum `underlyingTypeRef` must point to a ground integer.
4. Enum `variants` keys must fit within the underlying type's width.
5. No duplicate `name` values among a single struct's members.

---

## 5. Expressions Pool

### 5.1 Rationale

Expressions are opcode trees used for two purposes:

1. **Value reconstruction** -- when a variable is optimized into an inline expression, the tree describes how to compute its value from live signals.
2. **Conditions** -- boolean expressions used as `guardRef`, `enableRef`, and in dataflow edge conditions. Conditions are ordinary expressions whose root has `uint<1>` width.

A single unified pool handles both. No separate `conditions` pool.

### 5.2 Operand shapes

An operand is one of:

- `{ "varRef": "..." }` -- reference to a variable.
- `{ "constant": N }` -- integer literal, optional `width`.
- `{ "bitVector": "0101..." }` -- bit-pattern literal.
- `{ "sigName": "..." }` -- raw RTL signal name (when no variable wraps it).
- `{ "exprRef": "..." }` -- reference to another named expression in the pool.
- A nested `OpNode` object (inline sub-expression).

Leaf kinds are disambiguated by the unique key present -- no `kind` discriminator.

### 5.3 Opcode coverage

Opcodes are partitioned by the IR level at which they are meaningful. An expression attached to a variable's `representations["<k>"].value` should use opcodes consistent with the level of representation `<k>`. Emitters that walk multiple IR levels (e.g., pre-`LowerTypes` FIRRTL for `source` repr, post-lowering HW dialect for `hdl` repr) may therefore produce different expressions per repr for the same variable.

#### 5.3.1 IR/HDL-level (available in any representation)

Arithmetic: `+`, `-`, `*`, `/`, `%`, `neg`
Bitwise: `&`, `|`, `^`, `~`
Logical: `&&`, `||`, `!`
Reduction: `andr`, `orr`, `xorr`
Shift: `<<`, `>>`, `>>>`
Compare (2-state): `==`, `!=`, `<`, `>`, `<=`, `>=`
Mux: `?:` (3 operands)
Concat: `{}` (N operands)
Replicate: `R{}` (count, val)
Extract: `[]` (val, hi, lo)
Pad/cast: `pad`, `asUInt`, `asSInt`, `cvt`, `asClock`, `asAsyncReset`
Aggregate literal: `'{` (struct/vector literal)
Field: `.` (requires `fieldName`)
Index: `idx`

#### 5.3.2 HDL-level only (4-state simulation semantics)

These opcodes model SystemVerilog 4-state (`0/1/x/z`) comparisons. They are meaningful only in an `hdl`-kind representation and should not appear in `source` or `ir` repr expressions:

- `===`, `!==` -- 4-state identity / non-identity.
- `==?`, `!=?` -- wildcard equality / inequality.

If a 2-state `==` survives all the way to Verilog emission, it is still emitted as `===` (or the reverse) by the emitter; the choice of opcode here records *which comparison semantics the source intended*, not which Verilog operator will be written.

#### 5.3.3 Source-level only (pre-FIRRTL; do not appear in `ir` or `hdl` repr)

These are Chisel DSL operators that are eliminated during Chisel elaboration / FIRRTL conversion. They survive in `source`-kind representations only:

- `Mux`, `Cat`, `Fill`, `VecInit`

An emitter that does not track a pre-elaboration representation will never produce these opcodes.

### 5.4 JSON Schema

```jsonc
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://uhdi/expressions.schema.json",

  "$defs": {
    "ExprRef": { "type": "string" },
    "VarRef":  { "type": "string" },

    "LeafVar":    { "type": "object", "required": ["varRef"],
                    "additionalProperties": false,
                    "properties": { "varRef": { "$ref": "#/$defs/VarRef" } } },

    "LeafConst":  { "type": "object", "required": ["constant"],
                    "additionalProperties": false,
                    "properties": {
                      "constant": { "type": "integer" },
                      "width":    { "type": "integer", "minimum": 0 }
                    } },

    "LeafBitVec": { "type": "object", "required": ["bitVector"],
                    "additionalProperties": false,
                    "properties": {
                      "bitVector": { "type": "string",
                                     "pattern": "^[01xzXZ?]+$" }
                    } },

    "LeafSig":    { "type": "object", "required": ["sigName"],
                    "additionalProperties": false,
                    "properties": { "sigName": { "type": "string" } } },

    "LeafRef":    { "type": "object", "required": ["exprRef"],
                    "additionalProperties": false,
                    "properties": { "exprRef": { "$ref": "#/$defs/ExprRef" } } },

    "OpNode": {
      "type": "object",
      "required": ["opcode", "operands"],
      "additionalProperties": false,
      "properties": {
        "opcode": {
          "enum": [
            "+", "-", "*", "/", "%", "neg",
            "&", "|", "^", "~",
            "&&", "||", "!",
            "andr", "orr", "xorr",
            "<<", ">>", ">>>",
            "==", "!=", "<", ">", "<=", ">=",
            "===", "!==", "==?", "!=?",
            "?:",
            "{}", "R{}", "[]",
            "pad", "asUInt", "asSInt", "cvt", "asClock", "asAsyncReset",
            "'{", ".", "idx",
            "Mux", "Cat", "Fill", "VecInit"
          ]
        },
        "operands":  { "type": "array", "items": { "$ref": "#/$defs/Operand" } },
        "fieldName": { "type": "string" }
      }
    },

    "Operand": {
      "oneOf": [
        { "$ref": "#/$defs/LeafVar" },
        { "$ref": "#/$defs/LeafConst" },
        { "$ref": "#/$defs/LeafBitVec" },
        { "$ref": "#/$defs/LeafSig" },
        { "$ref": "#/$defs/LeafRef" },
        { "$ref": "#/$defs/OpNode" }
      ]
    },

    "Expression": { "$ref": "#/$defs/OpNode" }
  },

  "type": "object",
  "additionalProperties": { "$ref": "#/$defs/Expression" }
}
```

### 5.5 Examples

```jsonc
"expressions": {
  "reg_plus_1": {
    "opcode": "+",
    "operands": [
      { "varRef": "reg" },
      { "constant": 1, "width": 8 }
    ]
  },

  "guard_write": {
    "opcode": "&&",
    "operands": [
      { "varRef": "io_en" },
      { "opcode": "!", "operands": [ { "varRef": "reset" } ] }
    ]
  },

  "reg_slice": {
    "opcode": "[]",
    "operands": [ { "varRef": "reg" }, { "constant": 5 }, { "constant": 2 } ]
  },

  "chosen": {
    "opcode": "?:",
    "operands": [ { "varRef": "io_sel" }, { "varRef": "a" }, { "varRef": "b" } ]
  },

  "bundle_x": {
    "opcode": ".",
    "fieldName": "x",
    "operands": [ { "varRef": "bundle" } ]
  },

  "reg_plus_1_x2": {
    "opcode": "*",
    "operands": [ { "exprRef": "reg_plus_1" }, { "constant": 2 } ]
  }
}
```

### 5.6 Expression-level invariants

1. **Arity**: `?:` exactly 3 operands; `~`, `!`, `neg`, reductions exactly 1; `[]` exactly 3 (val, hi, lo); `R{}` exactly 2 (count, val); `{}`, `'{` at least 2; binary ops exactly 2.
2. **Width consistency**: computed widths (per FIRRTL inference rules) match any explicit `width` annotations.
3. **Boolean context**: expressions used as `guardRef`, `enableRef`, or edge conditions must evaluate to `uint<1>`.
4. **No cycles** through `exprRef` chains.
5. `fieldName` is legal only with opcode `.`.
6. Constants with explicit `width` must fit in that width.
7. **Opcode / repr-level consistency**: an expression used inside `representations["<k>"].value` must use opcodes valid for the `kind` of representation `<k>` per §5.3. Source-level opcodes (`Mux`, `Cat`, `Fill`, `VecInit`) in `ir`/`hdl` reprs are errors. 4-state compare opcodes (`===`, `!==`, `==?`, `!=?`) in `source` reprs are errors.

---

## 6. Variables Pool

### 6.1 Structure

A variable has two information layers:

1. **Representation-independent**: `typeRef`, `bindKind`, `ownerScopeRef`, `direction`, `delay`.
2. **Per-representation**: `name`, `location`, `value`, `status` -- stored in the `representations` map with arbitrary repr keys.

Value binding (per-repr) describes how to recover the value at that IR level: `sigName`, `exprRef`, `constant`, or `bitVector` (a fixed-width binary literal -- used when an integer constant is wider than 64 bits and would not fit a JSON number safely). Status (per-repr) records what happened to the variable *in that specific representation* -- the same variable may be preserved at the FIRRTL level and reconstructed or lost at the Verilog level after DCE.

### 6.2 BindKind values

- `port` -- module I/O (requires `direction`).
- `wire` -- combinational wire.
- `reg` -- register/flip-flop.
- `node` -- named intermediate result.
- `literal` -- compile-time constant (parameterized value).
- `mem` -- memory (typed as vector in types pool).
- `synthetic` -- created by a compiler pass, no direct source analog.
- `probe` -- FIRRTL read-only reference (`firrtl.probe` / RefType). No electrical realization; readable only via XMR / path-operator in formal tools or simulator tap interfaces. A `probe`-bind variable must not be consumed as data in a `connect` -- linter error.
- `rwprobe` -- FIRRTL read-write reference (`firrtl.rwprobe`). Same semantics as `probe` plus support for force/release operations.

Probes exist because Chisel 7+ and recent FIRRTL treat cross-module reads as first-class. Treating them as ordinary `synthetic` variables would lose the critical fact that (a) they have no corresponding VCD signal in the simulation representation for a naive consumer to sample, and (b) they must be accessed via path syntax rather than normal hierarchical names.

### 6.3 Status values

Status is declared **per representation**. The same variable can legitimately be `preserved` in one repr (e.g., a named register at the FIRRTL level) and `reconstructed` or `lost` in another (same register after aggressive optimization at the HDL level).

- `preserved` -- exists as a named signal at this representation level, directly observable.
- `reconstructed` -- not directly observable at this level, but value is computable via an expression referenced from `value.exprRef`.
- `lost` -- removed at this level; name is retained for source-level reference, but runtime value is unrecoverable at this repr.

A variable without a `status` in some repr is implicitly `preserved` -- a conservative default that does not distinguish "truly present" from "emitter didn't check". Strict emitters should always emit an explicit status.

### 6.4 JSON Schema

```jsonc
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://uhdi/variables.schema.json",

  "$defs": {
    "VarRef":    { "type": "string" },
    "TypeRef":   { "type": "string" },
    "ExprRef":   { "type": "string" },
    "ScopeRef":  { "type": "string" },

    "Location": {
      "type": "object",
      "required": ["file"],
      "additionalProperties": false,
      "properties": {
        "file":        { "type": "integer", "minimum": 0 },
        "beginLine":   { "type": "integer", "minimum": 1 },
        "endLine":     { "type": "integer", "minimum": 1 },
        "beginColumn": { "type": "integer", "minimum": 1 },
        "endColumn":   { "type": "integer", "minimum": 1 }
      }
    },

    "BindKind":  { "enum": ["port", "wire", "reg", "node",
                            "literal", "mem", "synthetic",
                            "probe", "rwprobe"] },
    "Direction": { "enum": ["input", "output", "inout"] },
    "Status":    { "enum": ["preserved", "reconstructed", "lost"] },

    "ValueBinding": {
      "oneOf": [
        { "type": "object", "required": ["sigName"],
          "additionalProperties": false,
          "properties": { "sigName": { "type": "string" } } },
        { "type": "object", "required": ["exprRef"],
          "additionalProperties": false,
          "properties": { "exprRef": { "$ref": "#/$defs/ExprRef" } } },
        { "type": "object", "required": ["constant"],
          "additionalProperties": false,
          "properties": { "constant": { "type": "integer" } } },
        { "type": "object", "required": ["bitVector"],
          "additionalProperties": false,
          "properties": { "bitVector": { "type": "string",
                                         "pattern": "^[01xzXZ?]+$" } } }
      ]
    },

    "PerRepresentation": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "name":     { "type": "string" },
        "location": { "$ref": "#/$defs/Location" },
        "value":    { "$ref": "#/$defs/ValueBinding" },
        "status":   { "$ref": "#/$defs/Status" }
      }
    },

    "Variable": {
      "type": "object",
      "required": ["typeRef", "bindKind", "ownerScopeRef"],
      "additionalProperties": false,
      "properties": {
        "typeRef":         { "$ref": "#/$defs/TypeRef" },
        "ownerScopeRef":   { "$ref": "#/$defs/ScopeRef" },
        "bindKind":        { "$ref": "#/$defs/BindKind" },
        "direction":       { "$ref": "#/$defs/Direction" },
        "representations": {
          "type": "object",
          "additionalProperties": { "$ref": "#/$defs/PerRepresentation" }
        },
        "delay":           { "type": "integer", "minimum": 0 }
      }
    }
  },

  "type": "object",
  "additionalProperties": { "$ref": "#/$defs/Variable" }
}
```

### 6.5 Examples

**Port:**
```jsonc
"io_en": {
  "typeRef":       "bool",
  "bindKind":      "port",
  "direction":     "input",
  "ownerScopeRef": "Counter_W8",
  "representations": {
    "chisel":  { "name": "io.en",
                 "status": "preserved",
                 "location": { "file": 0, "beginLine": 3, "beginColumn": 11 } },
    "verilog": { "name": "io_en",
                 "status": "preserved",
                 "value": { "sigName": "io_en" },
                 "location": { "file": 3, "beginLine": 2 } }
  }
}
```

**Register:**
```jsonc
"reg": {
  "typeRef":       "uint8",
  "bindKind":      "reg",
  "ownerScopeRef": "Counter_W8",
  "representations": {
    "chisel":  { "name": "reg",
                 "status": "preserved",
                 "location": { "file": 0, "beginLine": 7 } },
    "verilog": { "name": "reg_q",
                 "status": "preserved",
                 "value": { "sigName": "reg_q" } }
  }
}
```

**Reconstructed at HDL level, preserved at FIRRTL level:**
```jsonc
"partial_sum": {
  "typeRef":       "uint9",
  "bindKind":      "node",
  "ownerScopeRef": "ALU",
  "representations": {
    "chisel":  { "name": "partialSum",
                 "status": "preserved",
                 "location": { "file": 1, "beginLine": 17 } },
    "verilog": { "name": "partialSum",
                 "status": "reconstructed",
                 "value": { "exprRef": "a_plus_b" } }
  }
}
```

**Literal (parameter):**
```jsonc
"width_const": {
  "typeRef":       "uint8",
  "bindKind":      "literal",
  "ownerScopeRef": "Counter_W8",
  "representations": {
    "chisel": {
      "name":     "WIDTH",
      "status":   "preserved",
      "location": { "file": 0, "beginLine": 4 },
      "value":    { "constant": 8 }
    }
  }
}
```

**Lost at HDL level (survives in source only):**
```jsonc
"tmp_dead": {
  "typeRef":       "uint8",
  "bindKind":      "node",
  "ownerScopeRef": "ALU",
  "representations": {
    "chisel":  { "name": "tmp",
                 "status": "preserved",
                 "location": { "file": 1, "beginLine": 20 } },
    "verilog": { "status": "lost" }
  }
}
```

**Probe (read-only cross-module reference):**
```jsonc
"dbg_pc_tap": {
  "typeRef":       "uint32",
  "bindKind":      "probe",
  "ownerScopeRef": "Core_W64",
  "representations": {
    "chisel":  { "name": "dbg.pc",
                 "status": "preserved",
                 "location": { "file": 0, "beginLine": 42 } }
  }
}
```

### 6.6 Variable-level invariants

1. All `typeRef`, `ownerScopeRef`, `exprRef` references resolve.
2. `direction` is present only when `bindKind: "port"`.
3. `representations` keys are a subset of top-level `representations`.
4. **Per-repr status/value consistency**, evaluated independently in each repr:
   - `status: "preserved"` -> the repr entry has either `value.sigName` or `value.constant`, or (for `source`-kind repr) a `name` sufficient to locate the entity.
   - `status: "reconstructed"` -> the repr entry has `value.exprRef`.
   - `status: "lost"` -> the repr entry has no `value`.
5. `bindKind: "literal"` incompatible with `value.sigName` in any repr.
6. `bindKind: "probe"` and `"rwprobe"` incompatible with `value.sigName` in an `hdl`-kind repr -- probes have no electrical signal. Their value, if provided, must be an expression (`value.exprRef`) describing the path from which they read.
7. Bi-directional consistency with scope `variableRefs` **if the scope emits that field**. When `scope.variableRefs` is absent, consumers derive the index from variable `ownerScopeRef`.
8. Unique `name` within a scope at each representation.
9. If a variable has a `delay` value and the temporal layer is present, there should be a matching entry in `temporal.delays[]`.

### 6.7 Bundle flattening

When a Chisel `Bundle` (e.g. `io` with three fields) is lowered to Verilog, the bundle disappears and its fields become separate signals (`io_en`, `io_count`, `io_valid`). Two legitimate ways to express this in `uhdi`:

**Consolidated form** -- one variable `io` with `typeRef` pointing to a struct type; its `value` on the Verilog representation is an `'{` expression assembling the three signals back into a struct:

```jsonc
"io": {
  "typeRef":       "CounterIO",
  "bindKind":      "port",
  "ownerScopeRef": "Counter_W8",
  "representations": {
    "chisel":  { "name": "io", "status": "preserved" },
    "verilog": {
      "status": "reconstructed",
      "value":  { "exprRef": "io_assembled" }   // '{io_en, io_count, io_valid}
    }
  }
}
```

**Split form** -- three separate variables on the top level, each with a ground type, no bundle structure:

```jsonc
"io_en":    { "typeRef": "bool",  ... },
"io_count": { "typeRef": "uint8", ... },
"io_valid": { "typeRef": "bool",  ... }
```

Tywaves-style waveform viewers prefer the consolidated form (preserves structure). GDB-style debuggers work with either. **Consolidated is the recommended canonical form.** When both views are required, the provenance layer (§12) records the derivation between them.

### 6.8 Cross-module references (XMR)

FIRRTL supports hierarchical references via `RefType` / `firrtl.xmr.deref`. In `uhdi`, XMRs are modeled by `bindKind: "probe"` / `"rwprobe"` variables whose `ownerScopeRef` points to the module *declaring* the probe (typically the module exposing the internal signal), not the module *consuming* it. Consumers dereference by walking provenance or by reading the probe's `value.exprRef` which describes the source path.

An XMR read does not generate any dataflow `Data` edge *to the consuming variable* -- only a `Declaration` edge. This models the formal-verification semantics of probes (read-through without electrical load).

---

## 7. Scopes Pool

### 7.1 Structure

A scope represents a module definition in a specific monomorphization. It has:

- **Identity**: `name`, `kind`, optional `parameters`.
- **Per-representation data**: names and locations in each IR level.
- **Variable references**: backlinks to variables owned by this scope.
- **Instantiations**: named cross-refs to other scopes (with optional per-repr renaming).
- **Body**: ordered statement tree.

### 7.2 Scope kinds

- `module` -- normal module with body.
- `extmodule` -- extern module (no body).
- `inline` -- inlined module's debug info preserved without a standalone module. Optional `containerScopeRef` points at the enclosing module-kind scope; consumers (e.g. waveform viewers that splice inline scopes back into a parent's `children[]`) use it to graft the inline record into the right parent. Absent -> consumers fall back to walking `instantiates` from candidate parents.
- `layer_block` -- Chisel/FIRRTL `layer` block (`firrtl.layerblock` / `layer.block`). A scoping construct whose body is compiled conditionally (debug/verification layers may be stripped for synthesis). Its own `parameters` are always absent; its `instantiates` list is typically empty.

### 7.3 Statement kinds

**Regular statements (flow-of-control):**

- `decl` -- variable declaration point (watch visibility starts here).
- `connect` -- assignment of value to a variable.
- `block` -- grouping with optional `guardRef` (used for when/else). Optional `negated: true` flags the `else` branch of a paired when/else: the same `guardRef` appears on two sibling `block` statements, the first with `negated` absent (or `false`), the second with `negated: true`. A consumer combines guard + negation when computing the AND-reduced enable for nested statements.
- `none` -- empty marker for step-over support.

**Verification statements (distinguished because they do not generate hardware but participate in debug flows):**

- `assert` -- simulation or formal assertion; requires `condRef` and optional `message`. When the condition evaluates to 0, the verification tool reports a failure.
- `assume` -- constraint/assumption for formal tools; same fields as `assert`. Simulators treat as `assert`; formal tools use it to prune unreachable states.
- `cover` -- coverage point; requires `condRef`. No failure on false, but tools record hits for coverage statistics.

All three verification kinds accept `bp` metadata -- a failed assertion is the canonical "reason to stop" in formal flows.

Body array order is significant (FIRRTL last-connect semantics).

### 7.4 JSON Schema

```jsonc
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://uhdi/scopes.schema.json",

  "$defs": {
    "ScopeRef": { "type": "string" },
    "VarRef":   { "type": "string" },
    "ExprRef":  { "type": "string" },

    "Location": {
      "type": "object",
      "required": ["file"],
      "additionalProperties": false,
      "properties": {
        "file":        { "type": "integer", "minimum": 0 },
        "beginLine":   { "type": "integer", "minimum": 1 },
        "endLine":     { "type": "integer", "minimum": 1 },
        "beginColumn": { "type": "integer", "minimum": 1 },
        "endColumn":   { "type": "integer", "minimum": 1 }
      }
    },

    "Parameter": {
      "type": "object",
      "required": ["name", "value"],
      "additionalProperties": false,
      "properties": {
        "name":  { "type": "string" },
        "value": {}
      }
    },

    "Instantiation": {
      "type": "object",
      "required": ["as", "scopeRef"],
      "additionalProperties": false,
      "properties": {
        "as":       { "type": "string" },
        "scopeRef": { "$ref": "#/$defs/ScopeRef" },
        "representations": {
          "type": "object",
          "additionalProperties": {
            "type": "object",
            "additionalProperties": false,
            "properties": {
              "name":     { "type": "string" },
              "location": { "$ref": "#/$defs/Location" }
            }
          }
        }
      }
    },

    "BreakpointMeta": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "steppable":  { "type": "boolean", "default": true },
        "enableRef":  { "$ref": "#/$defs/ExprRef" },
        "priority":   { "type": "integer", "default": 0 },
        "watchpoint": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "kind":     { "enum": ["change", "rising", "falling", "value"] },
            "matchRef": { "$ref": "#/$defs/ExprRef" }
          },
          "required": ["kind"]
        },
        "throttle": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "maxHits": { "type": "integer", "minimum": 1 },
            "period":  { "type": "integer", "minimum": 1 }
          }
        },
        "category":   { "type": "array", "items": { "type": "string" } },
        "message":    { "type": "string" }
      }
    },

    "ScopeBreakpointMeta": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "stopOnEntry": { "type": "boolean", "default": false },
        "stopOnExit":  { "type": "boolean", "default": false },
        "category":    { "type": "array", "items": { "type": "string" } }
      }
    },

    "ValueRef": {
      "oneOf": [
        { "type": "object", "required": ["varRef"],
          "additionalProperties": false,
          "properties": { "varRef": { "$ref": "#/$defs/VarRef" } } },
        { "type": "object", "required": ["exprRef"],
          "additionalProperties": false,
          "properties": { "exprRef": { "$ref": "#/$defs/ExprRef" } } },
        { "type": "object", "required": ["constant"],
          "additionalProperties": false,
          "properties": { "constant": { "type": "integer" } } }
      ]
    },

    "LocationMap": {
      "type": "object",
      "additionalProperties": { "$ref": "#/$defs/Location" }
    },

    "StmtDecl": {
      "type": "object",
      "required": ["kind", "varRef"],
      "additionalProperties": false,
      "properties": {
        "kind":      { "const": "decl" },
        "varRef":    { "$ref": "#/$defs/VarRef" },
        "locations": { "$ref": "#/$defs/LocationMap" },
        "bp":        { "$ref": "#/$defs/BreakpointMeta" }
      }
    },

    "StmtConnect": {
      "type": "object",
      "required": ["kind", "varRef", "valueRef"],
      "additionalProperties": false,
      "properties": {
        "kind":      { "const": "connect" },
        "varRef":    { "$ref": "#/$defs/VarRef" },
        "valueRef":  { "$ref": "#/$defs/ValueRef" },
        "locations": { "$ref": "#/$defs/LocationMap" },
        "bp":        { "$ref": "#/$defs/BreakpointMeta" }
      }
    },

    "StmtBlock": {
      "type": "object",
      "required": ["kind", "body"],
      "additionalProperties": false,
      "properties": {
        "kind":      { "const": "block" },
        "guardRef":  { "$ref": "#/$defs/ExprRef" },
        "negated":   { "type": "boolean", "default": false },
        "body":      { "type": "array", "items": { "$ref": "#/$defs/Statement" } },
        "locations": { "$ref": "#/$defs/LocationMap" }
      }
    },

    "StmtNone": {
      "type": "object",
      "required": ["kind"],
      "additionalProperties": false,
      "properties": {
        "kind":      { "const": "none" },
        "locations": { "$ref": "#/$defs/LocationMap" },
        "bp":        { "$ref": "#/$defs/BreakpointMeta" }
      }
    },

    "StmtAssert": {
      "type": "object",
      "required": ["kind", "condRef"],
      "additionalProperties": false,
      "properties": {
        "kind":      { "const": "assert" },
        "condRef":   { "$ref": "#/$defs/ExprRef" },
        "message":   { "type": "string" },
        "locations": { "$ref": "#/$defs/LocationMap" },
        "bp":        { "$ref": "#/$defs/BreakpointMeta" }
      }
    },

    "StmtAssume": {
      "type": "object",
      "required": ["kind", "condRef"],
      "additionalProperties": false,
      "properties": {
        "kind":      { "const": "assume" },
        "condRef":   { "$ref": "#/$defs/ExprRef" },
        "message":   { "type": "string" },
        "locations": { "$ref": "#/$defs/LocationMap" },
        "bp":        { "$ref": "#/$defs/BreakpointMeta" }
      }
    },

    "StmtCover": {
      "type": "object",
      "required": ["kind", "condRef"],
      "additionalProperties": false,
      "properties": {
        "kind":      { "const": "cover" },
        "condRef":   { "$ref": "#/$defs/ExprRef" },
        "message":   { "type": "string" },
        "locations": { "$ref": "#/$defs/LocationMap" },
        "bp":        { "$ref": "#/$defs/BreakpointMeta" }
      }
    },

    "Statement": {
      "oneOf": [
        { "$ref": "#/$defs/StmtDecl" },
        { "$ref": "#/$defs/StmtConnect" },
        { "$ref": "#/$defs/StmtBlock" },
        { "$ref": "#/$defs/StmtNone" },
        { "$ref": "#/$defs/StmtAssert" },
        { "$ref": "#/$defs/StmtAssume" },
        { "$ref": "#/$defs/StmtCover" }
      ]
    },

    "Scope": {
      "type": "object",
      "required": ["name"],
      "additionalProperties": false,
      "properties": {
        "name":          { "type": "string" },
        "kind":          { "enum": ["module", "extmodule", "inline", "layer_block"],
                           "default": "module" },
        "parameters":    { "type": "array", "items": { "$ref": "#/$defs/Parameter" } },
        "representations": {
          "type": "object",
          "additionalProperties": {
            "type": "object",
            "additionalProperties": false,
            "properties": {
              "name":     { "type": "string" },
              "location": { "$ref": "#/$defs/Location" }
            }
          }
        },
        "variableRefs":      { "type": "array", "items": { "$ref": "#/$defs/VarRef" } },
        "instantiates":      { "type": "array", "items": { "$ref": "#/$defs/Instantiation" } },
        "containerScopeRef": { "$ref": "#/$defs/ScopeRef" },
        "body":              { "type": "array", "items": { "$ref": "#/$defs/Statement" } },
        "bp":                { "$ref": "#/$defs/ScopeBreakpointMeta" }
      }
    }
  },

  "type": "object",
  "additionalProperties": { "$ref": "#/$defs/Scope" }
}
```

### 7.5 Example

```jsonc
"scopes": {
  "Counter_W8": {
    "name": "Counter",
    "kind": "module",
    "parameters": [{ "name": "width", "value": 8 }],
    "representations": {
      "chisel":  { "name": "Counter",
                   "location": { "file": 0, "beginLine": 1, "endLine": 9 } },
      "verilog": { "name": "Counter_1",
                   "location": { "file": 3, "beginLine": 1, "endLine": 25 } }
    },
    "variableRefs": ["io_en", "io_count", "reg"],
    "instantiates": [],
    "body": [
      { "kind": "decl",
        "varRef": "reg",
        "locations": { "chisel": { "file": 0, "beginLine": 5 } }
      },
      { "kind": "block",
        "guardRef": "when_en",
        "locations": { "chisel": { "file": 0, "beginLine": 6 } },
        "body": [
          { "kind": "connect",
            "varRef":   "reg",
            "valueRef": { "exprRef": "reg_plus_1" },
            "locations": {
              "chisel":  { "file": 0, "beginLine": 6, "beginColumn": 20 },
              "verilog": { "file": 3, "beginLine": 12 }
            },
            "bp": { "steppable": true, "enableRef": "when_en" }
          }
        ]
      },
      { "kind": "connect",
        "varRef":   "io_count",
        "valueRef": { "varRef": "reg" },
        "locations": {
          "chisel":  { "file": 0, "beginLine": 8 },
          "verilog": { "file": 3, "beginLine": 15 }
        }
      }
    ]
  },

  "Top": {
    "name": "Top",
    "kind": "module",
    "representations": {
      "chisel": { "name": "Top", "location": { "file": 0, "beginLine": 11 } }
    },
    "variableRefs": [],
    "instantiates": [
      { "as": "c8", "scopeRef": "Counter_W8",
        "representations": { "verilog": { "name": "c8_inst" } }
      }
    ],
    "body": []
  }
}
```

Example with a verification assertion inside a layer block:

```jsonc
"scopes": {
  "Verification_Block": {
    "name": "verification",
    "kind": "layer_block",
    "body": [
      { "kind": "assert",
        "condRef": "overflow_impossible",
        "message": "counter value must never exceed MAX",
        "locations": { "chisel": { "file": 0, "beginLine": 14 } },
        "bp": { "category": ["formal", "overflow"] }
      },
      { "kind": "cover",
        "condRef": "reset_then_enable",
        "locations": { "chisel": { "file": 0, "beginLine": 16 } }
      }
    ]
  }
}
```

### 7.6 Scope-level invariants

1. All references resolve.
2. `kind: "extmodule"` -> body empty or absent.
3. `kind: "inline"` -> no `parameters`.
4. `kind: "layer_block"` -> no `parameters`; `instantiates` should be empty in typical use (nested module instantiations are legal but rare).
5. `variableRefs`, if present, is consistent bi-directionally with variable `ownerScopeRef`. When absent, consumers derive the index from `ownerScopeRef`.
6. Instantiation `as`-names unique within a scope.
7. `guardRef`, `enableRef`, and `condRef` (on verification statements) point to expressions with `uint<1>` result.
8. Body array order preserved (FIRRTL last-connect semantics).
9. Per-entity `representations` keys are a subset of top-level keys.
10. Verification statements (`assert`, `assume`, `cover`) are legal inside `module` and `layer_block` scopes; outside those, linter warning.
11. `containerScopeRef`, when present, must resolve to a scope of `kind: "module"` or `"extmodule"`; setting it on a non-`inline` scope is a linter warning.
12. Two sibling `StmtBlock`s sharing one `guardRef` model when/else only when exactly one has `negated: true`; any other combination (both negated, both unmarked) is a linter warning.

---

## 8. Complete Minimal Example

```jsonc
{
  "format": { "name": "uhdi", "version": "1.0" },
  "producer": {
    "name":      "chisel-circt",
    "version":   "7.0",
    "timestamp": "2026-04-22T12:00:00Z"
  },

  "representations": {
    "chisel":  { "kind": "source", "language": "Chisel",
                 "files": ["Counter.scala"] },
    "verilog": { "kind": "hdl",    "language": "SystemVerilog",
                 "files": ["Counter.v"] }
  },

  "roles": {
    "authoring":  "chisel",
    "simulation": "verilog",
    "canonical":  "verilog"
  },

  "top": ["Top"],

  "types": {
    "bool":  { "kind": "uint", "width": 1 },
    "uint8": { "kind": "uint", "width": 8 }
  },

  "expressions": {
    "reg_plus_1": {
      "opcode": "+",
      "operands": [ { "varRef": "reg" }, { "constant": 1, "width": 8 } ]
    },
    "when_en": {
      "opcode": "||",
      "operands": [ { "varRef": "io_en" }, { "constant": 0, "width": 1 } ]
    }
  },

  "variables": {
    "io_en": {
      "typeRef":       "bool",
      "bindKind":      "port",
      "direction":     "input",
      "ownerScopeRef": "Counter_W8",
      "representations": {
        "chisel":  { "name": "io.en", "status": "preserved" },
        "verilog": { "name": "io_en", "status": "preserved",
                     "value": { "sigName": "io_en" } }
      }
    },
    "io_count": {
      "typeRef":       "uint8",
      "bindKind":      "port",
      "direction":     "output",
      "ownerScopeRef": "Counter_W8",
      "representations": {
        "chisel":  { "name": "io.count", "status": "preserved" },
        "verilog": { "name": "io_count", "status": "preserved",
                     "value": { "sigName": "io_count" } }
      }
    },
    "reg": {
      "typeRef":       "uint8",
      "bindKind":      "reg",
      "ownerScopeRef": "Counter_W8",
      "representations": {
        "chisel":  { "name": "reg", "status": "preserved" },
        "verilog": { "name": "reg_q", "status": "preserved",
                     "value": { "sigName": "reg_q" } }
      }
    }
  },

  "scopes": {
    "Counter_W8": {
      "name": "Counter",
      "kind": "module",
      "parameters": [{ "name": "width", "value": 8 }],
      "variableRefs": ["io_en", "io_count", "reg"],
      "body": [
        { "kind": "decl", "varRef": "reg" },
        { "kind": "block", "guardRef": "when_en",
          "body": [
            { "kind": "connect", "varRef": "reg",
              "valueRef": { "exprRef": "reg_plus_1" },
              "bp": { "enableRef": "when_en" } }
          ]
        },
        { "kind": "connect", "varRef": "io_count",
          "valueRef": { "varRef": "reg" } }
      ]
    },
    "Top": {
      "name": "Top",
      "kind": "module",
      "variableRefs": [],
      "instantiates": [{ "as": "c", "scopeRef": "Counter_W8" }],
      "body": []
    }
  }
}
```

---

## 9. Breakpoint Metadata

### 9.1 Rationale

Breakpoint metadata is runtime information for interactive debuggers describing **under what conditions a user can halt at a given point** and **what happens on halt**. Waveform viewers and dataflow-based analyzers ignore this layer.

The crucial distinction from software debuggers: **one source line can spawn multiple physical breakpoints** after compiler transformations (SSA, loop unrolling, inlining). Each has its own activation condition. When the user sets a breakpoint on "line 9", the debugger must (a) find all such points, (b) check per cycle which are actually active, (c) halt only on active ones.

This is why the format attaches breakpoint metadata inline on every eligible statement -- it is dense annotation, not cross-cutting data.

### 9.2 Placement

Breakpoint metadata lives in the `bp` field of every statement kind except `StmtBlock` -- i.e. `StmtDecl`, `StmtConnect`, `StmtNone`, `StmtAssert`, `StmtAssume`, `StmtCover` -- and on scope objects. The schemas are defined in §7 (`BreakpointMeta` and `ScopeBreakpointMeta` under scopes). This section documents the semantics.

### 9.3 Statement-level fields

#### `steppable` (boolean, default `true`)

Can the debugger halt here at all? Not all statements are steppable:
- Synthetic statements created by compiler passes often lack meaningful source lines.
- Implicit connects (auto-generated `io.ready := true.B` for unused ports) exist in IR but users don't want to stop on them.
- Statements in `kind: "inline"` scopes may be better handled at the inlining site.

Emitters should set `steppable: false` when the statement is compiler-synthetic and user-facing breakpoint there would be confusing.

#### `enableRef` (expression reference)

Runtime condition under which this breakpoint is active in the current cycle. The referenced expression must evaluate to `uint<1>`.

**Critical distinction: `enableRef` vs enclosing `guardRef`.**

- `guardRef` on a `block` is **structural**: reflects static nesting (the code was inside `when(...)` in source).
- `enableRef` on a `connect` is **semantic**: reflects dynamic activation after SSA/unroll, potentially computed as AND-reduction over the SSA condition stack.

They coincide for trivial cases but diverge after optimization.

> **Implementation note (CIRCT/FIRRTL):** in the FIRRTL dialect, `enableRef` is computed by the same analysis that drives `firrtl-expand-whens`. A reference emitter should either hook into `ExpandWhensPass` to record the AND-reduced condition stack before `when`/`else` collapse, or run an equivalent analysis on pre-`ExpandWhens` IR. Post-`ExpandWhens` recovery is possible but requires reconstructing the predicate from mux trees, which loses source-level structure.
>
> **MVP shape (current `firrtl-uhdi-capture-when` + `EmitUHDI`):** instead of materialising the AND-reduced predicate as an entry in the `expressions` pool, the reference emitter writes `enableRef` as an `&`-joined list of variable stable_ids with optional `!` per leaf (e.g. `var_a_id&!var_b_id`). The unresolvable-leaf sentinel is the literal string `<complex>`. The format is purely a string-typed shortcut and is consumed verbatim by `uhdi-to-hgdb`; the schema-typed `ExprRef` shape above is the long-term target and a future revision will switch to it.

Example from the hgdb paper:

```scala
for (i <- 0 until 2) {
  if (data(i) % 2 == 0) { sum := sum + data(i) }
}
```

After unroll + SSA, two physical statements exist on the source line `sum := sum + data(i)`:

```jsonc
"expressions": {
  "data0_even": {
    "opcode": "==",
    "operands": [
      { "opcode": "%",
        "operands": [
          { "opcode": "idx",
            "operands": [ { "varRef": "data" }, { "constant": 0 } ] },
          { "constant": 2 }
        ] },
      { "constant": 0 }
    ]
  },
  "data1_even": { "opcode": "==", "operands": [ /* analogous for data[1] */ ] }
}
```

Both statements carry the same source line but different `enableRef`:

```jsonc
"body": [
  { "kind": "connect", "varRef": "sum",
    "valueRef": { "exprRef": "sum0_plus_data0" },
    "locations": { "chisel": { "file": 0, "beginLine": 4 } },
    "bp": { "enableRef": "data0_even" }
  },
  { "kind": "connect", "varRef": "sum",
    "valueRef": { "exprRef": "sum1_plus_data1" },
    "locations": { "chisel": { "file": 0, "beginLine": 4 } },
    "bp": { "enableRef": "data1_even" }
  }
]
```

Both point to `chisel:beginLine=4`. The debugger treats them as concurrent "threads" of the same source breakpoint -- user selects which to inspect when the breakpoint fires.

This cannot be expressed via `guardRef` alone because the source had no two distinct `when` blocks -- just one `for` + `if`.

#### `priority` (integer, default `0`)

Resolution order when multiple breakpoints share a source location. Lower values evaluate first. Used for:
- FIRRTL last-connect semantics (final `connect` gets highest priority for write-before-read visibility).
- Reverse debugging (iteration in reverse `priority` order).

When absent, default is lexicographic position in the `body` array.

#### `watchpoint` (object, optional)

Value-change trigger independent of control flow. Attaches to statements where the variable is declared or first assigned. Unlike `enableRef`, which asks "am I active?", watchpoints ask "did the value change in a way I care about?"

```jsonc
"bp": {
  "watchpoint": {
    "kind": "change",        // change | rising | falling | value
    "matchRef": "reg_is_42"  // required for kind: "value"
  }
}
```

Kinds:
- `change` -- fires on any value transition. For aggregate types (`struct`, `vector`), fires when **any** field / element changes; consumers wishing to observe only specific fields attach separate watchpoints to subfield variables.
- `rising` / `falling` -- for `uint<1>` variables, fires on 0->1 / 1->0. Illegal on aggregates and on integers wider than 1 bit.
- `value` -- fires when the variable matches the expression referenced by `matchRef`. For aggregates, `matchRef` must be an `'{` literal or an `exprRef` producing a value of matching type.

#### `throttle` (object, optional)

Rate-limiting to prevent flooding. A breakpoint inside a tight loop can fire millions of times per simulated second; throttling makes it manageable.

```jsonc
"bp": {
  "enableRef": "...",
  "throttle": { "maxHits": 1000 }   // OR: { "period": 100 }
}
```

- `maxHits`: stop firing after N hits (breakpoint becomes effectively disabled).
- `period`: fire every Nth hit.

Mutually exclusive -- specify one or the other.

#### `category` (array of strings, optional)

Tags for group management in large designs. Debuggers use these for bulk enable/disable, filtering the breakpoint list, coloring in the IDE.

```jsonc
"bp": {
  "category": ["fpu", "arithmetic", "debug-session-2026-04"]
}
```

#### `message` (string, optional)

Contextual hint shown to the user when the breakpoint fires. Useful for assertion-like breakpoints added during development ("arithmetic overflow suspected") or for teaching examples.

### 9.4 Scope-level fields (`ScopeBreakpointMeta`)

Function-entry/exit equivalent for hardware modules.

#### `stopOnEntry` (boolean, default `false`)

Halt on the first cycle when this scope's instance becomes active. "Active" typically means: first cycle after reset deassertion where the scope's clock is running, or first cycle when an FSM transfers control to this submodule.

Semantics are instance-dependent, not definition-dependent: if `Counter_W8` is instantiated twice, each instance has its own entry point. The debugger tracks them separately.

#### `stopOnExit` (boolean, default `false`)

Halt on the last cycle before the scope becomes inactive. Less common, but useful for FSM exit tracing.

#### `category` (array of strings, optional)

Same semantics as statement-level `category`.

### 9.5 Examples

#### Simple conditional breakpoint

```jsonc
{ "kind": "connect", "varRef": "reg",
  "valueRef": { "exprRef": "reg_plus_1" },
  "bp": { "steppable": true, "enableRef": "when_en" }
}
```

#### Rising-edge watchpoint on a bool

```jsonc
{ "kind": "decl", "varRef": "io_valid",
  "bp": {
    "watchpoint": { "kind": "rising" }
  }
}
```

#### Value-match watchpoint with throttling

```jsonc
{ "kind": "decl", "varRef": "state",
  "bp": {
    "watchpoint": {
      "kind":     "value",
      "matchRef": "state_is_error"
    },
    "throttle":   { "maxHits": 10 },
    "message":    "FSM entered ERROR state",
    "category":   ["fsm-errors"]
  }
}
```

#### Non-steppable synthetic assignment

```jsonc
{ "kind": "connect", "varRef": "io_unused_ready",
  "valueRef": { "constant": 1 },
  "bp": { "steppable": false }
}
```

#### Scope with entry-point halt

```jsonc
"scopes": {
  "FsmActiveState": {
    "name": "Active",
    "kind": "module",
    "bp": {
      "stopOnEntry": true,
      "category": ["fsm-transitions"]
    }
  }
}
```

### 9.6 Invariants

1. `enableRef` and `watchpoint.matchRef` must reference expressions with `uint<1>` result (for `matchRef`: with type matching the watched variable, for `kind: "value"`).
2. `watchpoint.kind: "rising"` and `"falling"` are legal only when the target variable's `typeRef` resolves to `uint<1>`.
3. `watchpoint.kind: "value"` requires `matchRef` to be present.
4. `throttle.maxHits` and `throttle.period` are mutually exclusive.
5. `bp` on `StmtBlock` is rejected by the schema (not listed in its `properties`); no runtime check needed.
6. Duplicate `priority` values among breakpoints sharing a source location -- linter warning.

### 9.7 Interaction with other layers

- **Dataflow layer (§10):** conditional edges carry expressions that may coincide with `enableRef` values. Consumers combining both layers can compute more precise activation (`dataflow_condition && enableRef`) but this is consumer logic, not format obligation.
- **Temporal layer (§11):** the clock domain assigned to a statement's owning scope determines *when* breakpoint enables are sampled. A statement in a different clock domain from the user's frame of reference requires temporal translation.
- **Provenance layer (§12):** for synthetic statements (`steppable: false`), provenance can explain what pass created them -- useful when a user wonders why a breakpoint they expected is marked non-steppable.

---

## 10. Dataflow Graph (Optional)

### 10.1 Rationale

The dataflow graph is the one layer that is not about "what lives where in source" but about "what depends on what semantically". Two primary use cases:

**Dynamic slicing** -- given an incorrect value at output X on cycle T, compute the minimal subset of the design that contributed to X. Without a dataflow graph, this requires either manual reading of all code or a coarse-grained RTL fan-in cone (which includes large amounts of irrelevant logic). With typed edges and dynamic conditions, slicing becomes precise.

**Root-cause analysis** -- when an assertion fails, determine which variables actually participated given the runtime activation of conditional branches.

### 10.2 Model

The dataflow graph is stored as a top-level side-table:

```jsonc
"dataflow": {
  "edges": [ /* array of edge objects */ ]
}
```

An edge is a **typed directed connection from consumer to producer**:

```jsonc
{ "from": <consumer>, "to": <producer>, "kind": "Data", ... }
```

The direction (`from = consumer`, `to = producer`) is inherited from PDG. Counter-intuitive at first, but convenient for backward slicing: starting at X, follow edges where `from == X` to find what X depends on.

### 10.3 Edge endpoints

An endpoint is either a variable reference or an expression reference:

```jsonc
"from": { "varRef": "..." }    // or
"from": { "exprRef": "..." }
```

Inline expressions are not allowed in endpoints -- only references. Rationale: edges are numerous (see §10.8 sizing), and inline expressions would cause massive duplication.

### 10.4 Edge kinds

Six kinds. The first four are adopted from PDG; the last two are `uhdi` additions.

| Kind | Meaning | Example |
|---|---|---|
| `Data` | B's value is used in computing A | `reg_next = reg + 1` yields `Data(reg_next, reg)` |
| `Conditional` | A executes only when a CF predicate is active | `when(en) { reg := ... }` yields `Conditional(reg, en)` |
| `Index` | A uses B as an index (not as data) | For `mem[i] := d`: `Index(mem, i)` plus `Data(mem, d)` |
| `Declaration` | Used during slicing to ensure the slice compiles | `use_of_x ← decl_of_x` |
| `Clock` | A is clocked by B | `reg ← clock_signal` |
| `Reset` | A is reset by B | `reg ← reset_signal` |

**Emitter choice: `Clock` / `Reset` edges may be omitted when the temporal layer (§11) is present.** When `temporal.domains` is emitted authoritatively, a consumer that needs slicing through clock/reset can synthesize these edges on the fly from the domain assignments. This eliminates the storage redundancy flagged in §14. An emitter that produces both layers SHOULD pick one authoritative source:

- **Minimal dataflow + full temporal** (recommended for interactive / waveform tools that don't slice).
- **Full dataflow including Clock/Reset + minimal temporal** (recommended for standalone slicers that don't want to cross layer boundaries).

When both are emitted, the linter cross-checks consistency (§11.9).

### 10.5 Conditional edges and `condition` field

A key PDG feature adopted here: edges can carry an **activation condition**. The edge is in the graph only when the referenced condition is true.

Example: the dynamic write `mem(io.idx) := io.din` unrolls into two Index edges:

```jsonc
{ "from": { "varRef": "connect_mem_0" }, "to": { "varRef": "probe_wr_idx" },
  "kind": "Index",
  "condition": { "exprRef": "probe_wr_idx_eq_0" }
},
{ "from": { "varRef": "connect_mem_1" }, "to": { "varRef": "probe_wr_idx" },
  "kind": "Index",
  "condition": { "exprRef": "probe_wr_idx_eq_1" }
}
```

In each cycle, only one is active in the graph. Consumers (slicers) read VCD traces to evaluate the `condition.exprRef` at the cycle of interest and prune inactive edges.

### 10.6 `clocked` flag

A boolean on each edge: does `to` contribute to `from` **via a register** (i.e., the value came from the previous cycle)?

- `clocked: false` (default): combinational dependency.
- `clocked: true`: registered dependency -- breaks self-loops per cycle.

Without this flag, slicing through registers produces infinite cycles (a register's current value "depends on itself" nominally). `clocked: true` breaks the cycle cleanly per cycle boundary.

### 10.7 `assignDelay`

Integer delay in cycles. Usually 0 (combinational) or 1 (single register). Values > 1 indicate multi-cycle pipelines or `delay` FIFO history (from temporal layer).

> **Open question (§14):** PDG has `assignDelay` both on vertices and edges. `uhdi` places it only on edges -- delay is a property of the connection, not the node. Revisit if this breaks conversion from PDG.

### 10.8 Scalability

Dataflow is the heaviest layer by document size. Estimates for RocketChip-scale designs (~500K SV LOC):

- Variables: ~100K
- Edges: ~500K to 2M (2-20× variable count)
- Per-edge JSON size: ~120 bytes
- Total: 60 MB - 250 MB raw JSON

**Chunking (required for designs over ~10K variables).** The dataflow layer MUST be emittable as a separate file referenced from the main document. The recommended layout:

- Main document: `<design>.uhdi.json` -- contains §3-§8 core pools plus `temporal`/`provenance` if produced.
- Per-top-scope dataflow: `<design>.<top-scope-id>.uhdi-dataflow.json` -- one file per entry in the top-level `top` array, containing only edges whose endpoints fall inside that scope's reachable variable set.
- Main document links chunks via an optional `dataflowChunks` array of relative paths alongside the inline `dataflow` field. A consumer that needs slicing loads the relevant chunk on demand.

Inter-chunk edges (edges whose `from` and `to` belong to different top scopes) are permitted and stored in whichever chunk the consumer picks up first; duplicates across chunks are deduplicated at load time.

**Binary encoding.** CBOR is the recommended binary form for chunks (3-5× shrinkage with no semantic change). `dataflowChunks` entries ending in `.cbor` are CBOR; `.json` are text.

**Critical:** the dataflow layer is never required. Interactive debuggers and waveform viewers should never load it.

### 10.9 JSON Schema

```jsonc
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://uhdi/dataflow.schema.json",

  "$defs": {
    "VarRef":  { "type": "string" },
    "ExprRef": { "type": "string" },

    "EndpointRef": {
      "oneOf": [
        { "type": "object", "required": ["varRef"],
          "additionalProperties": false,
          "properties": { "varRef": { "$ref": "#/$defs/VarRef" } } },
        { "type": "object", "required": ["exprRef"],
          "additionalProperties": false,
          "properties": { "exprRef": { "$ref": "#/$defs/ExprRef" } } }
      ]
    },

    "ConditionRef": {
      "type": "object",
      "required": ["exprRef"],
      "additionalProperties": false,
      "properties": { "exprRef": { "$ref": "#/$defs/ExprRef" } }
    },

    "Edge": {
      "type": "object",
      "required": ["from", "to", "kind"],
      "additionalProperties": false,
      "properties": {
        "from":        { "$ref": "#/$defs/EndpointRef" },
        "to":          { "$ref": "#/$defs/EndpointRef" },
        "kind":        { "enum": ["Data", "Conditional", "Index",
                                  "Declaration", "Clock", "Reset"] },
        "clocked":     { "type": "boolean", "default": false },
        "assignDelay": { "type": "integer", "minimum": 0, "default": 0 },
        "condition":   { "$ref": "#/$defs/ConditionRef" }
      }
    },

    "Dataflow": {
      "type": "object",
      "required": ["edges"],
      "additionalProperties": false,
      "properties": {
        "edges": { "type": "array", "items": { "$ref": "#/$defs/Edge" } }
      }
    }
  },

  "$ref": "#/$defs/Dataflow"
}
```

### 10.10 Examples

**Combinational Data dependency:**
```jsonc
{ "from": { "exprRef": "reg_plus_1" }, "to": { "varRef": "reg" },
  "kind": "Data", "clocked": false }
```

**Register dependency (self-feed through clock boundary):**
```jsonc
{ "from": { "varRef": "reg" }, "to": { "exprRef": "reg_plus_1" },
  "kind": "Data", "clocked": true, "assignDelay": 1 }
```

**Control-dependency (when-branch):**
```jsonc
{ "from": { "varRef": "reg" }, "to": { "varRef": "io_en" },
  "kind": "Conditional", "clocked": false }
```

**Index dependency with condition (dynamic write):**
```jsonc
{ "from": { "varRef": "mem_0" }, "to": { "varRef": "probe_wr_idx" },
  "kind": "Index",
  "condition": { "exprRef": "probe_wr_idx_eq_0" }
}
```

**Clock dependency:**
```jsonc
{ "from": { "varRef": "reg" }, "to": { "varRef": "clock" },
  "kind": "Clock" }
```

**Declaration edge (for slicing completeness):**
```jsonc
{ "from": { "varRef": "reg_use_at_line_42" }, "to": { "varRef": "reg" },
  "kind": "Declaration" }
```

### 10.11 Common consumer operations

The format itself does not perform these -- all algorithms belong in consumer tools. Listed here for reference.

1. **Backward slice** -- "what does X depend on?" BFS from X following edges where `from == X`, until fixpoint.
2. **Forward slice** -- "what depends on X?" BFS from X following edges where `to == X`.
3. **Conditional slice** -- backward slice with VCD trace: when visiting an edge with `condition`, evaluate `condition.exprRef` at the cycle of interest. Inactive edges are pruned.
4. **Minimal counter-example** -- for root-cause: only include dependencies whose values *changed* in the cycle under investigation.

### 10.12 Interaction with other layers

- **§5 Expressions:** endpoints can reference named expressions, avoiding duplication with scope body connects.
- **§7 Scope body:** each `connect` produces at least one `Data` edge (consumer -> value source) and optionally a `Conditional` edge (if inside a `block` with `guardRef`). An emitter generates these automatically.
- **§9 Breakpoint metadata:** edges carry `condition`, breakpoints carry `enableRef`. Debuggers combining both layers may compute `breakpoint.enableRef ∧ edge.condition` as the full activation predicate -- this is consumer logic, not format obligation.
- **§11 Temporal:** `Clock` and `Reset` edges correspond to clock/reset domain assignments in the temporal layer. If variable V has clock domain C, there should be a `Clock` edge from V to the signal of C. (See §10.4 open question on whether this cross-layer redundancy is worth keeping.)
- **§12 Provenance:** synthetic edges (e.g., those created by inlining passes) are traceable through the provenance layer.

### 10.13 Deliberate exclusions

Some PDG features were not carried over.

**Probe signals as a distinct category was initially excluded** and reintroduced in 0.8 as a dedicated `bindKind` (see §6.2, §6.8). Probes are modeled as variables with `bindKind: "probe"` / `"rwprobe"`, not as a separate vertex list. CFG predicates that PDG tracks as "probe signals" are ordinary `uint<1>` variables in `uhdi`.

**Explicit CFG block.** PDG has a standalone `cfg` section with `stmtRef`/`predStmtRef`/`trueBranch`/`falseBranch`. In `uhdi`, control flow is already expressed through nested `block` statements in §7 with `guardRef`. Duplication is unnecessary.

> **Open question (§14):** if direct PDG -> `uhdi` lossless conversion is required, CFG may need to be re-introduced. Deferred pending consumer-tool requirements.

### 10.14 Invariants

1. All `varRef` / `exprRef` in `from`, `to`, `condition` resolve to existing IDs.
2. `condition` is only legal on `Conditional` and `Index` edges. On other kinds -- linter warning.
3. `condition.exprRef` must resolve to an expression with `uint<1>` result.
4. `clocked: true` is incompatible with kinds `Declaration`, `Clock`, `Reset`.
5. Self-loops within one cycle (`from == to` with `clocked: false`) are errors.
6. Duplicate edges (same `from`, `to`, `kind`, `condition`) -- linter warning.
7. Every variable with `bindKind: "reg"` must have a resolvable clock: **either** a `Clock` edge in the dataflow layer **or** a clock assignment via `temporal.domains` (or its scope chain). Absence of both -- error.
8. `Declaration` edges are uni-directional: the reverse (`decl_of_X ← use_of_X`) should not exist.

---

## 11. Temporal Information (Optional)

### 11.1 Rationale

The temporal layer captures metadata about clocking, reset, and history access. It is distinct from the dataflow graph (§10) because clock/reset relationships are **structural properties of the design**, not dynamic dependencies derived from statements.

Three primary use cases:

**Multi-clock debugging.** In SoCs (e.g., RocketChip), multiple clock domains coexist (core, uncore, memory, peripheral). A breakpoint "halt when `reg == 42`" is ambiguous without domain info: which edge should sample trigger on? A debugger without this data either samples on every edge (false positives) or on a single global clock (missed events).

**Reverse debugging.** hgdb supports intra-cycle reverse via per-variable history FIFOs. Depth of each FIFO lives here.

**Reset-aware initial state.** When a debugger starts from cycle 0, it must know which signal is reset, what polarity, sync or async -- otherwise first-cycle state is undefined.

### 11.2 Model

Top-level side-table with four sub-sections:

```jsonc
"temporal": {
  "clocks":  { /* clockId -> clock descriptor */ },
  "resets":  { /* resetId -> reset descriptor */ },
  "domains": { /* scopeRef or varRef -> clock/reset assignment */ },
  "delays":  [ /* history FIFOs */ ],
  "defaultClockRef": "...",
  "defaultResetRef": "..."
}
```

Clocks and resets are separate pools (not inline with each assignment) because a single clock often drives thousands of variables; inlining would cause massive duplication.

### 11.3 Clocks

```jsonc
"clocks": {
  "clk_core": {
    "sigRef":      "clock",        // reference to variables pool (type must be clock)
    "edge":        "rising",       // rising | falling | both
    "frequencyHz": 1000000000      // optional: annotated frequency
  }
}
```

- `sigRef` references a variable whose `typeRef` resolves to a `clock` type.
- `edge` describes the sampling edge **as it appears in the `hdl`-kind representation** (e.g., `always_ff @(posedge clk)` vs `@(negedge)` vs DDR). FIRRTL / source representations use rising-edge semantics universally; the `edge` field becomes meaningful only after `lower-seq-to-sv` (or equivalent). An emitter targeting only a source/IR representation should emit `edge: "rising"`.
- `frequencyHz` is a hint for debugger pacing, not a correctness constraint.

### 11.4 Resets

```jsonc
"resets": {
  "rst_core": {
    "sigRef":       "reset",
    "kind":         "sync",              // sync | async
    "activeHigh":   true,
    "initialValue": { "constant": 0 }    // optional: default post-reset value
  }
}
```

- `sigRef` references a variable of type `reset` (for `sync`) or `asyncreset` (for `async`).
- `activeHigh: true` means reset asserts on signal value 1. Chisel's default is `true`; FIRRTL supports both.
- `initialValue` is an optional document-wide default for variables in this reset domain. Per-variable overrides live in the variable's `value` field (see §6.4).

### 11.5 Domain assignments

Links variables and scopes to clocks and resets. Keys are either `scopeRef` or `varRef`:

```jsonc
"domains": {
  "Core":        { "clock": "clk_core", "reset": "rst_core" },
  "L2Cache":     { "clock": "clk_uncore", "reset": "rst_core" },
  "reg_special": { "clock": "clk_peripheral" }
}
```

Resolution order for a given variable V:

1. If V has an entry in `domains`, use it.
2. Otherwise, look up V's owning scope; use that scope's entry if present.
3. Otherwise, use `defaultClockRef` / `defaultResetRef`.
4. If none of the above resolves and V is `reg` or `mem`, the linter flags an error.

This mirrors Chisel's `withClock(c) { ... }` / `withClockAndReset(c, r) { ... }` scoping.

**Emitter requirement:** whenever V's clock/reset binding differs from its owning scope's binding (typical case: a register inside a module wrapped in `withClock`), the emitter MUST emit an explicit `domains[V]` entry overriding the scope's default. Implicit override by omission leads to silently wrong clock attribution when consumers fall through to step 2.

### 11.6 Delay FIFOs

```jsonc
"delays": [
  { "varRef": "pipeline_stage_3", "depth": 4 },
  { "varRef": "branch_history",   "depth": 8, "clockRef": "clk_core" }
]
```

Stored as an array rather than a map -- delays are often batch-applied by the emitter and have no meaningful ID beyond the variable they attach to.

- `depth` must be ≥ 1 (depth 0 would be just the current value).
- `clockRef` is optional; if absent, sampling uses the clock from `domains` resolution for the variable.

### 11.7 JSON Schema

```jsonc
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://uhdi/temporal.schema.json",

  "$defs": {
    "VarRef":   { "type": "string" },
    "ScopeRef": { "type": "string" },
    "ClockRef": { "type": "string" },
    "ResetRef": { "type": "string" },

    "Clock": {
      "type": "object",
      "required": ["sigRef", "edge"],
      "additionalProperties": false,
      "properties": {
        "sigRef":      { "$ref": "#/$defs/VarRef" },
        "edge":        { "enum": ["rising", "falling", "both"] },
        "frequencyHz": { "type": "integer", "minimum": 1 }
      }
    },

    "Reset": {
      "type": "object",
      "required": ["sigRef", "kind", "activeHigh"],
      "additionalProperties": false,
      "properties": {
        "sigRef":       { "$ref": "#/$defs/VarRef" },
        "kind":         { "enum": ["sync", "async"] },
        "activeHigh":   { "type": "boolean" },
        "initialValue": {
          "oneOf": [
            { "type": "object", "required": ["constant"],
              "additionalProperties": false,
              "properties": { "constant": { "type": "integer" } } },
            { "type": "object", "required": ["bitVector"],
              "additionalProperties": false,
              "properties": { "bitVector": { "type": "string",
                                             "pattern": "^[01xzXZ?]+$" } } }
          ]
        }
      }
    },

    "DomainAssignment": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "clock": { "$ref": "#/$defs/ClockRef" },
        "reset": { "$ref": "#/$defs/ResetRef" }
      }
    },

    "Delay": {
      "type": "object",
      "required": ["varRef", "depth"],
      "additionalProperties": false,
      "properties": {
        "varRef":   { "$ref": "#/$defs/VarRef" },
        "depth":    { "type": "integer", "minimum": 1 },
        "clockRef": { "$ref": "#/$defs/ClockRef" }
      }
    },

    "Temporal": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "clocks": {
          "type": "object",
          "additionalProperties": { "$ref": "#/$defs/Clock" }
        },
        "resets": {
          "type": "object",
          "additionalProperties": { "$ref": "#/$defs/Reset" }
        },
        "domains": {
          "type": "object",
          "additionalProperties": { "$ref": "#/$defs/DomainAssignment" }
        },
        "delays": {
          "type": "array",
          "items": { "$ref": "#/$defs/Delay" }
        },
        "defaultClockRef": { "$ref": "#/$defs/ClockRef" },
        "defaultResetRef": { "$ref": "#/$defs/ResetRef" }
      }
    }
  },

  "$ref": "#/$defs/Temporal"
}
```

### 11.8 Examples

**Minimal single-clock design:**
```jsonc
"temporal": {
  "clocks": { "clk": { "sigRef": "clock", "edge": "rising" } },
  "resets": { "rst": { "sigRef": "reset", "kind": "sync", "activeHigh": true } },
  "defaultClockRef": "clk",
  "defaultResetRef": "rst"
}
```

All variables and scopes inherit defaults -- no explicit `domains` entries needed.

**Multi-clock SoC:**
```jsonc
"temporal": {
  "clocks": {
    "clk_core":       { "sigRef": "core_clock",   "edge": "rising",
                        "frequencyHz": 1000000000 },
    "clk_uncore":     { "sigRef": "uncore_clock", "edge": "rising",
                        "frequencyHz": 500000000 },
    "clk_peripheral": { "sigRef": "periph_clock", "edge": "rising",
                        "frequencyHz": 100000000 }
  },
  "resets": {
    "rst_core":  { "sigRef": "reset",     "kind": "sync",  "activeHigh": true },
    "rst_async": { "sigRef": "por_reset", "kind": "async", "activeHigh": true }
  },
  "domains": {
    "Core":       { "clock": "clk_core",       "reset": "rst_core" },
    "L2Cache":    { "clock": "clk_uncore",     "reset": "rst_core" },
    "UartDevice": { "clock": "clk_peripheral", "reset": "rst_core" },
    "PowerMgr":   { "reset": "rst_async" }
  },
  "defaultClockRef": "clk_core"
}
```

**Branch predictor with history:**
```jsonc
"temporal": {
  "clocks": { "clk": { "sigRef": "clock", "edge": "rising" } },
  "delays": [
    { "varRef": "bp_history_reg", "depth": 16 },
    { "varRef": "pipeline_ifid",  "depth": 1 },
    { "varRef": "pipeline_idex",  "depth": 2 }
  ]
}
```

**Falling-edge sampled register (negative-edge DDR-style logic):**
```jsonc
"temporal": {
  "clocks": { "clk_neg": { "sigRef": "clock_n", "edge": "falling" } },
  "domains": { "dqs_reg": { "clock": "clk_neg" } }
}
```

### 11.9 Interaction with other layers

**§6 Variables:** `reg` and `mem` variables without a resolvable clock are errors. The linter checks `defaultClockRef` or explicit domain assignment.

**§9 Breakpoints:** breakpoints fire on the clock edge of their scope/variable's domain. `stopOnEntry` triggers on the first active edge after reset deassertion.

**§10 Dataflow:** when the dataflow layer emits explicit `Clock` / `Reset` edges, they must be consistent with `domains` assignments (if `domains["reg_X"].clock == "clk_core"`, then any `Clock` edge from `reg_X` must point to `clocks["clk_core"].sigRef`). When the dataflow layer omits these edge kinds (see §10.4), the consumer synthesizes them from `domains` on demand. The linter warns only on mismatch between what's present, not on absence.

**§12 Provenance:** if a clock/reset signal was renamed by a CIRCT pass, provenance traces the original source name -> current HDL signal.

### 11.10 Deliberate non-goals

- **Relative clock relationships** (ratios, phase offsets) -- these belong in timing analysis, not debug metadata.
- **Clock gating** -- a gated clock is just another variable of type `clock` with its own `clocks[]` entry. The gating logic lives in expressions and dataflow.
- **Power domains** -- an orthogonal concern; not debug info.

### 11.11 Contested design decisions

Recorded here because they may resurface. None is a blocker.

1. **`domains` as a separate map vs. `clockRef`/`resetRef` fields on each variable.** Adopted: separate map. Rationale: compactness -- thousands of variables in one clock domain would cause thousandfold duplication if inline. Trade-off: indirect lookup.

2. **`initialValue` on the reset descriptor, not per variable.** Rationale: most variables sharing a reset also share an init value (typically 0); the 5% needing per-variable overrides express them through the variable's `value` field (§6.4).

3. **`delays` as an array, not a map.** Rationale: delays have no meaningful ID beyond their `varRef`; ordering irrelevant; map would force artificial key generation.

---

## 12. Provenance (Optional)

> **⚠ Implementability note.** This section is fully specified, but emitting provenance in practice is substantially harder than the other layers. Every CIRCT/FIRRTL pass would need to be instrumented to record its transformations -- most passes currently don't do this. Reference implementations of the emitter can adopt provenance **incrementally**: start with the Minimum Viable Provenance set defined in §12.5 (four passes), and leave other passes with empty provenance. A consumer seeing an entity without a provenance record should treat it as "origin unknown", not as a validation failure. This layer is therefore the most research-oriented part of the format -- it is specified to enable the work, not because existing toolchains produce it today.

### 12.1 Rationale

Provenance is the history of every synthetic entity in the document: when the debugger or waveform viewer shows a variable named `_GEN_42` or `partialSum_0_reg`, provenance answers where it came from, which compiler pass produced it, what source-level entity it derives from, and through which transformation.

This is the only layer where `uhdi` offers something **no existing format does**. hgdb, PDG, and HGLDD all provide post-compilation snapshots but don't preserve the history of how entities arrived at their final form. This makes provenance both the highest-value contribution of the format and the most research-oriented (no prior art to copy from).

### 12.2 Use cases

1. **Explaining "strange" names.** A user sees `_T_17` in the waveform; provenance explains it is the result of SSA on source variable `sum` at iteration 2.
2. **Compiler self-debugging.** When a CIRCT pass produces wrong names or loses source info, provenance allows reverse-engineering what it did. Mostly an academic use case for CIRCT/Chisel contributors.
3. **Source-value recovery through a chain.** If `reg_x` is derived from `reg`, which is derived from Chisel `myReg`, provenance yields the chain even if intermediate forms were optimized.
4. **Consistency checks.** Two variables with identical provenance origin may indicate a pass bug (e.g., failed CSE).

### 12.3 Model

Top-level side-table keyed by the entity kind, then by the entity's ID from the main document:

```jsonc
"provenance": {
  "variables":   { "_GEN_42": { /* record */ } },
  "expressions": { "expr_117": { /* record */ } },
  "scopes":      { "InlinedScope_0": { /* record */ } },
  "types":       { "AnonBundle_7": { /* record */ } }
}
```

Only the entity kinds with identity are covered -- temporal entries, dataflow edges, and breakpoint metadata are annotations without standalone identity.

> **Open question (§14):** whether to add provenance for dataflow edges and temporal entries. Synthetic clocks created by a pass could reasonably have provenance. Deferred until real emitters surface the need.

### 12.4 Provenance record

Minimal structure:

```jsonc
{
  "origin":      "pass:LowerTypes",
  "derivedFrom": [ { "varRef": "io" } ],
  "transform":   "bundle_flatten",
  "fieldPath":   ".a.b.c"
}
```

#### `origin` -- who produced the entity

String with a category prefix:

- `source` -- from authored code (Chisel, Spade, PyMTL).
- `elab` -- created during HGF elaboration (pre-FIRRTL).
- `pass:<Name>` -- produced by the named compiler pass.
- `external` -- imported from another document during merge.
- `inferred` -- derived by a consumer tool (rare).

#### `derivedFrom` -- source entities

Array of entity references, because one entity may derive from multiple predecessors (e.g., CSE collapsing multiple occurrences):

```jsonc
"derivedFrom": [
  { "varRef":  "original_signal" },
  { "exprRef": "old_computation" }
]
```

An empty array is legal -- indicates the entity was created *de novo* by the pass, with no source antecedent (e.g., auto-generated clock gating wrapper).

#### `transform` -- transformation kind

| Transform | Meaning |
|---|---|
| `rename` | Simple renaming (unique naming, collision avoidance) |
| `bundle_flatten` | Bundle stripped into scalar fields |
| `vec_unroll` | Vector expanded to indexed elements |
| `inline` | Submodule body inlined into host |
| `ssa_temp` | SSA-introduced intermediate |
| `cse` | Common subexpression elimination |
| `dce_preserve` | Would have been DCE'd but kept (DontTouch) |
| `const_fold` | Replaced by constant |
| `mux_lower` | Conditional lowered to mux |
| `clock_lower` | Clock/reset operation lowering |
| `custom` | Pass-specific; requires `detail` |

The list is not claimed to be complete. `custom` is the escape hatch for passes with unique semantics.

#### Transform-specific fields

Depending on `transform`, additional fields may appear:

- `fieldPath` (for `bundle_flatten`): which Bundle field.
- `index` (for `vec_unroll`): which Vec index.
- `hostInstance` (for `inline`): which instance the code was inlined into.
- `iteration` (for `ssa_temp`): which SSA iteration.
- `detail` (for `custom`): free-form description.

#### Optional metadata

- `timestamp` (ISO 8601): when the pass ran.
- `passVersion`: compiler/pass version for diagnostics.
- `reason`: human-readable explanation.
- `lossy` (boolean): the transformation lost information irrecoverably (e.g., `x * 0 -> 0` loses `x`).

### 12.5 Minimum Viable Provenance (MVP)

A reference emitter does not need to instrument every CIRCT/FIRRTL pass to be useful. The following four passes cover the majority of user-facing provenance value and can be instrumented in a scoped effort; all other transforms can be deferred without blocking a reference implementation.

| Priority | Pass | Transform kind | Why it's in MVP |
|---|---|---|---|
| 1 | `LowerTypes` (FIRRTL) | `bundle_flatten`, `vec_unroll` | Explains every `a_b_c` / `a_0` synthetic name a user sees in waveforms. Pass already maintains internal field-to-scalar mapping -- instrumentation is largely exposing that map. |
| 2 | `InlineModules` (`firrtl-inliner`) | `inline` | Needed for `hostInstance` to be meaningful. Single-pass scope; transformation is syntactic. |
| 3 | `DCE` (`firrtl-imdeadcodeelim`) | `dce_preserve` + setting `status: "lost"` on surviving repr entries | Without this, variable `status` defaults to `preserved` everywhere, and §6.3 loses its teeth. Instrumentation is just recording survival / removal. |
| 4 | `CSE` (applied within lowering) | `cse` | Explains why identical expressions produce one shared signal. Moderate complexity (must track all collapsed predecessors). |

Passes outside MVP (`rename`, `ssa_temp`, `const_fold`, `mux_lower`, `clock_lower`, `custom`) are incremental additions; each provides refinement but not coverage.

An MVP-only emitter produces a `provenance` section that is honest about its scope: synthetic entities from non-instrumented passes simply have no entry, and consumers treat that as "origin unknown" per §12.1.

### 12.6 Chains of transformations

When an entity has been transformed repeatedly (e.g., source -> LowerTypes -> Inline -> Rename), only the **most recent transformation** is recorded directly. Intermediate versions are separate entries, accessible by following `derivedFrom` recursively.

```jsonc
"variables": {
  "parent_myBundle_field_0": {
    "origin":      "pass:Rename",
    "derivedFrom": [{ "varRef": "parent_myBundle_field" }],
    "transform":   "rename"
  },
  "parent_myBundle_field": {
    "origin":      "pass:Inline",
    "derivedFrom": [{ "varRef": "myBundle_field" }],
    "transform":   "inline",
    "hostInstance": "parent"
  },
  "myBundle_field": {
    "origin":      "pass:LowerTypes",
    "derivedFrom": [{ "varRef": "myBundle" }],
    "transform":   "bundle_flatten",
    "fieldPath":   ".field"
  }
}
```

A consumer wishing the full chain walks `derivedFrom` lazily.

> **Open question (§14):** explicit `chain` array (as a single record) vs. the current linked-list model. Plain linked form chosen for consistency with pool/reference style used throughout `uhdi`. Revisit if chain depths routinely exceed ~10.

### 12.7 Reverse lookup

The format stores only forward provenance (from synthetic to source). A reverse map (from source to descendants) is built by the consumer at load time. Reason: avoiding duplication and consistency risk.

### 12.8 JSON Schema

```jsonc
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://uhdi/provenance.schema.json",

  "$defs": {
    "VarRef":   { "type": "string" },
    "ExprRef":  { "type": "string" },
    "ScopeRef": { "type": "string" },
    "TypeRef":  { "type": "string" },

    "EntityRef": {
      "oneOf": [
        { "type": "object", "required": ["varRef"],
          "additionalProperties": false,
          "properties": { "varRef": { "$ref": "#/$defs/VarRef" } } },
        { "type": "object", "required": ["exprRef"],
          "additionalProperties": false,
          "properties": { "exprRef": { "$ref": "#/$defs/ExprRef" } } },
        { "type": "object", "required": ["scopeRef"],
          "additionalProperties": false,
          "properties": { "scopeRef": { "$ref": "#/$defs/ScopeRef" } } },
        { "type": "object", "required": ["typeRef"],
          "additionalProperties": false,
          "properties": { "typeRef": { "$ref": "#/$defs/TypeRef" } } }
      ]
    },

    "Origin": {
      "type": "string",
      "pattern": "^(source|elab|pass:|external|inferred)"
    },

    "Transform": {
      "enum": [
        "rename", "bundle_flatten", "vec_unroll", "inline",
        "ssa_temp", "cse", "dce_preserve", "const_fold",
        "mux_lower", "clock_lower", "custom"
      ]
    },

    "Record": {
      "type": "object",
      "required": ["origin", "derivedFrom", "transform"],
      "additionalProperties": false,
      "properties": {
        "origin":       { "$ref": "#/$defs/Origin" },
        "derivedFrom":  { "type": "array", "items": { "$ref": "#/$defs/EntityRef" } },
        "transform":    { "$ref": "#/$defs/Transform" },

        "fieldPath":    { "type": "string" },
        "index":        { "type": "integer", "minimum": 0 },
        "hostInstance": { "type": "string" },
        "iteration":    { "type": "integer", "minimum": 0 },
        "detail":       { "type": "string" },

        "timestamp":    { "type": "string", "format": "date-time" },
        "passVersion":  { "type": "string" },
        "reason":       { "type": "string" },
        "lossy":        { "type": "boolean", "default": false }
      }
    },

    "Provenance": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "variables":   {
          "type": "object",
          "additionalProperties": { "$ref": "#/$defs/Record" }
        },
        "expressions": {
          "type": "object",
          "additionalProperties": { "$ref": "#/$defs/Record" }
        },
        "scopes":      {
          "type": "object",
          "additionalProperties": { "$ref": "#/$defs/Record" }
        },
        "types":       {
          "type": "object",
          "additionalProperties": { "$ref": "#/$defs/Record" }
        }
      }
    }
  },

  "$ref": "#/$defs/Provenance"
}
```

### 12.9 Examples

**Bundle flatten:**
```jsonc
"variables": {
  "io_a_b_c": {
    "origin":      "pass:LowerTypes",
    "derivedFrom": [{ "varRef": "io" }],
    "transform":   "bundle_flatten",
    "fieldPath":   ".a.b.c"
  }
}
```

**SSA temp with iteration:**
```jsonc
"variables": {
  "sum_1": {
    "origin":      "pass:SSA",
    "derivedFrom": [{ "varRef": "sum" }],
    "transform":   "ssa_temp",
    "iteration":   1
  },
  "sum_2": {
    "origin":      "pass:SSA",
    "derivedFrom": [{ "varRef": "sum" }],
    "transform":   "ssa_temp",
    "iteration":   2
  }
}
```

**Inline with reason:**
```jsonc
"variables": {
  "parent_sub_reg": {
    "origin":       "pass:InlineModules",
    "derivedFrom":  [{ "varRef": "sub_reg" }],
    "transform":    "inline",
    "hostInstance": "parent",
    "reason":       "single-use submodule inlined for optimization"
  }
}
```

**CSE collapsing multiple sources:**
```jsonc
"expressions": {
  "shared_sum": {
    "origin":      "pass:CSE",
    "derivedFrom": [
      { "exprRef": "a_plus_b_1" },
      { "exprRef": "a_plus_b_2" },
      { "exprRef": "a_plus_b_3" }
    ],
    "transform":   "cse",
    "reason":      "three identical a+b occurrences collapsed"
  }
}
```

**Preserved dead code:**
```jsonc
"variables": {
  "debug_only_reg": {
    "origin":      "pass:DCE",
    "derivedFrom": [{ "varRef": "debug_only_reg" }],
    "transform":   "dce_preserve",
    "reason":      "DontTouchAnnotation prevented elimination"
  }
}
```

Note: `derivedFrom` refers to itself. This is the one case where self-reference is legal -- means "this entity passed through unchanged".

**Lossy transformation:**
```jsonc
"variables": {
  "zero_result": {
    "origin":      "pass:ConstFold",
    "derivedFrom": [{ "varRef": "x" }],
    "transform":   "const_fold",
    "lossy":       true,
    "reason":      "x * 0 folded to 0; x value no longer traceable"
  }
}
```

### 12.10 Interaction with other layers

- **§6 Variables:** `status: "reconstructed"` or `status: "lost"` often correlates with a provenance record explaining which pass caused the transformation.
- **§7 Scopes:** inline scopes (§7 `kind: "inline"`) should have provenance with `transform: "inline"` and `hostInstance`.
- **§10 Dataflow:** synthetic edges rarely need provenance; endpoint provenance is usually sufficient.
- **§11 Temporal:** if a clock/reset signal was renamed by a pass, provenance on the signal variable traces the original name.

### 12.11 Scalability

Moderate size. For RocketChip-scale designs:

- Records: ~`N_variables + N_expressions + N_synthetic_scopes` ≈ 100K-300K.
- Per-record JSON size: ~150 bytes.
- Total: 15-45 MB raw.

Smaller than dataflow but still nontrivial. Lazy loading is recommended -- most consumers never need provenance, and it is only queried when the user asks "where did this signal come from?"

### 12.12 Invariants

1. All `derivedFrom[]` entity references resolve.
2. `origin: "pass:..."` has a non-empty pass name after the colon.
3. `transform: "bundle_flatten"` requires `fieldPath`.
4. `transform: "vec_unroll"` requires `index`.
5. `transform: "inline"` requires `hostInstance`.
6. `transform: "ssa_temp"` should have `iteration` (warning if absent).
7. `transform: "custom"` requires `detail`.
8. Cycles in `derivedFrom` are forbidden **except** self-reference with `transform: "dce_preserve"`. Cycle detection is the linter's responsibility.
9. Chain depth (recursive `derivedFrom`) exceeding 20 -- warning.
10. If the main document contains a variable whose name starts with a synthetic prefix (`_GEN`, `_T`, `_WIRE`, `_RAND`, …) -- a provenance record **should** exist. Warning if absent (but acceptable during incremental adoption).
11. Entities in provenance maps must correspond to entities of the matching kind in the main document (e.g., `provenance.variables["X"]` requires `variables["X"]` to exist).

### 12.13 Research-oriented open questions

This section summarises open questions with thesis-level scope. Each has been deferred for a reason: real answers depend on systematic study of CIRCT/Chisel passes and on actual implementation feedback.

1. **Canonical transform taxonomy.** The 11 transforms listed are a working draft based on informal pass review. A comprehensive taxonomy -- surveying all CIRCT and Chisel passes -- is itself a thesis contribution. Additional transforms will almost certainly surface.

2. **Lossless vs lossy formal classification.** When can the original be reconstructed from provenance? `rename` is lossless; `const_fold` is lossy; `inline` is reversible if `hostInstance` is present. A formal theory of pass invertibility based on provenance is open.

3. **Provenance-driven automated explanations.** Generating natural-language explanations of synthetic names from provenance chains -- UI/UX research territory.

4. **Merge semantics.** When documents from independent tools are merged (Chisel emits the authoring layer, CIRCT adds IR/HDL layers), how is provenance joined? Does the chain continue across merge boundaries, or are they independent trees?

5. **Provenance-based equivalence checking.** Two variables with identical provenance origin and transform are semantically equivalent. Can this be used for automated equivalence checks between compilation runs?

### 12.14 Contested design decisions

1. **Plain linked list (via `derivedFrom`) vs explicit `chain` array.** Adopted linked list for consistency with the rest of the format. Revisit if chain depths routinely exceed ~10.

2. **Pass names as free strings vs enum.** Adopted free strings (`pass:<Name>`). New passes emerge constantly; an enum would be outdated instantly.

3. **Self-referential `derivedFrom` legal only for `dce_preserve`.** Strict. May be relaxed for other no-op transformations if a need emerges.

4. **No provenance for dataflow / temporal entities.** Synthetic edges and clocks could reasonably have provenance; currently excluded for simplicity.

5. **`transform` mandatory, not optional.** Every entry must declare its transformation kind; `custom` covers the unknown. Alternative (making `transform` optional) was rejected -- forces emitters to document what they do, rather than hand-waving.

---

## 13. Linter Requirements

JSON Schema cannot express all invariants. A separate linter must validate:

- Cross-pool reference integrity (every `*Ref` resolves to an existing ID).
- No cycles in types (struct membership, vector elements).
- No cycles in expressions (via `exprRef` chains).
- No cycles in scope instantiation.
- Width consistency via FIRRTL-style inference.
- Opcode arity constraints.
- **Opcode / repr-level consistency**: source-level opcodes (`Mux`, `Cat`, `Fill`, `VecInit`) only in `source`-kind representations; 4-state compare opcodes (`===`, `!==`, `==?`, `!=?`) only in `hdl`-kind representations.
- Boolean context for conditions (`uint<1>` results).
- Bi-directional consistency of `ownerScopeRef` ↔ `variableRefs` **when `variableRefs` is present** (the field is now optional).
- **Per-representation status/value consistency** (§6.6 invariant 4) -- evaluated independently in each repr entry.
- `bindKind: "probe"` / `"rwprobe"` variables must not have `value.sigName` in `hdl`-kind repr; must not be a `varRef` target of a `Data` edge (only `Declaration` edges).
- Representation keys used in entities ⊆ top-level representation keys.
- Location file indices within bounds of the owning representation's files array.
- Uniqueness of names/IDs where required.
- Breakpoint `enableRef` and `watchpoint.matchRef` reference expressions with `uint<1>` result (for `matchRef`: with result type matching the watched variable when `kind: "value"`).
- Breakpoint `watchpoint.kind` of `rising`/`falling` only on variables whose type is `uint<1>`.
- Breakpoint `watchpoint.kind: "value"` requires `matchRef` present.
- Breakpoint `throttle.maxHits` and `throttle.period` are mutually exclusive.
- Verification statements (`assert` / `assume` / `cover`): `condRef` resolves to a `uint<1>` expression; only legal inside `module` or `layer_block` scopes.
- Scope `kind: "layer_block"`: `parameters` must be absent.
- Dataflow edge endpoints (`from`, `to`) reference existing variables or expressions.
- Dataflow `condition` is only legal on `Conditional` and `Index` edge kinds.
- Dataflow `condition.exprRef` resolves to a `uint<1>` expression.
- Dataflow `clocked: true` is incompatible with kinds `Declaration`, `Clock`, `Reset`.
- Dataflow self-loops within one cycle (`from == to` with `clocked: false`) are errors.
- Every `bindKind: "reg"` variable has a resolvable clock: **either** an explicit `Clock` edge in dataflow, **or** an entry in `temporal.domains` (or its scope chain), **or** `temporal.defaultClockRef`. Absence of all three -- error.
- When both dataflow `Clock` / `Reset` edges **and** `temporal.domains` are present, their clock/reset assignments for the same variable must agree.
- Temporal `clocks[...].sigRef` resolves to a variable of type `clock`.
- Temporal `resets[...].sigRef` resolves to a variable of type `reset` (for `kind: sync`) or `asyncreset` (for `kind: async`).
- Temporal `domains[...]` keys resolve either to an existing `scopeRef` or `varRef`.
- Temporal `domains[...].clock` / `.reset` resolve into respective pools.
- Temporal `delays[].varRef` resolves; if variable has a `delay` field, matching `depth` value.
- Temporal `defaultClockRef` / `defaultResetRef` resolve.
- Provenance `derivedFrom[]` entity references resolve to the correct pool.
- Provenance `origin: "pass:..."` has a non-empty pass name.
- Provenance `transform` requires its expected fields (`bundle_flatten` -> `fieldPath`, `vec_unroll` -> `index`, `inline` -> `hostInstance`, `custom` -> `detail`).
- Provenance `derivedFrom` cycles are forbidden, except self-reference with `transform: "dce_preserve"`.
- Provenance entries must correspond to entities in the main document of the matching kind.

Recommended: warn on unreachable expressions, duplicate `priority` within one source location, duplicate dataflow edges, `Declaration` edges in reverse direction, provenance chain depth exceeding 20, synthetic-prefixed names without provenance record, provenance entries lacking `iteration` on `ssa_temp`, verification statements outside `module` / `layer_block` scopes, explicit `status` absent in any `hdl`-kind representation entry.

---

## 14. Open Questions and Future Work

### Format-wide
- Exact semantics of `bitVector` ordering (LSB-first vs MSB-first) -- needs decision.
- Binary encoding (CBOR or Protobuf variant) for large designs. CBOR selected for dataflow chunks (§10.8); remainder of document still JSON-only.
- Derived SQLite index format analogous to hgdb's runtime representation.
- Merging protocol for documents produced by independent tools.
- Reference implementation: emitter (Chisel/CIRCT) and adapters (to hgdb, HGLDD).
- Test suite of canonical small-circuit examples.

### Dataflow layer (§10)
- Whether `assignDelay` should live on vertices in addition to edges (PDG has both). Current decision: edges only. Revisit if PDG-lossless conversion is required.
- Explicit CFG block is not stored (control flow is already in §7 scope body). Revisit if PDG -> `uhdi` lossless conversion is required.
- Chunking validated on real designs -- current §10.8 recommendation (one chunk per top-scope) needs benchmarking on RocketChip-scale targets.

### Temporal layer (§11)
- Whether to inline `clockRef`/`resetRef` on variables directly (instead of separate `domains` map). Current decision: separate map for compactness. May revisit if most variables end up with distinct domains (unlikely in practice).
- `initialValue` on reset vs per-variable -- current compromise: reset-level default + per-variable override through `value` field. Works for common cases; edge cases with mixed init values in one reset domain may be awkward.

### Provenance layer (§12)
Implementation barriers are the dominant open issue -- the specification is complete but emitters require systematic pass instrumentation that does not yet exist. MVP (§12.5) lowers the barrier to four passes, but full coverage remains research work. Most research-grade questions in this layer are flagged in §12.13; a short recap:

- Complete taxonomy of `transform` kinds beyond MVP (current set is a working draft based on informal pass review; full CIRCT/Chisel survey needed).
- Formal classification of passes as lossless vs lossy based on provenance reversibility.
- Whether explicit `chain` arrays are needed for deep transformation histories.
- Merge semantics when documents from independent tools are joined.
- Whether to add provenance coverage for dataflow edges and temporal entries.
- UX/UI approaches for presenting provenance chains to human users.

### Resolved in 0.8 (no longer open)
- ~~Whether to keep `Clock` / `Reset` as explicit edge kinds, or derive them from temporal.~~ Resolved: §10.4 allows either; emitter picks one authoritative source.
- ~~Probe signals as ordinary synthetic variables.~~ Resolved: added dedicated `bindKind: "probe"` / `"rwprobe"` (§6.2) with distinct dataflow semantics (§6.8).
- ~~Duplication of clock info between `temporal.domains` and dataflow `Clock`/`Reset` edges.~~ Resolved as above.

---

## 15. Conversion to Legacy Formats

### 15.1 Scope

This section specifies canonical projections from `uhdi` to three legacy hardware debug formats: HGLDD (CIRCT Debug Dialect emitter output, consumed by Tywaves / Surfer / Verdi alpha), hgdb (Hardware Generator Debugger SQLite symbol table), and PDG (Chisel trace / Program Dependency Graph).

Projections are not round-trips. `uhdi` by construction covers the union of all three formats' expressiveness (§2.1-§2.5), so conversion in this direction is always lossy -- the target format lacks fields for information `uhdi` stores. The contract is weaker: after projection, a consumer tool (hgdb-VSCode plugin, Tywaves waveform viewer, PDG-based slicer) must be unable to distinguish documents that originated as `uhdi` from documents emitted natively.

Ingestion (the reverse direction: hgdb / HGLDD / PDG -> `uhdi`) is a separate concern requiring auxiliary inputs (VCD, FIRRTL dump) for type-width recovery in two of three cases. It is not specified here.

### 15.2 Pre-conditions

| Projection | `uhdi` layers required on input | Auxiliary inputs |
|---|---|---|
| `uhdi` -> HGLDD | §3-§7 core; §11 only for Tywaves-extended HGLDD (enum types, module info) | None |
| `uhdi` -> hgdb | §3-§7 core + §9 breakpoint metadata + §11 delays | None |
| `uhdi` -> PDG | §3-§7 core + **§10 dataflow** | None, or dataflow derivation pass if §10 absent |

A `uhdi` document emitted for a source-level consumer (e.g., a Tywaves-targeted emitter that skipped §10) cannot be converted to PDG without first running a dataflow derivation pass. This is a pipeline step, not a limitation; it is documented in §15.5.4.

### 15.3 `uhdi` -> HGLDD

The simplest projection. HGLDD is a snapshot format whose information content is a subset of `uhdi` §3-§7. Conversion is a direct transliteration of fields.

#### 15.3.1 Field mapping

| `uhdi` source | HGLDD target |
|---|---|
| `representations[k]` with `kind == "source"` or `"hdl"` | `file_info[]` entries |
| `roles.authoring` repr `.files[]` | HGL-side file (anchors `hgl_loc`) |
| `roles.simulation` repr `.files[]` | HDL-side file (anchors `hdl_loc`) |
| `types[k].kind: "struct"` | deduplicated struct definition (uniqued by JSON content) |
| `types[k].kind: "enum"` | Tywaves `dbg.enumdef` (Tywaves variant only) |
| `types[k].kind: "vector"` (nested) | `packed_range` / `unpacked_range` chain per HGLDD §6.1 |
| `variables[k].representations["<hgl-role>"].location` | `hgl_loc` |
| `variables[k].representations["<hdl-role>"].location` | `hdl_loc` |
| `variables[k].representations["<hdl-role>"].value.sigName` | `value.sig_name` |
| `variables[k].representations["<hdl-role>"].value.exprRef` (inlined) | HGLDD expression tree |
| `variables[k].bindKind` + `direction` | HGLDD port semantic (input / output / inout) |
| `scopes[k].kind: "extmodule"` | `isExtModule: true` |
| `scopes[k].kind: "inline"` | inline scope record |
| `scopes[k].kind: "layer_block"` | inline scope with category marker; no native HGLDD support (emit as synthetic inline or drop per emitter policy) |
| `Instantiation.as` + `representations["<hdl-role>"].name` | `name` + `hdl_obj_name` |

#### 15.3.2 Required transformations

**Source-level opcode lowering.** If `uhdi` expressions include source-level opcodes (`Mux`, `Cat`, `Fill`, `VecInit` -- §5.3.3) that appear in an `hdl`-kind representation (possible in partial emitters, though forbidden by §5.6 invariant 7), the converter lowers them syntactically:

- `Mux(s, t, f)` -> `?:(s, t, f)`
- `Cat(...)` -> `{}(...)`
- `Fill(n, v)` -> `R{}(n, v)`
- `VecInit(...)` -> `'{}(...)`

A strict converter rejects such documents; a lenient one performs the lowering and emits a warning.

**Bundle choice.** HGLDD supports both consolidated and split forms; the converter preserves whichever the `uhdi` input used (§6.7).

#### 15.3.3 Dropped layers

§7 scope body, §9 breakpoints, §10 dataflow, §11 temporal (except Tywaves-consumed subset), §12 provenance. HGLDD consumers do not read these -- observable loss at the consumer level is zero.

#### 15.3.4 Effort estimate

3-5 days for a reference implementation. Essentially a backend variant of CIRCT's `EmitHGLDD` walk, reading `uhdi` JSON instead of the IR.

### 15.4 `uhdi` -> hgdb

Structural mapping to hgdb's SQLite schema is direct. The non-trivial work is serializing `uhdi` expression ASTs back to SystemVerilog expression strings.

#### 15.4.1 Table population

| hgdb table | `uhdi` source |
|---|---|
| `Instance.id` / `.name` | Recursive walk of `scopes[k].instantiates[]`; `id` freshly assigned per instance, `name` from `Instantiation.as` |
| `Variable.name` / `.value` | `variables[k].representations["<hdl-role>"].name` / `.value.sigName` |
| `Generator Variable` | variables with `bindKind: "literal"` |
| `Scope Variable` | variables whose `ownerScopeRef` equals the breakpoint's enclosing scope |
| `Breakpoint.filename` / `.line_num` / `.column` | `Statement.locations["<source-role>"]` |
| `Breakpoint.instance` | enclosing scope's instance ID |
| `Breakpoint.enable` | serialized expression string (§15.4.2) |

#### 15.4.2 Expression AST -> SV string serialization

The central challenge. `uhdi` `expressions[k]` is a typed opcode tree; hgdb `enable` is a string. The converter implements a pretty-printer with SV operator precedence:

```
precedence (highest -> lowest, matching SV LRM):
  12  unary ~, !, unary -
  11  **
  10  *, /, %
   9  binary +, -
   8  <<, >>, >>>
   7  <, <=, >, >=
   6  ==, !=, ===, !==, ==?, !=?
   5  &, ~&
   4  ^, ~^
   3  |, ~|
   2  &&
   1  ||
   0  ?:
```

Emit `(` + left + `op` + right + `)` if parent's precedence ≥ own, else drop parentheses. Unary, reduction, and mux follow standard SV syntax.

Leaf handling:
- `{varRef: x}` -> the variable's `representations["<hdl-role>"].name`
- `{constant: n, width: w}` -> `w'd<n>` (or bare integer when width absent)
- `{bitVector: "0101"}` -> `4'b0101`
- `{sigName: s}` -> literal string
- `{exprRef: k}` -> recurse into the referenced subtree (inline the result)

#### 15.4.3 AND-reduction of enclosing guards

hgdb pre-reduces the SSA condition stack into a single `enable` string -- the `firrtl-expand-whens` equivalent done at emit time. `uhdi` keeps `guardRef` on enclosing `StmtBlock`s (structural) and `bp.enableRef` on the connect (semantic, §9.3). The converter must recombine them:

```
serializeEnable(stmt, enclosingBlocks):
  parts = []
  for block in enclosingBlocks:    # outer to inner
    if block.guardRef:
      parts.append(serialize(block.guardRef))
  if stmt.bp.enableRef:
    parts.append(serialize(stmt.bp.enableRef))
  if parts.empty:
    return "1"
  return "(" + " && ".join(parts) + ")"
```

Duplicate sub-expressions across guards may emerge; SV parsing handles the redundancy, but output size grows. Optional post-pass: CSE on the concatenated AST before serialization.

#### 15.4.4 Bundle handling

hgdb requires split form. If the `uhdi` input uses consolidated form (§6.7), the converter expands each struct member into a separate `Variable` row, prefixing names with the parent (`io` struct -> `io.en`, `io.count`, `io.valid`). Vectors use the `indices` array per hgdb spec.

#### 15.4.5 Dropped fields

- `bp.watchpoint`, `bp.throttle`, `bp.category`, `bp.message` -- no hgdb field (partial exception: `watchpoint { kind: "change" }` can be approximated by the `target` attribute).
- §10 dataflow, §12 provenance -- entirely.
- Rich types -- hgdb variables carry `rtl: bool` and name only; struct / vector / enum type information is discarded.
- `status: "reconstructed"` / `"lost"` -- hgdb has no such notion; variables in these states are omitted from the `Variable` table unless they have a `sigName` in some repr.
- Verification statements (`assert` / `assume` / `cover`) -- dropped, or converted to `Breakpoint` rows with `steppable: false` and `enable = "!(condRef)"` (break on assertion violation). Emitter-policy decision.

#### 15.4.6 Effort estimate

~2 weeks. Roughly: 3 days for schema walk and SQLite population; 5 days for the expression-to-string serializer with comprehensive precedence tests; 2 days for bundle expansion and guard AND-reduction.

### 15.5 `uhdi` -> PDG

The dual of PDG -> `uhdi` ingestion. Where ingestion required lifting a flat CFG into a structured tree (nontrivial dominator analysis), projection lowers the tree back into flat vertex / edge representation -- algorithmically straightforward.

#### 15.5.1 Entity mapping

| `uhdi` source | PDG target |
|---|---|
| `variables[k]` `bindKind: "port"` | `IO` vertex |
| `variables[k]` `bindKind: "wire"` / `"node"` | `DataDefinition` vertex |
| `variables[k]` `bindKind: "reg"` / `"mem"` | `Definition` vertex |
| `variables[k]` `bindKind: "probe"` / `"rwprobe"` | push into PDG `predicates[]` list (not regular vertex list) |
| `variables[k]` `bindKind: "literal"` | `DataDefinition` with constant attribute |
| `StmtConnect` | `Connection` vertex |
| `StmtBlock.guardRef` | `ControlFlow` vertex |
| `dataflow.edges[]` | PDG edges (kind names match 1:1) |
| `dataflow.edges[].clocked` | PDG `clocked` on both vertex and edge (PDG duplicates) |
| `dataflow.edges[].assignDelay` | `assignDelay` on edge; max over incoming edges on vertex |
| `provenance[kind][k].origin == "source"` | `isChiselStatement: true` on the corresponding vertex |
| Any other provenance origin | `isChiselStatement: false` |

#### 15.5.2 Body-flattening algorithm

Pre-order traversal of each scope's `body[]`, maintaining the CFG chain via `stmtRef` / `predStmtRef`:

```
walkBody(body, currentPredStmtRef):
  for stmt in body:
    myStmtRef = freshStmtId()
    match stmt.kind:
      "block":
        cfVertex = emitControlFlowVertex(
          predicate   = stmt.guardRef,
          stmtRef     = myStmtRef,
          predStmtRef = currentPredStmtRef)
        walkBody(stmt.body, myStmtRef)
      "connect":
        emitConnectionVertex(
          target      = stmt.varRef,
          value       = stmt.valueRef,
          stmtRef     = myStmtRef,
          predStmtRef = currentPredStmtRef)
      "decl":
        emitDefinitionOrDataVertex(
          variable    = stmt.varRef,
          stmtRef     = myStmtRef,
          predStmtRef = currentPredStmtRef)
      "assert" | "assume" | "cover":
        emitControlFlowVertex(        # PDG has no verification concept
          predicate   = stmt.condRef,
          stmtRef     = myStmtRef,
          predStmtRef = currentPredStmtRef,
          annotation  = stmt.kind)    # carry as optional attribute
      "none":
        pass
```

No dominator analysis, no control-flow reconstruction: the `uhdi` body is already hierarchically structured, and pre-order traversal preserves FIRRTL last-connect ordering directly.

#### 15.5.3 Dynamic-write materialization

Dynamic memory writes (`mem[io.idx] := d`) are stored identically in both formats -- N `Index` edges with `condition.exprRef`, one per possible index value. Direct copy, no synthesis.

#### 15.5.4 Pre-condition on §10

PDG's dataflow edges are its source of utility for slicing. If the input `uhdi` document lacks §10, the converter must either fail explicitly or invoke a dataflow derivation pass first. The derivation walks §5 expression trees and §7 body connects to synthesize `Data` / `Conditional` / `Index` / `Declaration` edges -- the same computation an emitter performs when producing §10 initially. `Clock` / `Reset` edges are derived from §11 `temporal.domains` (preferred; see §10.4), or from register operand information when temporal is also absent.

Recommended CLI contract:

```
uhdi-to-pdg --input design.uhdi.json --require-dataflow
uhdi-to-pdg --input design.uhdi.json --derive-dataflow   # alternative
```

The second mode is slower and may produce less precise conditional edges (a derivation pass without pre-`ExpandWhens` context cannot reconstruct the original when-nesting as precisely as a native emitter would).

#### 15.5.5 Dropped fields

- §9 breakpoint metadata -- entirely.
- §11 temporal -- multi-clock domains, delays, reset polarity / kind / `initialValue` all lost. PDG retains only per-edge `clocked` bits.
- §12 provenance -- collapsed into a single `isChiselStatement` Boolean.
- `status: "reconstructed"` / `"lost"` -- PDG treats all variables as present.

#### 15.5.6 Effort estimate

~2 weeks assuming §10 on input. ~4 weeks if the dataflow-derivation pre-pass is included in the converter itself.

### 15.6 Round-trip semantics

The composition `X -> uhdi -> X` is the canonical regression test for converters. For each legacy format X, the following invariant should hold:

> For any document `D` natively emitted in format X, and any consumer tool `T` that reads format X, the behaviour of `T(D)` and `T(project_X(ingest(D)))` must be observably equivalent in `T`'s primary use case.

For HGLDD / Tywaves: the Surfer waveform viewer displays the same hierarchy, types, and signal values.

For hgdb: the hgdb-VSCode extension steps through breakpoints with identical `filename:line` triggering and identical enable conditions firing on the same cycles.

For PDG: a reference slicer produces the same reachable variable set for any (variable, cycle) query.

The reverse composition `uhdi -> X -> uhdi` is **not** an invariant and is not expected to hold. X is smaller than `uhdi` by definition (§2.1); round-tripping through X loses every `uhdi`-specific layer (breakpoints through HGLDD, dataflow through hgdb, provenance through all three).

### 15.7 Compatibility matrix

What each projected format supports, when projecting from a maximally-annotated `uhdi` source:

| `uhdi` layer | HGLDD out | hgdb out | PDG out |
|---|---|---|---|
| §3 representations | source + hdl roles preserved | hdl role only | hdl role only |
| §4 types (ground) | ✓ | name only | name only |
| §4 types (struct / vector / enum) | ✓ (enum: Tywaves only) | ✗ | ✗ |
| §5 expressions | ✓ | serialized to string | ✓ (in edges) |
| §6 variables (`preserved`) | ✓ | ✓ | ✓ |
| §6 variables (`reconstructed`) | ✓ (via expression tree) | ✗ | ✗ |
| §6 variables (`lost`) | name only | ✗ | ✗ |
| §6 probes | as variables | ✗ | ✓ (in `predicates[]`) |
| §7 body / control flow | ✗ (no CF model) | flat breakpoints | ✓ (CFG) |
| §7 verification statements | ✗ | partial (as break-on-false) | partial (as CF vertex with annotation) |
| §9 breakpoints | ✗ | ✓ | ✗ |
| §9 watchpoints | ✗ | partial (`change` only) | ✗ |
| §10 dataflow | ✗ | ✗ | ✓ |
| §11 temporal (clocks / resets) | Tywaves only | delays only | `clocked` bit only |
| §12 provenance | ✗ | ✗ | single bit (source vs not) |

This matrix is the practical answer to "which `uhdi` features can I rely on if my consumer is X?" -- reference for emitter authors writing `uhdi` with a known downstream target.

---

## Appendix A. Change Log

- **0.1** (2026-04-22) -- Initial draft. Core structure (document, types, expressions, variables, scopes) specified. Optional layers (dataflow, temporal, provenance) stubbed.
- **0.2** (2026-04-22) -- Renamed format from `udbg` to `uhdi` (Unified Hardware Debug Info).
- **0.3** (2026-04-22) -- Added §9 Breakpoint Metadata (full specification). Expanded `BreakpointMeta` schema with `watchpoint`, `throttle`, `category`, `message`. Added `ScopeBreakpointMeta` with `stopOnEntry`/`stopOnExit`. Renumbered §10-§14.
- **0.4** (2026-04-22) -- Restored analytical content: expanded §1 with per-format critique, rewrote §2 with 13-axis coverage matrix, intersections, unique features, gaps. Added §6.7 Bundle flattening note. Added Appendix B documenting rejected alternatives.
- **0.5** (2026-04-22) -- Added §10 Dataflow Graph (full specification with six edge kinds, conditional edges, scalability notes). Open questions inline in the section mark the three non-trivial design choices (Clock/Reset edges beyond PDG, `assignDelay` on edges only, probe signals as ordinary synthetic variables). Updated §13 linter invariants and §14 open questions accordingly.
- **0.6** (2026-04-22) -- Added §11 Temporal Information (clocks, resets, domain assignments, delay FIFOs, defaults). Schema reference wired into document schema. Updated §13 linter invariants (7 new rules). §14 records three contested temporal decisions (domain duplication with dataflow, inline vs side-table domains, initialValue placement).
- **0.7** (2026-04-22) -- Added §12 Provenance (full specification with 11 transform kinds, chains, research-oriented open questions). Section prefaced with explicit implementability caveat: emitters require systematic pass instrumentation which does not yet exist in CIRCT/Chisel. Incremental adoption guidance provided. Updated §13 linter (6 new rules, plus recommended warnings) and §14 open questions (research-grade items in the provenance subsection).
- **0.8** (2026-04-22) -- MLIR-implementability review pass. Breaking changes: (a) §6 -- `status` moved into per-representation record; previously-global status is no longer accepted by schema. (b) §6.2 -- added `probe` / `rwprobe` BindKinds; §6.8 models XMRs. (c) §7.2 -- added `layer_block` scope kind. (d) §7.3 -- added verification statement kinds `assert`, `assume`, `cover`. (e) §5.3 -- opcodes partitioned into IR/HDL-level, HDL-only (4-state), and source-only groups; opcode/repr-level consistency added as §5.6 invariant 7. Non-breaking changes: (f) §9.3 -- added CIRCT implementation note on `enableRef` computation via `ExpandWhensPass`; watchpoint semantics on aggregates clarified. (g) §9.6 -- removed invariant 5 (redundant with schema). (h) §10.4 -- `Clock`/`Reset` edges now MAY be omitted when temporal layer is authoritative; resolves the main §14 open question on dataflow/temporal redundancy. (i) §10.8 -- chunking protocol specified (per-top-scope files with CBOR option). (j) §11.3 -- `edge` field documented as HDL-repr-specific. (k) §11.5 -- explicit emitter requirement for `withClock` overrides. (l) §12.5 -- new subsection: Minimum Viable Provenance (four-pass MVP); remaining subsections 12.5->12.14 renumbered. (m) §13 linter updated; resolved items moved out of §14 open questions.
- **0.9** (2026-04-22) -- Added §15 Conversion to Legacy Formats. Specifies canonical projections `uhdi` -> HGLDD / hgdb / PDG: field-level mappings, required transformations (source-level opcode lowering; AST-to-SV-string serialization with SV precedence table; guard AND-reduction for hgdb; body-flattening pre-order traversal for PDG), dropped fields per target, effort estimates (3-5 days / ~2 weeks / ~2 weeks respectively). §15.2 fixes the pre-condition that `uhdi` -> PDG requires §10 dataflow (explicitly or via derivation pre-pass). §15.6 formalises the round-trip contract: `X -> uhdi -> X` is the regression-test invariant, `uhdi -> X -> uhdi` is not. §15.7 compatibility matrix summarises per-layer coverage per target format. No schema changes; additive documentation only.
- **0.9.1** (2026-04-24) -- Audit-driven alignment of spec text with the bundled JSON Schemas and reference emitter (`circt:fk-sc/uhdi-pool` / `EmitUHDI`): (a) §7.2 -- documented optional `containerScopeRef` on `inline`-kind scopes (already accepted by `schemas/scopes.schema.json`, emitted by `EmitUHDI::emitInlineScope`). (b) §7.3 -- documented optional `negated: boolean` on `StmtBlock` for the `else` branch of a paired when/else (emitted by `firrtl-uhdi-capture-when` and serialised by `EmitUHDI::emitStatementList`). (c) §7.4 -- JSON-Schema snippet refreshed to list both fields. (d) §7.6 -- added invariants 11 (containerScopeRef target kind) and 12 (when/else negation pairing). No schema-file change; no breaking change for emitters or consumers.

---

## Appendix B. Rejected Alternatives

This appendix records design alternatives considered and rejected. If the same question resurfaces, the reasoning is here.

### B.1 Physical layout of shared entities

| Alternative | Rejected because |
|---|---|
| Pure nested (everything inline, hgdb-max style) | No dedup; one shared `Bundle` referenced in 50 places -> 50× duplication. Does not scale to RocketChip-size designs. |
| Pure flat + integer index refs (PDG-max style) | Forces every consumer to rebuild hierarchy by traversal. Unnecessarily complex for 80% of use cases (interactive debug, waveform viewing). |
| ECS (Entity-Component-System with facet tables) | Elegant in the abstract but foreign to the HW toolchain ecosystem. Conversion to/from hgdb/HGLDD becomes a non-trivial join operation. High barrier to entry for contributors. |
| SQLite as the canonical format | Excellent for runtime queries (this is what hgdb uses internally), but bad for version control, diff, human inspection, and compiler-side emission. Decision: **JSON canonical, SQLite permissible as a derived index.** |

### B.2 Reference encoding

| Alternative | Rejected because |
|---|---|
| Prefix in value (`"varRef": "v:reg"`) | Redundant with field name; requires extra parsing step (split on `:`); adds visual noise at every reference site; doesn't add information. |
| JSON path (`"ref": "$.variables.reg"`) | Slower resolution (split + walk vs single dict lookup); brittle under pool renames; ties format to physical layout; harder to merge documents from independent tools; loses duplicate-ID detection at parse time. |
| Explicit object (`{pool: "variables", id: "reg"}`) | Verbose; adds nesting; no benefit over named field. |

Decision: **Named field (`typeRef`, `varRef`, `exprRef`, etc.)**. O(1) dict lookup with zero parsing. Field name encodes the target pool. Cross-pool integrity validated by the linter, not the schema.

### B.3 Parameter encoding in scope IDs

| Option | Status |
|---|---|
| Encode parameters in the ID string (e.g., `MyModule#n=8`) | Rejected. Fragile for type parameters; no canonical serialization (whitespace, field order, escapes); two compilers cannot guarantee identical string generation without a canonicalization spec; tools querying "what are this scope's parameters" must parse IDs backward. |
| Opaque IDs with structured parameters inside scope | **Adopted.** IDs are hash-derived or auto-counter (`MyModule_001`); parameters live as a structured array inside the scope object. |

### B.4 HDL-specific type naming

Early drafts kept `type_name: "logic"` as in HGLDD. **Rejected** -- this is a SystemVerilog leak. Ground types in `uhdi` are abstract (`uint`, `sint`, `clock`, `reset`, `asyncreset`, `analog`). Rendering to `logic` / `bit` / `std_logic` is an emitter concern, not a format concern.

### B.5 Separate `conditions` pool

Early drafts had a separate pool for boolean conditions distinct from expressions. **Rejected** -- conditions are just expressions with `uint<1>` results; identical grammar. Separate pools would duplicate the definition. Decision: single `expressions` pool; `guardRef`, `enableRef`, `condRef`, and `matchRef` all target it; linter validates `uint<1>` result where required.

### B.6 Global `value` binding on variables

Early drafts had one `value` per variable (global binding). **Rejected** -- the same variable may be reconstructed differently at different IR levels. In High FIRRTL it may be a direct signal; in Low FIRRTL after optimization, an inline expression; in Verilog, a renamed signal. Decision: `value` is per-representation, stored inside each entry of the variable's `representations` map.

### B.7 Fixed HGL/HDL location pair

HGLDD uses a fixed `hgl_loc` + `hdl_loc` pair. **Rejected** as too restrictive. CIRCT has four to five meaningful IR levels (Chisel, High FIRRTL, Low FIRRTL, HW dialect, SystemVerilog); a dual pair throws away intermediate levels that are valuable for debugging the compiler itself. Decision: N-way `representations` map with arbitrary string keys; dual pair is a special case.

### B.8 Strings vs ASTs for conditions

hgdb stores conditions as strings (`"!reset && (opcode == 3)"`), pre-AND-reduced by the SSA pass. **Rejected** for `uhdi` -- strings are not programmatically analyzable (cannot be normalized, compared, used for symbolic execution, or converted to PDG-style CFG predicates). Decision: conditions are ASTs (expressions with `uint<1>` result); the string form can be re-derived trivially when needed.
