import chisel3._
import chisel3.util.experimental.InlineInstance
import _root_.circt.stage.ChiselStage

class MemoryWord extends Bundle {
  val tag = UInt(3.W)
  val bytes = Vec(2, UInt(8.W))
}

class MemoryChild extends Module with InlineInstance {
  val address = IO(Input(UInt(2.W)))
  val write = IO(Input(Bool()))
  val data = IO(Input(new MemoryWord))
  val result = IO(Output(new MemoryWord))
  val storage = SyncReadMem(4, new MemoryWord)
  when(write) { storage.write(address, data) }
  result := storage.read(address, !write)
}

class MemoryHierarchy extends Module {
  val address = IO(Input(UInt(2.W)))
  val write = IO(Input(Bool()))
  val data = IO(Input(new MemoryWord))
  val result = IO(Output(new MemoryWord))
  val child = Module(new MemoryChild)
  child.address := address
  child.write := write
  child.data := data
  result := child.result
}

object Main extends App {
  print(ChiselStage.emitCHIRRTL(new MemoryHierarchy, args))
}
