#!/usr/bin/env nu
# Install the uhdi toolchain from GitHub Releases. Invoked via the
# tools/install.sh bash bootstrap. Set $GITHUB_TOKEN to dodge the 60
# req/hr unauthenticated GitHub API limit.

use lib/common.nu *

const REPO = "fkhaidari/uhdi"

# Install firtool, hgdb-py, tywaves, and print the chisel JitPack snippet.
def "main all" [
  --prefix: path = ""
  --release-tag: string = ""
  --chisel-tag: string = ""
  --force
] {
  let p = (resolve-prefix $prefix)
  let work_root = (mktemp -d | str trim)
  let did = (
    # hgdb-cli runs after hgdb-py because it symlinks the bindings.
    ["firtool" "hgdb-py" "chisel" "tywaves" "hgdb-cli"]
    | each {|c| dispatch $c $p $release_tag $chisel_tag $force $work_root }
    | flatten
    | uniq
  )
  rm -rf $work_root
  print-env-hints $p $did
}

# Install firtool only.
def "main firtool" [--prefix: path = "" --release-tag: string = "" --force] {
  run-single "firtool" $prefix $release_tag $force
}

# Install hgdb-py only (linux-x86_64).
def "main hgdb-py" [--prefix: path = "" --release-tag: string = "" --force] {
  run-single "hgdb-py" $prefix $release_tag $force
}

# Install tywaves only.
def "main tywaves" [--prefix: path = "" --release-tag: string = "" --force] {
  run-single "tywaves" $prefix $release_tag $force
}

# Install the upstream `hgdb` console debugger (Kuree/hgdb-debugger).
# Builds a 3.12 venv, pip-installs hgdb-debugger + deps, links the
# uhdi-tools hgdb python bindings into it, exposes `bin/hgdb` on the
# install prefix.  Requires `hgdb-py` already installed (provides the
# bindings and _hgdb.so this links against).
def "main hgdb-cli" [--prefix: path = "" --force] {
  run-single "hgdb-cli" $prefix "" $force
}

# Single-component variant of `main all`. Errors surface as nu does them
# -- a lone `firtool` subcommand has no fallback to silently skip to.
def run-single [component: string prefix: path release_tag: string force: bool] {
  let p = (resolve-prefix $prefix)
  let work_root = (mktemp -d | str trim)
  match $component {
    "firtool" => { install-firtool $p $release_tag $force $work_root }
    "hgdb-py" => { install-hgdb-py $p $release_tag $force $work_root }
    "tywaves" => { install-tywaves $p $release_tag $force $work_root }
    "hgdb-cli" => { install-hgdb-cli $p $force }
  }
  rm -rf $work_root
  print-env-hints $p [$component]
}

# Print the chisel JitPack snippet (writes nothing to disk).
def "main chisel" [
  --chisel-tag: string = ""
  # Other flags accepted for symmetry with `all`.
  --prefix: path = ""
  --release-tag: string = ""
  --force
] {
  install-chisel $chisel_tag
}

def main [
  --prefix: path = ""
  --release-tag: string = ""
  --chisel-tag: string = ""
  --force
] {
  main all --prefix $prefix --release-tag $release_tag --chisel-tag $chisel_tag --force=$force
}

# ---- internal helpers ------------------------------------------------------

export def resolve-prefix [prefix: path]: nothing -> path {
  if ($prefix | is-empty) { $env.HOME | path join ".local/uhdi-tools" } else { $prefix }
}

# Swallows hgdb-py / chisel failures (allowed to skip on platform
# mismatch or missing tag); returns the components that landed so
# `print-env-hints` can pick the right env vars to suggest.
def dispatch [
  component: string
  p: path
  release_tag: string
  chisel_tag: string
  force: bool
  work_root: path
]: nothing -> list<string> {
  match $component {
    "firtool" => {
      install-firtool $p $release_tag $force $work_root
      ["firtool"]
    }
    "hgdb-py" => {
      try { install-hgdb-py $p $release_tag $force $work_root; ["hgdb-py"] } catch { [] }
    }
    "chisel" => {
      try { install-chisel $chisel_tag; [] } catch { [] }
    }
    "tywaves" => {
      try { install-tywaves $p $release_tag $force $work_root; ["tywaves"] } catch { [] }
    }
    "hgdb-cli" => {
      try { install-hgdb-cli $p $force; ["hgdb-cli"] } catch { [] }
    }
  }
}

# Idempotent: returns false (and prints a notice) if the target already
# exists without --force, so callers can short-circuit without erroring.
# `install.sh all` should be safely re-runnable.
export def ensure-writable [target: path force: bool]: nothing -> bool {
  if ($target | path exists) and (not $force) {
    print $"  Already installed: ($target) \(use --force to reinstall\)"
    return false
  }
  rm -rf $target
  true
}

def asset-url-or-fail [
  repo: string
  tag: string
  pattern: string
]: nothing -> string {
  let url = (find-asset-url $repo $tag $pattern)
  if ($url | is-empty) {
    error make {msg: $"no asset matching '($pattern)' in ($repo)@($tag)"}
  }
  $url
}

def dl-release-asset [
  repo: string
  tag: string
  pattern: string
  dest_dir: path
]: nothing -> path {
  let url = (asset-url-or-fail $repo $tag $pattern)
  let filename = ($url | path basename)
  mkdir $dest_dir
  print -e $"  Download:   ($url)"
  let curl_args = (auth-curl-args)
  let dest = ($dest_dir | path join $filename)
  ^curl ...$curl_args -o $dest $url
  $dest
}

# Append `Authorization: Bearer ...` if GITHUB_TOKEN is set; the
# unauthenticated 60/hr rate limit blows through fast on test-install.
export def auth-curl-args []: nothing -> list<string> {
  let base = ["-fsSL"]
  if (($env.GITHUB_TOKEN? | default "") | is-empty) {
    $base
  } else {
    $base | append ["-H" $"Authorization: Bearer ($env.GITHUB_TOKEN)"]
  }
}

# Substitute `{platform}` placeholder in an asset glob.
export def apply-platform-pattern [pattern: string platform: string]: nothing -> string {
  $pattern | str replace --all "{platform}" $platform
}

# First name ending in `-uhdi`, or empty string. fkhaidari/chisel
# tags are like `v0.1.1-uhdi`; this filters out unrelated work on
# the same fork.
export def pick-uhdi-tag [names: list<string>]: nothing -> string {
  let m = ($names | where {|n| $n | str ends-with "-uhdi" } | first)
  if ($m == null) { "" } else { $m }
}

# ---- tarball-component install ---------------------------------------------

# Generic install path: download a tarball matching `pattern` from a
# release of $REPO, extract under `$p / extract_rel`. Platform-specific
# components (hgdb-py) check the host before calling.
#
# `pattern` may contain `{platform}`, replaced before lookup. `chmod_rel`
# is the install-relative path that needs +x; null skips chmod.
def install-tarball-component [
  spec: record # {name, pattern, target_rel, extract_rel, chmod_rel?}
  p: path
  release_tag: string
  force: bool
  work_root: path
] {
  print $"=== ($spec.name) ==="
  let platform = (detect-platform)
  let tag = (resolve-release-tag $REPO $release_tag)
  print $"  Repo:       ($REPO)"
  print $"  Tag:        ($tag)"
  print $"  Platform:   ($platform)"

  let target = ($p | path join $spec.target_rel)
  if not (ensure-writable $target $force) { return }

  let pattern = (apply-platform-pattern $spec.pattern $platform)
  let tmp = ($work_root | path join $spec.name)
  let tarball = (dl-release-asset $REPO $tag $pattern $tmp)

  let extract_to = ($p | path join $spec.extract_rel)
  mkdir $extract_to
  ^tar -xzf $tarball -C $extract_to
  if ($spec.chmod_rel? != null) {
    chmod +x ($p | path join $spec.chmod_rel)
  }
  print $"  Installed:  ($target)"
}

# ---- firtool ---------------------------------------------------------------

def install-firtool [p: path release_tag: string force: bool work_root: path] {
  install-tarball-component {
    name: "firtool"
    pattern: "firtool-{platform}-*.tar.gz"
    target_rel: "bin/firtool"
    extract_rel: "bin"
    chmod_rel: "bin/firtool"
  } $p $release_tag $force $work_root
}

# ---- hgdb-py ---------------------------------------------------------------

def install-hgdb-py [p: path release_tag: string force: bool work_root: path] {
  let platform = (detect-platform)
  if $platform != "linux-x86_64" {
    print -e $"  hgdb-py prebuilt is linux-x86_64 only \(got: ($platform)\)"
    print -e "  build from source: tools/release/release-hgdb-py.nu build"
    error make {msg: "platform mismatch"}
  }
  install-tarball-component {
    name: "hgdb-py"
    pattern: "hgdb-py-linux-x86_64-*.tar.gz"
    target_rel: "lib/hgdb"
    extract_rel: "lib/hgdb"
  } $p $release_tag $force $work_root
}

# ---- chisel (snippet only) -------------------------------------------------

def install-chisel [chisel_tag: string] {
  print "=== chisel (JitPack snippet) ==="
  let resolved = if ($chisel_tag | is-empty) or ($chisel_tag == "latest") {
    resolve-chisel-tag
  } else { $chisel_tag }
  print $"  Tag:        ($resolved)"
  print ""
  print-chisel-snippet $resolved
}

# fkhaidari/chisel ships JitPack tags via release-chisel.nu, which only
# creates git tags (no GitHub Releases). Prefer /tags; fall back to
# /releases for forward compatibility. Filter to *-uhdi tags so we don't
# pick up unrelated work on the same fork.
def resolve-chisel-tag []: nothing -> string {
  let from_tags = (
    try {
      pick-uhdi-tag (
        http get "https://api.github.com/repos/fkhaidari/chisel/tags?per_page=100"
        | get name
      )
    } catch { "" }
  )
  if (not ($from_tags | is-empty)) { return $from_tags }
  let from_releases = (
    try {
      pick-uhdi-tag (
        http get "https://api.github.com/repos/fkhaidari/chisel/releases?per_page=30"
        | get tag_name
      )
    } catch { "" }
  )
  if (not ($from_releases | is-empty)) { return $from_releases }
  print -e "  no *-uhdi tag found in fkhaidari/chisel; printing placeholder"
  "<chisel-tag>"
}

def print-chisel-snippet [tag: string] {
  # Raw single-quote string (no nu interpolation) + `str replace` for
  # the tag placeholder. Avoids escaping every paren / quote against
  # nu's `$"..."` parser.
  let template = '# --- Mill (build.mill, Mill 0.11+ / 1.x) -----------------------------------
import coursier.maven.MavenRepository
def repositoriesTask = Task.Anon {
    super.repositoriesTask() ++ Seq(MavenRepository("https://jitpack.io"))
}
// in your ScalaModule:
def mvnDeps = Seq(
    ivy"com.github.fkhaidari.chisel::chisel:CHISEL_TAG",
    ivy"com.github.fkhaidari.chisel:chisel-plugin_2.13.18:CHISEL_TAG",
)

# --- sbt (build.sbt) -------------------------------------------------------
resolvers += "jitpack" at "https://jitpack.io"
libraryDependencies ++= Seq(
    "com.github.fkhaidari.chisel" %% "chisel" % "CHISEL_TAG",
    "com.github.fkhaidari.chisel" % "chisel-plugin_2.13.18" % "CHISEL_TAG",
)

# --- scala-cli -------------------------------------------------------------
//> using repository "https://jitpack.io"
//> using dep "com.github.fkhaidari.chisel::chisel:CHISEL_TAG"
//> using dep "com.github.fkhaidari.chisel:chisel-plugin_2.13.18:CHISEL_TAG"'
  print ($template | str replace --all "CHISEL_TAG" $tag)
}

# ---- tywaves ---------------------------------------------------------------

def install-tywaves [p: path release_tag: string force: bool work_root: path] {
  install-tarball-component {
    name: "tywaves"
    pattern: "tywaves-{platform}-*.tar.gz"
    target_rel: "bin/tywaves"
    extract_rel: "bin"
    chmod_rel: "bin/tywaves"
  } $p $release_tag $force $work_root
}

# ---- hgdb-cli (upstream `hgdb` console debugger) ---------------------------

def install-hgdb-cli [p: path force: bool] {
  print "=== hgdb-cli ==="
  let hgdb_bin = ($p | path join "bin/hgdb")
  let venv = ($p | path join "cli-venv")
  let bindings = ($p | path join "lib/hgdb/bindings/python")
  let so = ($bindings | path join "build/lib.linux-x86_64-cpython-312/_hgdb.cpython-312-x86_64-linux-gnu.so")

  # ABI: shipped _hgdb.so is built for cpython-3.12; venv must match.
  let py_ver = (^python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" | str trim)
  if $py_ver != "3.12" {
    print -e $"  python3 is ($py_ver) but _hgdb.so is cpython-3.12; install python3.12 and re-run. Skipping."
    error make {msg: $"python version mismatch: ($py_ver)"}
  }

  if (not ($bindings | path join "hgdb" | path exists)) or (not ($so | path exists)) {
    print -e "  hgdb python bindings not found. Run install.sh hgdb-py first."
    error make {msg: "missing hgdb-py bindings"}
  }

  if not (ensure-writable $hgdb_bin $force) { return }
  rm -rf $venv

  print "  Building 3.12 venv + hgdb-debugger..."
  ^python3 -m venv $venv
  let pip = ($venv | path join "bin/pip")
  # PIP_CONFIG_FILE override sidesteps any site-wide artifactory proxy.
  # `--no-deps` on hgdb-debugger because it declares a `hgdb[client]`
  # extra that doesn't exist on PyPI -- the symlinks below provide it.
  with-env {PIP_CONFIG_FILE: "/dev/null"} {
    ^$pip install --quiet --disable-pip-version-check --index-url "https://pypi.org/simple" "websockets<11" prompt-toolkit pygments
    ^$pip install --quiet --disable-pip-version-check --index-url "https://pypi.org/simple" --no-deps hgdb-debugger
  }

  let sp = ($venv | path join "lib/python3.12/site-packages")
  ^ln -sfn ($bindings | path join "hgdb") ($sp | path join "hgdb")
  ^ln -sfn $so ($sp | path join ($so | path basename))
  ^ln -sfn ($venv | path join "bin/hgdb") $hgdb_bin
  print $"  Installed:  ($hgdb_bin)"
}

# ---- end-of-run summary ----------------------------------------------------

def print-env-hints [p: path did: list<string>] {
  print ""
  # $env.PATH is auto-split into a list<string> by nu; pass through
  # as-is for env-hint-lines's `not-in` check.
  for line in (env-hint-lines $p $did $env.PATH) { print $line }
}

# Pure list-builder for the post-install env hints. `path_segments` is
# a parameter (not read from $env) so tests can pin it deterministically.
export def env-hint-lines [
  p: path
  did: list<string>
  path_segments: list<string>
]: nothing -> list<string> {
  let header = ["=== Done ==="]
  let component_hints = (
    $did
    | each {|c|
      match $c {
        "firtool" => $"  export FIRTOOL=\"($p)/bin/firtool\""
        "hgdb-py" => $"  export HGDB_PY=\"($p)/lib/hgdb/bindings/python\""
        "tywaves" => $"  export TYWAVES=\"($p)/bin/tywaves\""
        "hgdb-cli" => $"  export HGDB_DEBUGGER=\"($p)/bin/hgdb\""
        _ => null
      }
    }
    | where {|x| $x != null }
  )
  let path_hint = if ("firtool" in $did) or ("tywaves" in $did) or ("hgdb-cli" in $did) {
    let bin = ($p | path join "bin" | into string)
    if ($bin not-in $path_segments) {
      [$"  export PATH=\"($bin):$PATH\""]
    } else { [] }
  } else { [] }
  $header | append $component_hints | append $path_hint
}
