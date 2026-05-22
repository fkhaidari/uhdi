// Fifo fixture (lifted from demo/fifo/app/src/Fifo.scala). Adapted to
// emit CHIRRTL for the bench pipeline. Exercises `SyncReadMem` reads /
// writes and Decoupled-style ready/valid handshakes -- a more typical
// bundle/port shape than Counter or GCD; useful for surfacing divergences
// in struct/bundle handling (§6.7 consolidated vs split form).

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
  print(ChiselStage.emitCHIRRTL(new Fifo, args))
}
