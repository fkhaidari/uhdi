#!/usr/bin/env nu
# Release fkhaidari/chisel fork to JitPack: rebase the release branch
# onto the working branch, ensure jitpack.yml, tag, push. JitPack
# auto-builds on tag. --from-docker also extracts prebuilt jars from
# the uhdi-tools image for offline use.

use ../lib/common.nu *

# Mill publishes to ivy2-local; JitPack's Maven publisher needs ~/.m2,
# so the install script mirrors one to the other.
const JITPACK_YML = "jdk:
  - openjdk21
install:
  - ./mill --no-daemon -j 0 unipublish[2.13].publishLocal
  - ./mill --no-daemon -j 0 \"plugin.cross[2.13.18].publishLocal\"
  - |
    IVY=\"$HOME/.ivy2/local\"
    M2=\"$HOME/.m2/repository\"
    find \"$IVY\" -type d -name \"jars\" | while read jars_dir; do
      ver=$(basename \"$(dirname \"$jars_dir\")\")
      mod=$(basename \"$(dirname \"$(dirname \"$jars_dir\")\")\")
      org=$(basename \"$(dirname \"$(dirname \"$(dirname \"$jars_dir\")\")\")\")
      m2dir=\"$M2/$(echo \"$org\" | tr '.' '/')/$mod/$ver\"
      mkdir -p \"$m2dir\"
      for f in \"$jars_dir\"/*; do
        [ -f \"$f\" ] || continue
        cp \"$f\" \"$m2dir/$(basename \"$f\" .${f##*.})-${ver}.${f##*.}\"
      done
      srcs_dir=\"$(dirname \"$jars_dir\")/srcs\"
      [ -d \"$srcs_dir\" ] && for f in \"$srcs_dir\"/*; do
        [ -f \"$f\" ] || continue
        cp \"$f\" \"$m2dir/$(basename \"$f\" .${f##*.})-${ver}-sources.${f##*.}\"
      done
      docs_dir=\"$(dirname \"$jars_dir\")/docs\"
      [ -d \"$docs_dir\" ] && for f in \"$docs_dir\"/*; do
        [ -f \"$f\" ] || continue
        cp \"$f\" \"$m2dir/$(basename \"$f\" .${f##*.})-${ver}-javadoc.${f##*.}\"
      done
      poms_dir=\"$(dirname \"$jars_dir\")/poms\"
      [ -d \"$poms_dir\" ] && for f in \"$poms_dir\"/*; do
        [ -f \"$f\" ] || continue
        cp \"$f\" \"$m2dir/$(basename \"$f\" .${f##*.})-${ver}.${f##*.}\"
      done
    done
"

def main [
  tag: string # version tag, e.g. v0.1.2
  --repo: path = "" # path to fkhaidari/chisel checkout (default: ../chisel)
  --from-docker # also extract prebuilt jars from image into /tmp
  --source: string = "fk-sc/debug-info" # branch carrying the debug-info changes
  --release-branch: string = "fk-sc/debug-info-release" # branch we rebase + tag
] {
  let chisel_repo = if ($repo | is-empty) {
    ($REPO_ROOT | path dirname | path join "chisel")
  } else { $repo }

  if not ($chisel_repo | path join ".git" | path exists) {
    error make {msg: $"Not a git repo: ($chisel_repo). Pass --repo <path> to point at fkhaidari/chisel checkout."}
  }

  # ---- docker extract (optional, before git ops) -------------------------
  if $from_docker {
    let image = (image-ref)
    let out_dir = $"/tmp/chisel-ivy2-local-($tag)"

    print "=== Docker: extracting prebuilt Chisel ==="
    print $"  Image:      ($image)"
    print $"  Output:     ($out_dir)"
    docker-extract $image "/opt/ivy2-local" $out_dir

    print "Extracted. Link with:"
    print $"  ln -sf ($out_dir) ~/.ivy2/local"
    print ""
  }

  # ---- JitPack release ---------------------------------------------------
  print "=== Chisel JitPack release ==="
  print $"  Repo:       ($chisel_repo)"
  print $"  Source:     ($source)"
  print $"  Release:    ($release_branch)"
  print $"  Tag:        ($tag)"

  cd $chisel_repo

  print ""
  print "Fetching..."
  ^git fetch origin

  let source_ok = (
    try {
      ^git rev-parse --verify $"origin/($source)" o> /dev/null e> /dev/null
      true
    } catch { false }
  )
  if not $source_ok {
    error make {msg: $"origin/($source) not found"}
  }

  print $"Preparing ($release_branch) \(rebased onto ($source)\)..."
  ^git checkout -B $release_branch $"origin/($source)"

  # ---- ensure jitpack.yml ------------------------------------------------
  if not ("jitpack.yml" | path exists) {
    $JITPACK_YML | save -f jitpack.yml
    ^git add jitpack.yml
  }

  # ---- commit (if dirty) -------------------------------------------------
  let dirty = (
    try {
      ^git diff --cached --quiet
      ^git diff --quiet
      false
    } catch { true }
  )
  if $dirty {
    ^git add -A
    ^git commit -m $"release: ($tag)"
  }

  # ---- tag ---------------------------------------------------------------
  let tag_exists = (
    try {
      ^git rev-parse $tag o> /dev/null e> /dev/null
      true
    } catch { false }
  )
  if $tag_exists {
    print $"Tag ($tag) already exists, moving..."
    ^git tag -f $tag
  } else {
    ^git tag $tag
  }

  # ---- push --------------------------------------------------------------
  print ""
  print $"Pushing ($release_branch) + tag ($tag)..."
  ^git push origin $release_branch --force-with-lease
  ^git push origin $tag --force

  print ""
  print "JitPack will build at:"
  print $"  https://jitpack.io/#fkhaidari/chisel/($tag)"
}
