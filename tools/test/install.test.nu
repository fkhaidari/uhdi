#!/usr/bin/env nu
# Unit tests for tools/install.nu helpers. The big install-tarball-component
# and resolve-chisel-tag are network/filesystem-bound, so they're tested
# only via end-to-end test-install.nu; what we cover here is the pure
# logic each one delegates to.

use ../install.nu *
use _runner.nu *
use std/assert

def main [] {
  run-cases [
    # ---- resolve-prefix ----------------------------------------
    {
      name: "resolve-prefix empty -> default"
      body: {||
        let r = (resolve-prefix "")
        assert equal $r ($env.HOME | path join ".local/uhdi-tools")
      }
    }
    {
      name: "resolve-prefix passes through"
      body: {||
        assert equal (resolve-prefix "/opt/foo") "/opt/foo"
      }
    }

    # ---- ensure-writable ---------------------------------------
    {
      name: "ensure-writable proceeds when target missing"
      body: {||
        let target = $"/tmp/test-(random int 100000..999999)"
        assert equal (ensure-writable $target false) true
      }
    }
    {
      name: "ensure-writable skips existing target without --force"
      body: {||
        let target = (mktemp -t ew.XXXXXX | str trim)
        let proceed = (ensure-writable $target false)
        let still_there = ($target | path exists)
        rm -f $target
        assert equal $proceed false
        assert equal $still_there true
      }
    }
    {
      name: "ensure-writable removes existing target with --force"
      body: {||
        let target = (mktemp -t ew.XXXXXX | str trim)
        let proceed = (ensure-writable $target true)
        assert equal $proceed true
        assert equal ($target | path exists) false
      }
    }

    # ---- auth-curl-args ----------------------------------------
    {
      name: "auth-curl-args without GITHUB_TOKEN"
      body: {||
        with-env {GITHUB_TOKEN: ""} {
          assert equal (auth-curl-args) ["-fsSL"]
        }
      }
    }
    {
      name: "auth-curl-args with GITHUB_TOKEN"
      body: {||
        with-env {GITHUB_TOKEN: "abc123"} {
          assert equal (auth-curl-args) ["-fsSL" "-H" "Authorization: Bearer abc123"]
        }
      }
    }

    # ---- apply-platform-pattern --------------------------------
    {
      name: "apply-platform-pattern substitutes"
      body: {||
        assert equal (apply-platform-pattern "firtool-{platform}-*.tar.gz" "linux-x86_64") "firtool-linux-x86_64-*.tar.gz"
      }
    }
    {
      name: "apply-platform-pattern leaves untouched"
      body: {||
        assert equal (apply-platform-pattern "hgdb-py-linux-x86_64-*.tar.gz" "linux-x86_64") "hgdb-py-linux-x86_64-*.tar.gz"
      }
    }
    {
      name: "apply-platform-pattern multiple occurrences"
      body: {||
        assert equal (apply-platform-pattern "{platform}/x/{platform}" "macos-aarch64") "macos-aarch64/x/macos-aarch64"
      }
    }

    # ---- pick-uhdi-tag -----------------------------------------
    {
      name: "pick-uhdi-tag picks first match"
      body: {||
        assert equal (pick-uhdi-tag ["v0.1.0" "v0.1.1-uhdi" "v0.1.2-uhdi"]) "v0.1.1-uhdi"
      }
    }
    {
      name: "pick-uhdi-tag empty when none match"
      body: {||
        assert equal (pick-uhdi-tag ["v0.1.0" "v0.2.0"]) ""
      }
    }
    {
      name: "pick-uhdi-tag empty input"
      body: {||
        assert equal (pick-uhdi-tag []) ""
      }
    }

    # ---- env-hint-lines ----------------------------------------
    {
      name: "env-hint-lines all components, bin not on PATH"
      body: {||
        let lines = (env-hint-lines "/p" ["firtool" "hgdb-py" "tywaves"] ["/usr/bin" "/bin"])
        assert equal $lines [
          "=== Done ==="
          '  export FIRTOOL="/p/bin/firtool"'
          '  export HGDB_PY="/p/lib/hgdb/bindings/python"'
          '  export TYWAVES="/p/bin/tywaves"'
          '  export PATH="/p/bin:$PATH"'
        ]
      }
    }
    {
      name: "env-hint-lines firtool only"
      body: {||
        let lines = (env-hint-lines "/p" ["firtool"] ["/usr/bin"])
        assert equal $lines [
          "=== Done ==="
          '  export FIRTOOL="/p/bin/firtool"'
          '  export PATH="/p/bin:$PATH"'
        ]
      }
    }
    {
      name: "env-hint-lines skips PATH hint when bin already there"
      body: {||
        let lines = (env-hint-lines "/p" ["firtool"] ["/p/bin" "/usr/bin"])
        assert equal $lines [
          "=== Done ==="
          '  export FIRTOOL="/p/bin/firtool"'
        ]
      }
    }
    {
      name: "env-hint-lines hgdb-py-only doesn't print PATH hint"
      body: {||
        # hgdb-py lives under lib/, not bin/; no PATH change needed
        # even when bin isn't on PATH.
        let lines = (env-hint-lines "/p" ["hgdb-py"] ["/usr/bin"])
        assert equal $lines [
          "=== Done ==="
          '  export HGDB_PY="/p/lib/hgdb/bindings/python"'
        ]
      }
    }
    {
      name: "env-hint-lines empty did"
      body: {||
        let lines = (env-hint-lines "/p" [] ["/usr/bin"])
        assert equal $lines ["=== Done ==="]
      }
    }
  ]
}
