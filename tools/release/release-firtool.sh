#!/usr/bin/env bash
# Release or install modified firtool (--emit-uhdi).
#
# Subcommands:
#   build                  Build from source → tarball in /tmp (default)
#   build --from-docker    Extract from prebuilt Docker image → tarball in /tmp
#   install                Download prebuilt binary from GitHub Releases
#
# With --release <tag> after a build, also publish the tarball to GitHub Releases.
#
# Usage:
#   tools/release/release-firtool.sh build                       # source build
#   tools/release/release-firtool.sh build --from-docker          # docker extract
#   tools/release/release-firtool.sh build --from-docker --release v0.1.0
#   tools/release/release-firtool.sh install                      # download + install
#   tools/release/release-firtool.sh install --prefix /opt/firtool
#   curl -fsSL https://raw.githubusercontent.com/fkhaidari/uhdi/main/tools/release/release-firtool.sh | bash -s -- install
set -euo pipefail

cd "$(dirname "$0")/../.."

cmd="${1:-build}"
shift || true

release_tag=""
from_docker=0
prefix="${HOME}/.local/bin"
install_tag=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --release)      release_tag="$2"; shift 2 ;;
        --from-docker)  from_docker=1; shift ;;
        --prefix)       prefix="$2"; shift 2 ;;
        --tag)          install_tag="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,/^set -euo/p' "$0" | head -n -1
            exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

detect_platform() {
    local os arch
    case "$(uname -s)" in
        Linux)  os="linux" ;;
        Darwin) os="macos" ;;
        *) echo "unsupported OS: $(uname -s)" >&2; exit 1 ;;
    esac
    case "$(uname -m)" in
        x86_64|amd64) arch="x86_64" ;;
        aarch64|arm64) arch="aarch64" ;;
        *) echo "unsupported arch: $(uname -m)" >&2; exit 1 ;;
    esac
    echo "${os}-${arch}"
}

platform=$(detect_platform)

# ---- install ----------------------------------------------------------------
if [[ "$cmd" == "install" ]]; then
    repo="fkhaidari/uhdi"

    if [[ -z "$install_tag" ]]; then
        install_tag=$(curl -fsSL "https://api.github.com/repos/${repo}/releases/latest" \
            | grep '"tag_name":' | sed -E 's/.*"tag_name": *"([^"]+)".*/\1/')
        if [[ -z "$install_tag" ]]; then
            echo "Could not determine latest release tag; pass --tag <tag>" >&2
            exit 1
        fi
    fi

    asset_name="firtool-${platform}-${install_tag}.tar.gz"
    asset_url="https://github.com/${repo}/releases/download/${install_tag}/${asset_name}"

    echo "Platform:  ${platform}"
    echo "Release:   ${install_tag}"
    echo "Download:  ${asset_url}"

    tmpdir=$(mktemp -d)
    trap 'rm -rf "$tmpdir"' EXIT

    echo "Downloading..."
    curl -fsSL "$asset_url" -o "$tmpdir/$asset_name"

    echo "Extracting to ${prefix}..."
    mkdir -p "$prefix"
    tar -xzf "$tmpdir/$asset_name" -C "$prefix"
    chmod +x "$prefix/firtool"

    echo
    echo "firtool installed to ${prefix}/firtool"
    echo
    echo "Add to PATH or set FIRTOOL:"
    echo "  export FIRTOOL=${prefix}/firtool"
    if [[ ":$PATH:" != *":$prefix:"* ]]; then
        echo "  export PATH=\"$prefix:\$PATH\""
    fi
    exit 0
fi

# ---- build ------------------------------------------------------------------
if [[ "$cmd" != "build" ]]; then
    echo "unknown subcommand: $cmd" >&2
    echo "use: build | install" >&2
    exit 2
fi

tarball="/tmp/firtool-${platform}${release_tag:+-${release_tag}}.tar.gz"

echo "=== Building firtool ==="
echo "  Platform:   $platform"

if [[ "$from_docker" -eq 1 ]]; then
    set -a; . tools/versions.env; set +a
    image_tag=$(cat tools/docker/image-tag.txt)
    owner=$(git config --get remote.origin.url \
        | sed -E 's#^(git@|https?://)([^/:]+)[:/]([^/]+)/.+$#\3#' \
        | tr '[:upper:]' '[:lower:]')
    image="ghcr.io/${owner}/uhdi-tools:${image_tag}"

    echo "  Mode:       docker"
    echo "  Image:      $image"
    echo "Pulling..."
    docker pull "$image"

    echo "Extracting /opt/circt/bin/firtool..."
    cid=$(docker create "$image")
    docker cp "$cid:/opt/circt/bin/firtool" "$tarball.firtool"
    docker rm "$cid" > /dev/null

    echo "Packaging..."
    tar -czf "$tarball" \
        -C "$(dirname "$tarball.firtool")" "$(basename "$tarball.firtool")" \
        --transform "s/$(basename "$tarball.firtool")/firtool/"
    rm -f "$tarball.firtool"
else
    set -a; . tools/versions.env; set +a
    workdir="$(pwd)/.cache/firtool-build"

    echo "  Mode:       source"
    echo "  CIRCT URL:  $CIRCT_URL"
    echo "  CIRCT SHA:  $CIRCT_REV"
    echo "  Work dir:   $workdir"

    if [[ ! -d "$workdir/circt/.git" ]]; then
        rm -rf "$workdir/circt"
        mkdir -p "$workdir"
        echo "Cloning circt (shallow, single commit)..."
        git init "$workdir/circt"
        git -C "$workdir/circt" remote add origin "$CIRCT_URL"
        git -C "$workdir/circt" fetch --depth=1 origin "$CIRCT_REV"
        git -C "$workdir/circt" checkout FETCH_HEAD
        git -C "$workdir/circt" submodule update --init --depth=1 llvm
    fi

    build_dir="$workdir/circt/build"
    if [[ ! -f "$build_dir/bin/firtool" ]]; then
        echo "Configuring cmake..."
        cmake -G Ninja -S "$workdir/circt/llvm/llvm" -B "$build_dir" \
            -DCMAKE_BUILD_TYPE=Release \
            -DLLVM_ENABLE_PROJECTS=mlir \
            -DLLVM_EXTERNAL_PROJECTS=circt \
            -DLLVM_EXTERNAL_CIRCT_SOURCE_DIR="$workdir/circt" \
            -DLLVM_TARGETS_TO_BUILD=host \
            -DLLVM_ENABLE_ASSERTIONS=OFF \
            -DLLVM_BUILD_EXAMPLES=OFF \
            -DLLVM_INCLUDE_TESTS=OFF \
            -DLLVM_INCLUDE_BENCHMARKS=OFF \
            -DCIRCT_LLHD_SIM_ENABLED=OFF \
            -DCIRCT_BINDINGS_PYTHON_ENABLED=OFF

        echo "Building firtool (this may take 30-60 min)..."
        cmake --build "$build_dir" --target firtool
    fi

    echo "Packaging..."
    cp "$build_dir/bin/firtool" /tmp/firtool-strip
    strip /tmp/firtool-strip || true
    tar -czf "$tarball" -C /tmp firtool-strip --transform 's/firtool-strip/firtool/'
    rm -f /tmp/firtool-strip
fi

echo
echo "Built: $tarball ($(du -h "$tarball" | cut -f1))"

# ---- release (optional) ----------------------------------------------------
if [[ -n "$release_tag" ]]; then
    if ! command -v gh &>/dev/null; then
        echo "gh CLI not found; install from https://cli.github.com/" >&2
        exit 2
    fi
    echo "Creating GitHub Release $release_tag..."
    gh release create "$release_tag" "$tarball" \
        --title "firtool $release_tag" \
        --notes "Prebuilt firtool with --emit-uhdi for $platform.

$([[ "$from_docker" -eq 1 ]] \
    && echo 'Extracted from Docker image (same as CI).' \
    || echo "Built from $CIRCT_URL @ $CIRCT_REV")"
    echo "Done: https://github.com/$(git config --get remote.origin.url \
        | sed 's|.*github.com[:/]\(.*\)\.git|\1|')/releases/tag/$release_tag"
fi
