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
  glob $"($dir)/*.sv" | each { rm $in } | ignore
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

  let sv_files = (glob $"($dir)/*.sv")
  let sv_name = if ($sv_files | is-empty) { "<TopModule>.sv" } else { $sv_files.0 | path basename }
  let top = ($sv_name | path parse | get stem)

  print ""
  print "=== Done ==="
  print "  design.uhdi.json   - UHDI debug info"
  print "  design.dd          - HGLDD (for tywaves)"
  print "  design.db          - HGDB SQLite (for hgdb)"
  print $"  ($sv_name | fill -a left -w 18) - SystemVerilog"
  print ""
  print "Simulate (Chisel testbench): ./run.sh simulate"
  print $"  -> writes design.vcd and prints the tywaves command for ($top)"
}

# Just fetch firtool into <demo>/.bin/.
def "main download-only" [demo: string] {
  let dir = (demo-dir $demo)
  download-firtool $dir
}

# Drive the design with a Chisel testbench (chisel3.simulator). Each demo
# ships an `app/src/<Top>Sim.scala`. The Chisel sim emits
# design.uhdi.json + design.vcd in one firtool invocation, so the
# converters are re-run here too -- they bind to the freshly-emitted UHDI
# rather than whatever `Main` produced earlier.
def "main simulate" [demo: string] {
  let dir = (demo-dir $demo)

  let chisel_sim_files = (glob $"($dir)/app/src/*Sim.scala")
  if ($chisel_sim_files | is-empty) {
    print -e $"no app/src/*Sim.scala in ($dir); nothing to simulate"
    return
  }
  let sim_main = ($chisel_sim_files | first | path basename | str replace ".scala" "")
  let top = ($sim_main | str replace -r "Sim$" "")

  print $"Simulating via Chisel testbench \(($sim_main)) -> design.vcd..."
  download-firtool $dir
  cd $dir
  with-env {CHISEL_FIRTOOL_PATH: ($dir | path join ".bin")} {
    ^./millw app.runMain $sim_main
  }
  print "Re-running UHDI converters against sim-emitted UHDI..."
  with-env {PYTHONPATH: ($REPO_ROOT | path join "converter/src")} {
    ^python3 -m uhdi_to_hgldd design.uhdi.json -o design.dd
    ^python3 -m uhdi_to_hgdb  design.uhdi.json -o design.db
  }
  print ""
  print $"Open in tywaves: tywaves design.vcd --hgldd-dir . --top-module ($top) --extra-scopes TOP svsimTestbench dut"
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
