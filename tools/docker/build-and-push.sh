#!/usr/bin/env bash
# Build uhdi-tools image; optionally push to GHCR.
# Usage: tools/docker/build-and-push.sh [--push] [--owner <name>]
# First build is 30-60 min; subsequent builds reuse layer cache.
set -euo pipefail

cd "$(dirname "$0")/../.."

push=0
owner=""
while [ $# -gt 0 ]; do
    case "$1" in
        --push)  push=1; shift ;;
        --owner) owner="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,/^set -euo/p' "$0" | head -n -1
            exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

# Default owner: lowercased path-segment of `git remote.origin.url`.
if [ -z "$owner" ]; then
    remote=$(git config --get remote.origin.url || true)
    # `#` as s-delimiter (regex contains `@` from `git@`).
    owner=$(printf '%s' "$remote" \
        | sed -E 's#^(git@|https?://)([^/:]+)[:/]([^/]+)/.+$#\3#' \
        | tr '[:upper:]' '[:lower:]')
    if [ -z "$owner" ]; then
        echo "could not infer owner from git remote; pass --owner <name>" >&2
        exit 2
    fi
fi

# Verify image-tag.txt is in sync (prevents stale push).
tools/docker/check-tag.sh

set -a
# shellcheck disable=SC1091
. tools/versions.env
set +a

tag=$(cat tools/docker/image-tag.txt)
ref="ghcr.io/${owner}/uhdi-tools:${tag}"
# Bare repo name (no tag): podman's --cache-from rejects tagged refs.
cache_repo="ghcr.io/${owner}/uhdi-tools"

# Reuse layers from the previously published :latest. No-op on first publish.
docker pull "${cache_repo}:latest" 2>/dev/null || echo "  (no cache image; cold build)"

# Only keys declared as ARG in Dockerfile.
build_args=(
    --build-arg "CIRCT_URL=${CIRCT_URL}"
    --build-arg "CIRCT_REV=${CIRCT_REV}"
    --build-arg "LLVM_URL=${LLVM_URL}"
    --build-arg "LLVM_REV=${LLVM_REV}"
    --build-arg "HGDB_CIRCT_URL=${HGDB_CIRCT_URL}"
    --build-arg "HGDB_CIRCT_REV=${HGDB_CIRCT_REV}"
    --build-arg "HGDB_CIRCT_LLVM_URL=${HGDB_CIRCT_LLVM_URL}"
    --build-arg "HGDB_CIRCT_LLVM_REV=${HGDB_CIRCT_LLVM_REV}"
    --build-arg "HGDB_FIRRTL_URL=${HGDB_FIRRTL_URL}"
    --build-arg "HGDB_FIRRTL_REV=${HGDB_FIRRTL_REV}"
    --build-arg "HGDB_URL=${HGDB_URL}"
    --build-arg "HGDB_REV=${HGDB_REV}"
    --build-arg "CHISEL_TYWAVES_URL=${CHISEL_TYWAVES_URL}"
    --build-arg "CHISEL_TYWAVES_REV=${CHISEL_TYWAVES_REV}"
    --build-arg "CHISEL_UHDI_URL=${CHISEL_UHDI_URL}"
    --build-arg "CHISEL_UHDI_REV=${CHISEL_UHDI_REV}"
    --build-arg "TYWAVES_URL=${TYWAVES_URL}"
    --build-arg "TYWAVES_REV=${TYWAVES_REV}"
    --build-arg "CHISEL_STOCK_VERSION=${CHISEL_STOCK_VERSION}"
    --build-arg "SCALA_CLI_VERSION=${SCALA_CLI_VERSION}"
)

echo "Building $ref"
# --network=host fixes rootless podman bridge dropping some external hosts;
# --http-proxy=false (podman-only) skips host-proxy injection -- drop for docker.
docker build \
    -f tools/docker/Dockerfile \
    -t "$ref" \
    --network=host \
    --http-proxy=false \
    --cache-from "$cache_repo" \
    --build-arg BUILDKIT_INLINE_CACHE=1 \
    "${build_args[@]}" \
    tools/docker/

if [ "$push" -eq 1 ]; then
    echo "Pushing $ref"
    docker push "$ref"
    latest="ghcr.io/${owner}/uhdi-tools:latest"
    docker tag "$ref" "$latest"
    docker push "$latest"
fi

echo
echo "Done."
echo "  ref:  $ref"
# if/fi (not `&& echo`): the && form would propagate the test's exit
# code as the script's, making a successful push end with $?=1.
if [ "$push" -eq 0 ]; then
    echo "  (not pushed; pass --push to publish)"
fi
