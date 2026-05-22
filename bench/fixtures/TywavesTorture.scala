// Tywaves torture fixture -- exercises every HGLDD feature the tywaves-rs
// parser actually reads (hgldd/spec.rs), so a golden diff against native
// `firtool --emit-hgldd` shows what uhdi_to_hgldd preserves vs loses.
// Gap tags: [OK] round-trips, [CONV] converter gap, [CIRCT] producer gap.
//
//   1. [OK] Nested Bundle-in-Bundle (Outer { Inner, Bool }):
//      => _topo_sorted_struct_ids sorts Inner before Outer; two struct
//      objects with cross-referencing type_name.
//
//   2. [OK] Multi-dimensional Vec: Vec(2, Vec(4, UInt(8.W))):
//      => _type_description recurses, splicing unpacked_range to [1,0,3,0].
//      tywaves-rs VariableKind::Vector nesting (spec.rs Vector{fields}).
//
//   3. [OK] Vec[Bundle]: Vec(depth, new Inner):
//      => vector elementRef points at a struct; _first_vector_element_sig
//      names the port_var after element 0.
//
//   4. [CIRCT-via-new-chisel] ChiselEnum on a port and inside a struct:
//      => _populate_enum_index walks struct members; enum_defs per scope +
//      enum_def_ref on port_vars.  New chisel emits circt_debug_enumdef so
//      this works in the uhdi pipeline; tywaves pipeline (EnumComponent-
//      Annotation) would crash firtool -> mark _PENDING there.
//
//   5. [OK] Ctor params: TywavesTorture(width, depth) + Alu(width):
//      => moduleinfo intrinsic carries params -> sourceLangType.params ->
//      HGLDD source_lang_type_info.params (ConstructorParams in spec.rs).
//
//   6. [OK] Expression opcode coverage in port_var.value: the combo block
//      below forces +, -, &, |, ==, <<, Cat ({}), Fill (R{}), and Mux (?:)
//      through _expression_to_hgldd / _opnode_to_hgldd.
//
//   7. [OK] Inline when-scopes with a name collision (`tmp` in both
//      branches): _uniquify renames the second occurrence.
//
//   8. [OK] Submodule instance: _instance_child {name, obj_name, module_name}.
//
// uhdi pipeline only (enum). _PENDING for tywaves/hgdb pipelines.

import chisel3._
import chisel3.util._
import _root_.circt.stage.ChiselStage

// ---- Enum (5 variants) ----
object Op extends ChiselEnum {
  val Add, Sub, And, Or, Pass = Value
}

// ---- Nested structs (two levels), one carrying an enum field ----
class Inner extends Bundle {
  val lo  = UInt(4.W)
  val hi  = UInt(4.W)
  val tag = Op()           // enum inside a struct
}

class Outer extends Bundle {
  val inner = new Inner
  val valid = Bool()
}

// ---- Parametric ALU submodule ----
class Alu(width: Int = 8) extends Module {
  val io = IO(new Bundle {
    val a   = Input(UInt(width.W))
    val b   = Input(UInt(width.W))
    val op  = Input(Op())
    val out = Output(UInt(width.W))
  })

  io.out := MuxLookup(io.op, io.a)(Seq(
    Op.Add  -> (io.a + io.b),
    Op.Sub  -> (io.a - io.b),
    Op.And  -> (io.a & io.b),
    Op.Or   -> (io.a | io.b),
    Op.Pass -> io.a,
  ))
}

// ---- Top module with ctor params ----
class TywavesTorture(width: Int = 8, depth: Int = 4) extends Module {
  val io = IO(new Bundle {
    val packet = Input(new Outer)                       // nested struct + enum
    val grid   = Input(Vec(2, Vec(depth, UInt(width.W)))) // 2-D Vec
    val bvec   = Input(Vec(depth, new Inner))           // Vec[Bundle]
    val op     = Input(Op())                            // enum port
    val result = Output(UInt(width.W))
    val sum    = Output(UInt(width.W))
    val combo  = Output(UInt((2 * width).W))
  })

  val alu = Module(new Alu(width))
  alu.io.a  := io.packet.inner.lo
  alu.io.b  := io.packet.inner.hi
  alu.io.op := io.op

  // [OK] Expression opcode coverage: comparison, shift, Cat, Fill, Mux.
  val eq    = io.packet.inner.lo === io.packet.inner.hi   // ==
  val shifted = io.packet.inner.hi << 2                   // <<
  val packed  = Cat(io.packet.inner.hi, io.packet.inner.lo) // {}
  val filled  = Fill(2, io.packet.inner.lo)               // R{}
  io.combo := Mux(eq, packed, filled << shifted(1, 0))    // ?: + slice

  // [OK] Inline when-scopes: both branches bind a local `tmp`.
  val acc = RegInit(0.U(width.W))
  when(io.packet.valid) {
    val tmp = io.grid(0).reduce(_ + _)
    acc := tmp
  }.otherwise {
    val tmp = io.bvec(0).lo + io.bvec(0).hi
    acc := tmp
  }

  io.result := alu.io.out
  io.sum    := acc
}

object Main extends App {
  print(ChiselStage.emitCHIRRTL(new TywavesTorture, args))
}
