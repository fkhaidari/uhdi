#!/usr/bin/env nu
# Shared runner for every demo/<name>/. The bash shim in each demo's
# run.sh locates `nu` and dispatches here, passing its directory name
# (gcd, fsm, fifo, pipeline, bus) as the `demo` arg.

use ../tools/lib/common.nu *

def main [demo: string] {
  main build $demo
}

# Build chisel -> firtool -> UHDI -> HGLDD/HGDB.
def "main build" [demo: string] {
  let dir = (demo-dir $demo)
  cd $dir
  download-firtool $dir

  print "Building Chisel -> UHDI..."
  rm -f design.uhdi.json design.dd design.db
  # Drop any previously-emitted top-module .sv so a renamed Main can't
  # leave a stale sibling next to the fresh one.
  glob $"($dir)/*.sv" | where {|p| ($p | path basename) != "tb.sv" } | each { rm $in } | ignore
  with-env {CHISEL_FIRTOOL_PATH: ($dir | path join ".bin")} {
    ^./millw app.runMain Main o> /dev/null
  }

  print "Converting UHDI -> HGLDD..."
  with-env {PYTHONPATH: ($REPO_ROOT | path join "converter/src")} {
    ^python3 -m uhdi_to_hgldd design.uhdi.json -o design.dd
  }

  print "Converting UHDI -> HGDB..."
  with-env {PYTHONPATH: ($REPO_ROOT | path join "converter/src")} {
    ^python3 -m uhdi_to_hgdb design.uhdi.json -o design.db
  }

  # Demo dirs may have a hand-written tb.sv next to firtool's emitted
  # top-module .sv; filter it out so we report the real top.
  let sv_files = (
    glob $"($dir)/*.sv"
    | where {|p| ($p | path basename) != "tb.sv" }
  )
  let sv_name = if ($sv_files | is-empty) { "<TopModule>.sv" } else { $sv_files.0 | path basename }

  print ""
  print "=== Done ==="
  print "  design.uhdi.json   - UHDI debug info"
  print "  design.dd          - HGLDD (for tywaves)"
  print "  design.db          - HGDB SQLite (for hgdb)"
  print $"  ($sv_name | fill -a left -w 18) - SystemVerilog"
  print ""
  print "Simulate:        ./run.sh simulate    (needs verilator + tb.sv)"
  print "Open in tywaves: tywaves --hgldd-dir . design.vcd"
}

# Just fetch firtool into <demo>/.bin/.
def "main download-only" [demo: string] {
  let dir = (demo-dir $demo)
  download-firtool $dir
}

# Run verilator against tb.sv, write design.vcd. Only gcd ships a TB.
def "main simulate" [demo: string] {
  let dir = (demo-dir $demo)
  if (which verilator | is-empty) {
    print -e "verilator not found, skipping VCD generation"
    return
  }
  let sv_files = (
    glob $"($dir)/*.sv"
    | where {|p| ($p | path basename) != "tb.sv" }
  )
  if ($sv_files | is-empty) {
    print -e $"no .sv in ($dir) \(run ./run.sh first\)"
    return
  }
  let sv = ($sv_files | first)
  let top = ($sv | path basename | str replace ".sv" "")
  let tb = ($dir | path join "tb.sv")
  if not ($tb | path exists) {
    print -e $"no testbench at ($tb); simulate is a no-op for this demo"
    print -e "  (only the gcd demo ships a TB out of the box)"
    return
  }
  print $"Simulating ($top) + tb.sv -> design.vcd..."
  rm -rf /tmp/demo_obj
  cd $dir
  let v_args = [
    "--binary"
    "--trace"
    "-j"
    "0"
    "-Wno-fatal"
    "-Wno-WIDTH"
    "-Wno-CASEINCOMPLETE"
    "-Wno-STMTDLY"
    "-Wno-INITIALDLY"
    $"+define+layers_($top)_Verification_Assert"
    $"+define+layers_($top)_Verification_Assume"
    $"+define+layers_($top)_Verification_Cover"
    "--top-module"
    "tb"
    "-Mdir"
    "/tmp/demo_obj"
    $sv
    $tb
  ]
  ^verilator ...$v_args o> /dev/null e> /dev/null
  ^/tmp/demo_obj/Vtb
  rm -rf /tmp/demo_obj
  print "Wrote: design.vcd"
}

# Start the hgdb-replay debug server. Reads design.vcd, exposes a hgdb
# WebSocket on the chosen port preloaded with design.db. Foregrounded
# so Ctrl-C in this terminal stops the server. Pair with `./run.sh debug`
# in a second terminal.
def "main debug-server" [demo: string --port: int = 8888] {
  let dir = (demo-dir $demo)
  cd $dir
  if not ("design.vcd" | path exists) {
    print -e "no design.vcd; run ./run.sh simulate first"
    return
  }
  if not ("design.db" | path exists) {
    print -e "no design.db; run ./run.sh first"
    return
  }
  let replay = (locate-hgdb-replay)
  print $"Listening on ws://localhost:($port) -- attach with: ./run.sh debug ($demo)"
  with-env {DEBUG_DATABASE_FILENAME: ($dir | path join "design.db")} {
    ^$replay design.vcd --port $port --debug
  }
}

# Attach the upstream `hgdb` console debugger (from `hgdb-debugger` on
# PyPI) to a running debug-server. Gdb-style: b/c/n/p, plus reverse
# debugging (step-back, rc, go) which pairs with hgdb-replay.
def "main debug" [demo: string --port: int = 8888] {
  let dir = (demo-dir $demo)
  let bin = (locate-hgdb-debugger)
  ^$bin $"localhost:($port)" ($dir | path join "design.db")
}

# Search order:
#   1. $HGDB_DEBUGGER override
#   2. ~/.local/uhdi-tools/cli-venv/bin/hgdb (preferred -- ABI-matched venv)
#   3. PATH lookup
def locate-hgdb-debugger [] {
  let override = ($env.HGDB_DEBUGGER? | default "")
  if (not ($override | is-empty)) and ($override | path exists) { return $override }
  let venv_bin = ($env.HOME | path join ".local/uhdi-tools/cli-venv/bin/hgdb")
  if ($venv_bin | path exists) { return $venv_bin }
  let from_path = (which hgdb)
  if (not ($from_path | is-empty)) { return ($from_path | first | get path) }
  error make {msg: "hgdb console debugger not found; install with: pip install hgdb-debugger (and link the workspace's hgdb python bindings into the same venv)"}
}

def locate-hgdb-replay [] {
  # 1. Honour explicit override.  2. PATH lookup. 3. Sibling practice/hgdb checkout.
  let override = ($env.HGDB_REPLAY? | default "")
  if (not ($override | is-empty)) and ($override | path exists) { return $override }
  let from_path = (which hgdb-replay)
  if (not ($from_path | is-empty)) { return ($from_path | first | get path) }
  let sibling = ($REPO_ROOT | path dirname | path join "hgdb/build/tools/hgdb-replay/hgdb-replay")
  if ($sibling | path exists) { return $sibling }
  error make {msg: "hgdb-replay not found; build it from upstream hgdb (https://github.com/Kuree/hgdb) and put it on $PATH or set $HGDB_REPLAY"}
}

# ---- helpers ---------------------------------------------------------------

def demo-dir [demo: string]: nothing -> path {
  let dir = ($REPO_ROOT | path join "demo" $demo)
  if not ($dir | path exists) {
    error make {msg: $"demo not found: ($dir)"}
  }
  $dir
}

# Resolve order: cache > docker image > GitHub Releases.
def download-firtool [dir: path] {
  let firtool = ($dir | path join ".bin/firtool")
  if ($firtool | path exists) {
    print $"firtool already at ($firtool)"
    return
  }
  mkdir ($dir | path join ".bin")

  if (not (which docker | is-empty)) {
    print "Downloading firtool from Docker image..."
    docker-extract (image-ref) "/opt/circt/bin/firtool" $firtool
    chmod +x $firtool
  } else if ((not (which gh | is-empty)) and (gh-authed)) {
    print "Downloading firtool from GitHub Releases..."
    let tag = (
      ^gh release list -R fkhaidari/uhdi --limit 1 --json tagName -q '.[0].tagName'
      | str trim
    )
    let dl_dir = "/tmp/firtool-dl"
    mkdir $dl_dir
    ^gh release download $tag -R fkhaidari/uhdi -D $dl_dir -p 'firtool-linux-*.tar.gz'
    let tarballs = (glob $"($dl_dir)/firtool-linux-*.tar.gz")
    ^tar -xzf ($tarballs | first) -C ($dir | path join ".bin")
    rm -rf $dl_dir
    chmod +x $firtool
  } else {
    error make {msg: "need docker or gh CLI to download firtool"}
  }
  print $"firtool: ($firtool)"
}

def gh-authed []: nothing -> bool {
  try {
    ^gh auth status o> /dev/null e> /dev/null
    true
  } catch { false }
}
