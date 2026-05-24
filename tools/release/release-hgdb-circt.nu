#!/usr/bin/env nu
# Build or install hgdb-circt firtool (legacy --hgdb=<file> flag, LLVM-16 era).

use ../lib/common.nu *

const REPO = "fkhaidari/uhdi"

# ---- install ---------------------------------------------------------------

# Download a prebuilt hgdb-circt firtool tarball from a fkhaidari/uhdi release.
def "main install" [
  --tag: string = ""    # release tag (default: latest)
  --prefix: path = ""   # install prefix (default: ~/.local/uhdi-tools)
] {
  let platform = (detect-platform)
  let prefix_path = if ($prefix | is-empty) { $env.HOME | path join ".local/uhdi-tools" } else { $prefix }
  let resolved_tag = (resolve-release-tag $REPO $tag)

  let asset_name = $"hgdb-circt-firtool-($platform)-($resolved_tag).tar.gz"
  let asset_url = $"https://github.com/($REPO)/releases/download/($resolved_tag)/($asset_name)"

  print $"Platform:  ($platform)"
  print $"Release:   ($resolved_tag)"
  print $"Download:  ($asset_url)"

  let tmpdir = (mktemp -d | str trim)
  try {
    print "Downloading..."
    ^curl -fsSL $asset_url -o ($tmpdir | path join $asset_name)

    print $"Extracting to ($prefix_path)/bin..."
    mkdir ($prefix_path | path join "bin")
    ^tar -xzf ($tmpdir | path join $asset_name) -C ($prefix_path | path join "bin")
    chmod +x ($prefix_path | path join "bin/hgdb-circt-firtool")
  } catch {|e|
    rm -rf $tmpdir
    error make {msg: $"install failed: ($e.msg)"}
  }
  rm -rf $tmpdir

  print ""
  print $"hgdb-circt firtool installed to ($prefix_path)/bin/hgdb-circt-firtool"
  print ""
  print "Set env var for bench:"
  print $"  export HGDB_CIRCT_FIRTOOL=($prefix_path)/bin/hgdb-circt-firtool"
}

# ---- build -----------------------------------------------------------------

# Build hgdb-circt firtool from source; with --release <tag>, also publish.
def "main build" [
  --release: string = ""  # GitHub Release tag to publish to
  --from-docker           # extract from prebuilt uhdi-tools image
] {
  let platform = (detect-platform)
  let suffix = if ($release | is-empty) { "" } else { $"-($release)" }
  let tarball = $"/tmp/hgdb-circt-firtool-($platform)($suffix).tar.gz"

  print "=== Building hgdb-circt firtool ==="
  print $"  Platform:   ($platform)"

  let versions = (load-env ($REPO_ROOT | path join "tools/versions.env"))

  if $from_docker {
    let image = (image-ref)
    print $"  Mode:       docker"
    print $"  Image:      ($image)"
    let staged = $"($tarball).hgdb-circt-firtool"
    docker-extract $image "/opt/hgdb-circt/bin/firtool" $staged

    print "Packaging..."
    ^tar -czf $tarball -C ($staged | path dirname) ($staged | path basename) \
      $"--transform=s/($staged | path basename)/hgdb-circt-firtool/"
    rm -f $staged
  } else {
    let workdir = ($REPO_ROOT | path join ".cache/hgdb-circt-build")

    print $"  Mode:       source"
    print $"  HGDB_CIRCT URL:  ($versions.HGDB_CIRCT_URL)"
    print $"  HGDB_CIRCT SHA:  ($versions.HGDB_CIRCT_REV)"
    print $"  LLVM URL:        ($versions.HGDB_CIRCT_LLVM_URL)"
    print $"  LLVM SHA:        ($versions.HGDB_CIRCT_LLVM_REV)"
    print $"  Work dir:        ($workdir)"

    let hgdb_circt_dir = ($workdir | path join "hgdb-circt")
    if not ($hgdb_circt_dir | path join ".git" | path exists) {
      rm -rf $hgdb_circt_dir
      mkdir $workdir
      print "Cloning hgdb-circt (shallow, single commit)..."
      ^git init $hgdb_circt_dir
      ^git -C $hgdb_circt_dir remote add origin $versions.HGDB_CIRCT_URL
      ^git -C $hgdb_circt_dir fetch --depth=1 origin $versions.HGDB_CIRCT_REV
      ^git -C $hgdb_circt_dir checkout FETCH_HEAD

      print "Cloning hgdb-circt LLVM (shallow, single commit)..."
      let llvm_dir = ($hgdb_circt_dir | path join "llvm")
      ^git init $llvm_dir
      ^git -C $llvm_dir remote add origin $versions.HGDB_CIRCT_LLVM_URL
      ^git -C $llvm_dir fetch --depth=1 origin $versions.HGDB_CIRCT_LLVM_REV
      ^git -C $llvm_dir checkout FETCH_HEAD
    }

    let build_dir = ($hgdb_circt_dir | path join "build")
    if not ($build_dir | path join "bin/firtool" | path exists) {
      print "Configuring cmake (LLVM-16 era unified build)..."
      let cmake_args = [
        "-G"
        "Ninja"
        "-S"
        ($hgdb_circt_dir | path join "llvm/llvm")
        "-B"
        $build_dir
        "-DCMAKE_BUILD_TYPE=Release"
        "-DLLVM_ENABLE_PROJECTS=mlir"
        "-DLLVM_EXTERNAL_PROJECTS=circt"
        $"-DLLVM_EXTERNAL_CIRCT_SOURCE_DIR=($hgdb_circt_dir)"
        "-DLLVM_TARGETS_TO_BUILD=host"
        "-DLLVM_ENABLE_ASSERTIONS=OFF"
        "-DLLVM_BUILD_EXAMPLES=OFF"
        "-DLLVM_INCLUDE_TESTS=OFF"
        "-DLLVM_INCLUDE_BENCHMARKS=OFF"
      ]
      ^cmake ...$cmake_args

      print "Building hgdb-circt firtool (this may take 30-60 min)..."
      ^cmake --build $build_dir --target firtool
    }

    print "Packaging..."
    cp ($build_dir | path join "bin/firtool") /tmp/hgdb-circt-firtool
    try { ^strip /tmp/hgdb-circt-firtool } catch { print "  (strip failed; continuing)" }
    ^tar -czf $tarball -C /tmp hgdb-circt-firtool
    rm -f /tmp/hgdb-circt-firtool
  }

  print ""
  print $"Built: ($tarball) \((human-size $tarball)\)"

  if not ($release | is-empty) {
    let source_note = if $from_docker {
      "Extracted from Docker image (same as CI)."
    } else {
      $"Built from ($versions.HGDB_CIRCT_URL) @ ($versions.HGDB_CIRCT_REV)"
    }
    let notes = $"Prebuilt hgdb-circt firtool \(legacy --hgdb=<file> flag\) for ($platform).\n\nSet HGDB_CIRCT_FIRTOOL to point to the extracted binary.\n\n($source_note)"
    gh-publish-tarball $REPO $release $tarball $"hgdb-circt firtool ($release)" $notes
  }
}

def main [] { main build }
