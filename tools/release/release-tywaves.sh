#!/usr/bin/env bash
# Release or install the surfer-tywaves waveform viewer (binary: tywaves).
#
# Subcommands:
#   build                  Build from source -> tarball in /tmp
#   build --from-docker    Extract from prebuilt Docker image -> tarball in /tmp
#   install                Download prebuilt binary from GitHub Releases
#
# With --release <tag> after a build, also publish the tarball to GitHub
# Releases (gh CLI required, --clobber so the same tag can carry firtool +
# hgdb-py + tywaves together).
#
# Usage:
#   tools/release/release-tywaves.sh build --from-docker
#   tools/release/release-tywaves.sh build --from-docker --release firtool-v0.1.1
#   tools/release/release-tywaves.sh install
#   tools/release/release-tywaves.sh install --prefix /opt/tywaves
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

    asset_name="tywaves-${platform}-${install_tag}.tar.gz"
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
    chmod +x "$prefix/tywaves"

    echo
    echo "tywaves installed to ${prefix}/tywaves"
    echo
    echo "Add to PATH or set TYWAVES:"
    echo "  export TYWAVES=${prefix}/tywaves"
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

tarball="/tmp/tywaves-${platform}${release_tag:+-${release_tag}}.tar.gz"

echo "=== Building tywaves ==="
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

    echo "Extracting /opt/tywaves/bin/tywaves..."
    cid=$(docker create "$image")
    docker cp "$cid:/opt/tywaves/bin/tywaves" "$tarball.tywaves"
    docker rm "$cid" > /dev/null

    echo "Packaging..."
    tar -czf "$tarball" \
        -C "$(dirname "$tarball.tywaves")" "$(basename "$tarball.tywaves")" \
        --transform "s/$(basename "$tarball.tywaves")/tywaves/"
    rm -f "$tarball.tywaves"
else
    set -a; . tools/versions.env; set +a
    workdir="$(pwd)/.cache/tywaves-build"

    echo "  Mode:       source"
    echo "  Source URL: $TYWAVES_URL"
    echo "  Source SHA: $TYWAVES_REV"
    echo "  Work dir:   $workdir"

    if ! command -v cargo &>/dev/null; then
        echo "cargo not found; install Rust 1.75+ from https://rustup.rs/" >&2
        exit 1
    fi

    if [[ ! -d "$workdir/surfer-tywaves/.git" ]]; then
        rm -rf "$workdir/surfer-tywaves"
        mkdir -p "$workdir"
        echo "Cloning surfer-tywaves (shallow, single commit)..."
        git init "$workdir/surfer-tywaves"
        git -C "$workdir/surfer-tywaves" remote add origin "$TYWAVES_URL"
        git -C "$workdir/surfer-tywaves" fetch --depth=1 origin "$TYWAVES_REV"
        git -C "$workdir/surfer-tywaves" checkout FETCH_HEAD
    fi

    echo "Building (cargo build --release --locked, ~5-10 min cold)..."
    (cd "$workdir/surfer-tywaves" && cargo build --release --locked)

    echo "Packaging..."
    cp "$workdir/surfer-tywaves/target/release/surfer-tywaves" /tmp/tywaves-strip
    strip /tmp/tywaves-strip || true
    tar -czf "$tarball" -C /tmp tywaves-strip --transform 's/tywaves-strip/tywaves/'
    rm -f /tmp/tywaves-strip
fi

echo
echo "Built: $tarball ($(du -h "$tarball" | cut -f1))"

# ---- release (optional) ----------------------------------------------------
if [[ -n "$release_tag" ]]; then
    if ! command -v gh &>/dev/null; then
        echo "gh CLI not found; install from https://cli.github.com/" >&2
        exit 2
    fi
    echo "Uploading to GitHub Release $release_tag..."
    # Reuse an existing release if firtool already created one; otherwise create.
    if gh release view "$release_tag" > /dev/null 2>&1; then
        gh release upload "$release_tag" "$tarball" --clobber
    else
        gh release create "$release_tag" "$tarball" \
            --title "uhdi $release_tag" \
            --notes "Prebuilt tywaves (surfer fork) for $platform.

$([[ "$from_docker" -eq 1 ]] \
    && echo 'Extracted from Docker image (same as CI).' \
    || echo "Built from $TYWAVES_URL @ $TYWAVES_REV")"
    fi
    echo "Done: https://github.com/$(git config --get remote.origin.url \
        | sed 's|.*github.com[:/]\(.*\)\.git|\1|')/releases/tag/$release_tag"
fi
