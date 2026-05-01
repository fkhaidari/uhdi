#!/usr/bin/env nu
# Release or install modified firtool (--emit-uhdi).

use ../lib/common.nu *

const REPO = "fkhaidari/uhdi"

# ---- install ---------------------------------------------------------------

# Download a prebuilt firtool tarball from a fkhaidari/uhdi release.
def "main install" [
  --tag: string = "" # release tag (default: latest)
  --prefix: path = "" # install prefix (default: ~/.local/bin)
] {
  let platform = (detect-platform)
  let prefix_path = if ($prefix | is-empty) { $env.HOME | path join ".local/bin" } else { $prefix }
  let resolved_tag = (resolve-release-tag $REPO $tag)

  let asset_name = $"firtool-($platform)-($resolved_tag).tar.gz"
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
    chmod +x ($prefix_path | path join "firtool")
  } catch {|e|
    rm -rf $tmpdir
    error make {msg: $"install failed: ($e.msg)"}
  }
  rm -rf $tmpdir

  print ""
  print $"firtool installed to ($prefix_path)/firtool"
  print ""
  print "Add to PATH or set FIRTOOL:"
  print $"  export FIRTOOL=($prefix_path)/firtool"
  let path_segments = ($env.PATH | split row (char esep))
  if ($prefix_path not-in $path_segments) {
    print $"  export PATH=\"($prefix_path):$PATH\""
  }
}

# ---- build -----------------------------------------------------------------

# Build a firtool tarball; with --release <tag>, also publish.
def "main build" [
  --release: string = "" # GitHub Release tag to publish to
  --from-docker # extract from prebuilt uhdi-tools image
] {
  let platform = (detect-platform)
  let suffix = if ($release | is-empty) { "" } else { $"-($release)" }
  let tarball = $"/tmp/firtool-($platform)($suffix).tar.gz"

  print "=== Building firtool ==="
  print $"  Platform:   ($platform)"

  let versions = (load-env ($REPO_ROOT | path join "tools/versions.env"))

  if $from_docker {
    let image = (image-ref)
    print $"  Mode:       docker"
    print $"  Image:      ($image)"
    let staged = $"($tarball).firtool"
    docker-extract $image "/opt/circt/bin/firtool" $staged

    print "Packaging..."
    ^tar -czf $tarball -C ($staged | path dirname) ($staged | path basename) \
    $"--transform=s/($staged | path basename)/firtool/"
    rm -f $staged
  } else {
    let workdir = ($REPO_ROOT | path join ".cache/firtool-build")

    print $"  Mode:       source"
    print $"  CIRCT URL:  ($versions.CIRCT_URL)"
    print $"  CIRCT SHA:  ($versions.CIRCT_REV)"
    print $"  Work dir:   ($workdir)"

    let circt_dir = ($workdir | path join "circt")
    if not ($circt_dir | path join ".git" | path exists) {
      rm -rf $circt_dir
      mkdir $workdir
      print "Cloning circt (shallow, single commit)..."
      ^git init $circt_dir
      ^git -C $circt_dir remote add origin $versions.CIRCT_URL
      ^git -C $circt_dir fetch --depth=1 origin $versions.CIRCT_REV
      ^git -C $circt_dir checkout FETCH_HEAD
      ^git -C $circt_dir submodule update --init --depth=1 llvm
    }

    let build_dir = ($circt_dir | path join "build")
    if not ($build_dir | path join "bin/firtool" | path exists) {
      print "Configuring cmake..."
      let cmake_args = [
        "-G"
        "Ninja"
        "-S"
        ($circt_dir | path join "llvm/llvm")
        "-B"
        $build_dir
        "-DCMAKE_BUILD_TYPE=Release"
        "-DLLVM_ENABLE_PROJECTS=mlir"
        "-DLLVM_EXTERNAL_PROJECTS=circt"
        $"-DLLVM_EXTERNAL_CIRCT_SOURCE_DIR=($circt_dir)"
        "-DLLVM_TARGETS_TO_BUILD=host"
        "-DLLVM_ENABLE_ASSERTIONS=OFF"
        "-DLLVM_BUILD_EXAMPLES=OFF"
        "-DLLVM_INCLUDE_TESTS=OFF"
        "-DLLVM_INCLUDE_BENCHMARKS=OFF"
        "-DCIRCT_LLHD_SIM_ENABLED=OFF"
        "-DCIRCT_BINDINGS_PYTHON_ENABLED=OFF"
      ]
      ^cmake ...$cmake_args

      print "Building firtool (this may take 30-60 min)..."
      ^cmake --build $build_dir --target firtool
    }

    print "Packaging..."
    cp ($build_dir | path join "bin/firtool") /tmp/firtool-strip
    try { ^strip /tmp/firtool-strip } catch { print "  (strip failed; continuing)" }
    ^tar -czf $tarball -C /tmp firtool-strip --transform=s/firtool-strip/firtool/
    rm -f /tmp/firtool-strip
  }

  print ""
  print $"Built: ($tarball) \((human-size $tarball)\)"

  if not ($release | is-empty) {
    let source_note = if $from_docker {
      "Extracted from Docker image (same as CI)."
    } else {
      $"Built from ($versions.CIRCT_URL) @ ($versions.CIRCT_REV)"
    }
    gh-publish-tarball $release $tarball $"firtool ($release)" $"Prebuilt firtool with --emit-uhdi for ($platform).\n\n($source_note)"
  }
}

def main [] { main build }
