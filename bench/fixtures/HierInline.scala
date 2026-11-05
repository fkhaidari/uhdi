// Hierarchy fixture: an inlined module that holds its own instance, two
// instances of one module (dedup), and a black box. Covers debug scopes
// of inlined instances and the class name of an external module.
// No `//> using` directives: the dep set comes from uhdi_bench.compile.

import chisel3._
import chisel3.util.{Cat, HasBlackBoxInline}
import chisel3.util.experimental.InlineInstance
import _root_.circt.stage.ChiselStage

class Pair extends Bundle {
  val a = UInt(4.W)
  val b = UInt(4.W)
}

class Adder extends Module {
  val io = IO(new Bundle {
    val in  = Input(new Pair)
    val sum = Output(UInt(5.W))
  })
  val acc = RegInit(0.U(5.W))
  acc := io.in.a +& io.in.b
  io.sum := acc
}

class Inner extends Module with InlineInstance {
  val io = IO(new Bundle {
    val x = Input(UInt(4.W))
    val y = Output(UInt(4.W))
  })
  val leaf = Module(new Adder)
  leaf.io.in.a := io.x
  leaf.io.in.b := 1.U
  val r = RegNext(io.x ^ 5.U, 0.U)
  io.y := r + leaf.io.sum(3, 0)
}

class XorBox extends BlackBox with HasBlackBoxInline {
  val io = IO(new Bundle {
    val a = Input(UInt(4.W))
    val b = Input(UInt(4.W))
    val o = Output(UInt(4.W))
  })
  setInline("XorBox.sv",
    """module XorBox(input [3:0] a, input [3:0] b, output [3:0] o);
      |  assign o = a ^ b;
      |endmodule
      |""".stripMargin)
}

class HierInline extends Module {
  val in  = IO(Input(new Pair))
  val out = IO(Output(UInt(8.W)))
  val inner = Module(new Inner)
  val add   = Module(new Adder)
  val box   = Module(new XorBox)
  inner.io.x := in.a
  add.io.in := in
  box.io.a := in.a
  box.io.b := in.b
  out := Cat(inner.io.y, box.io.o) + add.io.sum
}

object Main extends App {
  print(ChiselStage.emitCHIRRTL(new HierInline, args))
}
