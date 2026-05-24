#!/usr/bin/env nu
# Build or install hgdb-firrtl.jar (Scala FIRRTL 1.x, sbt assembly).

use ../lib/common.nu *

const REPO = "fkhaidari/uhdi"

# ---- install ---------------------------------------------------------------

# Download a prebuilt hgdb-firrtl.jar from a fkhaidari/uhdi release.
def "main install" [
  --tag: string = ""    # release tag (default: latest)
  --prefix: path = ""   # install prefix (default: ~/.local/uhdi-tools)
] {
  let prefix_path = if ($prefix | is-empty) { $env.HOME | path join ".local/uhdi-tools" } else { $prefix }
  let resolved_tag = (resolve-release-tag $REPO $tag)

  let asset_name = $"hgdb-firrtl-($resolved_tag).jar"
  let asset_url = $"https://github.com/($REPO)/releases/download/($resolved_tag)/($asset_name)"

  print $"Release:   ($resolved_tag)"
  print $"Download:  ($asset_url)"

  mkdir ($prefix_path | path join "bin")
  let dest = ($prefix_path | path join "bin/hgdb-firrtl.jar")

  try {
    print "Downloading..."
    ^curl -fsSL $asset_url -o $dest
  } catch {|e|
    rm -f $dest
    error make {msg: $"install failed: ($e.msg)"}
  }

  print ""
  print $"hgdb-firrtl.jar installed to ($dest)"
  print ""
  print "Set env var for bench (requires Java 17, not 21+):"
  print $"  export HGDB_FIRRTL_JAR=($dest)"
  print "  # java -jar $HGDB_FIRRTL_JAR --help"
}

# ---- build -----------------------------------------------------------------

# Build hgdb-firrtl.jar from source via sbt assembly; with --release <tag>, also publish.
def "main build" [
  --release: string = ""  # GitHub Release tag to publish to
  --from-docker           # extract from prebuilt uhdi-tools image
] {
  let jar_out = "/tmp/hgdb-firrtl-build-output.jar"

  print "=== Building hgdb-firrtl.jar ==="

  let versions = (load-env ($REPO_ROOT | path join "tools/versions.env"))

  if $from_docker {
    let image = (image-ref)
    print $"  Mode:   docker"
    print $"  Image:  ($image)"
    docker-extract $image "/opt/hgdb-firrtl/bin/hgdb-firrtl.jar" $jar_out
  } else {
    let workdir = ($REPO_ROOT | path join ".cache/hgdb-firrtl-build")

    print $"  Mode:       source"
    print $"  URL:        ($versions.HGDB_FIRRTL_URL)"
    print $"  SHA:        ($versions.HGDB_FIRRTL_REV)"
    print $"  Work dir:   ($workdir)"

    let hgdb_firrtl_dir = ($workdir | path join "hgdb-firrtl")
    if not ($hgdb_firrtl_dir | path join ".git" | path exists) {
      rm -rf $hgdb_firrtl_dir
      mkdir $workdir
      print "Cloning hgdb-firrtl..."
      ^git init $hgdb_firrtl_dir
      ^git -C $hgdb_firrtl_dir remote add origin $versions.HGDB_FIRRTL_URL
      ^git -C $hgdb_firrtl_dir fetch --depth=1 origin $versions.HGDB_FIRRTL_REV
      ^git -C $hgdb_firrtl_dir checkout FETCH_HEAD
    }

    let built_jar = ($hgdb_firrtl_dir | path join "bin/hgdb-firrtl.jar")
    if not ($built_jar | path exists) {
      # hgdb-firrtl pins sbt 1.4.x which calls setSecurityManager() -- broken
      # on JDK 18+. Requires JDK 17. Check before wasting 10+ min on sbt.
      let java_ver = (
        try { ^java -version e>| str trim | lines | first } catch { "unknown" }
      )
      print $"  Java:       ($java_ver)"
      print "Running sbt assembly (may take 10-20 min on first run)..."
      # --add-opens required for sbt launcher on JDK 17 w/ newer Scala compiler.
      # COURSIER_MIRRORS=/dev/null bypasses corporate Artifactory mirror.properties
      # that would route Maven Central through an intranet host (fails outside VPN).
      # sbt must run from the repo dir; use bash subshell to cd there.
      ^bash -c $"cd ($hgdb_firrtl_dir) && COURSIER_MIRRORS=/dev/null JAVA_OPTS='--add-opens=java.base/java.io=ALL-UNNAMED --add-opens=java.base/sun.nio.ch=ALL-UNNAMED' sbt --batch assembly"
    }

    cp $built_jar $jar_out
  }

  print ""
  print $"Built: ($jar_out) \((human-size $jar_out)\)"

  if not ($release | is-empty) {
    let asset_name = $"hgdb-firrtl-($release).jar"
    cp $jar_out $"/tmp/($asset_name)"
    let source_note = if $from_docker {
      "Extracted from Docker image (same as CI)."
    } else {
      $"Built from ($versions.HGDB_FIRRTL_URL) @ ($versions.HGDB_FIRRTL_REV)"
    }
    if (which gh | is-empty) {
      error make {msg: "gh CLI not found; install from https://cli.github.com/"}
    }
    let exists = (
      try {
        ^gh release view $release --repo $REPO o> /dev/null e> /dev/null
        true
      } catch { false }
    )
    let firrtl_notes = $"Prebuilt hgdb-firrtl.jar \(Scala FIRRTL 1.x, sbt assembly\).\n\nSet HGDB_FIRRTL_JAR to the downloaded jar path \(requires Java 17\).\n\n($source_note)"
    if $exists {
      ^gh release upload $release $"/tmp/($asset_name)" --repo $REPO --clobber
    } else {
      ^gh release create $release $"/tmp/($asset_name)" --repo $REPO --title $"hgdb-firrtl ($release)" --notes $firrtl_notes
    }
    rm -f $"/tmp/($asset_name)"
    print $"Done: https://github.com/($REPO)/releases/tag/($release)"
  }
}

def main [] { main build }
