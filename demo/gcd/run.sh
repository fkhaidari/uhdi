#!/usr/bin/env bash
# Full UHDI pipeline for the GCD demo.
#   ./run.sh          – download firtool, build, emit UHDI, convert to HGLDD/HGDB
#   ./run.sh --download-only  – only download firtool
#   ./run.sh --simulate       – simulate SV → VCD (requires verilator)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

UHDI_ROOT="$SCRIPT_DIR/../.."
BIN_DIR="$SCRIPT_DIR/.bin"
FIRTOOL="$BIN_DIR/firtool"
DEMO_ROOT="$SCRIPT_DIR/.."
export COURSIER_REPOSITORIES="https://jitpack.io|https://repo1.maven.org/maven2"

# ---- helpers -----------------------------------------------------------------
download_firtool() {
    if [[ -f "$FIRTOOL" ]]; then
        echo "firtool already at $FIRTOOL"
        return
    fi
    mkdir -p "$BIN_DIR"

    # Try Docker first, fall back to gh release download.
    if command -v docker &>/dev/null; then
        echo "Downloading firtool from Docker image..."
        image_tag=$(cat "$UHDI_ROOT/tools/docker/image-tag.txt")
        owner=$(cd "$UHDI_ROOT" && git config --get remote.origin.url \
            | sed -E 's#^(git@|https?://)([^/:]+)[:/]([^/]+)/.+$#\3#' \
            | tr '[:upper:]' '[:lower:]')
        image="ghcr.io/${owner}/uhdi-tools:${image_tag}"
        docker pull "$image" >&2
        cid=$(docker create "$image")
        docker cp "$cid:/opt/circt/bin/firtool" "$FIRTOOL"
        docker rm "$cid" > /dev/null
        chmod +x "$FIRTOOL"
    elif command -v gh &>/dev/null && gh auth status &>/dev/null; then
        echo "Downloading firtool from GitHub Releases..."
        tag=$(gh release list -R fkhaidari/uhdi --limit 1 --json tagName -q '.[0].tagName')
        gh release download "$tag" -R fkhaidari/uhdi -D /tmp/firtool-dl -p 'firtool-linux-*.tar.gz'
        tar -xzf /tmp/firtool-dl/firtool-linux-*.tar.gz -C "$BIN_DIR"
        rm -rf /tmp/firtool-dl
        chmod +x "$FIRTOOL"
    else
        echo "ERROR: need docker or gh CLI to download firtool" >&2
        echo "  Install gh: https://cli.github.com/" >&2
        echo "  Or build manually: cd $UHDI_ROOT && tools/release/release-firtool.sh build" >&2
        exit 1
    fi
    echo "firtool: $FIRTOOL"
}

simulate() {
    local sv="${1:-GCD.sv}"
    if ! command -v verilator &>/dev/null; then
        echo "verilator not found, skipping VCD generation" >&2
        return
    fi
    echo "Simulating $sv → gcd.vcd..."

    # Testbench. Port names match firtool's chisel-flattened SV
    # (clock, reset, io_a, io_b, io_en, io_q, io_rdy). The
    # `wait(!io_rdy); wait(io_rdy)` pattern waits for a full busy
    # cycle, dodging a verilator active-vs-NBA region race where a
    # bare wait(io_rdy) would fire on the previous test's stale rdy=1.
    cat > /tmp/gcd_tb.sv << 'TB'
`timescale 1ns/1ps
module gcd_tb;
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
    $dumpfile("gcd.vcd");
    $dumpvars(0, gcd_tb);
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
TB

    # +define+layers_*: short-circuit the `\`include "layers-*.sv"`
    # blocks firtool emits for the verification layer; the layer
    # bodies are inlined in GCD.sv with macro guards, so the includes
    # are redundant but verilator still tries to resolve them.
    rm -rf /tmp/gcd_obj
    verilator --binary --trace -j 0 \
        -Wno-fatal -Wno-WIDTH -Wno-CASEINCOMPLETE -Wno-STMTDLY -Wno-INITIALDLY \
        +define+layers_GCD_Verification_Assert \
        +define+layers_GCD_Verification_Assume \
        +define+layers_GCD_Verification_Cover \
        --top-module gcd_tb -Mdir /tmp/gcd_obj \
        "$SCRIPT_DIR/$sv" /tmp/gcd_tb.sv > /dev/null 2>&1
    /tmp/gcd_obj/Vgcd_tb
    rm -rf /tmp/gcd_tb.sv /tmp/gcd_obj
    echo "Wrote: gcd.vcd"
}

# ---- main --------------------------------------------------------------------
case "${1:-}" in
    --download-only)
        download_firtool
        exit 0 ;;
    --simulate)
        simulate
        exit 0 ;;
esac

echo "=== GCD UHDI pipeline ==="

# 1. Download firtool
download_firtool

# 2. Build Chisel → FIRRTL → UHDI + SV
echo "Building Chisel → UHDI..."
export CHISEL_FIRTOOL_PATH="$BIN_DIR"
# Main writes gcd.uhdi.json + GCD.sv to its JVM cwd; mill is run from
# $DEMO_ROOT, so the artifacts land there. Relocate them into $SCRIPT_DIR
# so the converter steps (run from $SCRIPT_DIR) read them by relative name.
rm -f gcd.uhdi.json GCD.sv "$DEMO_ROOT/gcd.uhdi.json" "$DEMO_ROOT/GCD.sv"
(cd "$DEMO_ROOT" && ./millw gcd.runMain Main) > /dev/null
mv "$DEMO_ROOT/gcd.uhdi.json" "$DEMO_ROOT/GCD.sv" "$SCRIPT_DIR/"

# 3. Convert UHDI → HGLDD
echo "Converting UHDI → HGLDD..."
PYTHONPATH="$UHDI_ROOT/converter/src" python3 -m uhdi_to_hgldd \
    gcd.uhdi.json -o gcd.dd

# 4. Convert UHDI → HGDB
echo "Converting UHDI → HGDB..."
PYTHONPATH="$UHDI_ROOT/converter/src" python3 -m uhdi_to_hgdb \
    gcd.uhdi.json -o gcd.db

echo
echo "=== Done ==="
echo "  gcd.uhdi.json   – UHDI debug info"
echo "  gcd.dd          – HGLDD (for Tywaves)"
echo "  gcd.db          – HGDB SQLite (for hgdb debugger)"
echo "  GCD.sv          – SystemVerilog"
echo
echo "Simulate with:   ./run.sh --simulate  (needs iverilog)"
echo "Tywaves viewer:  tywaves GCD.fir gcd.dd gcd.vcd"
