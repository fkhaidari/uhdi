import chisel3._
import chisel3.util._
import _root_.circt.stage.ChiselStage

// 3-stage pipelined multiply-accumulate: q = (a * b) + acc.
// Each stage is its own Module so the hierarchy is visible in tywaves
// and so hgdb can step across pipe registers.

class MulStage extends Module {
  val io = IO(new Bundle {
    val a, b = Input(UInt(8.W))
    val out  = Output(UInt(16.W))
  })
  io.out := io.a * io.b
}

class AddStage extends Module {
  val io = IO(new Bundle {
    val x, y = Input(UInt(16.W))
    val out  = Output(UInt(16.W))
  })
  io.out := io.x + io.y
}

class Pipeline extends Module {
  val io = IO(new Bundle {
    val a   = Input(UInt(8.W))
    val b   = Input(UInt(8.W))
    val acc = Input(UInt(16.W))
    val q   = Output(UInt(16.W))
  })

  val mul = Module(new MulStage)
  val add = Module(new AddStage)

  // Stage 1: register inputs, feed multiplier.
  val a_s1   = RegNext(io.a)
  val b_s1   = RegNext(io.b)
  val acc_s1 = RegNext(io.acc)
  mul.io.a := a_s1
  mul.io.b := b_s1

  // Stage 2: register multiplier output and forward acc, feed adder.
  val prod_s2 = RegNext(mul.io.out)
  val acc_s2  = RegNext(acc_s1)
  add.io.x := prod_s2
  add.io.y := acc_s2

  // Stage 3: register adder output to user-visible q.
  io.q := RegNext(add.io.out)
}

object Main extends App {
  val uhdi = "design.uhdi.json"
  ChiselStage.emitSystemVerilog(
    new Pipeline,
    args = Array("--with-debug-intrinsics"),
    firtoolOpts = Array("-g", "-O=debug", "--emit-uhdi", s"--uhdi-output-file=$uhdi", "-o", "Pipeline.sv")
  )
  println(uhdi + " + Pipeline.sv written")
}
