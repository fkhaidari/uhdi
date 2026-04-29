#!/usr/bin/env bash
# Release fkhaidari/chisel fork to JitPack.
# Rebases fk-sc/debug-info-release onto fk-sc/debug-info, ensures jitpack.yml,
# tags, and pushes. JitPack auto-builds on tag.
#
# With --from-docker, also extracts prebuilt jars from uhdi-tools Docker image
# into a local directory for offline use (symlink to ~/.ivy2/local).
#
# Usage:
#   tools/release/release-chisel.sh v0.1.2
#   tools/release/release-chisel.sh --from-docker v0.1.2
#   tools/release/release-chisel.sh --repo ../chisel v0.1.2
set -euo pipefail

cd "$(dirname "$0")/../.."

repo=""
tag=""
from_docker=0
source_branch="fk-sc/debug-info"
release_branch="fk-sc/debug-info-release"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo)           repo="$2";           shift 2 ;;
        --from-docker)    from_docker=1;       shift ;;
        --source)         source_branch="$2";  shift 2 ;;
        --release-branch) release_branch="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,/^set -euo/p' "$0" | head -n -1
            exit 0 ;;
        -*) echo "unknown flag: $1" >&2; exit 2 ;;
        *)  tag="$1"; shift ;;
    esac
done

if [[ -z "$tag" ]]; then
    echo "Usage: $0 [--from-docker] [--repo <path>] <version-tag>" >&2
    echo "Example: $0 v0.1.2" >&2
    exit 2
fi

# ---- resolve repo ----------------------------------------------------------
if [[ -z "$repo" ]]; then
    repo="$(pwd)/../chisel"
fi

if [[ ! -d "$repo/.git" ]]; then
    echo "Not a git repo: $repo" >&2
    echo "Pass --repo <path> to point at fkhaidari/chisel checkout" >&2
    exit 1
fi

# ---- docker extract (optional, before git ops) -----------------------------
if [[ "$from_docker" -eq 1 ]]; then
    image_tag=$(cat tools/docker/image-tag.txt)
    owner=$(git config --get remote.origin.url \
        | sed -E 's#^(git@|https?://)([^/:]+)[:/]([^/]+)/.+$#\3#' \
        | tr '[:upper:]' '[:lower:]')
    image="ghcr.io/${owner}/uhdi-tools:${image_tag}"
    out_dir="/tmp/chisel-ivy2-local-${tag}"

    echo "=== Docker: extracting prebuilt Chisel ==="
    echo "  Image:      $image"
    echo "  Output:     $out_dir"

    docker pull "$image"
    cid=$(docker create "$image")
    docker cp "$cid:/opt/ivy2-local" "$out_dir"
    docker rm "$cid" > /dev/null

    echo "Extracted. Link with:"
    echo "  ln -sf $out_dir ~/.ivy2/local"
    echo
fi

# ---- JitPack release -------------------------------------------------------
echo "=== Chisel JitPack release ==="
echo "  Repo:       $repo"
echo "  Source:     $source_branch"
echo "  Release:    $release_branch"
echo "  Tag:        $tag"

cd "$repo"

echo
echo "Fetching..."
git fetch origin

if ! git rev-parse --verify "origin/$source_branch" > /dev/null 2>&1; then
    echo "ERROR: origin/$source_branch not found" >&2
    exit 1
fi

echo "Preparing $release_branch (rebased onto $source_branch)..."
git checkout -B "$release_branch" "origin/$source_branch"

# ---- ensure jitpack.yml ----------------------------------------------------
if [[ ! -f jitpack.yml ]]; then
    cat > jitpack.yml <<'JITPACK'
jdk:
  - openjdk21
install:
  - ./mill --no-daemon -j 0 unipublish[2.13].publishLocal
  - ./mill --no-daemon -j 0 "plugin.cross[2.13.18].publishLocal"
  - |
    IVY="$HOME/.ivy2/local"
    M2="$HOME/.m2/repository"
    find "$IVY" -type d -name "jars" | while read jars_dir; do
      ver=$(basename "$(dirname "$jars_dir")")
      mod=$(basename "$(dirname "$(dirname "$jars_dir")")")
      org=$(basename "$(dirname "$(dirname "$(dirname "$jars_dir")")")")
      m2dir="$M2/$(echo "$org" | tr '.' '/')/$mod/$ver"
      mkdir -p "$m2dir"
      for f in "$jars_dir"/*; do
        [ -f "$f" ] || continue
        cp "$f" "$m2dir/$(basename "$f" .${f##*.})-${ver}.${f##*.}"
      done
      srcs_dir="$(dirname "$jars_dir")/srcs"
      [ -d "$srcs_dir" ] && for f in "$srcs_dir"/*; do
        [ -f "$f" ] || continue
        cp "$f" "$m2dir/$(basename "$f" .${f##*.})-${ver}-sources.${f##*.}"
      done
      docs_dir="$(dirname "$jars_dir")/docs"
      [ -d "$docs_dir" ] && for f in "$docs_dir"/*; do
        [ -f "$f" ] || continue
        cp "$f" "$m2dir/$(basename "$f" .${f##*.})-${ver}-javadoc.${f##*.}"
      done
      poms_dir="$(dirname "$jars_dir")/poms"
      [ -d "$poms_dir" ] && for f in "$poms_dir"/*; do
        [ -f "$f" ] || continue
        cp "$f" "$m2dir/$(basename "$f" .${f##*.})-${ver}.${f##*.}"
      done
    done
JITPACK
    git add jitpack.yml
fi

# ---- commit (if dirty) -----------------------------------------------------
if ! git diff --cached --quiet || ! git diff --quiet; then
    git add -A
    git commit -m "release: $tag"
fi

# ---- tag -------------------------------------------------------------------
if git rev-parse "$tag" > /dev/null 2>&1; then
    echo "Tag $tag already exists, moving..."
    git tag -f "$tag"
else
    git tag "$tag"
fi

# ---- push ------------------------------------------------------------------
echo
echo "Pushing $release_branch + tag $tag..."
git push origin "$release_branch" --force-with-lease
git push origin "$tag" --force

echo
echo "JitPack will build at:"
echo "  https://jitpack.io/#fkhaidari/chisel/$tag"
