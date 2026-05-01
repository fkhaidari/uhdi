# Shared helpers for tools/* nushell scripts.

# `path self` is parse-only, hence const, not let. Resolves to the repo
# root no matter where this module is `use`'d from.
export const REPO_ROOT = path self ../..

# Map nu's host info into the {linux,macos}-{x86_64,aarch64} naming our
# release artefacts use. Errors out for anything else so the release
# matrix stays explicit.
export def detect-platform []: nothing -> string {
  let os = match $nu.os-info.name {
    "linux" => "linux"
    "macos" => "macos"
    $other => { error make {msg: $"unsupported OS: ($other)"} }
  }
  let arch = match $nu.os-info.arch {
    "x86_64" => "x86_64"
    "aarch64" => "aarch64"
    $other => { error make {msg: $"unsupported arch: ($other)"} }
  }
  $"($os)-($arch)"
}

# Shell-style KEY=VALUE file -> record. Comments + blanks skipped, no
# shell expansion. Splits on the first `=` so values may contain `=`.
export def load-env [path: path]: nothing -> record {
  open --raw $path
  | lines
  | where {|l|
    let t = ($l | str trim)
    (not ($t | str starts-with '#')) and ($t | str contains '=')
  }
  | parse '{key}={value}'
  | reduce -f {} {|it acc| $acc | upsert $it.key $it.value }
}

# Pure URL parsers exposed so unit tests can hit them without spawning git.
export def parse-remote-owner [url: string]: nothing -> string {
  $url
  | str trim
  | parse --regex '^(?:git@|https?://)[^/:]+[:/]([^/]+)/'
  | get capture0.0
  | str downcase
}

export def parse-remote-slug [url: string]: nothing -> string {
  $url
  | str trim
  | parse --regex '[:/]([^/]+/[^/]+?)(?:\.git)?$'
  | get capture0.0
}

# Owner of THIS repo's origin (lowercased -- GHCR rejects mixed case).
export def repo-owner []: nothing -> string {
  parse-remote-owner (^git -C $REPO_ROOT config --get remote.origin.url)
}

export def repo-slug []: nothing -> string {
  parse-remote-slug (^git -C $REPO_ROOT config --get remote.origin.url)
}

export def human-size [path: path]: nothing -> string {
  (ls $path | get 0.size) | into string
}

export def image-ref []: nothing -> string {
  let tag = (open --raw ($REPO_ROOT | path join "tools/docker/image-tag.txt") | str trim)
  $"ghcr.io/(repo-owner)/uhdi-tools:($tag)"
}

# `tag` empty or "latest" -> hit /releases/latest; otherwise pass through.
# Errors out if the repo has no releases yet.
export def resolve-release-tag [repo: string tag: string]: nothing -> string {
  if (not ($tag | is-empty)) and ($tag != "latest") {
    return $tag
  }
  let resolved = (
    http get $"https://api.github.com/repos/($repo)/releases/latest"
    | get tag_name?
  )
  if ($resolved | is-empty) {
    error make {msg: $"could not resolve latest release for ($repo); pass --tag <tag>"}
  }
  $resolved
}

# First asset URL matching `pattern` (`*` wildcard). Empty string if no
# match -- callers decide whether that's fatal.
export def find-asset-url [
  repo: string
  tag: string
  pattern: string
]: nothing -> string {
  let release = (
    try {
      http get $"https://api.github.com/repos/($repo)/releases/tags/($tag)"
    } catch {
      return ""
    }
  )
  let regex = ('^' + ($pattern | str replace --all '.' '\.' | str replace --all '*' '.*') + '$')
  let match = (
    $release.assets?
    | default []
    | where {|a| $a.name =~ $regex }
    | first
  )
  if ($match == null) { "" } else { $match.browser_download_url }
}

# `docker pull image; docker create; docker cp <src_path> <dest>; docker rm`.
# Always cleans up the created container, even on cp failure.
export def docker-extract [image: string src_path: string dest: path] {
  print "Pulling..."
  ^docker pull $image
  let cid = (^docker create $image | str trim)
  try {
    ^docker cp $"($cid):($src_path)" $dest
  } catch {|e|
    ^docker rm $cid o> /dev/null
    error make {msg: $"docker cp failed: ($e.msg)"}
  }
  ^docker rm $cid o> /dev/null
}

# Upsert a tarball onto a GitHub release: create if missing, else
# upload with --clobber so the same tag can carry multiple artefacts
# (firtool + hgdb-py + tywaves all ride on one fkhaidari/uhdi tag).
export def gh-publish-tarball [
  tag: string
  tarball: path
  title: string
  notes: string
] {
  if (which gh | is-empty) {
    error make {msg: "gh CLI not found; install from https://cli.github.com/"}
  }
  let exists = (
    try {
      ^gh release view $tag o> /dev/null e> /dev/null
      true
    } catch { false }
  )
  if $exists {
    ^gh release upload $tag $tarball --clobber
  } else {
    ^gh release create $tag $tarball --title $title --notes $notes
  }
  print $"Done: https://github.com/(repo-slug)/releases/tag/($tag)"
}
