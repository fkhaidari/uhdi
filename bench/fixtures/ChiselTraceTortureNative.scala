// Native-PDG sidecar for ChiselTraceTorture. The chiseltrace fork's
// ChiselStage(addChiselTrace=true) is the ONLY native PDG producer, and that
// fork (chisel 6.4.3-tywaves-chiseltrace) is a different version from the uhdi
// fork (7.1.1) -- whose ChiselStage has no addChiselTrace/withDebug params.
// So the PdgMain entry point cannot live in ChiselTraceTorture.scala (which is
// compiled by the uhdi pipeline for the UHDI side); it lives here, compiled
// only by the bench `chiseltrace` pipeline via `--main-class PdgMain`.
//
// The Stage + ChiselTraceTorture classes are duplicated from the main fixture
// on purpose: keeping them in sync by hand is less fragile than a cross-file
// `//> using file` include across two Chisel forks. If they drift, the PDG
// pair just compares different designs -- which a diff would surface anyway.

import chisel3._
import chisel3.util._
import _root_.circt.stage.ChiselStage

class Stage(width: Int = 8) extends Module {
  val io = IO(new Bundle {
    val in  = Input(UInt(width.W))
    val en  = Input(Bool())
    val out = Output(UInt(width.W))
  })
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

  val stage = Module(new Stage(width))
  stage.io.in := io.din
  stage.io.en := io.en

  val mem = SyncReadMem(depth, UInt(width.W))
  io.dout := mem.read(io.rdAddr)

  when(io.en) {
    when(io.we) {
      when(io.cond) {
        mem.write(io.wrAddr, stage.io.out)
      }
    }
  }

  val regs = RegInit(VecInit(Seq.fill(depth)(0.U(width.W))))
  when(io.we) {
    regs(io.sel) := io.din
  }
  io.rsel := regs(io.sel)

  io.muxed := Mux(io.cond, stage.io.out, io.dout)

  assert(!(io.we && io.en) || io.wrAddr =/= io.rdAddr,
    "no same-cycle read/write to the same address")
}

object PdgMain extends App {
  new ChiselStage(withDebug = false, addChiselTrace = true).execute(
    Array("--target", "chirrtl", "-td", args(0)),
    Seq(chisel3.stage.ChiselGeneratorAnnotation(() => new ChiselTraceTorture()),
        _root_.circt.stage.FirtoolOption("-g")))
}
