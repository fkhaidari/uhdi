# Shared test-case dispatcher for tools/test/*.test.nu. Each test file
# builds a list of `{name, body}` records and calls `run-cases $cases`.
# Failures don't stop the run -- all cases execute, then the runner
# exits 1 if any failed.

# Run a list of {name, body: closure} cases. Prints `ok <name>` /
# `FAIL <name>: <msg>` per case, then a summary; exits 1 on any failure.
export def run-cases [cases: list<record>] {
  let results = (
    $cases | each {|c|
      let outcome = (
        try {
          do $c.body
          {ok: true msg: ""}
        } catch {|e|
          {ok: false msg: $e.msg}
        }
      )
      if $outcome.ok {
        print $"ok    ($c.name)"
      } else {
        print -e $"FAIL  ($c.name)\n      ($outcome.msg)"
      }
      $outcome
    }
  )
  let failed = ($results | where {|r| not $r.ok })
  print ""
  if ($failed | is-empty) {
    print $"all ($cases | length) tests passed"
  } else {
    print -e $"($failed | length) of ($cases | length) tests failed"
    exit 1
  }
}
