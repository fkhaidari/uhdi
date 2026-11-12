import chisel3._
import chisel3.util.experimental.InlineInstance
import _root_.circt.stage.ChiselStage

object DebugLayer extends layer.Layer(layer.LayerConfig.Extract())

class LayerLeaf extends Module {
  val in = IO(Input(UInt(8.W)))
  val out = IO(Output(UInt(8.W)))
  val r = RegNext(in, 0.U(8.W))
  out := r + 1.U
}

class LayerInline extends Module with InlineInstance {
  val in = IO(Input(UInt(8.W)))
  val out = IO(Output(UInt(8.W)))
  val child = Module(new LayerLeaf)
  child.in := in
  val mixed = RegNext(in ^ "h5a".U, 0.U(8.W))
  out := mixed ^ child.out
}

class LayerDead extends Module

class LayerHierarchy extends Module {
  val in = IO(Input(UInt(8.W)))
  val out = IO(Output(UInt(8.W)))
  val left = Module(new LayerLeaf)
  val right = Module(new LayerLeaf)
  val dead = Module(new LayerDead)
  left.in := in
  right.in := left.out
  out := right.out
  layer.block(DebugLayer) {
    val inline = Module(new LayerInline)
    inline.in := left.out
    val observed = WireDefault(inline.out)
    dontTouch(observed)
  }
}

object Main extends App {
  print(ChiselStage.emitCHIRRTL(new LayerHierarchy, args))
}
