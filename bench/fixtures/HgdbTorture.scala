// hgdb torture fixture -- exercises every non-trivial code path in
// uhdi_to_hgdb, plus deliberately surfaces the converter/CIRCT gaps so a
// golden diff against the native hgdb-firrtl flow shows exactly what UHDI
// loses.  Gap category is tagged on each item:
//   [CONV] converter gap   -- info is in the UHDI doc, converter drops it
//   [CIRCT] CIRCT-pass gap  -- info never reaches UHDI (firtool --emit-uhdi)
//   [OK]   should round-trip identically to native
//
//   1. [OK] Deep guard stack: when(a) { when(b) { when(c) { ... } } }
//      => _walk_body threads guard tokens 3 levels deep; each inner
//      breakpoint's `condition` ANDs the whole stack.
//
//   2. [CONV-partial] Compound boolean guard: when(io.a && io.b && !io.c)
//      => On CIRCT pin 43a716db1 there is NO `<complex>`: CIRCT names the
//      sub-terms (expr_* pool + var_* ids) and the converter renders the
//      guard as `!reset && _GEN & (io_c ^ 1)`.  The !io_c half and the
//      reset/AND structure round-trip; the residual gap is `_GEN` -- CIRCT
//      folds `io.a && io.b` into an intermediate node and the converter
//      emits the raw `_GEN` token instead of expanding to io_a && io_b.
//      (The old `<complex>` framing is obsolete on this pin.)
//
//   3. [OK] Multi-instance hierarchy: Top instantiates two Sub(width).
//      => _instance_rows emits "HgdbTorture.sub0" / ".sub1"; each gets its
//      own generator_variable rows over the same Variable pool entry.
//
//   4. [CONV] Deeply nested Bundle ports: io.data.x / io.data.y
//      => build_dotted_name_map rebuilds io_data_x -> io.data.x.  hgdb stores
//      generator_variable.name = source name, variable.value = RTL name.
//
//   5. [OK] Non-standard clock: Sub runs on an explicit `myClk` (not in
//      hgdb's _CLOCK_NAMES heuristic {clk,clock,...}).  Without an explicit
//      `annotation(clock,...)` row the runtime can't auto-find it -- shows
//      why the converter must emit the clock annotation rather than rely on
//      the name heuristic.
//
//   6. [OK] Hex literal in a guard: when(io.opcode === "hA".U(4.W))
//      => _render_expression must emit a valid SV sized literal (4'd10) that
//      hgdb's PEGTL condition parser accepts.
//
//   7. [CONV] Data-breakpoint target: `acc` is assigned in two distinct
//      when-branches.  A hgdb data-breakpoint (watch on `acc`) needs an
//      `assignment` row per write site.  The converter currently emits
//      assignment rows only for the connect's own breakpoint; multiple write
//      sites for one source name exercise whether watches see every write.
//
//   8. [CONV] Delayed variable: Sub keeps a 3-deep shift history of `reg`.
//      hgdb context_variable type=1 with depth>1 surfaces N-cycles-ago
//      values; the converter does not yet emit delayed context_variables,
//      so the history regs appear only as plain signals.
//
// Compatible with all three bench pipelines (tywaves/uhdi/hgdb) -- only
// --with-experimental-debug-intrinsics; no probe/enum APIs.

import chisel3._
import chisel3.util._
import _root_.circt.stage.ChiselStage

// ---- Inner bundle (two levels deep once nested under io) ----
class Pair extends Bundle {
  val x = UInt(8.W)
  val y = UInt(8.W)
}

// ---- Parametric submodule with a non-standard clock name ----
// RawModule + explicit `myClk` so the clock signal is NOT named clock/clk;
// hgdb's _CLOCK_NAMES heuristic won't find it without a clock annotation.
class Sub(width: Int = 8) extends Module {
  val io = IO(new Bundle {
    val in  = Input(UInt(width.W))
    val sel = Input(Bool())
    val out = Output(UInt(width.W))
  })
  val myClk = IO(Input(Clock()))

  withClock(myClk) {
    val reg = RegInit(0.U(width.W))

    // [OK] Deep guard stack (3 levels) -> nested capture-whens.
    when(io.sel) {
      when(io.in =/= 0.U) {
        when(reg < io.in) {
          reg := io.in
        }
      }
    }.otherwise {
      reg := 0.U
    }

    // [CONV] 3-deep shift history of reg -> source for delayed
    // context_variables (type=1, depth>1).
    val hist0 = RegNext(reg, 0.U(width.W))
    val hist1 = RegNext(hist0, 0.U(width.W))
    val hist2 = RegNext(hist1, 0.U(width.W))

    io.out := reg + hist0 + hist1 + hist2
  }
}

// ---- Top module ----
class HgdbTorture extends Module {
  val io = IO(new Bundle {
    val data   = Input(new Pair)    // [CONV] nested bundle -> io.data.x / io.data.y
    val en     = Input(Bool())
    val a      = Input(Bool())
    val b      = Input(Bool())
    val c      = Input(Bool())
    val opcode = Input(UInt(4.W))
    val q      = Output(UInt(8.W))
  })

  // [OK] Two instances of the same submodule -> .sub0 / .sub1 paths.
  val sub0 = Module(new Sub(8))
  val sub1 = Module(new Sub(8))

  sub0.io.in  := io.data.x
  sub0.io.sel := io.en
  sub0.myClk  := clock
  sub1.io.in  := io.data.y
  sub1.io.sel := io.en
  sub1.myClk  := clock

  // [CONV-partial] Compound boolean guard -- see header item 2: renders as
  // `_GEN & (io_c ^ 1)`, the io.a&&io.b half folded into _GEN by CIRCT.
  val acc = RegInit(0.U(8.W))
  when(io.a && io.b && !io.c) {
    acc := sub0.io.out + sub1.io.out
  }

  // [CONV] Second write site for `acc` -- data-breakpoint / assignment test.
  // [OK] Hex literal in the guard exercises the SV literal renderer.
  when(io.opcode === "hA".U(4.W)) {
    acc := sub0.io.out
  }

  // [OK] Simple guard for comparison: should get a real condition string.
  when(io.en) {
    io.q := acc
  }.otherwise {
    io.q := 0.U
  }
}

object Main extends App {
  print(ChiselStage.emitCHIRRTL(new HgdbTorture, args))
}
