#!/usr/bin/env bash
# Shared shim; each demo/<name>/run.sh symlinks here. Invoked through
# the symlink so basename($0) gives the demo name.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
UHDI_ROOT="$SCRIPT_DIR/../.."

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

# Subcommand (if any) goes ahead of demo name -- run.nu dispatches on it.
demo=$(basename "$SCRIPT_DIR")
if [[ $# -gt 0 ]]; then
    sub="$1"; shift
    exec "$NU" "$UHDI_ROOT/demo/run.nu" "$sub" "$demo" "$@"
else
    exec "$NU" "$UHDI_ROOT/demo/run.nu" "$demo"
fi
