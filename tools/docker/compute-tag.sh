#!/usr/bin/env bash
# Compute the deterministic image tag from versions.env + Dockerfile +
# entrypoint.sh. The build-tools workflow uses the same formula, so
# this script's output is what tools/image-tag.txt must contain.
#
# Usage:
#   tools/docker/compute-tag.sh                      # print tag
#   tools/docker/compute-tag.sh > tools/docker/image-tag.txt
set -euo pipefail
cd "$(dirname "$0")/../.."
sha256sum tools/versions.env tools/docker/Dockerfile tools/docker/entrypoint.sh \
    | sha256sum | cut -c1-16
