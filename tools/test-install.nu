#!/usr/bin/env nu
# Smoke tests for tools/install.nu. Reads env vars (no flags):
#   UHDI_TAG     fkhaidari/uhdi release (default: latest)
#   CHISEL_TAG   fkhaidari/chisel JitPack tag (default: latest *-uhdi)
#   UHDI_E2E=1   also resolve chisel from JitPack + compile a tiny mill
#                project (slow, needs `mill` on PATH)

use lib/common.nu *
use std/assert

def fail [msg: string] { error make {msg: $"FAIL: ($msg)"} }
def ok [msg: string] { print $"ok:   ($msg)" }
def skip [msg: string] { print -e $"skip: ($msg)" }
def section [name: string] { print ""; print $"--- ($name) ---" }

def main [] {
  let prefix = (mktemp -d | str trim)
  let platform = (detect-platform)

  try {
    # ---- 1. install all components into a throwaway prefix --------
    section "install"
    let install_args = (
      [
        "all"
        "--prefix"
        $prefix
      ]
      | append (if (($env.UHDI_TAG? | default "") | is-not-empty) { ["--release-tag" $env.UHDI_TAG] } else { [] })
      | append (if (($env.CHISEL_TAG? | default "") | is-not-empty) { ["--chisel-tag" $env.CHISEL_TAG] } else { [] })
    )
    # `all` returns 0 even if hgdb-py is missing on this platform
    # (linux-x86_64 only), but firtool + chisel + tywaves must succeed.
    ^bash ($REPO_ROOT | path join "tools/install.sh") ...$install_args

    # ---- 2. firtool ----------------------------------------------
    section "firtool"
    let firtool = ($prefix | path join "bin/firtool")
    if not ($firtool | path exists) {
      fail $"firtool not installed at ($firtool)"
    }
    let help_text = (^$firtool --help | complete | get stdout)
    if ($help_text | str contains "--emit-uhdi") {
      ok "firtool installed and supports --emit-uhdi"
    } else {
      fail "firtool present but --help does not mention --emit-uhdi"
    }

    # ---- 3. hgdb-py ----------------------------------------------
    section "hgdb-py"
    let hgdb_py = ($prefix | path join "lib/hgdb/bindings/python")
    if ($hgdb_py | path exists) {
      let toml2hgdb = ($hgdb_py | path join "scripts/toml2hgdb")
      if not ($toml2hgdb | path exists) {
        fail "hgdb-py installed but scripts/toml2hgdb missing"
      }
      # `ls $string` doesn't expand globs (DoNotExpand); cast through
      # `glob` first so the *.so / lib.* patterns actually match.
      let so_files = (
        try {
          glob ($hgdb_py | path join "build/lib.*/_hgdb*.so")
        } catch { [] }
      )
      if ($so_files | is-empty) {
        fail "hgdb-py installed but _hgdb C extension missing"
      }
      let buildlib = (glob ($hgdb_py | path join "build/lib.*") | first)
      let import_ok = (
        try {
          with-env {PYTHONPATH: $"($hgdb_py):($buildlib)"} {
            ^python3 -c 'import hgdb, _hgdb' o> /dev/null e> /dev/null
          }
          true
        } catch { false }
      )
      if $import_ok {
        ok "hgdb-py installed and importable"
      } else {
        # Layout-correct but unimportable usually means glibc/python
        # ABI mismatch on this host -- still a fail; we can't run
        # the bench against a broken install.
        fail "hgdb-py present but cannot import (glibc/python ABI mismatch?)"
      }
    } else if $platform == "linux-x86_64" {
      skip "hgdb-py not installed (release artifact missing for this tag?)"
    } else {
      skip $"hgdb-py prebuilt is linux-x86_64 only \(got ($platform)\)"
    }

    # ---- 4. chisel snippet ---------------------------------------
    section "chisel"
    let chisel_args = (
      [
        "chisel"
      ] | append (if (($env.CHISEL_TAG? | default "") | is-not-empty) { ["--chisel-tag" $env.CHISEL_TAG] } else { [] })
    )
    let snippet = (^bash ($REPO_ROOT | path join "tools/install.sh") ...$chisel_args | complete | get stdout)
    if not ($snippet | str contains "jitpack.io") {
      fail "chisel snippet does not mention jitpack.io"
    }
    if not ($snippet =~ 'com\.github\.fkhaidari\.chisel.*-uhdi') {
      fail "chisel snippet does not contain a -uhdi coord"
    }
    ok "chisel snippet printed with jitpack repo + uhdi coord"

    # ---- 5. tywaves ----------------------------------------------
    section "tywaves"
    let ty = ($prefix | path join "bin/tywaves")
    if not ($ty | path exists) {
      fail $"tywaves not installed at ($ty)"
    }
    # tywaves is a GUI app; --help text varies between versions, so
    # run-without-crash is the contract we assert here.
    let ty_responds = (
      try {
        ^$ty --version o> /dev/null e> /dev/null
        true
      } catch {
        try {
          ^$ty --help o> /dev/null e> /dev/null
          true
        } catch { false }
      }
    )
    if $ty_responds {
      ok $"tywaves installed at ($ty) \(version/help exits 0\)"
    } else {
      fail $"tywaves at ($ty) does not respond to --help/--version"
    }

    # ---- 6. (optional) end-to-end JitPack -> firtool -------------
    if (($env.UHDI_E2E? | default "0") == "1") {
      run-e2e $snippet
    }
  } catch {|e|
    rm -rf $prefix
    error make {msg: $e.msg}
  }
  rm -rf $prefix

  print ""
  print "=== all tests passed ==="
}

def run-e2e [snippet: string] {
  section "e2e"
  if (which mill | is-empty) {
    skip "mill not on PATH; skipping e2e"
    return
  }

  let e2e = (mktemp -d | str trim)
  try {
    # Read tag from the chisel snippet (single source of truth).
    let ctag = (
      $snippet
      | parse --regex 'com\.github\.fkhaidari\.chisel.+:(?<t>v[^"]+-uhdi)'
      | get t?
      | default []
      | first
      | default ""
    )
    if ($ctag | is-empty) {
      error make {msg: "could not extract chisel tag from snippet"}
    }

    # Raw single-quote strings (no interpolation), then `str replace`
    # the placeholder. Avoids escaping parens/braces against nu's
    # `$"..."` interpolation parser.
    let scala_src = 'import chisel3._
import chisel3.util.Counter
import chisel3.stage.ChiselStage
class MinimalCounter extends Module {
  val io = IO(new Bundle {
    val out = Output(UInt(4.W))
  })
  val (count, _) = Counter(true.B, 16)
  io.out := count
}
object Main extends App {
  ChiselStage.emitFIRRTLDialect(new MinimalCounter)
}'
    $scala_src | save -f ($e2e | path join "MinimalCounter.scala")

    let mill_src = (
      '//| mill-version: 1.0.6-jvm
import mill._
import mill.scalalib._
import coursier.maven.MavenRepository

object app extends ScalaModule {
  def scalaVersion = "2.13.18"
  def mainClass = Task { Some("Main") }
  def repositoriesTask = Task.Anon {
    super.repositoriesTask() ++ Seq(MavenRepository("https://jitpack.io"))
  }
  def mvnDeps = Seq(
    ivy"com.github.fkhaidari.chisel::chisel:CHISEL_TAG",
    ivy"com.github.fkhaidari.chisel:chisel-plugin_2.13.18:CHISEL_TAG",
  )
  def scalacOptions = Task {
    val plugin = compileClasspath()
      .find(_.path.toString.contains("chisel-plugin"))
      .map(p => s"-Xplugin:${p.path}")
    Seq("-Ymacro-annotations") ++ plugin
  }
}' | str replace --all "CHISEL_TAG" $ctag
    )
    $mill_src | save -f ($e2e | path join "build.mill")

    print "Resolving chisel from JitPack and compiling..."
    cd $e2e
    try {
      ^mill app.compile
    } catch {
      error make {msg: "mill compile failed against JitPack"}
    }
    cd $REPO_ROOT
    ok "JitPack chisel resolved and compiled"
  } catch {|e|
    rm -rf $e2e
    error make {msg: $e.msg}
  }
  rm -rf $e2e
}
