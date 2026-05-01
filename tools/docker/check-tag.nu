#!/usr/bin/env nu
# Refuse if tools/docker/image-tag.txt is stale vs. compute-tag.nu.

use ../lib/common.nu *

def main [] {
  let recorded_path = ($REPO_ROOT | path join "tools/docker/image-tag.txt")
  let recorded = if ($recorded_path | path exists) {
    open --raw $recorded_path | str trim
  } else { "<missing>" }
  # Spawn compute-tag.nu rather than duplicating the hash formula.
  let expected = (^nu ($REPO_ROOT | path join "tools/docker/compute-tag.nu") | str trim)

  if $expected == $recorded { return }

  error make --unspanned {
    msg: $"tools/docker/image-tag.txt is out of sync.
  recorded: ($recorded)
  expected: ($expected)

Fix:
  tools/docker/compute-tag.nu | save -f tools/docker/image-tag.txt
  git add tools/docker/image-tag.txt"
  }
}
