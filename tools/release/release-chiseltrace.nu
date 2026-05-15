#!/usr/bin/env nu
# Release or install the chiseltrace PDG slicer + Tauri GUI viewer.
# Ships two binaries in one tarball: `chiseltrace-cli` (consume the
# output of uhdi-to-pdg; slice / DynPDG / convert-to-source) and
# `chiseltrace` (Tauri GUI -- same graph rendered interactively).
#
# `build --release <tag>` uploads with --clobber so the tarball can
# ride alongside firtool + hgdb-py + tywaves on the same fkhaidari/uhdi
# release tag.

use ../lib/common.nu *

const REPO = "fkhaidari/uhdi"

# ---- install ---------------------------------------------------------------

# Download a prebuilt chiseltrace tarball from a fkhaidari/uhdi release.
# Mirrors release-tywaves.nu's install shape; consumers normally invoke
# this via `tools/install.sh chiseltrace` rather than directly.
def "main install" [
  --tag: string = "" # release tag (default: latest)
  --prefix: path = "" # install prefix (default: ~/.local/bin)
] {
  let platform = (detect-platform)
  let prefix_path = if ($prefix | is-empty) { $env.HOME | path join ".local/bin" } else { $prefix }
  let resolved_tag = (resolve-release-tag $REPO $tag)

  let asset_name = $"chiseltrace-($platform)-($resolved_tag).tar.gz"
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
    chmod +x ($prefix_path | path join "chiseltrace-cli")
    let gui = ($prefix_path | path join "chiseltrace")
    if ($gui | path exists) { chmod +x $gui }
  } catch {|e|
    rm -rf $tmpdir
    error make {msg: $"install failed: ($e.msg)"}
  }
  rm -rf $tmpdir

  print ""
  print $"chiseltrace-cli installed to ($prefix_path)/chiseltrace-cli"
  if (($prefix_path | path join "chiseltrace") | path exists) {
    print $"chiseltrace     installed to ($prefix_path)/chiseltrace"
  }
  print ""
  print "Add to PATH or set CHISELTRACE:"
  print $"  export CHISELTRACE=($prefix_path)/chiseltrace-cli"
  let path_segments = ($env.PATH | split row (char esep))
  if ($prefix_path not-in $path_segments) {
    print $"  export PATH=\"($prefix_path):$PATH\""
  }
}

# ---- build -----------------------------------------------------------------

# Build a chiseltrace tarball (CLI + GUI); with --release <tag>, also publish.
#
# GUI build needs Tauri CLI + npm + system libs (webkit2gtk-4.1, librsvg2,
# libsoup-3.0). The script preflights these and surfaces a single actionable
# error when missing, rather than letting cargo fail mid-build.
def "main build" [
  --release: string = ""
  --from-docker          # placeholder: docker recipe doesn't include chiseltrace yet
] {
  let platform = (detect-platform)
  let suffix = if ($release | is-empty) { "" } else { $"-($release)" }
  let tarball = $"/tmp/chiseltrace-($platform)($suffix).tar.gz"

  print "=== Building chiseltrace ==="
  print $"  Platform:   ($platform)"

  # chiseltrace pin lives in tools/release/chiseltrace-pin.env, not in
  # tools/versions.env -- keeps the uhdi-tools image-tag hash stable
  # across chiseltrace bumps.
  let versions = (load-env ($REPO_ROOT | path join "tools/release/chiseltrace-pin.env"))

  if $from_docker {
    # The uhdi-tools image deliberately does not include chiseltrace
    # (would couple chiseltrace bumps to the bench-image rebake).
    # Fall through to source build rather than producing a stub.
    error make {msg: "chiseltrace is not in the uhdi-tools image by design; run without --from-docker for a source build"}
  }

  let workdir = ($REPO_ROOT | path join ".cache/chiseltrace-build")

  print $"  Mode:       source"
  print $"  Source URL: ($versions.CHISELTRACE_URL)"
  print $"  Source SHA: ($versions.CHISELTRACE_REV)"
  print $"  Work dir:   ($workdir)"

  preflight-build

  let src_dir = ($workdir | path join "chiseltrace")
  if not ($src_dir | path join ".git" | path exists) {
    rm -rf $src_dir
    mkdir $workdir
    print "Cloning chiseltrace (shallow, single commit)..."
    ^git init $src_dir
    ^git -C $src_dir remote add origin $versions.CHISELTRACE_URL
    ^git -C $src_dir fetch --depth=1 origin $versions.CHISELTRACE_REV
    ^git -C $src_dir checkout FETCH_HEAD
  }

  # CLI: standalone cargo package, no Tauri deps. Fast (~3-5 min cold).
  print "Building chiseltrace-cli (cargo build --release --package chiseltrace-cli)..."
  ^cargo build --release --locked --manifest-path ($src_dir | path join "Cargo.toml") --package chiseltrace-cli

  # GUI: Tauri build -- npm install + vite build + cargo. ~10-15 min cold.
  # `--no-bundle` skips .deb/.AppImage packaging; we ship the raw binary.
  print "Installing GUI frontend deps (npm install)..."
  ^npm --prefix ($src_dir | path join "gui") install --silent

  print "Building chiseltrace GUI (cargo tauri build --no-bundle, ~10-15 min cold)..."
  with-env {CARGO_TARGET_DIR: ($src_dir | path join "target")} {
    cd ($src_dir | path join "gui/src-tauri")
    ^cargo tauri build --no-bundle
    cd $REPO_ROOT
  }

  let cli_bin = ($src_dir | path join "target/release/chiseltrace-cli")
  let gui_bin = ($src_dir | path join "target/release/chiseltrace")
  if not ($cli_bin | path exists) {
    error make {msg: $"chiseltrace-cli not produced at ($cli_bin)"}
  }
  if not ($gui_bin | path exists) {
    error make {msg: $"chiseltrace GUI not produced at ($gui_bin)"}
  }

  print "Packaging..."
  let stage = (mktemp -d | str trim)
  cp $cli_bin ($stage | path join "chiseltrace-cli")
  cp $gui_bin ($stage | path join "chiseltrace")
  try {
    ^strip ($stage | path join "chiseltrace-cli")
    ^strip ($stage | path join "chiseltrace")
  } catch { print "  (strip failed; continuing)" }
  ^tar -czf $tarball -C $stage chiseltrace-cli chiseltrace
  rm -rf $stage

  print ""
  print $"Built: ($tarball) \((human-size $tarball)\)"

  if not ($release | is-empty) {
    let source_note = $"Built from ($versions.CHISELTRACE_URL) @ ($versions.CHISELTRACE_REV)"
    gh-publish-tarball $REPO $release $tarball $"uhdi ($release)" $"Prebuilt chiseltrace \(PDG CLI + Tauri GUI\) for ($platform).\n\n($source_note)"
  }
}

# Surface missing build deps up front -- cargo's "tauri-cli not installed"
# error is buried in a 200-line build log; a check here is friendlier.
def preflight-build [] {
  if (which cargo | is-empty) {
    error make {msg: "cargo not found; install Rust 1.75+ from https://rustup.rs/"}
  }
  if (which npm | is-empty) {
    error make {msg: "npm not found; needed for the Tauri frontend (apt install npm or use nvm)"}
  }
  # `cargo tauri` is provided by `cargo install tauri-cli`. We can't
  # detect it via `which` (it's a cargo subcommand); probe directly.
  let tauri_ok = (try { ^cargo tauri --version o> /dev/null e> /dev/null; true } catch { false })
  if not $tauri_ok {
    error make {msg: "cargo tauri subcommand missing; install with: cargo install tauri-cli --version '^2.0.0' --locked"}
  }
}

def main [] { main build }
