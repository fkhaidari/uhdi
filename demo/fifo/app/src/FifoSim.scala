import chisel3._
import chisel3.simulator._
import chisel3.testing.HasTestingDirectory
import svsim.verilator.Backend.CompilationSettings
import svsim.verilator.Backend.CompilationSettings.{TraceKind, TraceStyle}
import java.nio.file.{Files, Path, Paths, StandardCopyOption}

object FifoSim extends App with ChiselSim {
  private val cwd          = Paths.get("").toAbsolutePath
  private val workspaceDir = cwd.resolve("out/sim")
  Files.createDirectories(workspaceDir)

  private val uhdiPath = cwd.resolve("design.uhdi.json").toString
  private val vcdPath  = cwd.resolve("design.vcd")

  implicit val testDir: HasTestingDirectory = new HasTestingDirectory {
    override def getDirectory: Path = workspaceDir
  }

  implicit val hasSimulator: HasSimulator = HasSimulator.simulators.verilator(
    verilatorSettings = CompilationSettings(
      traceStyle = Some(TraceStyle(kind = TraceKind.Vcd))
    )
  )

  simulate(
    new Fifo,
    chiselOpts = Array("--with-debug-intrinsics"),
    firtoolOpts = Array(
      "-g", "-O=debug",
      "--emit-uhdi",
      s"--uhdi-output-file=$uhdiPath",
    ),
    settings = Settings.default[Fifo].copy(enableWavesAtTimeZero = true),
  ) { dut =>
    dut.io.enq.valid.poke(false.B)
    dut.io.deq.ready.poke(false.B)
    dut.clock.step(2)

    // Fill the FIFO (depth=4).
    for (v <- Seq(0xA1, 0xB2, 0xC3, 0xD4)) {
      dut.io.enq.bits.poke(v.U)
      dut.io.enq.valid.poke(true.B)
      while (!dut.io.enq.ready.peek().litToBoolean) dut.clock.step()
      dut.clock.step()
    }
    dut.io.enq.valid.poke(false.B)
    dut.clock.step(2)

    // Drain it.
    for (i <- 0 until 4) {
      dut.io.deq.ready.poke(true.B)
      while (!dut.io.deq.valid.peek().litToBoolean) dut.clock.step()
      val v = dut.io.deq.bits.peek().litValue
      println(f"deq[$i] = 0x${v}%X")
      dut.clock.step()
    }
    dut.io.deq.ready.poke(false.B)

    // Concurrent enq+deq for one round.
    dut.io.enq.bits.poke(0xEE.U)
    dut.io.enq.valid.poke(true.B)
    dut.io.deq.ready.poke(true.B)
    dut.clock.step(3)
    dut.io.enq.valid.poke(false.B)
    dut.io.deq.ready.poke(false.B)
    dut.clock.step(5)
  }

  val tracePath = workspaceDir.resolve("workdir-verilator").resolve("trace.vcd")
  if (Files.exists(tracePath)) {
    Files.copy(tracePath, vcdPath, StandardCopyOption.REPLACE_EXISTING)
    println(s"Wrote: $vcdPath")
  } else {
    System.err.println(s"WARN: VCD not found at $tracePath")
    sys.exit(1)
  }
}
