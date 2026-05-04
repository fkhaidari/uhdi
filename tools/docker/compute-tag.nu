#!/usr/bin/env nu
# Image tag = sha256(versions.env + Dockerfile + entrypoint.sh)[:16].
# Output is what tools/docker/image-tag.txt must contain; CI's
# build-image job runs the same formula.

use ../lib/common.nu *

def main []: nothing -> string {
  let inputs = [
    "tools/versions.env"
    "tools/docker/Dockerfile"
    "tools/docker/entrypoint.sh"
  ]

  # `sha256sum a b c` prints "<hash>  <name>\n" per file, then we
  # hash that whole stream again. Reproduce the exact wire format
  # (two-space separator, repo-relative names) so the tag matches
  # what `sha256sum tools/... | sha256sum | cut -c1-16` produces.
  cd $REPO_ROOT
  $inputs
  | each {|f| $"(open --raw $f | hash sha256)  ($f)\n" }
  | str join
  | hash sha256
  | str substring 0..<16
}
