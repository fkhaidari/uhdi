#!/usr/bin/env bash
# UHDI pipeline for this Chisel demo.
#   ./run.sh                 build, emit UHDI, convert to HGLDD/HGDB
#   ./run.sh --download-only only fetch firtool
#   ./run.sh --simulate      additionally simulate via verilator (needs tb.sv)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

UHDI_ROOT="$SCRIPT_DIR/../.."
BIN_DIR="$SCRIPT_DIR/.bin"
FIRTOOL="$BIN_DIR/firtool"
export COURSIER_REPOSITORIES="https://jitpack.io|https://repo1.maven.org/maven2"

# ---- helpers -----------------------------------------------------------------
download_firtool() {
    if [[ -f "$FIRTOOL" ]]; then
        echo "firtool already at $FIRTOOL"
        return
    fi
    mkdir -p "$BIN_DIR"

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
        exit 1
    fi
    echo "firtool: $FIRTOOL"
}

simulate() {
    if ! command -v verilator &>/dev/null; then
        echo "verilator not found, skipping VCD generation" >&2
        return
    fi
    local sv top tb
    sv=$(ls "$SCRIPT_DIR"/*.sv 2>/dev/null | head -1)
    if [[ -z "$sv" ]]; then
        echo "no .sv in $SCRIPT_DIR (run ./run.sh first)" >&2
        return
    fi
    top=$(basename "$sv" .sv)
    tb="$SCRIPT_DIR/tb.sv"
    if [[ ! -f "$tb" ]]; then
        echo "no testbench at $tb; --simulate is a no-op for this demo" >&2
        echo "  (only the gcd demo ships a TB out of the box)" >&2
        return
    fi
    echo "Simulating $top + tb.sv → design.vcd..."
    rm -rf /tmp/demo_obj
    verilator --binary --trace -j 0 \
        -Wno-fatal -Wno-WIDTH -Wno-CASEINCOMPLETE -Wno-STMTDLY -Wno-INITIALDLY \
        "+define+layers_${top}_Verification_Assert" \
        "+define+layers_${top}_Verification_Assume" \
        "+define+layers_${top}_Verification_Cover" \
        --top-module tb -Mdir /tmp/demo_obj \
        "$sv" "$tb" > /dev/null 2>&1
    /tmp/demo_obj/Vtb
    rm -rf /tmp/demo_obj
    echo "Wrote: design.vcd"
}

# ---- main --------------------------------------------------------------------
case "${1:-}" in
    --download-only) download_firtool; exit 0 ;;
    --simulate)      simulate;          exit 0 ;;
esac

echo "=== UHDI demo pipeline ==="

download_firtool

echo "Building Chisel → UHDI..."
export CHISEL_FIRTOOL_PATH="$BIN_DIR"
rm -f design.uhdi.json design.dd design.db
./millw app.runMain Main > /dev/null

echo "Converting UHDI → HGLDD..."
PYTHONPATH="$UHDI_ROOT/converter/src" python3 -m uhdi_to_hgldd \
    design.uhdi.json -o design.dd

echo "Converting UHDI → HGDB..."
PYTHONPATH="$UHDI_ROOT/converter/src" python3 -m uhdi_to_hgdb \
    design.uhdi.json -o design.db

sv_file=$(ls *.sv 2>/dev/null | head -1)

echo
echo "=== Done ==="
echo "  design.uhdi.json   – UHDI debug info"
echo "  design.dd          – HGLDD (for tywaves)"
echo "  design.db          – HGDB SQLite (for hgdb)"
printf "  %-18s – SystemVerilog\n" "${sv_file:-<TopModule>.sv}"
echo
echo "Simulate:        ./run.sh --simulate    (needs verilator + tb.sv)"
echo "Open in tywaves: tywaves design.dd design.vcd"
