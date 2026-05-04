import chisel3._
import chisel3.simulator._
import chisel3.testing.HasTestingDirectory
import svsim.verilator.Backend.CompilationSettings
import svsim.verilator.Backend.CompilationSettings.{TraceKind, TraceStyle}
import java.nio.file.{Files, Path, Paths, StandardCopyOption}

object MemControllerSim extends App with ChiselSim {
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
    new MemController,
    chiselOpts = Array("--with-debug-intrinsics"),
    firtoolOpts = Array(
      "-g", "-O=debug",
      "--emit-uhdi",
      s"--uhdi-output-file=$uhdiPath",
    ),
    settings = Settings.default[MemController].copy(enableWavesAtTimeZero = true),
  ) { dut =>
    dut.io.req.valid.poke(false.B)
    dut.io.resp.ready.poke(false.B)
    dut.clock.step(2)

    def doReq(addr: Int, data: Int, write: Boolean): Unit = {
      dut.io.req.bits.addr.poke(addr.U)
      dut.io.req.bits.data.poke(data.U)
      dut.io.req.bits.write.poke(write.B)
      dut.io.req.valid.poke(true.B)
      while (!dut.io.req.ready.peek().litToBoolean) dut.clock.step()
      dut.clock.step()
      dut.io.req.valid.poke(false.B)

      dut.io.resp.ready.poke(true.B)
      while (!dut.io.resp.valid.peek().litToBoolean) dut.clock.step()
      val rdata = dut.io.resp.bits.data.peek().litValue
      val ok    = dut.io.resp.bits.ok.peek().litToBoolean
      val tag   = if (write) "W" else "R"
      println(f"$tag addr=0x$addr%X data=0x$rdata%X ok=$ok")
      dut.clock.step()
      dut.io.resp.ready.poke(false.B)
    }

    doReq(0x10, 0xCAFE, write = true)
    doReq(0x10, 0,      write = false)
    doReq(0x20, 0xBEEF, write = true)
    doReq(0x20, 0,      write = false)
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
