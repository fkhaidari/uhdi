import chisel3._
import chisel3.util._
import _root_.circt.stage.ChiselStage

object Light extends ChiselEnum {
  val Red, RedYellow, Green, Yellow = Value
}

class TrafficLight(period: Int = 8) extends Module {
  val io = IO(new Bundle {
    val pedestrian = Input(Bool())
    val state      = Output(Light())
    val active     = Output(Bool())
  })

  val state = RegInit(Light.Red)
  val timer = RegInit(0.U(log2Ceil(period + 1).W))

  io.state  := state
  io.active := state =/= Light.Red

  // Pedestrian button shortens Green by half.
  val greenLimit = Mux(io.pedestrian, (period / 2).U, period.U)

  switch(state) {
    is(Light.Red) {
      when(timer === period.U) { state := Light.RedYellow; timer := 0.U }
        .otherwise { timer := timer + 1.U }
    }
    is(Light.RedYellow) {
      when(timer === 2.U) { state := Light.Green; timer := 0.U }
        .otherwise { timer := timer + 1.U }
    }
    is(Light.Green) {
      when(timer === greenLimit) { state := Light.Yellow; timer := 0.U }
        .otherwise { timer := timer + 1.U }
    }
    is(Light.Yellow) {
      when(timer === 2.U) { state := Light.Red; timer := 0.U }
        .otherwise { timer := timer + 1.U }
    }
  }
}

object Main extends App {
  val uhdi = "design.uhdi.json"
  ChiselStage.emitSystemVerilog(
    new TrafficLight,
    args = Array("--with-debug-intrinsics"),
    firtoolOpts = Array("-g", "-O=debug", "--emit-uhdi", s"--uhdi-output-file=$uhdi", "-o", "TrafficLight.sv")
  )
  println(uhdi + " + TrafficLight.sv written")
}
