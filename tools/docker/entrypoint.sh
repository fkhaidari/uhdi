#!/usr/bin/env bash
# Symlink baked /opt/ivy2-local under $HOME (UID may differ from build time).
set -euo pipefail

mkdir -p "${HOME}/.ivy2"
if [ ! -e "${HOME}/.ivy2/local" ]; then
    ln -s /opt/ivy2-local "${HOME}/.ivy2/local"
fi

exec "$@"
