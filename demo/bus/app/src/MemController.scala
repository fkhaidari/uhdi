import chisel3._
import chisel3.util._
import _root_.circt.stage.ChiselStage

// Mini-bus over Decoupled with nested bundles. tywaves should render
// req/resp as structured records (addr/data/op/...), not flattened wires.

class MemRequest(addrW: Int = 8, dataW: Int = 32) extends Bundle {
  val addr  = UInt(addrW.W)
  val data  = UInt(dataW.W)
  val write = Bool()
}

class MemResponse(dataW: Int = 32) extends Bundle {
  val data = UInt(dataW.W)
  val ok   = Bool()
}

class MemController(addrW: Int = 8, dataW: Int = 32, depth: Int = 16) extends Module {
  val io = IO(new Bundle {
    val req  = Flipped(Decoupled(new MemRequest(addrW, dataW)))
    val resp = Decoupled(new MemResponse(dataW))
  })

  val mem = SyncReadMem(depth, UInt(dataW.W))

  // Single in-flight request: latch it on accept, surface response next cycle.
  val pending  = RegInit(false.B)
  val pendData = RegInit(0.U(dataW.W))
  val pendOk   = RegInit(false.B)

  val accept = io.req.fire
  io.req.ready := !pending || io.resp.fire

  when(accept) {
    when(io.req.bits.write) {
      mem.write(io.req.bits.addr, io.req.bits.data)
      pendData := io.req.bits.data
      pendOk   := true.B
    }.otherwise {
      pendData := mem.read(io.req.bits.addr)
      pendOk   := true.B
    }
    pending := true.B
  }.elsewhen(io.resp.fire) {
    pending := false.B
    pendOk  := false.B
  }

  io.resp.valid     := pending
  io.resp.bits.data := pendData
  io.resp.bits.ok   := pendOk
}

object Main extends App {
  val uhdi = "design.uhdi.json"
  ChiselStage.emitSystemVerilog(
    new MemController,
    args = Array("--with-debug-intrinsics"),
    firtoolOpts = Array("-g", "-O=debug", "--emit-uhdi", s"--uhdi-output-file=$uhdi", "-o", "MemController.sv")
  )
  println(uhdi + " + MemController.sv written")
}
