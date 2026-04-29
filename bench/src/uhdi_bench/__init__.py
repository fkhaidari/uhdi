"""Integration harness for the uhdi pipeline.

End-to-end: Scala/Chisel source -> FIR (via scala-cli) -> UHDI (via
firtool --emit-uhdi) -> projection (via the uhdi_common Backend
registry) -> structural diff against the native reference (firtool
--emit-hgldd for tywaves; hgdb-circt's --hgdb=<file> for hgdb).

Three pipelines exist because each consumer's idiomatic Chisel front
differs:

  * tywaves -- rameloni-chisel fork, ships Tywaves-aware annotations
                that end up in HGLDD.
  * uhdi    -- Farid's chisel fork, adds `circt_debug_*` intrinsics
                so the FIR carries source-language type metadata into
                the dbg dialect.
  * hgdb    -- stock Chisel; hgdb-circt ingests vanilla FIR.

Each pipeline compiles the same Scala source against its own Chisel,
producing three FIR snapshots per fixture.
"""
