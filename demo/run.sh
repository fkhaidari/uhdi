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

# Always pass an explicit subcommand so a demo whose name happens to
# match a `main <sub>` definition in run.nu can't shadow it.
demo=$(basename "$SCRIPT_DIR")
sub="${1:-build}"
[[ $# -gt 0 ]] && shift
exec "$NU" "$UHDI_ROOT/demo/run.nu" "$sub" "$demo" "$@"
