// GCD fixture (lifted from demo/gcd/app/src/GCD.scala). Adapted for the
// bench's compile pipeline by emitting CHIRRTL rather than running the
// full Chisel -> firtool walk. Exercises a multi-branch `when`/`elsewhen`
// chain on top of registers, slightly more complex than Counter's single
// `when` -- catches divergences in the SSA condition-stack serialisation
// (§9.3 enableRef MVP joined form vs native firtool's expression tree).

import chisel3._
import _root_.circt.stage.ChiselStage

class GCD(width: Int = 16) extends Module {
  val io = IO(new Bundle {
    val a   = Input(UInt(width.W))
    val b   = Input(UInt(width.W))
    val en  = Input(Bool())
    val q   = Output(UInt(width.W))
    val rdy = Output(Bool())
  })

  val x = Reg(UInt(width.W))
  val y = Reg(UInt(width.W))
  val busy = RegInit(false.B)

  when(io.en) {
    x   := io.a
    y   := io.b
    busy := true.B
  }.elsewhen(x > y) {
    x := x - y
  }.elsewhen(y > x) {
    y := y - x
  }.otherwise {
    busy := false.B
  }

  io.q   := x
  io.rdy := !busy
}

object Main extends App {
  print(ChiselStage.emitCHIRRTL(new GCD, args))
}
