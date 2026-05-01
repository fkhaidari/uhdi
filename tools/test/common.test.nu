#!/usr/bin/env nu
# Unit tests for tools/lib/common.nu.

use ../lib/common.nu *
use _runner.nu *
use std/assert

def main [] {
  run-cases [
    # ---- parse-remote-owner ------------------------------------
    {
      name: "parse-remote-owner ssh"
      body: {||
        assert equal (parse-remote-owner "git@github.com:fkhaidari/uhdi.git") "fkhaidari"
      }
    }
    {
      name: "parse-remote-owner https"
      body: {||
        assert equal (parse-remote-owner "https://github.com/fkhaidari/uhdi.git") "fkhaidari"
      }
    }
    {
      name: "parse-remote-owner http no .git"
      body: {||
        assert equal (parse-remote-owner "http://github.com/Foo/bar") "foo"
      }
    }
    {
      name: "parse-remote-owner lowercases"
      body: {||
        assert equal (parse-remote-owner "git@github.com:OneTwo/three.git") "onetwo"
      }
    }
    {
      name: "parse-remote-owner gitlab"
      body: {||
        assert equal (parse-remote-owner "git@gitlab.com:foo/bar.git") "foo"
      }
    }

    # ---- parse-remote-slug -------------------------------------
    {
      name: "parse-remote-slug strips .git"
      body: {||
        assert equal (parse-remote-slug "https://github.com/fkhaidari/uhdi.git") "fkhaidari/uhdi"
      }
    }
    {
      name: "parse-remote-slug ssh"
      body: {||
        assert equal (parse-remote-slug "git@github.com:fkhaidari/uhdi.git") "fkhaidari/uhdi"
      }
    }
    {
      name: "parse-remote-slug no-suffix"
      body: {||
        assert equal (parse-remote-slug "git@github.com:foo/bar") "foo/bar"
      }
    }

    # ---- load-env ----------------------------------------------
    {
      name: "load-env round-trip"
      body: {||
        let tmp = (mktemp -t test-env.XXXXXX | str trim)
        "# comment line\nFOO=bar\n  # indented comment\n\nBAZ=quux\nMULTI=a=b=c\n" | save -f $tmp
        let parsed = (load-env $tmp)
        rm -f $tmp
        assert equal $parsed.FOO "bar"
        assert equal $parsed.BAZ "quux"
        # Values may contain `=` -- nu's `parse '{key}={value}'` only
        # splits on the first `=`. Verify the contract.
        assert equal $parsed.MULTI "a=b=c"
      }
    }
    {
      name: "load-env all-comments yields empty record"
      body: {||
        let tmp = (mktemp -t test-env.XXXXXX | str trim)
        "# only-comment\n\n# more\n" | save -f $tmp
        let parsed = (load-env $tmp)
        rm -f $tmp
        assert equal ($parsed | columns | length) 0
      }
    }

    # ---- detect-platform ---------------------------------------
    # On other hosts this assertion is a no-op; CI always lands on
    # linux-x86_64.
    {
      name: "detect-platform on linux-x86_64"
      body: {||
        let host = $"($nu.os-info.name)-($nu.os-info.arch)"
        if $host == "linux-x86_64" {
          assert equal (detect-platform) "linux-x86_64"
        }
      }
    }
  ]
}
