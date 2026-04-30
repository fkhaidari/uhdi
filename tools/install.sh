#!/usr/bin/env bash
# Install the uhdi toolchain (firtool, hgdb-py, tywaves) into a local prefix
# and print a build-tool snippet for the fkhaidari/chisel JitPack coord.
#
# Sources every binary from a GitHub Release. No Docker required on the
# consumer side. Runs from a checkout or via curl-pipe:
#
#   curl -fsSL https://raw.githubusercontent.com/fkhaidari/uhdi/main/tools/install.sh \
#       | bash -s -- all
#
# Subcommands (default: all):
#   firtool       circt firtool with --emit-uhdi (fkhaidari/uhdi GH Release)
#   hgdb-py       hgdb python bindings: toml2hgdb + _hgdb C ext
#                 (fkhaidari/uhdi GH Release, linux-x86_64 only)
#   chisel        print build.mill / build.sbt snippet for the JitPack coord
#                 (fkhaidari/chisel) -- writes nothing to disk
#   tywaves       tywaves waveform viewer (fkhaidari/uhdi GH Release)
#   all           every component above
#
# Flags:
#   --prefix DIR        install root (default: $HOME/.local/uhdi-tools)
#   --release-tag TAG   fkhaidari/uhdi tag for firtool + hgdb-py + tywaves
#                       (default: latest)
#   --chisel-tag TAG    fkhaidari/chisel JitPack tag (default: latest *-uhdi)
#   --force             overwrite existing files
#   -h, --help          show this help
#
# Auth: public releases work without auth. Set $GITHUB_TOKEN to avoid
# the unauthenticated rate limit (60 req/hr per IP).
set -euo pipefail

# ---- arg parse --------------------------------------------------------------
cmd="${1:-all}"
case "$cmd" in
    firtool|hgdb-py|chisel|tywaves|all) shift ;;
    -h|--help) sed -n '2,/^set -euo/p' "$0" | head -n -1; exit 0 ;;
    -*|"") cmd="all" ;;
    *) echo "unknown subcommand: $cmd" >&2; exit 2 ;;
esac

prefix="${HOME}/.local/uhdi-tools"
release_tag=""
chisel_tag=""
force=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --prefix)       prefix="$2";       shift 2 ;;
        --release-tag)  release_tag="$2";  shift 2 ;;
        --chisel-tag)   chisel_tag="$2";   shift 2 ;;
        --force)        force=1;           shift ;;
        -h|--help)      sed -n '2,/^set -euo/p' "$0" | head -n -1; exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

# Treat "latest" as the default (= use /releases/latest endpoint).
[[ "$release_tag" == "latest" ]] && release_tag=""

# ---- platform ---------------------------------------------------------------
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

# ---- shared helpers ---------------------------------------------------------
work_root=$(mktemp -d)
trap 'rm -rf "$work_root"' EXIT

curl_args=(-fsSL)
if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    curl_args+=(-H "Authorization: Bearer $GITHUB_TOKEN")
fi

# Resolve a release tag: empty -> latest. Returns the resolved tag on stdout.
resolve_tag() {
    local repo="$1" tag="$2"
    if [[ -n "$tag" ]]; then
        echo "$tag"
        return
    fi
    local resolved
    resolved=$(curl "${curl_args[@]}" \
        "https://api.github.com/repos/${repo}/releases/latest" \
        | grep '"tag_name":' \
        | head -1 \
        | sed -E 's/.*"tag_name": *"([^"]+)".*/\1/')
    if [[ -z "$resolved" ]]; then
        echo "could not resolve latest release for $repo" >&2
        exit 1
    fi
    echo "$resolved"
}

# Find the first asset URL whose name matches a glob in a release. Empty
# stdout if no match or the endpoint 4xx's (no python traceback on failure).
find_asset_url() {
    local repo="$1" tag="$2" pattern="$3"
    curl "${curl_args[@]}" \
        "https://api.github.com/repos/${repo}/releases/tags/${tag}" 2>/dev/null \
        | python3 -c "
import json, sys, fnmatch
try:
    data = json.load(sys.stdin)
except (json.JSONDecodeError, ValueError):
    sys.exit(0)
for asset in data.get('assets', []):
    if fnmatch.fnmatch(asset['name'], sys.argv[1]):
        print(asset['browser_download_url'])
        break
" "$pattern" || true
}

# Download a release asset to a directory. Picks the first asset whose name
# matches the pattern. Prints the local path on stdout.
dl_release_asset() {
    local repo="$1" tag="$2" pattern="$3" dest_dir="$4"
    local url filename
    url=$(find_asset_url "$repo" "$tag" "$pattern")
    if [[ -z "$url" ]]; then
        echo "no asset matching '$pattern' in $repo@$tag" >&2
        return 1
    fi
    filename=$(basename "$url")
    mkdir -p "$dest_dir"
    echo "  Download:   $url" >&2
    curl "${curl_args[@]}" -o "$dest_dir/$filename" "$url"
    echo "$dest_dir/$filename"
}

ensure_writable() {
    local target="$1"
    if [[ -e "$target" && "$force" -eq 0 ]]; then
        echo "exists: $target (use --force to overwrite)" >&2
        return 1
    fi
    rm -rf "$target"
}

# ---- firtool ----------------------------------------------------------------
install_firtool() {
    echo "=== firtool ==="
    local repo="fkhaidari/uhdi"
    local tag; tag=$(resolve_tag "$repo" "$release_tag")
    echo "  Repo:       $repo"
    echo "  Tag:        $tag"
    echo "  Platform:   $platform"

    local target="$prefix/bin/firtool"
    ensure_writable "$target" || return 1

    local tmp="$work_root/firtool"
    local tarball
    tarball=$(dl_release_asset "$repo" "$tag" \
        "firtool-${platform}-*.tar.gz" "$tmp")

    mkdir -p "$prefix/bin"
    tar -xzf "$tarball" -C "$prefix/bin"
    chmod +x "$prefix/bin/firtool"
    echo "  Installed:  $prefix/bin/firtool"
}

# ---- hgdb-py ----------------------------------------------------------------
install_hgdb_py() {
    echo "=== hgdb-py ==="
    if [[ "$platform" != "linux-x86_64" ]]; then
        echo "  hgdb-py prebuilt is linux-x86_64 only (got: $platform)" >&2
        echo "  build from source: tools/release/release-hgdb-py.sh build" >&2
        return 1
    fi

    local repo="fkhaidari/uhdi"
    local tag; tag=$(resolve_tag "$repo" "$release_tag")
    echo "  Repo:       $repo"
    echo "  Tag:        $tag"

    local target="$prefix/lib/hgdb"
    ensure_writable "$target" || return 1

    local tmp="$work_root/hgdb-py"
    local tarball
    tarball=$(dl_release_asset "$repo" "$tag" \
        "hgdb-py-linux-x86_64-*.tar.gz" "$tmp")

    mkdir -p "$prefix/lib/hgdb"
    tar -xzf "$tarball" -C "$prefix/lib/hgdb"
    echo "  Installed:  $prefix/lib/hgdb/bindings/python"
}

# ---- chisel (snippet only) --------------------------------------------------
install_chisel() {
    echo "=== chisel (JitPack snippet) ==="
    local tag="$chisel_tag"
    if [[ -z "$tag" || "$tag" == "latest" ]]; then
        # fkhaidari/chisel ships JitPack tags via release-chisel.sh, which
        # only creates git tags (no GitHub Releases). Query /tags first;
        # fall back to /releases for forward compatibility.
        tag=$(curl "${curl_args[@]}" \
            "https://api.github.com/repos/fkhaidari/chisel/tags?per_page=100" \
            | grep '"name":' \
            | sed -E 's/.*"name": *"([^"]+)".*/\1/' \
            | grep -- '-uhdi$' \
            | head -1 || true)
        if [[ -z "$tag" ]]; then
            tag=$(curl "${curl_args[@]}" \
                "https://api.github.com/repos/fkhaidari/chisel/releases?per_page=30" \
                | grep '"tag_name":' \
                | sed -E 's/.*"tag_name": *"([^"]+)".*/\1/' \
                | grep -- '-uhdi$' \
                | head -1 || true)
        fi
        if [[ -z "$tag" ]]; then
            echo "  no *-uhdi tag found in fkhaidari/chisel; printing placeholder" >&2
            tag="<chisel-tag>"
        fi
    fi
    echo "  Tag:        $tag"
    echo
    cat <<MILL
# --- Mill (build.mill, Mill 0.11+ / 1.x) -----------------------------------
import coursier.maven.MavenRepository
def repositoriesTask = Task.Anon {
    super.repositoriesTask() ++ Seq(MavenRepository("https://jitpack.io"))
}
// in your ScalaModule:
def mvnDeps = Seq(
    ivy"com.github.fkhaidari.chisel::chisel:${tag}",
    ivy"com.github.fkhaidari.chisel:chisel-plugin_2.13.18:${tag}",
)

# --- sbt (build.sbt) -------------------------------------------------------
resolvers += "jitpack" at "https://jitpack.io"
libraryDependencies ++= Seq(
    "com.github.fkhaidari.chisel" %% "chisel" % "${tag}",
    "com.github.fkhaidari.chisel" % "chisel-plugin_2.13.18" % "${tag}",
)

# --- scala-cli -------------------------------------------------------------
//> using repository "https://jitpack.io"
//> using dep "com.github.fkhaidari.chisel::chisel:${tag}"
//> using dep "com.github.fkhaidari.chisel:chisel-plugin_2.13.18:${tag}"
MILL
}

# ---- tywaves (surfer fork; binary distributed as `tywaves`) -----------------
install_tywaves() {
    echo "=== tywaves ==="
    local repo="fkhaidari/uhdi"
    local tag; tag=$(resolve_tag "$repo" "$release_tag")
    echo "  Repo:       $repo"
    echo "  Tag:        $tag"
    echo "  Platform:   $platform"

    local target="$prefix/bin/tywaves"
    ensure_writable "$target" || return 1

    local tmp="$work_root/tywaves"
    local tarball
    tarball=$(dl_release_asset "$repo" "$tag" \
        "tywaves-${platform}-*.tar.gz" "$tmp")

    mkdir -p "$prefix/bin"
    tar -xzf "$tarball" -C "$prefix/bin"
    chmod +x "$prefix/bin/tywaves"
    echo "  Installed:  $prefix/bin/tywaves"
}

# ---- end-of-run summary -----------------------------------------------------
print_env_hints() {
    local did="$1"
    echo
    echo "=== Done ==="
    if [[ "$did" == *firtool* ]]; then
        echo "  export FIRTOOL=\"$prefix/bin/firtool\""
    fi
    if [[ "$did" == *hgdb-py* ]]; then
        echo "  export HGDB_PY=\"$prefix/lib/hgdb/bindings/python\""
    fi
    if [[ "$did" == *tywaves* ]]; then
        echo "  export TYWAVES=\"$prefix/bin/tywaves\""
    fi
    if [[ "$did" == *firtool* || "$did" == *tywaves* ]]; then
        if [[ ":$PATH:" != *":$prefix/bin:"* ]]; then
            echo "  export PATH=\"$prefix/bin:\$PATH\""
        fi
    fi
}

# ---- dispatch ---------------------------------------------------------------
did=""
case "$cmd" in
    firtool)  install_firtool;  did="firtool" ;;
    hgdb-py)  install_hgdb_py;  did="hgdb-py" ;;
    chisel)   install_chisel ;;
    tywaves)  install_tywaves;  did="tywaves" ;;
    all)
        install_firtool;          did="$did firtool"
        install_hgdb_py || true;  did="$did hgdb-py"
        install_chisel || true
        install_tywaves || true;  did="$did tywaves"
        ;;
esac

print_env_hints "$did"
