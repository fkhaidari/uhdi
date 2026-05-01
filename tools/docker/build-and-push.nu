#!/usr/bin/env nu
# Build uhdi-tools image; optionally push to GHCR. First build is
# 30-60 min; subsequent builds reuse layer cache.

use ../lib/common.nu *

def main [
  --push # publish to GHCR after build
  --owner: string = "" # override repo-owner (defaults: lowercased segment of git remote)
] {
  cd $REPO_ROOT

  let resolved_owner = if ($owner | is-empty) { repo-owner } else { $owner }
  if ($resolved_owner | is-empty) {
    error make {msg: "could not infer owner from git remote; pass --owner <name>"}
  }

  # Refuse stale image-tag.txt -- prevents pushing under a wrong tag.
  ^nu ($REPO_ROOT | path join "tools/docker/check-tag.nu")

  let versions = (load-env ($REPO_ROOT | path join "tools/versions.env"))
  let tag = (open --raw ($REPO_ROOT | path join "tools/docker/image-tag.txt") | str trim)
  let ref = $"ghcr.io/($resolved_owner)/uhdi-tools:($tag)"
  # Bare repo name (no tag): podman's --cache-from rejects tagged refs.
  let cache_repo = $"ghcr.io/($resolved_owner)/uhdi-tools"

  print "Pulling cache image..."
  try {
    ^docker pull $"($cache_repo):latest"
  } catch {
    print "  (no cache image; cold build)"
  }

  # Listed explicitly so a forgotten key surfaces here, not as a
  # "variable not set" deep in the image build.
  let arg_keys = [
    "CIRCT_URL"
    "CIRCT_REV"
    "LLVM_URL"
    "LLVM_REV"
    "HGDB_CIRCT_URL"
    "HGDB_CIRCT_REV"
    "HGDB_CIRCT_LLVM_URL"
    "HGDB_CIRCT_LLVM_REV"
    "HGDB_FIRRTL_URL"
    "HGDB_FIRRTL_REV"
    "HGDB_URL"
    "HGDB_REV"
    "CHISEL_TYWAVES_URL"
    "CHISEL_TYWAVES_REV"
    "CHISEL_UHDI_URL"
    "CHISEL_UHDI_REV"
    "TYWAVES_URL"
    "TYWAVES_REV"
    "CHISEL_STOCK_VERSION"
    "SCALA_CLI_VERSION"
  ]
  let build_args = (
    $arg_keys
    | each {|k|
      let v = ($versions | get -o $k)
      if ($v == null) {
        error make {msg: $"versions.env missing required key: ($k)"}
      }
      ["--build-arg" $"($k)=($v)"]
    }
    | flatten
  )

  print $"Building ($ref)"
  # --network=host: rootless podman bridge sometimes drops external hosts.
  # --http-proxy=false: podman-only, drop on docker-CE if it complains.
  let docker_args = (
    [
      "build"
      "-f"
      "tools/docker/Dockerfile"
      "-t"
      $ref
      "--network=host"
      "--http-proxy=false"
      "--cache-from"
      $cache_repo
      "--build-arg"
      "BUILDKIT_INLINE_CACHE=1"
    ] | append $build_args | append "tools/docker/"
  )
  ^docker ...$docker_args

  if $push {
    print $"Pushing ($ref)"
    ^docker push $ref
    let latest = $"ghcr.io/($resolved_owner)/uhdi-tools:latest"
    ^docker tag $ref $latest
    ^docker push $latest
  }

  print ""
  print "Done."
  print $"  ref:  ($ref)"
  if not $push {
    print "  (not pushed; pass --push to publish)"
  }
}
