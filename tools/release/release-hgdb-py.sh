#!/usr/bin/env bash
# Release or install the hgdb python bindings (toml2hgdb + _hgdb C ext).
#
# Subcommands:
#   build                  Build from source -> tarball in /tmp (linux-x86_64)
#   build --from-docker    Extract from prebuilt Docker image -> tarball in /tmp
#   install                Download prebuilt tarball from GitHub Releases
#
# With --release <tag> after a build, also publish the tarball to GitHub
# Releases (gh CLI required).
#
# Usage:
#   tools/release/release-hgdb-py.sh build --from-docker
#   tools/release/release-hgdb-py.sh build --from-docker --release v0.1.0
#   tools/release/release-hgdb-py.sh install
#   tools/release/release-hgdb-py.sh install --prefix /opt/hgdb
#   curl -fsSL https://raw.githubusercontent.com/fkhaidari/uhdi/main/tools/release/release-hgdb-py.sh | bash -s -- install
#
# Layout produced:
#   <prefix>/bindings/python/{hgdb/, build/lib.<plat>-cpython-XYZ/_hgdb*.so,
#                            scripts/toml2hgdb}
# This matches what `bench/runner.py` expects under HGDB_PY=<prefix>/bindings/python.
set -euo pipefail

cd "$(dirname "$0")/../.."

case "${1:-}" in
    -h|--help) sed -n '2,/^set -euo/p' "$0" | head -n -1; exit 0 ;;
esac

cmd="${1:-build}"
shift || true

release_tag=""
from_docker=0
prefix="${HOME}/.local/hgdb"
install_tag=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --release)      release_tag="$2"; shift 2 ;;
        --from-docker)  from_docker=1;    shift ;;
        --prefix)       prefix="$2";      shift 2 ;;
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
        x86_64|amd64)  arch="x86_64" ;;
        aarch64|arm64) arch="aarch64" ;;
        *) echo "unsupported arch: $(uname -m)" >&2; exit 1 ;;
    esac
    echo "${os}-${arch}"
}

platform=$(detect_platform)

# ---- install ---------------------------------------------------------------
if [[ "$cmd" == "install" ]]; then
    if [[ "$platform" != "linux-x86_64" ]]; then
        echo "hgdb-py prebuilt is linux-x86_64 only (got: $platform)" >&2
        echo "build from source on this host: $0 build" >&2
        exit 1
    fi
    repo="fkhaidari/uhdi"

    if [[ -z "$install_tag" ]]; then
        install_tag=$(curl -fsSL "https://api.github.com/repos/${repo}/releases/latest" \
            | grep '"tag_name":' | sed -E 's/.*"tag_name": *"([^"]+)".*/\1/')
        if [[ -z "$install_tag" ]]; then
            echo "Could not determine latest release tag; pass --tag <tag>" >&2
            exit 1
        fi
    fi

    asset_name="hgdb-py-linux-x86_64-${install_tag}.tar.gz"
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

    echo
    echo "hgdb-py installed at ${prefix}/bindings/python"
    echo
    echo "Set HGDB_PY:"
    echo "  export HGDB_PY=${prefix}/bindings/python"
    exit 0
fi

# ---- build -----------------------------------------------------------------
if [[ "$cmd" != "build" ]]; then
    echo "unknown subcommand: $cmd" >&2
    echo "use: build | install" >&2
    exit 2
fi

if [[ "$platform" != "linux-x86_64" ]]; then
    echo "hgdb-py build is linux-x86_64 only (got: $platform)" >&2
    echo "the C extension is built against glibc; cross-compiles are out of scope" >&2
    exit 1
fi

tarball="/tmp/hgdb-py-${platform}${release_tag:+-${release_tag}}.tar.gz"
stage="/tmp/hgdb-py-stage.$$"
trap 'rm -rf "$stage"' EXIT

echo "=== Building hgdb-py ==="
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

    echo "Extracting /opt/hgdb/bindings/python..."
    mkdir -p "$stage"
    cid=$(docker create "$image")
    docker cp "$cid:/opt/hgdb/bindings/python" "$stage/bindings/"
    docker rm "$cid" > /dev/null
    # docker cp puts the contents one level shallow; normalise to
    # bindings/python/...
    if [[ -d "$stage/bindings/python" ]]; then
        :
    elif [[ -d "$stage/bindings" ]]; then
        mv "$stage/bindings" "$stage/_b"
        mkdir -p "$stage/bindings"
        mv "$stage/_b" "$stage/bindings/python"
    fi
else
    set -a; . tools/versions.env; set +a
    workdir="$(pwd)/.cache/hgdb-py-build"

    echo "  Mode:       source"
    echo "  HGDB URL:   $HGDB_URL"
    echo "  HGDB SHA:   $HGDB_REV"
    echo "  Work dir:   $workdir"

    if [[ ! -d "$workdir/hgdb/.git" ]]; then
        rm -rf "$workdir/hgdb"
        mkdir -p "$workdir"
        echo "Cloning hgdb (shallow, single commit)..."
        git init "$workdir/hgdb"
        git -C "$workdir/hgdb" remote add origin "$HGDB_URL"
        git -C "$workdir/hgdb" fetch --depth=1 origin "$HGDB_REV"
        git -C "$workdir/hgdb" checkout FETCH_HEAD
        git -C "$workdir/hgdb" submodule update --init --recursive --depth=1
    fi

    echo "Building C extension..."
    pushd "$workdir/hgdb/bindings/python" > /dev/null
    pip install --user --no-cache-dir pybind11 setuptools wheel
    python3 setup.py build_ext --inplace
    popd > /dev/null

    echo "Staging..."
    mkdir -p "$stage/bindings/python/build" \
             "$stage/bindings/python/scripts"
    cp -r "$workdir/hgdb/bindings/python/build/"lib.* \
        "$stage/bindings/python/build/"
    cp -r "$workdir/hgdb/bindings/python/hgdb" \
        "$stage/bindings/python/"
    cp "$workdir/hgdb/bindings/python/scripts/toml2hgdb" \
        "$stage/bindings/python/scripts/"
fi

# Sanity: required files exist.
if [[ ! -f "$stage/bindings/python/scripts/toml2hgdb" ]]; then
    echo "missing toml2hgdb in stage" >&2
    exit 1
fi
if ! ls "$stage/bindings/python/build/"lib.*/_hgdb*.so > /dev/null 2>&1; then
    echo "missing _hgdb C extension in stage" >&2
    exit 1
fi

echo "Packaging..."
tar -czf "$tarball" -C "$stage" bindings

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
            --notes "Prebuilt hgdb python bindings for $platform.

$([[ "$from_docker" -eq 1 ]] \
    && echo 'Extracted from Docker image (same as CI).' \
    || echo "Built from $HGDB_URL @ $HGDB_REV")"
    fi
    echo "Done: https://github.com/$(git config --get remote.origin.url \
        | sed 's|.*github.com[:/]\(.*\)\.git|\1|')/releases/tag/$release_tag"
fi
