#!/usr/bin/env nu
# Release or install the surfer-tywaves waveform viewer (binary: tywaves).
# `build --release <tag>` uploads with --clobber so the tarball can ride
# alongside firtool + hgdb-py on the same fkhaidari/uhdi release tag.

use ../lib/common.nu *

const REPO = "fkhaidari/uhdi"

# ---- install ---------------------------------------------------------------

# Download a prebuilt tywaves tarball from a fkhaidari/uhdi release.
def "main install" [
  --tag: string = "" # release tag (default: latest)
  --prefix: path = "" # install prefix (default: ~/.local/bin)
] {
  let platform = (detect-platform)
  let prefix_path = if ($prefix | is-empty) { $env.HOME | path join ".local/bin" } else { $prefix }
  let resolved_tag = (resolve-release-tag $REPO $tag)

  let asset_name = $"tywaves-($platform)-($resolved_tag).tar.gz"
  let asset_url = $"https://github.com/($REPO)/releases/download/($resolved_tag)/($asset_name)"

  print $"Platform:  ($platform)"
  print $"Release:   ($resolved_tag)"
  print $"Download:  ($asset_url)"

  let tmpdir = (mktemp -d | str trim)
  try {
    print "Downloading..."
    ^curl -fsSL $asset_url -o ($tmpdir | path join $asset_name)

    print $"Extracting to ($prefix_path)..."
    mkdir $prefix_path
    ^tar -xzf ($tmpdir | path join $asset_name) -C $prefix_path
    chmod +x ($prefix_path | path join "tywaves")
  } catch {|e|
    rm -rf $tmpdir
    error make {msg: $"install failed: ($e.msg)"}
  }
  rm -rf $tmpdir

  print ""
  print $"tywaves installed to ($prefix_path)/tywaves"
  print ""
  print "Add to PATH or set TYWAVES:"
  print $"  export TYWAVES=($prefix_path)/tywaves"
  let path_segments = ($env.PATH | split row (char esep))
  if ($prefix_path not-in $path_segments) {
    print $"  export PATH=\"($prefix_path):$PATH\""
  }
}

# ---- build -----------------------------------------------------------------

# Build a tywaves tarball; with --release <tag>, also publish.
def "main build" [
  --release: string = ""
  --from-docker
] {
  let platform = (detect-platform)
  let suffix = if ($release | is-empty) { "" } else { $"-($release)" }
  let tarball = $"/tmp/tywaves-($platform)($suffix).tar.gz"

  print "=== Building tywaves ==="
  print $"  Platform:   ($platform)"

  let versions = (load-env ($REPO_ROOT | path join "tools/versions.env"))

  if $from_docker {
    let image = (image-ref)
    print $"  Mode:       docker"
    print $"  Image:      ($image)"
    let staged = $"($tarball).tywaves"
    docker-extract $image "/opt/tywaves/bin/tywaves" $staged

    print "Packaging..."
    ^tar -czf $tarball -C ($staged | path dirname) ($staged | path basename) \
    $"--transform=s/($staged | path basename)/tywaves/"
    rm -f $staged
  } else {
    let workdir = ($REPO_ROOT | path join ".cache/tywaves-build")

    print $"  Mode:       source"
    print $"  Source URL: ($versions.TYWAVES_URL)"
    print $"  Source SHA: ($versions.TYWAVES_REV)"
    print $"  Work dir:   ($workdir)"

    if (which cargo | is-empty) {
      error make {msg: "cargo not found; install Rust 1.75+ from https://rustup.rs/"}
    }

    let src_dir = ($workdir | path join "surfer-tywaves")
    if not ($src_dir | path join ".git" | path exists) {
      rm -rf $src_dir
      mkdir $workdir
      print "Cloning surfer-tywaves (shallow, single commit)..."
      ^git init $src_dir
      ^git -C $src_dir remote add origin $versions.TYWAVES_URL
      ^git -C $src_dir fetch --depth=1 origin $versions.TYWAVES_REV
      ^git -C $src_dir checkout FETCH_HEAD
    }

    print "Building (cargo build --release --locked, ~5-10 min cold)..."
    cd $src_dir
    ^cargo build --release --locked

    print "Packaging..."
    cp ($src_dir | path join "target/release/surfer-tywaves") /tmp/tywaves-strip
    try { ^strip /tmp/tywaves-strip } catch { print "  (strip failed; continuing)" }
    ^tar -czf $tarball -C /tmp tywaves-strip --transform=s/tywaves-strip/tywaves/
    rm -f /tmp/tywaves-strip
  }

  print ""
  print $"Built: ($tarball) \((human-size $tarball)\)"

  if not ($release | is-empty) {
    let source_note = if $from_docker {
      "Extracted from Docker image (same as CI)."
    } else {
      $"Built from ($versions.TYWAVES_URL) @ ($versions.TYWAVES_REV)"
    }
    gh-publish-tarball $release $tarball $"uhdi ($release)" $"Prebuilt tywaves \(surfer fork\) for ($platform).\n\n($source_note)"
  }
}

def main [] { main build }
