import chisel3._
import chisel3.simulator._
import chisel3.testing.HasTestingDirectory
import svsim.verilator.Backend.CompilationSettings
import svsim.verilator.Backend.CompilationSettings.{TraceKind, TraceStyle}
import java.nio.file.{Files, Path, Paths, StandardCopyOption}

object TrafficLightSim extends App with ChiselSim {
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
    new TrafficLight,
    chiselOpts = Array("--with-debug-intrinsics"),
    firtoolOpts = Array(
      "-g", "-O=debug",
      "--emit-uhdi",
      s"--uhdi-output-file=$uhdiPath",
    ),
    settings = Settings.default[TrafficLight].copy(enableWavesAtTimeZero = true),
  ) { dut =>
    // Pedestrian=false: one full cycle Red->RedYellow->Green(period=8)->Yellow->Red.
    dut.io.pedestrian.poke(false.B)
    dut.clock.step(25)

    // Pedestrian=true during Green: halves the green window.
    dut.io.pedestrian.poke(true.B)
    dut.clock.step(20)

    dut.io.pedestrian.poke(false.B)
    dut.clock.step(20)
    println("TrafficLight ran 65 cycles across two pedestrian regimes")
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
