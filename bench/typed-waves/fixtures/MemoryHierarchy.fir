FIRRTL version 7.0.0
circuit MemoryHierarchy :%[[
  {
    "class":"firrtl.transforms.DedupGroupAnnotation",
    "target":"~|MemoryChild",
    "group":"MemoryChild"
  },
  {
    "class":"firrtl.transforms.DedupGroupAnnotation",
    "target":"~|MemoryHierarchy",
    "group":"MemoryHierarchy"
  },
  {
    "class":"firrtl.passes.InlineAnnotation",
    "target":"~|MemoryChild"
  },
  {
    "class":"firrtl.transforms.NoDedupAnnotation",
    "target":"~|MemoryChild"
  }
]]
  layer Verification, bind, "verification" :
    layer Assert, bind, "verification/assert" :
      layer Temporal, inline :
    layer Assume, bind, "verification/assume" :
      layer Temporal, inline :
    layer Cover, bind, "verification/cover" :
      layer Temporal, inline :

  module MemoryChild : @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 10:7]
    input clock : Clock @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 10:7]
    input reset : Reset @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 10:7]
    input address : UInt<2> @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 11:19]
    input write : UInt<1> @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 12:17]
    input data : { tag : UInt<3>, bytes : UInt<8>[2]} @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 13:16]
    output result : { tag : UInt<3>, bytes : UInt<8>[2]} @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 14:18]

    smem storage : { tag : UInt<3>, bytes : UInt<8>[2]}[4] @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 15:28]
    when write : @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 16:15]
      write mport MPORT = storage[address], clock @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 16:30]
      connect MPORT, data @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 16:30]
    node _result_T = eq(write, UInt<1>(0h0)) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 17:35]
    wire _result_WIRE : UInt<2> @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 17:25]
    invalidate _result_WIRE @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 17:25]
    when _result_T : @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 17:25]
      connect _result_WIRE, address @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 17:25]
      read mport result_MPORT = storage[_result_WIRE], clock @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 17:25]
    connect result, result_MPORT @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 17:10]
    intrinsic(circt_debug_moduleinfo<typeName = "MemoryChild">) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 10:7]
    node clock_h = intrinsic(circt_debug_value<typeName = "Clock"> : UInt<0>, clock) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 10:7]
    intrinsic(circt_debug_var<name = "clock">, clock_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 10:7]
    node reset_h = intrinsic(circt_debug_value<typeName = "Reset"> : UInt<0>, reset) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 10:7]
    intrinsic(circt_debug_var<name = "reset">, reset_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 10:7]
    node address_h = intrinsic(circt_debug_value<typeName = "UInt<2>"> : UInt<0>, address) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 11:19]
    intrinsic(circt_debug_var<name = "address">, address_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 11:19]
    node write_h = intrinsic(circt_debug_value<typeName = "Bool"> : UInt<0>, write) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 12:17]
    intrinsic(circt_debug_var<name = "write">, write_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 12:17]
    node data_tag_h = intrinsic(circt_debug_value<name = "tag", typeName = "UInt<3>"> : UInt<0>, data.tag) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 13:16]
    node data_bytes_0_h = intrinsic(circt_debug_value<typeName = "UInt<8>"> : UInt<0>, data.bytes[0]) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 13:16]
    node data_bytes_1_h = intrinsic(circt_debug_value<typeName = "UInt<8>"> : UInt<0>, data.bytes[1]) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 13:16]
    node data_bytes_a = intrinsic(circt_debug_array : UInt<0>, data_bytes_0_h, data_bytes_1_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 13:16]
    node data_bytes_h = intrinsic(circt_debug_value<name = "bytes", typeName = "UInt<8>[2]"> : UInt<0>, data_bytes_a) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 13:16]
    node data_s = intrinsic(circt_debug_struct : UInt<0>, data_tag_h, data_bytes_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 13:16]
    node data_h = intrinsic(circt_debug_value<typeName = "MemoryWord"> : UInt<0>, data_s) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 13:16]
    intrinsic(circt_debug_var<name = "data">, data_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 13:16]
    node result_tag_h = intrinsic(circt_debug_value<name = "tag", typeName = "UInt<3>"> : UInt<0>, result.tag) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 14:18]
    node result_bytes_0_h = intrinsic(circt_debug_value<typeName = "UInt<8>"> : UInt<0>, result.bytes[0]) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 14:18]
    node result_bytes_1_h = intrinsic(circt_debug_value<typeName = "UInt<8>"> : UInt<0>, result.bytes[1]) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 14:18]
    node result_bytes_a = intrinsic(circt_debug_array : UInt<0>, result_bytes_0_h, result_bytes_1_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 14:18]
    node result_bytes_h = intrinsic(circt_debug_value<name = "bytes", typeName = "UInt<8>[2]"> : UInt<0>, result_bytes_a) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 14:18]
    node result_s = intrinsic(circt_debug_struct : UInt<0>, result_tag_h, result_bytes_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 14:18]
    node result_h = intrinsic(circt_debug_value<typeName = "MemoryWord"> : UInt<0>, result_s) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 14:18]
    intrinsic(circt_debug_var<name = "result">, result_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 14:18]
    intrinsic(circt_debug_var<typeName = "MemoryWord[4]", name = "storage", params = "{\"x-chisel\":{\"params\":[{\"name\":\"t\",\"typeName\":\"T\"},{\"name\":\"n\",\"typeName\":\"BigInt\"},{\"name\":\"readUnderWrite\",\"typeName\":\"Value\",\"value\":\"undefined\"},{\"name\":\"sourceInfo\",\"typeName\":\"SourceInfo\"}]}}">) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 15:28]


  public module MemoryHierarchy : @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 20:7]
    input clock : Clock @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 20:7]
    input reset : UInt<1> @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 20:7]
    input address : UInt<2> @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 21:19]
    input write : UInt<1> @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 22:17]
    input data : { tag : UInt<3>, bytes : UInt<8>[2]} @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 23:16]
    output result : { tag : UInt<3>, bytes : UInt<8>[2]} @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 24:18]

    inst child of MemoryChild @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 25:21]
    connect child.clock, clock
    connect child.reset, reset
    connect child.address, address @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 26:17]
    connect child.write, write @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 27:15]
    connect child.data.bytes[0], data.bytes[0] @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 28:14]
    connect child.data.bytes[1], data.bytes[1] @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 28:14]
    connect child.data.tag, data.tag @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 28:14]
    connect result, child.result @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 29:10]
    intrinsic(circt_debug_moduleinfo<typeName = "MemoryHierarchy">) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 20:7]
    node clock_h = intrinsic(circt_debug_value<typeName = "Clock"> : UInt<0>, clock) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 20:7]
    intrinsic(circt_debug_var<name = "clock">, clock_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 20:7]
    node reset_h = intrinsic(circt_debug_value<typeName = "Bool"> : UInt<0>, reset) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 20:7]
    intrinsic(circt_debug_var<name = "reset">, reset_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 20:7]
    node address_h = intrinsic(circt_debug_value<typeName = "UInt<2>"> : UInt<0>, address) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 21:19]
    intrinsic(circt_debug_var<name = "address">, address_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 21:19]
    node write_h = intrinsic(circt_debug_value<typeName = "Bool"> : UInt<0>, write) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 22:17]
    intrinsic(circt_debug_var<name = "write">, write_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 22:17]
    node data_tag_h = intrinsic(circt_debug_value<name = "tag", typeName = "UInt<3>"> : UInt<0>, data.tag) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 23:16]
    node data_bytes_0_h = intrinsic(circt_debug_value<typeName = "UInt<8>"> : UInt<0>, data.bytes[0]) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 23:16]
    node data_bytes_1_h = intrinsic(circt_debug_value<typeName = "UInt<8>"> : UInt<0>, data.bytes[1]) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 23:16]
    node data_bytes_a = intrinsic(circt_debug_array : UInt<0>, data_bytes_0_h, data_bytes_1_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 23:16]
    node data_bytes_h = intrinsic(circt_debug_value<name = "bytes", typeName = "UInt<8>[2]"> : UInt<0>, data_bytes_a) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 23:16]
    node data_s = intrinsic(circt_debug_struct : UInt<0>, data_tag_h, data_bytes_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 23:16]
    node data_h = intrinsic(circt_debug_value<typeName = "MemoryWord"> : UInt<0>, data_s) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 23:16]
    intrinsic(circt_debug_var<name = "data">, data_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 23:16]
    node result_tag_h = intrinsic(circt_debug_value<name = "tag", typeName = "UInt<3>"> : UInt<0>, result.tag) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 24:18]
    node result_bytes_0_h = intrinsic(circt_debug_value<typeName = "UInt<8>"> : UInt<0>, result.bytes[0]) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 24:18]
    node result_bytes_1_h = intrinsic(circt_debug_value<typeName = "UInt<8>"> : UInt<0>, result.bytes[1]) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 24:18]
    node result_bytes_a = intrinsic(circt_debug_array : UInt<0>, result_bytes_0_h, result_bytes_1_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 24:18]
    node result_bytes_h = intrinsic(circt_debug_value<name = "bytes", typeName = "UInt<8>[2]"> : UInt<0>, result_bytes_a) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 24:18]
    node result_s = intrinsic(circt_debug_struct : UInt<0>, result_tag_h, result_bytes_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 24:18]
    node result_h = intrinsic(circt_debug_value<typeName = "MemoryWord"> : UInt<0>, result_s) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 24:18]
    intrinsic(circt_debug_var<name = "result">, result_h) @[/home/farid/thesis/practice/circt/.worktrees/typed-waves/e2e/fixtures/MemoryHierarchy.scala 24:18]
