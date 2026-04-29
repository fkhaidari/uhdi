#!/usr/bin/env bash
# Full UHDI pipeline for the GCD demo.
#   ./run.sh          – download firtool, build, emit UHDI, convert to HGLDD/HGDB
#   ./run.sh --download-only  – only download firtool
#   ./run.sh --simulate       – simulate SV → VCD (requires iverilog)
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
    if ! command -v iverilog &>/dev/null; then
        echo "iverilog not found, skipping VCD generation" >&2
        return
    fi
    echo "Simulating $sv → gcd.vcd..."

    # Simple testbench
    cat > /tmp/gcd_tb.sv << 'TB'
`timescale 1ns/1ps
module gcd_tb;
  reg clk = 0;
  reg rst = 1;
  reg [15:0] a, b;
  reg en;
  wire [15:0] q;
  wire rdy;

  GCD dut(.*);

  always #5 clk = ~clk;

  initial begin
    $dumpfile("gcd.vcd");
    $dumpvars(0, gcd_tb);
    #2 rst = 0;
    @(posedge clk);
    a = 48; b = 18; en = 1;
    @(posedge clk);
    en = 0;
    wait(rdy);
    $display("GCD(48, 18) = %d", q);
    a = 15; b = 45; en = 1;
    @(posedge clk);
    en = 0;
    wait(rdy);
    $display("GCD(15, 45) = %d", q);
    #20 $finish;
  end
endmodule
TB
    iverilog -g2012 -o /tmp/gcd_sim "$SCRIPT_DIR/GCD.sv" /tmp/gcd_tb.sv
    vvp /tmp/gcd_sim
    rm -f /tmp/gcd_tb.sv /tmp/gcd_sim
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
rm -f gcd.uhdi.json GCD.sv
(cd "$DEMO_ROOT" && ./millw gcd.runMain Main) > /dev/null

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
