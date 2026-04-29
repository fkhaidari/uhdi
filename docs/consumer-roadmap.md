# Consumer roadmap

This document describes the *target* downstream-consumer experience
for the UHDI pipeline -- how a third-party Chisel project will pick up
the modified `chisel` + `firtool` and run the full UHDI projection
without cloning anything else.

**Status**: roadmap only.  The publish workflows referenced here do not
exist yet; this PR delivers the bench-side toolchain image (`tools/`)
that *generates* the same artifacts these future workflows will
publish.  Each item below lands in a follow-up PR scoped to one
artifact.

## Target downstream UX

A user who wants to use the modified pipeline in their own Chisel
project should need exactly:

1. One resolver line + one dep line in their `build.sc` / `build.sbt` /
   `//> using` block (modified Chisel).
2. One `curl | sh` (modified `firtool`) or its tarball equivalent.
3. One `pip install` (Python projector for `--emit-uhdi` output -> HGLDD
   / HGDB).

No git clones, no submodules, no `mill publishLocal` of anyone else's
fork on their machine.

## Artifacts and their hosts

### Modified `firtool` -- `fkhaidari/uhdi` GitHub Releases ✅ manual

`tools/release/release-firtool.sh` produces a release tarball. Three subcommands:

```sh
# Build from source (30-60 min):
tools/release/release-firtool.sh build

# Extract from prebuilt Docker image (30 sec):
tools/release/release-firtool.sh build --from-docker

# Publish to GitHub Release:
tools/release/release-firtool.sh build --from-docker --release firtool-v0.1.0

# End-user install (download from GitHub Releases):
tools/release/release-firtool.sh install
```

The build recipe is identical to the `circt-build` stage in
`tools/docker/Dockerfile` -- same cmake/ninja invocation, same LLVM submodule
pin from `tools/versions.env`. That makes the bench image and the
downstream tarballs share one source of truth for what "modified
firtool" means.

### Modified `chisel` -- JitPack from `fkhaidari/chisel` ✅ implemented

`tools/release/release-chisel.sh` rebases the release branch, ensures `jitpack.yml`,
tags, and pushes. JitPack auto-builds on tag -- no CI needed:

```sh
tools/release/release-chisel.sh v0.1.2
```

Same resolver works in `mill`, `sbt`, and `scala-cli`:

```scala
// build.sc (mill)
def repositoriesTask = T.task {
    super.repositoriesTask() ++ Seq(
        coursier.MavenRepository("https://jitpack.io"))
}
def ivyDeps = Agg(ivy"com.github.fkhaidari:chisel:<release-tag>")
```

```scala
// build.sbt
resolvers += "jitpack" at "https://jitpack.io"
libraryDependencies += "com.github.fkhaidari" % "chisel" % "<release-tag>"
```

```scala
// scala-cli
//> using repository "https://jitpack.io"
//> using dep "com.github.fkhaidari:chisel:<release-tag>"
```

#### One fork, merged patches

Chisel is single-classpath: a project can't depend on two `chisel`
forks at once.  To combine "tywaves" and "uhdi" pipelines downstream
the `fkhaidari/chisel` fork must hold a *single* coherent superset of
patches (uhdi debug intrinsics on top of tywaves).  This already
matches the bench's own arrangement -- `bench/src/uhdi_bench/compile.py`
has `_TYWAVES` and `_UHDI` Pipelines using the *same* artifact name
(`6.4.3-tywaves-SNAPSHOT`), reflecting that a single fork carries
both patch sets.

Combining `hgdb` is harder: the hgdb pipeline pins stock chisel 6.4.0,
which predates the tywaves patches.  Two reasonable directions:

- Backport the uhdi+tywaves patches onto the 6.4.0 base in a separate
  branch / artifact, e.g. `com.github.fkhaidari:chisel:6.4.0-uhdi-hgdb`.
- Forward-port hgdb's chisel-side hooks onto 6.4.3+, eliminating the
  separate base.

Decision deferred until the publish workflow lands.

### `uhdi-converter` -- PyPI

The Python package in `converter/` is already structured for a vanilla
PyPI publish (`converter/pyproject.toml` declares name, version,
deps).  A follow-up PR adds `.github/workflows/pypi-publish.yml` that
publishes on tag push.  Downstream:

```sh
pip install uhdi-converter
uhdi-to-hgldd input.uhdi.json -o input.dd
uhdi-to-hgdb  input.uhdi.json -o input.db
```

## What's *not* in this PR

- ~~`release.yml` in `fkhaidari/circt`~~ -- firtool release workflow now lives
  in this repo at `.github/workflows/release-firtool.yml` (done).
- ~~JitPack-friendly tag scheme + tag-on-push workflow in
   `fkhaidari/chisel`~~ -- done: `tools/release/release-chisel.sh` handles the
   two-branch pattern (`fk-sc/debug-info` → `fk-sc/debug-info-release` + tag).
- `.github/workflows/pypi-publish.yml` for `uhdi-converter`.
- A Scala "uhdi-stage" helper that wraps `--emit-uhdi` + projector in
  a single Chisel-side call (open question: ship as part of this
  workspace, or as a separate `fkhaidari/uhdi-stage` repo?).

Each is a follow-up PR with explicit scope.  This roadmap is the
contract that ties them together: the `tools/docker/Dockerfile` recipe is the
canonical build for what those workflows will produce, so any change
to "what a modified firtool / chisel means" lands here first and
propagates outward.
