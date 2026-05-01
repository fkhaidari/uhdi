#!/usr/bin/env bash
# Bootstrap shim: locates `nu` (left behind by tools/install.sh) and
# dispatches to demo/run.nu with this directory's name as the demo arg.
#
# Subcommands forwarded as-is:
#   ./run.sh                         pipeline (default)
#   ./run.sh download-only           only fetch firtool
#   ./run.sh simulate                run verilator + tb.sv
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
UHDI_ROOT="$SCRIPT_DIR/../.."

# Find nu: PATH > ~/.local/uhdi-tools/bin > $UHDI_PREFIX/bin.
if command -v nu >/dev/null 2>&1; then
    NU="$(command -v nu)"
elif [[ -x "$HOME/.local/uhdi-tools/bin/nu" ]]; then
    NU="$HOME/.local/uhdi-tools/bin/nu"
elif [[ -n "${UHDI_PREFIX:-}" && -x "$UHDI_PREFIX/bin/nu" ]]; then
    NU="$UHDI_PREFIX/bin/nu"
else
    echo "ERROR: nu (nushell) not found. Run tools/install.sh first." >&2
    exit 1
fi

# Pass the subcommand (if any) ahead of the demo name; demo/run.nu's
# `def "main pipeline" [demo]` etc. expects (subcommand, demo).
demo=$(basename "$SCRIPT_DIR")
if [[ $# -gt 0 ]]; then
    sub="$1"; shift
    exec "$NU" "$UHDI_ROOT/demo/run.nu" "$sub" "$demo" "$@"
else
    exec "$NU" "$UHDI_ROOT/demo/run.nu" "$demo"
fi
