`timescale 1ns/1ps
module tb;
  reg clock = 0;
  reg reset = 1;
  reg [15:0] io_a, io_b;
  reg io_en;
  wire [15:0] io_q;
  wire io_rdy;

  GCD dut (
    .clock  (clock), .reset  (reset),
    .io_a   (io_a),  .io_b   (io_b),
    .io_en  (io_en), .io_q   (io_q), .io_rdy (io_rdy)
  );

  always #5 clock = ~clock;

  initial begin
    $dumpfile("design.vcd");
    $dumpvars(0, tb);
    #2 reset <= 0;
    @(posedge clock);
    io_a <= 48; io_b <= 18; io_en <= 1;
    @(posedge clock); #1 io_en <= 0;
    wait(!io_rdy); wait(io_rdy);
    $display("GCD(48, 18) = %d", io_q);
    io_a <= 15; io_b <= 45; io_en <= 1;
    @(posedge clock); #1 io_en <= 0;
    wait(!io_rdy); wait(io_rdy);
    $display("GCD(15, 45) = %d", io_q);
    #20 $finish;
  end
endmodule
