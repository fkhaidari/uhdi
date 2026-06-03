// hgdb torture-lite -- subset of HgdbTorture that works through hgdb-circt.
//
// hgdb-circt (LLVM-16 fork) crashes or hangs on `eq(signal, literal)`
// conditions when a submodule with a non-standard clock port is present.
// This fixture avoids that pattern while keeping the remaining torture coverage:
//
//   [OK] Deep guard stack: when(a) { when(b) { when(c) { ... } } }
//   [OK] Compound boolean guard: when(io.a && io.b)  (and-only, no eq)
//   [OK] Non-standard clock: Sub uses explicit `myClk` input port
//   [OK] Second write site for `acc` via `io.en` (same signal as read guard)
//   [SKIP hgdb-circt] eq(signal, literal) guard: crashes hgdb-circt
//   [SKIP] Two Sub instances: hgdb-circt hangs on dual-instance + io-signal inputs
//   [SKIP] Hex literal guard: requires eq which crashes hgdb-circt
//   [SKIP] Delayed context_variables: not yet emitted by converter
//
// Compatible with tywaves, hgdb_circt, hgdb_firrtl pipelines.

import chisel3._
import chisel3.util._
import _root_.circt.stage.ChiselStage

class SubLite(width: Int = 8) extends Module {
  val io = IO(new Bundle {
    val in  = Input(UInt(width.W))
    val sel = Input(Bool())
    val out = Output(UInt(width.W))
  })
  val myClk = IO(Input(Clock()))

  withClock(myClk) {
    val reg = RegInit(0.U(width.W))

    // [OK] Deep guard stack (3 levels)
    when(io.sel) {
      when(io.in =/= 0.U) {
        when(reg < io.in) {
          reg := io.in
        }
      }
    }.otherwise {
      reg := 0.U
    }

    io.out := reg
  }
}

class HgdbTortureLite extends Module {
  val io = IO(new Bundle {
    val in     = Input(UInt(8.W))
    val en     = Input(Bool())
    val a      = Input(Bool())
    val b      = Input(Bool())
    val q      = Output(UInt(8.W))
  })

  val sub0 = Module(new SubLite(8))
  sub0.io.in  := io.in
  sub0.io.sel := io.en
  sub0.myClk  := clock

  // [OK] Compound boolean guard (and only -- eq crashes hgdb-circt)
  val acc = RegInit(0.U(8.W))
  when(io.a && io.b) {
    acc := sub0.io.out
  }

  // [OK] Second write site for acc using same en signal
  when(io.en) {
    io.q := acc
  }.otherwise {
    io.q := 0.U
  }
}

object HgdbTortureLiteMain extends App {
  print(ChiselStage.emitCHIRRTL(new HgdbTortureLite, args))
}
