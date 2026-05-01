#!/usr/bin/env bash
# Bootstrap shim: ensure nushell is on PATH, then exec install.nu.
#
# The actual install logic lives in tools/install.nu (typed CLI, real
# error model, structured GitHub-API access). This shim fetches the
# pinned `nu` binary on first run (~30 MB) into <prefix>/bin, then
# hands off.
#
# Usage:
#   tools/install.sh                     # install all (default)
#   tools/install.sh firtool             # one component
#   tools/install.sh all --prefix /opt   # custom prefix
#   tools/install.sh --help              # forwarded to install.nu
#
# Run from a clone of fkhaidari/uhdi. Curl-pipe-bash is no longer
# supported (the install logic spans multiple .nu files; clone first).
set -euo pipefail

NU_VERSION="0.112.2"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Pull --prefix out of argv (just for finding/installing nu); pass the
# full argv through to install.nu unchanged.
prefix=""
prev=""
for a in "$@"; do
    if [[ "$prev" == "--prefix" ]]; then
        prefix="$a"
        prev=""
    elif [[ "$a" == "--prefix" ]]; then
        prev="--prefix"
    fi
done
prefix="${prefix:-$HOME/.local/uhdi-tools}"

# Find or fetch nu. Order: PATH > prefix-installed > download.
if command -v nu &>/dev/null; then
    NU="$(command -v nu)"
elif [[ -x "$prefix/bin/nu" ]]; then
    NU="$prefix/bin/nu"
else
    echo "Bootstrapping nushell ${NU_VERSION} into $prefix/bin..." >&2
    case "$(uname -s)-$(uname -m)" in
        Linux-x86_64)   triple="x86_64-unknown-linux-gnu" ;;
        Linux-aarch64)  triple="aarch64-unknown-linux-gnu" ;;
        Darwin-x86_64)  triple="x86_64-apple-darwin" ;;
        Darwin-arm64)   triple="aarch64-apple-darwin" ;;
        *)
            echo "ERROR: unsupported platform $(uname -s)-$(uname -m)" >&2
            echo "Install nushell manually from https://www.nushell.sh/" >&2
            exit 1 ;;
    esac
    url="https://github.com/nushell/nushell/releases/download/${NU_VERSION}/nu-${NU_VERSION}-${triple}.tar.gz"
    mkdir -p "$prefix/bin"
    tmpd=$(mktemp -d)
    trap 'rm -rf "$tmpd"' EXIT
    curl -fsSL -o "$tmpd/nu.tar.gz" "$url"
    tar -xzf "$tmpd/nu.tar.gz" -C "$tmpd"
    # Tarball layout: nu-<version>-<triple>/nu
    cp "$tmpd/nu-${NU_VERSION}-${triple}/nu" "$prefix/bin/nu"
    chmod +x "$prefix/bin/nu"
    NU="$prefix/bin/nu"
fi

exec "$NU" "$SCRIPT_DIR/install.nu" "$@"
