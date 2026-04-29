#!/usr/bin/env bash
# Verify tools/docker/image-tag.txt matches sha256(versions.env + docker/Dockerfile +
# docker/entrypoint.sh). Exits 0 in sync; non-zero with a fix instruction.
set -euo pipefail
cd "$(dirname "$0")/../.."

expected=$(tools/docker/compute-tag.sh)
recorded=$(cat tools/docker/image-tag.txt 2>/dev/null || echo "<missing>")

if [ "$expected" = "$recorded" ]; then
    exit 0
fi

cat >&2 <<EOF
tools/docker/image-tag.txt is out of sync.
  recorded: $recorded
  expected: $expected

Fix:
  tools/docker/compute-tag.sh > tools/docker/image-tag.txt
  git add tools/docker/image-tag.txt
EOF
exit 1
