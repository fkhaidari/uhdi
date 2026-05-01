#!/usr/bin/env nu
# Release or install the hgdb python bindings (toml2hgdb + _hgdb C ext).
# Tarball layout produced -- matches what bench/runner.py expects under
# HGDB_PY=<prefix>/bindings/python:
#   <prefix>/bindings/python/{hgdb/, build/lib.<plat>-cpython-XYZ/_hgdb*.so,
#                             scripts/toml2hgdb}

use ../lib/common.nu *

const REPO = "fkhaidari/uhdi"

# ---- install ---------------------------------------------------------------

# Download a prebuilt hgdb-py tarball (linux-x86_64 only).
def "main install" [
  --tag: string = ""
  --prefix: path = "" # install prefix (default: ~/.local/hgdb)
] {
  let platform = (detect-platform)
  if $platform != "linux-x86_64" {
    error make {
      msg: $"hgdb-py prebuilt is linux-x86_64 only \(got: ($platform)\)
build from source on this host: tools/release/release-hgdb-py.nu build"
    }
  }

  let prefix_path = if ($prefix | is-empty) { $env.HOME | path join ".local/hgdb" } else { $prefix }
  let resolved_tag = (resolve-release-tag $REPO $tag)

  let asset_name = $"hgdb-py-linux-x86_64-($resolved_tag).tar.gz"
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
  } catch {|e|
    rm -rf $tmpdir
    error make {msg: $"install failed: ($e.msg)"}
  }
  rm -rf $tmpdir

  print ""
  print $"hgdb-py installed at ($prefix_path)/bindings/python"
  print ""
  print "Set HGDB_PY:"
  print $"  export HGDB_PY=($prefix_path)/bindings/python"
}

# ---- build -----------------------------------------------------------------

# Build the hgdb-py tarball; with --release <tag>, also publish.
def "main build" [
  --release: string = ""
  --from-docker
] {
  let platform = (detect-platform)
  if $platform != "linux-x86_64" {
    error make {
      msg: $"hgdb-py build is linux-x86_64 only \(got: ($platform)\)
the C extension is built against glibc; cross-compiles are out of scope"
    }
  }

  let suffix = if ($release | is-empty) { "" } else { $"-($release)" }
  let tarball = $"/tmp/hgdb-py-($platform)($suffix).tar.gz"
  let stage = $"/tmp/hgdb-py-stage.(random int 100000..999999)"

  print "=== Building hgdb-py ==="
  print $"  Platform:   ($platform)"

  let versions = (load-env ($REPO_ROOT | path join "tools/versions.env"))

  try {
    if $from_docker {
      let image = (image-ref)
      print $"  Mode:       docker"
      print $"  Image:      ($image)"
      mkdir $"($stage)/bindings"
      docker-extract $image "/opt/hgdb/bindings/python" $"($stage)/bindings/"
    } else {
      let workdir = ($REPO_ROOT | path join ".cache/hgdb-py-build")

      print $"  Mode:       source"
      print $"  HGDB URL:   ($versions.HGDB_URL)"
      print $"  HGDB SHA:   ($versions.HGDB_REV)"
      print $"  Work dir:   ($workdir)"

      let hgdb_dir = ($workdir | path join "hgdb")
      if not ($hgdb_dir | path join ".git" | path exists) {
        rm -rf $hgdb_dir
        mkdir $workdir
        print "Cloning hgdb (shallow, single commit)..."
        ^git init $hgdb_dir
        ^git -C $hgdb_dir remote add origin $versions.HGDB_URL
        ^git -C $hgdb_dir fetch --depth=1 origin $versions.HGDB_REV
        ^git -C $hgdb_dir checkout FETCH_HEAD
        ^git -C $hgdb_dir submodule update --init --recursive --depth=1
      }

      print "Building C extension..."
      cd ($hgdb_dir | path join "bindings/python")
      ^pip install --user --no-cache-dir pybind11 setuptools wheel
      ^python3 setup.py build_ext --inplace
      cd $REPO_ROOT

      print "Staging..."
      mkdir $"($stage)/bindings/python/build"
      mkdir $"($stage)/bindings/python/scripts"
      # Copy the build/lib.<plat>-cpython-XYZ/ tree (whatever name
      # python picked for this host). `ls $string` doesn't expand
      # globs, so go through `glob` for variable-fed patterns.
      for d in (glob ($hgdb_dir | path join "bindings/python/build/lib.*")) {
        ^cp -r $d $"($stage)/bindings/python/build/"
      }
      ^cp -r ($hgdb_dir | path join "bindings/python/hgdb") $"($stage)/bindings/python/"
      ^cp ($hgdb_dir | path join "bindings/python/scripts/toml2hgdb") $"($stage)/bindings/python/scripts/"
    }

    # Sanity: required files exist.
    if not ($"($stage)/bindings/python/scripts/toml2hgdb" | path exists) {
      error make {msg: "missing toml2hgdb in stage"}
    }
    let so_files = (glob $"($stage)/bindings/python/build/lib.*/_hgdb*.so")
    if ($so_files | is-empty) {
      error make {msg: "missing _hgdb C extension in stage"}
    }

    print "Packaging..."
    ^tar -czf $tarball -C $stage bindings
  } catch {|e|
    rm -rf $stage
    error make {msg: $"build failed: ($e.msg)"}
  }
  rm -rf $stage

  print ""
  print $"Built: ($tarball) \((human-size $tarball)\)"

  if not ($release | is-empty) {
    let source_note = if $from_docker {
      "Extracted from Docker image (same as CI)."
    } else {
      $"Built from ($versions.HGDB_URL) @ ($versions.HGDB_REV)"
    }
    gh-publish-tarball $release $tarball $"uhdi ($release)" $"Prebuilt hgdb python bindings for ($platform).\n\n($source_note)"
  }
}

def main [] { main build }
