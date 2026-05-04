import chisel3._
import chisel3.simulator._
import chisel3.testing.HasTestingDirectory
import svsim.verilator.Backend.CompilationSettings
import svsim.verilator.Backend.CompilationSettings.{TraceKind, TraceStyle}
import java.nio.file.{Files, Path, Paths, StandardCopyOption}

object PipelineSim extends App with ChiselSim {
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
    new Pipeline,
    chiselOpts = Array("--with-debug-intrinsics"),
    firtoolOpts = Array(
      "-g", "-O=debug",
      "--emit-uhdi",
      s"--uhdi-output-file=$uhdiPath",
    ),
    settings = Settings.default[Pipeline].copy(enableWavesAtTimeZero = true),
  ) { dut =>
    // 3-stage MAC pipeline: q = (a*b)+acc, latency 3 cycles.
    val inputs = Seq((3, 4, 10), (5, 6, 100), (7, 8, 1000), (9, 11, 5))

    for ((a, b, acc) <- inputs) {
      dut.io.a.poke(a.U)
      dut.io.b.poke(b.U)
      dut.io.acc.poke(acc.U)
      dut.clock.step()
    }

    // Drain pipeline (3 cycles latency + a few extra for VCD tail).
    dut.io.a.poke(0.U)
    dut.io.b.poke(0.U)
    dut.io.acc.poke(0.U)
    for (i <- 0 until inputs.length + 4) {
      println(s"cycle $i: q = ${dut.io.q.peek().litValue}")
      dut.clock.step()
    }
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
