import chisel3._
import chisel3.simulator._
import chisel3.testing.HasTestingDirectory
import svsim.verilator.Backend.CompilationSettings
import svsim.verilator.Backend.CompilationSettings.{TraceKind, TraceStyle}
import java.nio.file.{Files, Path, Paths, StandardCopyOption}

object GCDSim extends App with ChiselSim {
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
    new GCD,
    chiselOpts = Array("--with-debug-intrinsics"),
    firtoolOpts = Array(
      "-g", "-O=debug",
      "--emit-uhdi",
      s"--uhdi-output-file=$uhdiPath",
    ),
    settings = Settings.default[GCD].copy(enableWavesAtTimeZero = true),
  ) { dut =>
    def runOne(a: Int, b: Int): BigInt = {
      dut.io.a.poke(a.U)
      dut.io.b.poke(b.U)
      dut.io.en.poke(true.B)
      dut.clock.step()
      dut.io.en.poke(false.B)
      while (!dut.io.rdy.peek().litToBoolean) dut.clock.step()
      val q = dut.io.q.peek().litValue
      println(s"GCD($a, $b) = $q")
      dut.clock.step()
      q
    }

    runOne(48, 18)
    runOne(15, 45)
    dut.clock.step(20) // settle for VCD tail
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
