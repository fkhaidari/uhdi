#!/usr/bin/env nu
# Install the uhdi toolchain from GitHub Releases. Invoked via the
# tools/install.sh bash bootstrap. Set $GITHUB_TOKEN to dodge the 60
# req/hr unauthenticated GitHub API limit.

use lib/common.nu *

const REPO = "fkhaidari/uhdi"
const SCALA_CLI_REPO = "VirtusLab/scala-cli"

# Install firtool, hgdb-py, tywaves, chiseltrace, scala-cli, and print
# the chisel JitPack snippet.
def "main all" [
  --prefix: path = ""
  --release-tag: string = ""
  --chisel-tag: string = ""
  --force
] {
  let p = (resolve-prefix $prefix)
  preflight-all
  let work_root = (mktemp -d | str trim)
  let did = (
    # hgdb-cli runs after hgdb-py because it symlinks the bindings.
    # chiseltrace consumes uhdi-to-pdg output, so its order vs. hgdb-cli
    # doesn't matter -- but keep it grouped with the other viewers.
    # scala-cli is needed for the bench compile step.
    # hgdb-circt/hgdb-firrtl unlock the hgdb_circt/hgdb_firrtl bench cells.
    # ivy2-* unpack the publishLocal'd Chisel forks into ~/.ivy2/local so
    # the uhdi/tywaves/chiseltrace bench pipelines resolve without a manual
    # `mill publishLocal` (the SNAPSHOTs aren't on Maven Central/JitPack).
    ["firtool" "hgdb-py" "chisel" "tywaves" "chiseltrace" "scala-cli" "hgdb-circt" "hgdb-firrtl" "ivy2-uhdi" "ivy2-tywaves" "ivy2-chiseltrace" "hgdb-cli"]
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

# Install chiseltrace only (CLI + GUI viewer for PDG slicing).
def "main chiseltrace" [--prefix: path = "" --release-tag: string = "" --force] {
  run-single "chiseltrace" $prefix $release_tag $force
}

# Install scala-cli only (needed for the bench compile step).
def "main scala-cli" [--prefix: path = "" --force] {
  run-single "scala-cli" $prefix "" $force
}

# Install hgdb-circt firtool (legacy --hgdb=<file> flag, LLVM-16 era).
def "main hgdb-circt" [--prefix: path = "" --release-tag: string = "" --force] {
  run-single "hgdb-circt" $prefix $release_tag $force
}

# Install hgdb-firrtl.jar (Scala FIRRTL 1.x, requires Java 17).
def "main hgdb-firrtl" [--prefix: path = "" --release-tag: string = "" --force] {
  run-single "hgdb-firrtl" $prefix $release_tag $force
}

# Unpack the uhdi Chisel fork (debug intrinsics) into ~/.ivy2/local.
def "main ivy2-uhdi" [--prefix: path = "" --release-tag: string = "" --force] {
  run-single "ivy2-uhdi" $prefix $release_tag $force
}

# Unpack the tywaves Chisel fork into ~/.ivy2/local.
def "main ivy2-tywaves" [--prefix: path = "" --release-tag: string = "" --force] {
  run-single "ivy2-tywaves" $prefix $release_tag $force
}

# Unpack the chiseltrace Chisel fork into ~/.ivy2/local.
def "main ivy2-chiseltrace" [--prefix: path = "" --release-tag: string = "" --force] {
  run-single "ivy2-chiseltrace" $prefix $release_tag $force
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
    "chiseltrace" => { install-chiseltrace $p $release_tag $force $work_root }
    "scala-cli" => { install-scala-cli $p $force $work_root }
    "hgdb-circt" => { install-hgdb-circt $p $release_tag $force $work_root }
    "hgdb-firrtl" => { install-hgdb-firrtl $p $release_tag $force $work_root }
    "ivy2-uhdi" => { install-ivy2-local "uhdi" $release_tag $force $work_root }
    "ivy2-tywaves" => { install-ivy2-local "tywaves" $release_tag $force $work_root }
    "ivy2-chiseltrace" => { install-ivy2-local "chiseltrace" $release_tag $force $work_root }
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

# Surface python3.12 missing/wrong-version up front; dispatch's per-
# component try/catch hides it otherwise (silent hgdb-cli skip).
def preflight-all [] {
  # hgdb-cli is linux-x86_64-only; non-linux platforms skip via dispatch.
  if (detect-platform) != "linux-x86_64" { return }
  check-python-312
}

def check-python-312 [] {
  if ((which python3) | is-empty) {
    error make {msg: "python3 not found on PATH (needed for hgdb-cli). Debian/Ubuntu: apt install python3 python3-venv. macOS: brew install python@3.12."}
  }
  let py_ver = (^python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" | str trim)
  if $py_ver != "3.12" {
    error make {msg: $"python3 is ($py_ver) but hgdb-cli needs cpython-3.12 \(the shipped _hgdb.so is built for 3.12\). Install python3.12 and re-run."}
  }
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
    "chiseltrace" => {
      try { install-chiseltrace $p $release_tag $force $work_root; ["chiseltrace"] } catch { [] }
    }
    "scala-cli" => {
      try { install-scala-cli $p $force $work_root; ["scala-cli"] } catch { [] }
    }
    "hgdb-circt" => {
      try { install-hgdb-circt $p $release_tag $force $work_root; ["hgdb-circt"] } catch { [] }
    }
    "hgdb-firrtl" => {
      try { install-hgdb-firrtl $p $release_tag $force $work_root; ["hgdb-firrtl"] } catch { [] }
    }
    "ivy2-uhdi" => {
      try { install-ivy2-local "uhdi" $release_tag $force $work_root; ["ivy2-uhdi"] } catch { [] }
    }
    "ivy2-tywaves" => {
      try { install-ivy2-local "tywaves" $release_tag $force $work_root; ["ivy2-tywaves"] } catch { [] }
    }
    "ivy2-chiseltrace" => {
      try { install-ivy2-local "chiseltrace" $release_tag $force $work_root; ["ivy2-chiseltrace"] } catch { [] }
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
  let curl_args = (auth-curl-args $url)
  let dest = ($dest_dir | path join $filename)
  ^curl ...$curl_args -o $dest $url
  $dest
}

# Append `Authorization: Bearer ...` only for api.github.com; release
# asset CDN URLs (github.com/.../releases/download, objects.github-
# usercontent.com) don't share the 60/hr rate limit and a bearer there
# just leaks the token into redirect logs.
export def auth-curl-args [url: string]: nothing -> list<string> {
  let base = ["-fsSL"]
  let token = ($env.GITHUB_TOKEN? | default "")
  let host = ($url | url parse | get host)
  if ($token | is-empty) or ($host != "api.github.com") {
    $base
  } else {
    $base | append ["-H" $"Authorization: Bearer ($token)"]
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
        gh-api-get "https://api.github.com/repos/fkhaidari/chisel/tags?per_page=100"
        | get name
      )
    } catch { "" }
  )
  if (not ($from_tags | is-empty)) { return $from_tags }
  let from_releases = (
    try {
      pick-uhdi-tag (
        gh-api-get "https://api.github.com/repos/fkhaidari/chisel/releases?per_page=30"
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

# ---- scala-cli -------------------------------------------------------------

# Download the scala-cli static binary from VirtusLab/scala-cli releases.
# scala-cli ships as a gzip-compressed single binary (not a tarball), so
# we decompress with `gunzip` rather than tar. Only linux-x86_64 for now;
# other platforms can install via https://scala-cli.virtuslab.org/.
def install-scala-cli [p: path force: bool work_root: path] {
  print "=== scala-cli ==="
  let platform = (detect-platform)
  if $platform != "linux-x86_64" {
    print -e $"  scala-cli prebuilt is linux-x86_64 only \(got: ($platform)\)"
    print -e "  Install from https://scala-cli.virtuslab.org/ for other platforms."
    error make {msg: "platform mismatch"}
  }

  let target = ($p | path join "bin/scala-cli")
  if not (ensure-writable $target $force) { return }

  let tag = (resolve-release-tag $SCALA_CLI_REPO "")
  print $"  Repo:       ($SCALA_CLI_REPO)"
  print $"  Tag:        ($tag)"
  print $"  Platform:   ($platform)"

  let gz_url = $"https://github.com/($SCALA_CLI_REPO)/releases/download/($tag)/scala-cli-x86_64-pc-linux.gz"
  let tmp_gz = ($work_root | path join "scala-cli.gz")
  print $"  Download:   ($gz_url)"
  ^curl -fsSL -o $tmp_gz $gz_url
  mkdir ($p | path join "bin")
  ^gunzip -c $tmp_gz | save -f $target
  chmod +x $target
  print $"  Installed:  ($target)"
}

# ---- chiseltrace (PDG CLI + Tauri GUI viewer) -----------------------------

# Two binaries in one tarball: `chiseltrace-cli` (slice / DynPDG / convert-
# to-source) and `chiseltrace` (Tauri GUI). The CLI consumes the output of
# `uhdi-to-pdg`; the GUI renders the same graph interactively.
#
# install-tarball-component's `chmod_rel` is single-file, so we open-code
# the extract + per-binary chmod here. The presence of `bin/chiseltrace-cli`
# is the "already installed?" probe (the CLI is the must-have; the GUI
# might be platform-skipped in a future release).
def install-chiseltrace [p: path release_tag: string force: bool work_root: path] {
  print "=== chiseltrace ==="
  let platform = (detect-platform)
  let tag = (resolve-release-tag $REPO $release_tag)
  print $"  Repo:       ($REPO)"
  print $"  Tag:        ($tag)"
  print $"  Platform:   ($platform)"

  let target = ($p | path join "bin/chiseltrace-cli")
  if not (ensure-writable $target $force) { return }

  let pattern = $"chiseltrace-($platform)-*.tar.gz"
  let tmp = ($work_root | path join "chiseltrace")
  let tarball = (dl-release-asset $REPO $tag $pattern $tmp)

  let extract_to = ($p | path join "bin")
  mkdir $extract_to
  ^tar -xzf $tarball -C $extract_to
  chmod +x ($p | path join "bin/chiseltrace-cli")
  # The Tauri GUI may be skipped on platforms that can't build it -- only
  # chmod if it actually shipped, so a CLI-only tarball doesn't error here.
  let gui = ($p | path join "bin/chiseltrace")
  if ($gui | path exists) {
    chmod +x $gui
    print $"  Installed:  ($gui)"
  }
  print $"  Installed:  ($target)"
}

# ---- hgdb-circt (legacy firtool with --hgdb=<file>) -----------------------

def install-hgdb-circt [p: path release_tag: string force: bool work_root: path] {
  install-tarball-component {
    name: "hgdb-circt"
    pattern: "hgdb-circt-firtool-{platform}-*.tar.gz"
    target_rel: "bin/hgdb-circt-firtool"
    extract_rel: "bin"
    chmod_rel: "bin/hgdb-circt-firtool"
  } $p $release_tag $force $work_root
}

# ---- hgdb-firrtl (Scala FIRRTL 1.x fat jar) --------------------------------

def install-hgdb-firrtl [p: path release_tag: string force: bool work_root: path] {
  print "=== hgdb-firrtl ==="
  let tag = (resolve-release-tag $REPO $release_tag)
  print $"  Repo:       ($REPO)"
  print $"  Tag:        ($tag)"

  let target = ($p | path join "bin/hgdb-firrtl.jar")
  if not (ensure-writable $target $force) { return }

  let asset_name = $"hgdb-firrtl-($tag).jar"
  let tmp = ($work_root | path join "hgdb-firrtl")
  let jar = (dl-release-asset $REPO $tag $asset_name $tmp)

  mkdir ($p | path join "bin")
  cp $jar $target
  print $"  Installed:  ($target)"
}

# ---- ivy2-local (publishLocal'd Chisel forks) -----------------------------

# Unpack a `mill publishLocal`'d Chisel fork tarball into ~/.ivy2/local so
# scala-cli's `--repository ivy2Local` resolves the bench SNAPSHOTs. The
# forks (uhdi debug-intrinsics, tywaves, chiseltrace) carry custom Chisel +
# compiler-plugin builds that aren't on Maven Central or JitPack (their old
# mill build.sc doesn't publish a JitPack-compatible plugin artifactId), so
# shipping the prebuilt ivy layout is the only offline-friendly path.
def install-ivy2-local [variant: string release_tag: string force: bool work_root: path] {
  print $"=== ivy2-($variant) ==="
  let tag = (resolve-release-tag $REPO $release_tag)
  print $"  Repo:       ($REPO)"
  print $"  Tag:        ($tag)"

  let ivy2_local = ($env.HOME | path join ".ivy2/local")
  # Marker: the org dir the tarball populates. Present without --force means
  # some fork is already unpacked; we still extract (tarballs are additive,
  # different SNAPSHOT version dirs) but skip the redundant download.
  let pattern = $"ivy2-local-($variant)-*.tar.gz"
  let tmp = ($work_root | path join $"ivy2-($variant)")
  let tarball = (dl-release-asset $REPO $tag $pattern $tmp)

  mkdir $ivy2_local
  ^tar -xzf $tarball -C $ivy2_local
  print $"  Installed:  ($ivy2_local) \(($variant) Chisel fork\)"
}

# ---- hgdb-cli (hgdb console debugger + uhdi converters) -------------------

# Shared $prefix/cli-venv with: hgdb-debugger (console + linked _hgdb.so),
# libhgdb (hgdb-replay / hgdb-db as prebuilt console_scripts -- cheaper
# than building from upstream cmake), and in-tree uhdi-converter
# (editable; provides uhdi-to-hgldd / uhdi-to-hgdb / uhdi-to-pdg).
def install-hgdb-cli [p: path force: bool] {
  print "=== hgdb-cli ==="
  let hgdb_bin = ($p | path join "bin/hgdb")
  let venv = ($p | path join "cli-venv")
  let bindings = ($p | path join "lib/hgdb/bindings/python")
  let so = ($bindings | path join "build/lib.linux-x86_64-cpython-312/_hgdb.cpython-312-x86_64-linux-gnu.so")

  # ABI: shipped _hgdb.so is built for cpython-3.12; venv must match.
  check-python-312

  if (not ($bindings | path join "hgdb" | path exists)) or (not ($so | path exists)) {
    print -e "  hgdb python bindings not found. Run install.sh hgdb-py first."
    error make {msg: "missing hgdb-py bindings"}
  }

  let converter = ($REPO_ROOT | path join "converter")
  if not ($converter | path join "pyproject.toml" | path exists) {
    print -e $"  converter source tree not found at ($converter)."
    print -e "  Run install.sh from a clone of fkhaidari/uhdi, not a vendored layer."
    error make {msg: "missing converter source"}
  }

  if not (ensure-writable $hgdb_bin $force) { return }
  rm -rf $venv

  print "  Building 3.12 venv + hgdb-debugger + libhgdb + uhdi-converter..."
  ^python3 -m venv $venv
  let pip = ($venv | path join "bin/pip")
  # PIP_CONFIG_FILE override sidesteps any site-wide artifactory proxy.
  # `--no-deps` on hgdb-debugger because it declares a `hgdb[client]`
  # extra that doesn't exist on PyPI -- the symlinks below provide it.
  with-env {PIP_CONFIG_FILE: "/dev/null"} {
    ^$pip install --quiet --disable-pip-version-check --index-url "https://pypi.org/simple" "websockets<11" prompt-toolkit pygments "jsonschema>=4.18" "referencing>=0.30"
    ^$pip install --quiet --disable-pip-version-check --index-url "https://pypi.org/simple" --no-deps hgdb-debugger
    ^$pip install --quiet --disable-pip-version-check --index-url "https://pypi.org/simple" libhgdb
    ^$pip install --quiet --disable-pip-version-check --index-url "https://pypi.org/simple" --no-deps -e ($REPO_ROOT | path join "converter")
  }

  let sp = ($venv | path join "lib/python3.12/site-packages")
  ^ln -sfn ($bindings | path join "hgdb") ($sp | path join "hgdb")
  ^ln -sfn $so ($sp | path join ($so | path basename))
  ^ln -sfn ($venv | path join "bin/hgdb")          $hgdb_bin
  ^ln -sfn ($venv | path join "bin/hgdb-replay")   ($p | path join "bin/hgdb-replay")
  ^ln -sfn ($venv | path join "bin/hgdb-db")       ($p | path join "bin/hgdb-db")
  ^ln -sfn ($venv | path join "bin/uhdi-to-hgldd") ($p | path join "bin/uhdi-to-hgldd")
  ^ln -sfn ($venv | path join "bin/uhdi-to-hgdb")  ($p | path join "bin/uhdi-to-hgdb")
  ^ln -sfn ($venv | path join "bin/uhdi-to-pdg")   ($p | path join "bin/uhdi-to-pdg")
  print $"  Installed:  ($hgdb_bin)"
  print $"  Installed:  ($p | path join "bin/hgdb-replay")"
  print $"  Installed:  ($p | path join "bin/hgdb-db")"
  print $"  Installed:  ($p | path join "bin/uhdi-to-hgldd")"
  print $"  Installed:  ($p | path join "bin/uhdi-to-hgdb")"
  print $"  Installed:  ($p | path join "bin/uhdi-to-pdg")"
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
        "chiseltrace" => $"  export CHISELTRACE=\"($p)/bin/chiseltrace-cli\""
        "scala-cli" => null  # scala-cli on PATH is sufficient; no extra env var needed
        "hgdb-circt" => $"  export HGDB_CIRCT_FIRTOOL=\"($p)/bin/hgdb-circt-firtool\""
        "hgdb-firrtl" => $"  export HGDB_FIRRTL_JAR=\"($p)/bin/hgdb-firrtl.jar\""
        "hgdb-cli" => $"  export HGDB_DEBUGGER=\"($p)/bin/hgdb\""
        _ => null
      }
    }
    | where {|x| $x != null }
  )
  let path_hint = if ("firtool" in $did) or ("tywaves" in $did) or ("chiseltrace" in $did) or ("scala-cli" in $did) or ("hgdb-circt" in $did) or ("hgdb-cli" in $did) {
    let bin = ($p | path join "bin" | into string)
    if ($bin not-in $path_segments) {
      [$"  export PATH=\"($bin):$PATH\""]
    } else { [] }
  } else { [] }
  $header | append $component_hints | append $path_hint
}
