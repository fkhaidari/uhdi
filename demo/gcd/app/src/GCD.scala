import chisel3._
import circt.stage.ChiselStage

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
  val uhdi = "design.uhdi.json"
  ChiselStage.emitSystemVerilog(
    new GCD,
    args = Array("--with-debug-intrinsics"),
    firtoolOpts = Array("-g", "-O=debug", "--emit-uhdi", s"--uhdi-output-file=$uhdi", "-o", "GCD.sv")
  )
  println(uhdi + " + GCD.sv written")
}
