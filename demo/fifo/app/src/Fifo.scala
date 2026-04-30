import chisel3._
import chisel3.util._
import _root_.circt.stage.ChiselStage

class Fifo(width: Int = 8, depth: Int = 4) extends Module {
  require(isPow2(depth), "depth must be a power of 2")
  val io = IO(new Bundle {
    val enq   = Flipped(Decoupled(UInt(width.W)))
    val deq   = Decoupled(UInt(width.W))
    val count = Output(UInt(log2Ceil(depth + 1).W))
  })

  val mem = SyncReadMem(depth, UInt(width.W))

  val ptrW   = log2Ceil(depth)
  val enqPtr = RegInit(0.U(ptrW.W))
  val deqPtr = RegInit(0.U(ptrW.W))
  val cnt    = RegInit(0.U(log2Ceil(depth + 1).W))

  val empty = cnt === 0.U
  val full  = cnt === depth.U

  io.enq.ready := !full
  io.deq.valid := !empty
  io.count     := cnt

  io.deq.bits := mem.read(deqPtr)

  when(io.enq.fire) {
    mem.write(enqPtr, io.enq.bits)
    enqPtr := enqPtr + 1.U
  }
  when(io.deq.fire) {
    deqPtr := deqPtr + 1.U
  }
  when(io.enq.fire && !io.deq.fire) {
    cnt := cnt + 1.U
  }.elsewhen(!io.enq.fire && io.deq.fire) {
    cnt := cnt - 1.U
  }
}

object Main extends App {
  val uhdi = "design.uhdi.json"
  ChiselStage.emitSystemVerilog(
    new Fifo,
    args = Array("--with-debug-intrinsics"),
    firtoolOpts = Array("-g", "-O=debug", "--emit-uhdi", s"--uhdi-output-file=$uhdi", "-o", "Fifo.sv")
  )
  println(uhdi + " + Fifo.sv written")
}
