FIRRTL version 7.0.0
circuit LayerHierarchy :%[[
  {
    "class":"firrtl.transforms.DedupGroupAnnotation",
    "target":"~|LayerLeaf",
    "group":"LayerLeaf"
  },
  {
    "class":"firrtl.transforms.DedupGroupAnnotation",
    "target":"~|LayerLeaf_1",
    "group":"LayerLeaf"
  },
  {
    "class":"firrtl.transforms.DedupGroupAnnotation",
    "target":"~|LayerDead",
    "group":"LayerDead"
  },
  {
    "class":"firrtl.transforms.DedupGroupAnnotation",
    "target":"~|LayerLeaf_2",
    "group":"LayerLeaf"
  },
  {
    "class":"firrtl.transforms.DedupGroupAnnotation",
    "target":"~|LayerInline",
    "group":"LayerInline"
  },
  {
    "class":"firrtl.transforms.DedupGroupAnnotation",
    "target":"~|LayerHierarchy",
    "group":"LayerHierarchy"
  },
  {
    "class":"firrtl.passes.InlineAnnotation",
    "target":"~|LayerInline"
  },
  {
    "class":"firrtl.transforms.NoDedupAnnotation",
    "target":"~|LayerInline"
  },
  {
    "class":"firrtl.transforms.DontTouchAnnotation",
    "target":"~|LayerHierarchy>observed"
  }
]]
  layer Verification, bind, "verification" :
    layer Assert, bind, "verification/assert" :
      layer Temporal, inline :
    layer Assume, bind, "verification/assume" :
      layer Temporal, inline :
    layer Cover, bind, "verification/cover" :
      layer Temporal, inline :
  layer DebugLayer, bind, "DebugLayer" : @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 5:33]

  module LayerLeaf : @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 7:7]
    input clock : Clock @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 7:7]
    input reset : Reset @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 7:7]
    input in : UInt<8> @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 8:14]
    output out : UInt<8> @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 9:15]

    regreset r : UInt, clock, reset, UInt<8>(0h0) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 10:18]
    connect r, in @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 10:18]
    node _out_T = add(r, UInt<1>(0h1)) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 11:12]
    node _out_T_1 = tail(_out_T, 1) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 11:12]
    connect out, _out_T_1 @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 11:7]
    intrinsic(circt_debug_moduleinfo<typeName = "LayerLeaf">) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 7:7]
    node clock_h = intrinsic(circt_debug_value<typeName = "Clock"> : UInt<0>, clock) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 7:7]
    intrinsic(circt_debug_var<name = "clock">, clock_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 7:7]
    node reset_h = intrinsic(circt_debug_value<typeName = "Reset"> : UInt<0>, reset) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 7:7]
    intrinsic(circt_debug_var<name = "reset">, reset_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 7:7]
    node in_h = intrinsic(circt_debug_value<typeName = "UInt<8>"> : UInt<0>, in) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 8:14]
    intrinsic(circt_debug_var<name = "in">, in_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 8:14]
    node out_h = intrinsic(circt_debug_value<typeName = "UInt<8>"> : UInt<0>, out) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 9:15]
    intrinsic(circt_debug_var<name = "out">, out_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 9:15]
    node r_h = intrinsic(circt_debug_value<typeName = "UInt"> : UInt<0>, r) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 10:18]
    intrinsic(circt_debug_var<name = "r">, r_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 10:18]


  module LayerLeaf_1 : @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 7:7]
    input clock : Clock @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 7:7]
    input reset : Reset @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 7:7]
    input in : UInt<8> @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 8:14]
    output out : UInt<8> @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 9:15]

    regreset r : UInt, clock, reset, UInt<8>(0h0) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 10:18]
    connect r, in @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 10:18]
    node _out_T = add(r, UInt<1>(0h1)) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 11:12]
    node _out_T_1 = tail(_out_T, 1) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 11:12]
    connect out, _out_T_1 @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 11:7]
    intrinsic(circt_debug_moduleinfo<typeName = "LayerLeaf">) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 7:7]
    node clock_h = intrinsic(circt_debug_value<typeName = "Clock"> : UInt<0>, clock) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 7:7]
    intrinsic(circt_debug_var<name = "clock">, clock_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 7:7]
    node reset_h = intrinsic(circt_debug_value<typeName = "Reset"> : UInt<0>, reset) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 7:7]
    intrinsic(circt_debug_var<name = "reset">, reset_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 7:7]
    node in_h = intrinsic(circt_debug_value<typeName = "UInt<8>"> : UInt<0>, in) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 8:14]
    intrinsic(circt_debug_var<name = "in">, in_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 8:14]
    node out_h = intrinsic(circt_debug_value<typeName = "UInt<8>"> : UInt<0>, out) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 9:15]
    intrinsic(circt_debug_var<name = "out">, out_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 9:15]
    node r_h = intrinsic(circt_debug_value<typeName = "UInt"> : UInt<0>, r) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 10:18]
    intrinsic(circt_debug_var<name = "r">, r_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 10:18]


  module LayerDead : @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 23:7]
    input clock : Clock @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 23:7]
    input reset : Reset @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 23:7]

    intrinsic(circt_debug_moduleinfo<typeName = "LayerDead">) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 23:7]
    node clock_h = intrinsic(circt_debug_value<typeName = "Clock"> : UInt<0>, clock) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 23:7]
    intrinsic(circt_debug_var<name = "clock">, clock_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 23:7]
    node reset_h = intrinsic(circt_debug_value<typeName = "Reset"> : UInt<0>, reset) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 23:7]
    intrinsic(circt_debug_var<name = "reset">, reset_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 23:7]


  module LayerLeaf_2 : @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 7:7]
    input clock : Clock @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 7:7]
    input reset : Reset @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 7:7]
    input in : UInt<8> @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 8:14]
    output out : UInt<8> @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 9:15]

    regreset r : UInt, clock, reset, UInt<8>(0h0) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 10:18]
    connect r, in @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 10:18]
    node _out_T = add(r, UInt<1>(0h1)) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 11:12]
    node _out_T_1 = tail(_out_T, 1) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 11:12]
    connect out, _out_T_1 @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 11:7]
    intrinsic(circt_debug_moduleinfo<typeName = "LayerLeaf">) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 7:7]
    node clock_h = intrinsic(circt_debug_value<typeName = "Clock"> : UInt<0>, clock) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 7:7]
    intrinsic(circt_debug_var<name = "clock">, clock_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 7:7]
    node reset_h = intrinsic(circt_debug_value<typeName = "Reset"> : UInt<0>, reset) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 7:7]
    intrinsic(circt_debug_var<name = "reset">, reset_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 7:7]
    node in_h = intrinsic(circt_debug_value<typeName = "UInt<8>"> : UInt<0>, in) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 8:14]
    intrinsic(circt_debug_var<name = "in">, in_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 8:14]
    node out_h = intrinsic(circt_debug_value<typeName = "UInt<8>"> : UInt<0>, out) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 9:15]
    intrinsic(circt_debug_var<name = "out">, out_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 9:15]
    node r_h = intrinsic(circt_debug_value<typeName = "UInt"> : UInt<0>, r) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 10:18]
    intrinsic(circt_debug_var<name = "r">, r_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 10:18]


  module LayerInline : @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 14:7]
    input clock : Clock @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 14:7]
    input reset : Reset @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 14:7]
    input in : UInt<8> @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 15:14]
    output out : UInt<8> @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 16:15]

    inst child of LayerLeaf_2 @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 17:21]
    connect child.clock, clock
    connect child.reset, reset
    connect child.in, in @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 18:12]
    node _mixed_T = xor(in, UInt<7>(0h5a)) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 19:26]
    regreset mixed : UInt, clock, reset, UInt<8>(0h0) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 19:22]
    connect mixed, _mixed_T @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 19:22]
    node _out_T = xor(mixed, child.out) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 20:16]
    connect out, _out_T @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 20:7]
    intrinsic(circt_debug_moduleinfo<typeName = "LayerInline">) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 14:7]
    node clock_h = intrinsic(circt_debug_value<typeName = "Clock"> : UInt<0>, clock) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 14:7]
    intrinsic(circt_debug_var<name = "clock">, clock_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 14:7]
    node reset_h = intrinsic(circt_debug_value<typeName = "Reset"> : UInt<0>, reset) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 14:7]
    intrinsic(circt_debug_var<name = "reset">, reset_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 14:7]
    node in_h = intrinsic(circt_debug_value<typeName = "UInt<8>"> : UInt<0>, in) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 15:14]
    intrinsic(circt_debug_var<name = "in">, in_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 15:14]
    node out_h = intrinsic(circt_debug_value<typeName = "UInt<8>"> : UInt<0>, out) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 16:15]
    intrinsic(circt_debug_var<name = "out">, out_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 16:15]
    node mixed_h = intrinsic(circt_debug_value<typeName = "UInt"> : UInt<0>, mixed) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 19:22]
    intrinsic(circt_debug_var<name = "mixed">, mixed_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 19:22]


  public module LayerHierarchy : @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 25:7]
    input clock : Clock @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 25:7]
    input reset : UInt<1> @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 25:7]
    input in : UInt<8> @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 26:14]
    output out : UInt<8> @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 27:15]

    inst left of LayerLeaf @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 28:20]
    connect left.clock, clock
    connect left.reset, reset
    inst right of LayerLeaf_1 @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 29:21]
    connect right.clock, clock
    connect right.reset, reset
    inst dead of LayerDead @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 30:20]
    connect dead.clock, clock
    connect dead.reset, reset
    connect left.in, in @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 31:11]
    connect right.in, left.out @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 32:12]
    connect out, right.out @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 33:7]
    wire _WIRE : Probe<UInt, DebugLayer> @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 34:27]
    layerblock DebugLayer : @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 34:27]
      inst inline of LayerInline @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 35:24]
      connect inline.clock, clock
      connect inline.reset, reset
      connect inline.in, left.out @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 36:15]
      wire observed : UInt @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 37:31]
      connect observed, inline.out @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 37:31]
      define _WIRE = probe(observed) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 34:27]
      node observed_h = intrinsic(circt_debug_value<typeName = "UInt"> : UInt<0>, observed) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 37:31]
      intrinsic(circt_debug_var<name = "observed">, observed_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 37:31]
    intrinsic(circt_debug_moduleinfo<typeName = "LayerHierarchy">) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 25:7]
    node clock_h = intrinsic(circt_debug_value<typeName = "Clock"> : UInt<0>, clock) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 25:7]
    intrinsic(circt_debug_var<name = "clock">, clock_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 25:7]
    node reset_h = intrinsic(circt_debug_value<typeName = "Bool"> : UInt<0>, reset) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 25:7]
    intrinsic(circt_debug_var<name = "reset">, reset_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 25:7]
    node in_h = intrinsic(circt_debug_value<typeName = "UInt<8>"> : UInt<0>, in) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 26:14]
    intrinsic(circt_debug_var<name = "in">, in_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 26:14]
    node out_h = intrinsic(circt_debug_value<typeName = "UInt<8>"> : UInt<0>, out) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 27:15]
    intrinsic(circt_debug_var<name = "out">, out_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/LayerHierarchy.scala 27:15]
