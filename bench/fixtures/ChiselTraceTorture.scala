// ChiselTrace torture fixture -- exercises uhdi_to_pdg against the real
// PDG features chiseltrace consumes (pdg_spec.rs / ChiselTracePDGBuilder.scala),
// and deliberately includes the dynamic-index constructs that the native
// chiseltrace plugin handles via compile-time probe insertion -- which the
// UHDI flow cannot reconstruct.  Gap tags:
//   [OK]    _derive_edges covers it (Data/Conditional/Declaration)
//   [CIRCT] needs a CIRCT pass (probe insertion / Vec scalarisation); the
//           probes are synthesised in ChiselTracePDGBuilder BEFORE emission,
//           so firtool --emit-uhdi never sees them and the converter can't
//           invent Index edges or mem.0/mem.1 split vertices.
//
//   1. [OK] Long combinational chain a -> b -> c -> out (wire/node):
//      => _derive_edges emits one Data edge per referenced var, no dup.
//
//   2. [OK] Nested when/elsewhen/otherwise (2-3 levels):
//      => one ControlFlow vertex per block; body_guard_chain yields a
//      Conditional edge per enclosing block.
//
//   3. [OK] Registers: clocked=true vertices; Connection into a reg.
//
//   4. [OK] assert(cond): _walk_body's assert branch -> ControlFlow vertex
//      with annotation, plus Data edge to the asserted signal.
//
//   5. [OK] Submodule hierarchy: modulePath = [Top, sub]; cross-module
//      Connection vertices.
//
//   6. [CIRCT] Dynamic memory index: mem.read(io.rdAddr) / mem.write(io.wrAddr).
//      Native chiseltrace inserts probe_rd_idx / probe_wr_idx and emits
//      Index edges + condition {probeName, probeValue}.  UHDI has neither
//      the probes nor Vec scalarisation, so _controlflow_vertex collapses
//      the index to cond_on_[<complex>] and Index edges are absent.
//
//   7. [CIRCT] Vec register with dynamic index: regs(io.sel) := ...
//      Same story -- native scalarises regs into regs.0..regs.n and routes
//      via probe; UHDI keeps the aggregate and loses the per-element edges.
//
//   8. [CIRCT] Mux with a signal selector: Mux(io.cond, x, y).  Native
//      inserts a mux probe so the slicer can follow the taken branch; UHDI
//      keeps a plain ?: expression with no probe predicate.
//
// SyncReadMem is used (NOT the SRAM stdlib type, which chiseltrace's readme
// says is unsupported).  Compatible across pipelines (no enum/probe APIs).

import chisel3._
import chisel3.util._
import _root_.circt.stage.ChiselStage

// ---- Submodule for hierarchy / modulePath coverage ----
class Stage(width: Int = 8) extends Module {
  val io = IO(new Bundle {
    val in  = Input(UInt(width.W))
    val en  = Input(Bool())
    val out = Output(UInt(width.W))
  })

  // [OK] Combinational chain: in -> a -> b -> out.
  val a = WireDefault(io.in + 1.U)
  val b = WireDefault(a ^ "hFF".U(width.W))
  val r = RegInit(0.U(width.W))
  when(io.en) {
    r := b
  }
  io.out := r
}

class ChiselTraceTorture(width: Int = 8, depth: Int = 4) extends Module {
  val io = IO(new Bundle {
    val din    = Input(UInt(width.W))
    val rdAddr = Input(UInt(log2Ceil(depth).W))
    val wrAddr = Input(UInt(log2Ceil(depth).W))
    val sel    = Input(UInt(log2Ceil(depth).W))
    val we     = Input(Bool())
    val cond   = Input(Bool())
    val en     = Input(Bool())
    val dout   = Output(UInt(width.W))
    val rsel   = Output(UInt(width.W))
    val muxed  = Output(UInt(width.W))
  })

  // [OK] Submodule instance.
  val stage = Module(new Stage(width))
  stage.io.in := io.din
  stage.io.en := io.en

  // [CIRCT] SyncReadMem with dynamic read/write index -> probe/Index gap.
  val mem = SyncReadMem(depth, UInt(width.W))
  io.dout := mem.read(io.rdAddr)

  // [OK] Nested when (3 levels) + [CIRCT] dynamic write inside.
  when(io.en) {
    when(io.we) {
      when(io.cond) {
        mem.write(io.wrAddr, stage.io.out)   // dynamic index, nested guards
      }
    }
  }

  // [CIRCT] Vec register with dynamic index.
  val regs = RegInit(VecInit(Seq.fill(depth)(0.U(width.W))))
  when(io.we) {
    regs(io.sel) := io.din
  }
  io.rsel := regs(io.sel)

  // [CIRCT] Mux with a signal selector (mux probe in native).
  io.muxed := Mux(io.cond, stage.io.out, io.dout)

  // [OK] assert -> ControlFlow vertex with annotation + Data edge.
  assert(!(io.we && io.en) || io.wrAddr =/= io.rdAddr,
    "no same-cycle read/write to the same address")
}

// UHDI side only. Native PDG (chiseltrace fork, addChiselTrace=true) lives in
// the sidecar ChiselTraceTortureNative.scala -- that fork's ChiselStage API
// differs from this (uhdi) fork's, so the two entry points cannot share a file.
object Main extends App {
  print(ChiselStage.emitCHIRRTL(new ChiselTraceTorture, args))
}
