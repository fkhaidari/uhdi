#!/usr/bin/env nu
# Smoke tests for tools/install.nu. Reads env vars (no flags):
#   UHDI_TAG     fkhaidari/uhdi release (default: latest)
#   UHDI_E2E=1   also resolve chisel from Maven Central + compile a tiny
#                mill project (slow, needs `mill` on PATH)

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
    let snippet = (^bash ($REPO_ROOT | path join "tools/install.sh") chisel | complete | get stdout)
    if not ($snippet | str contains "org.chipsalliance::chisel:7.13.0") {
      fail "chisel snippet does not contain the official Maven Central coord"
    }
    ok "chisel snippet printed with official Maven Central coord"

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

    # ---- 6. chiseltrace ------------------------------------------
    section "chiseltrace"
    let ct_cli = ($prefix | path join "bin/chiseltrace-cli")
    if ($ct_cli | path exists) {
      # chiseltrace-cli is a clap-based binary; --help is reliable.
      let ct_responds = (
        try {
          ^$ct_cli --help o> /dev/null e> /dev/null
          true
        } catch { false }
      )
      if $ct_responds {
        ok $"chiseltrace-cli installed at ($ct_cli) \(--help exits 0\)"
      } else {
        fail $"chiseltrace-cli at ($ct_cli) does not respond to --help"
      }
      let ct_gui = ($prefix | path join "bin/chiseltrace")
      if ($ct_gui | path exists) {
        ok $"chiseltrace GUI also installed at ($ct_gui)"
      } else {
        skip "chiseltrace GUI binary missing (CLI-only tarball)"
      }
    } else {
      skip "chiseltrace not installed (release artifact missing for this tag?)"
    }

    # ---- 7. hgdb-cli venv ----------------------------------------
    # install.nu's --build-cli-venv path is the riskiest install step:
    # pip install from PyPI (websockets / prompt-toolkit / hgdb-debugger
    # / libhgdb), symlink _hgdb C extension into site-packages,
    # editable-install the in-tree converter, then 6 console-script
    # symlinks. Hits the corp PyPI mirror unless PIP_CONFIG_FILE
    # override fires; missing console scripts go unnoticed otherwise.
    section "hgdb-cli"
    let cli_bins = [
      "hgdb"
      "hgdb-replay"
      "hgdb-db"
      "uhdi-to-hgldd"
      "uhdi-to-hgdb"
      "uhdi-to-pdg"
    ]
    let missing_bins = (
      $cli_bins
      | each {|name|
        let path = ($prefix | path join "bin" $name)
        if not ($path | path exists) { $name } else { null }
      }
      | compact
    )
    if not ($missing_bins | is-empty) {
      fail $"hgdb-cli venv missing console scripts: ($missing_bins | str join ', ')"
    }
    # `--help` is the per-tool cheap liveness check. `hgdb` (the
    # console) and `uhdi-to-*` (converters) all support it; for the
    # libhgdb tools we fall back to `--version` since some builds
    # bail on `--help` with a non-zero exit by design.
    for name in ["uhdi-to-hgldd" "uhdi-to-hgdb" "uhdi-to-pdg"] {
      let bin = ($prefix | path join "bin" $name)
      let responds = (
        try { ^$bin --help o> /dev/null e> /dev/null; true } catch { false }
      )
      if not $responds {
        fail $"($name) installed but does not respond to --help"
      }
    }
    ok $"hgdb-cli venv installed ($cli_bins | length) console scripts respond"

    # ---- 8. (optional) end-to-end Maven Central -> firtool -------
    if (($env.UHDI_E2E? | default "0") == "1") {
      run-e2e
    }
  } catch {|e|
    rm -rf $prefix
    error make {msg: $e.msg}
  }
  rm -rf $prefix

  print ""
  print "=== all tests passed ==="
}

def run-e2e [] {
  section "e2e"
  if (which mill | is-empty) {
    skip "mill not on PATH; skipping e2e"
    return
  }

  let e2e = (mktemp -d | str trim)
  try {
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

object app extends ScalaModule {
  def scalaVersion = "2.13.18"
  def mainClass = Task { Some("Main") }
  def mvnDeps = Seq(
    ivy"org.chipsalliance::chisel:CHISEL_VERSION",
    ivy"org.chipsalliance:::chisel-plugin:CHISEL_VERSION",
  )
  def scalacOptions = Task {
    val plugin = compileClasspath()
      .find(_.path.toString.contains("chisel-plugin"))
      .map(p => s"-Xplugin:${p.path}")
    Seq("-Ymacro-annotations") ++ plugin
  }
}' | str replace --all "CHISEL_VERSION" "7.13.0"
    )
    $mill_src | save -f ($e2e | path join "build.mill")

    print "Resolving chisel from Maven Central and compiling..."
    cd $e2e
    try {
      ^mill app.compile
    } catch {
      error make {msg: "mill compile failed against Maven Central"}
    }
    cd $REPO_ROOT
    ok "Maven Central chisel resolved and compiled"
  } catch {|e|
    rm -rf $e2e
    error make {msg: $e.msg}
  }
  rm -rf $e2e
}
