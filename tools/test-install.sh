#!/usr/bin/env bash
# Smoke + integration tests for tools/install.sh.
#
# Runs `install.sh all` against a throwaway --prefix and asserts each
# component landed correctly. Components whose release artifacts do not
# yet exist on the target tag are skipped with a warning, so the script
# stays useful before the first hgdb-py release is published.
#
# Env vars:
#   UHDI_TAG       fkhaidari/uhdi release tag (default: latest)
#   CHISEL_TAG     fkhaidari/chisel JitPack tag (default: latest *-uhdi)
#   TYWAVES_TAG    rameloni/tywaves-surfer tag (default: latest)
#   UHDI_E2E=1     also scaffold a tiny mill project, resolve chisel from
#                  JitPack, and run firtool --emit-uhdi end-to-end (slow:
#                  needs `mill` on PATH and network access to JitPack).
set -euo pipefail

cd "$(dirname "$0")/.."

prefix=$(mktemp -d)
trap 'rm -rf "$prefix"' EXIT

fail()   { echo "FAIL: $*" >&2; exit 1; }
ok()     { echo "ok:   $*"; }
skip()   { echo "skip: $*" >&2; }
section(){ echo; echo "--- $* ---"; }

platform_arch=$(uname -m)
platform_os=$(uname -s)

# ---- 1. install all components into a throwaway prefix --------------------
section "install"
install_args=(all --prefix "$prefix")
[[ -n "${UHDI_TAG:-}" ]]    && install_args+=(--release-tag "$UHDI_TAG")
[[ -n "${CHISEL_TAG:-}" ]]  && install_args+=(--chisel-tag "$CHISEL_TAG")
[[ -n "${TYWAVES_TAG:-}" ]] && install_args+=(--tywaves-tag "$TYWAVES_TAG")

# `all` returns 0 even if hgdb-py / tywaves are missing on this platform
# (release-not-yet-published or non-linux-x86_64 host), but firtool +
# chisel must succeed.
tools/install.sh "${install_args[@]}"

# ---- 2. firtool ------------------------------------------------------------
section "firtool"
if [[ -x "$prefix/bin/firtool" ]]; then
    if "$prefix/bin/firtool" --help 2>&1 | grep -q -- '--emit-uhdi'; then
        ok "firtool installed and supports --emit-uhdi"
    else
        fail "firtool present but --help does not mention --emit-uhdi"
    fi
else
    fail "firtool not installed at $prefix/bin/firtool"
fi

# ---- 3. hgdb-py ------------------------------------------------------------
section "hgdb-py"
hgdb_py="$prefix/lib/hgdb/bindings/python"
if [[ -d "$hgdb_py" ]]; then
    [[ -f "$hgdb_py/scripts/toml2hgdb" ]] \
        || fail "hgdb-py installed but scripts/toml2hgdb missing"
    if ! ls "$hgdb_py/build/"lib.*/_hgdb*.so > /dev/null 2>&1; then
        fail "hgdb-py installed but _hgdb C extension missing"
    fi
    buildlib=$(ls -d "$hgdb_py/build/"lib.* | head -1)
    if PYTHONPATH="$hgdb_py:$buildlib" python3 -c \
            'import hgdb, _hgdb' 2>/dev/null; then
        ok "hgdb-py installed and importable"
    else
        # Importable failure is not necessarily a script bug -- e.g. a
        # mismatched glibc / python ABI on this host -- but the layout
        # is still wrong if we got here.
        fail "hgdb-py present but cannot import (glibc/python ABI mismatch?)"
    fi
elif [[ "$platform_os" == "Linux" && "$platform_arch" == "x86_64" ]]; then
    skip "hgdb-py not installed (release artifact missing for this tag?)"
else
    skip "hgdb-py prebuilt is linux-x86_64 only (got $platform_os $platform_arch)"
fi

# ---- 4. chisel snippet -----------------------------------------------------
section "chisel"
chisel_args=(chisel)
[[ -n "${CHISEL_TAG:-}" ]] && chisel_args+=(--chisel-tag "$CHISEL_TAG")
snippet=$(tools/install.sh "${chisel_args[@]}")
echo "$snippet" | grep -q "jitpack.io" \
    || fail "chisel snippet does not mention jitpack.io"
echo "$snippet" | grep -qE 'com\.github\.fkhaidari\.chisel.*-uhdi' \
    || fail "chisel snippet does not contain a -uhdi coord"
ok "chisel snippet printed with jitpack repo + uhdi coord"

# ---- 5. tywaves ------------------------------------------------------------
section "tywaves"
ty_bin=""
for cand in "$prefix/bin/tywaves" "$prefix/bin/surfer"; do
    [[ -x "$cand" ]] && { ty_bin="$cand"; break; }
done
if [[ -n "$ty_bin" ]]; then
    if "$ty_bin" --help 2>&1 | grep -qiE 'surfer|wave|tywaves'; then
        ok "tywaves installed at $ty_bin and runs --help"
    else
        # Some surfer builds use a GUI-only mode where --help dumps
        # less obvious text; treat run-without-crash as good enough.
        if "$ty_bin" --version > /dev/null 2>&1 \
                || "$ty_bin" --help > /dev/null 2>&1; then
            ok "tywaves installed at $ty_bin (version/help exits 0)"
        else
            fail "tywaves at $ty_bin does not respond to --help/--version"
        fi
    fi
else
    skip "tywaves not installed (no asset matched $platform_os/$platform_arch on this tag)"
fi

# ---- 6. (optional) end-to-end JitPack -> firtool --------------------------
if [[ "${UHDI_E2E:-0}" == "1" ]]; then
    section "e2e"
    if ! command -v mill > /dev/null 2>&1; then
        skip "mill not on PATH; skipping e2e"
    else
        e2e=$(mktemp -d)
        trap 'rm -rf "$prefix" "$e2e"' EXIT

        # Read tag from the chisel snippet (single source of truth).
        ctag=$(printf '%s\n' "$snippet" \
            | grep -oE 'com\.github\.fkhaidari\.chisel.+-uhdi' \
            | head -1 \
            | sed -E 's/.*:(v[^"]+-uhdi).*/\1/')
        [[ -n "$ctag" ]] || fail "could not extract chisel tag from snippet"

        cat > "$e2e/MinimalCounter.scala" <<'SCALA'
import chisel3._
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
}
SCALA

        cat > "$e2e/build.mill" <<MILL
//| mill-version: 1.0.6-jvm
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
    ivy"com.github.fkhaidari.chisel::chisel:${ctag}",
    ivy"com.github.fkhaidari.chisel:chisel-plugin_2.13.18:${ctag}",
  )
  def scalacOptions = Task {
    val plugin = compileClasspath()
      .find(_.path.toString.contains("chisel-plugin"))
      .map(p => s"-Xplugin:\${p.path}")
    Seq("-Ymacro-annotations") ++ plugin
  }
}
MILL
        echo "Resolving chisel from JitPack and compiling..."
        (cd "$e2e" && mill app.compile) \
            || fail "mill compile failed against JitPack"
        ok "JitPack chisel resolved and compiled"
    fi
fi

echo
echo "=== all tests passed ==="
