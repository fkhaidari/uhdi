// Smoke-test fixture for the bench's Scala -> FIR -> UHDI/HGLDD -> diff
// pipeline.  Deliberately minimal: one input port, one output port,
// one register, one when-clause.  Compiles unmodified against any
// Chisel that ships `chisel3._` and `circt.stage.ChiselStage` --
// rameloni-chisel (tywaves), Farid's fork (uhdi/debug intrinsics),
// or stock Chisel (hgdb).  No `//> using` directives: the dep set is
// passed on the scala-cli command line per pipeline (see
// uhdi_bench.compile).

import chisel3._
import _root_.circt.stage.ChiselStage

class Counter extends Module {
  val en = IO(Input(Bool()))
  val q  = IO(Output(UInt(8.W)))

  val r = RegInit(0.U(8.W))
  when(en) { r := r + 1.U }
  q := r
}

object Main extends App {
  // CHIRRTL is the input we want to feed firtool downstream.  Use
  // emitCHIRRTL (vs emitFIRRTLDialect/emitSystemVerilog) so the
  // output is the un-lowered FIR text -- that's what `firtool
  // --emit-uhdi` and `--emit-hgldd` consume directly.
  print(ChiselStage.emitCHIRRTL(new Counter))
}
