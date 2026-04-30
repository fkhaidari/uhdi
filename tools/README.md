# tools/ -- bench toolchain image

Builds `ghcr.io/fkhaidari/uhdi-tools:<tag>`, a Linux/amd64 image with
prebuilt copies of every fork the bench needs:

| Path inside image | What it is | Source repo (pinned in `versions.env`) |
|-------------------|------------|----------------------------------------|
| `/opt/circt/bin/firtool` | `firtool --emit-uhdi` | `CIRCT_URL` @ `CIRCT_REV` |
| `/opt/hgdb-circt/bin/firtool` | hgdb-circt `--hgdb=<file>` | `HGDB_CIRCT_URL` @ `HGDB_CIRCT_REV` |
| `/opt/hgdb-firrtl/bin/hgdb-firrtl.jar` | Scala FIRRTL 1.x assembly | `HGDB_FIRRTL_URL` @ `HGDB_FIRRTL_REV` |
| `/opt/hgdb/bindings/python/` | toml2hgdb + `_hgdb` C ext | `HGDB_URL` @ `HGDB_REV` |
| `/opt/ivy2-local/` (-> `~/.ivy2/local/`) | mill publishLocal of both Chisel forks | `CHISEL_TYWAVES_*` + `CHISEL_UHDI_*` |
| `/opt/coursier-cache/` | seeded Maven cache w/ stock chisel | `CHISEL_STOCK_VERSION` |

The image also has `python3` (3.12 on Ubuntu 24.04), `pip`,
`scala-cli`, JRE 21, and `git` on `PATH`. It deliberately does
**not** preinstall `uhdi_converter` / `uhdi_bench` -- the converter
ships from the workspace via
`pip install -e ./converter -e ./bench[dev]` per CI invocation.

## Installing the toolchain in a downstream Chisel project

`tools/install.sh` is the consumer-side installer. It pulls every
binary (firtool, hgdb-py, tywaves) from a single GitHub Release on
`fkhaidari/uhdi` and prints the JitPack snippet for the chisel fork.
**No Docker required on the consumer side.**

```sh
# from a checkout
tools/install.sh all --prefix ~/.local/uhdi-tools

# or curl-pipe (no checkout needed)
curl -fsSL https://raw.githubusercontent.com/fkhaidari/uhdi/main/tools/install.sh \
    | bash -s -- all
```

Subcommands: `firtool`, `hgdb-py`, `chisel` (prints snippet only),
`tywaves`, `all`. Each accepts `--prefix DIR` (default
`$HOME/.local/uhdi-tools`); pin a release with `--release-tag` (one
release on `fkhaidari/uhdi` carries firtool, hgdb-py, and tywaves) or
`--chisel-tag` (separate, JitPack tag on `fkhaidari/chisel`). Hint
exports are printed at the end so the same install plugs into
`bench/runner.py`'s env-var discovery.

Caveats:

- `firtool` ships for all four platforms (linux x86_64/aarch64,
  macos x86_64/aarch64).
- `hgdb-py` is **linux-x86_64-only** for now -- the C extension is
  built against the uhdi-tools image's glibc. Other platforms fall
  back to `tools/release/release-hgdb-py.sh build`.
- `chisel` is JVM-only. `install.sh chisel` writes nothing; it just
  prints the resolver + coord block to paste into `build.mill` /
  `build.sbt` / `scala-cli`. The build tool fetches it from JitPack
  on first compile.
- `tywaves` is the surfer waveform viewer with tywaves rendering
  patches; built from a mirror of
  `gitlab.com/rameloni/surfer-tywaves-demo` at
  `fkhaidari/surfer-tywaves`. Currently linux-x86_64 only. Other
  platforms fall back to `tools/release/release-tywaves.sh build`
  (needs Rust 1.75+).

## Running the image

```sh
TAG=$(cat tools/docker/image-tag.txt)
docker run --rm -v "$PWD":/work -w /work \
    ghcr.io/fkhaidari/uhdi-tools:$TAG \
    bash -c 'pip install -e ./converter -e ./bench[dev] && cd bench && pytest -v'
```

For an interactive shell (debugging cell failures inside the image):

```sh
docker run --rm -it -v "$PWD":/work ghcr.io/fkhaidari/uhdi-tools:$(cat tools/docker/image-tag.txt)
```

## Local development without Docker

The image is a *convenience*, not a requirement.  `bench/runner.py`
still honours the `FIRTOOL`, `HGDB_CIRCT_FIRTOOL`, `HGDB_FIRRTL_JAR`,
`HGDB_PY` env vars, and the sibling-checkout fallback layout
(`../../circt/build/bin/firtool`, etc.) is preserved as the last
resort. Hack on the forks natively, then point env vars at your
local builds.

---

The remaining sections are for the repository owner: how the image
tag is derived, when CI rebakes the image, and how to cut releases
of firtool / hgdb-py / chisel. Day-to-day consumers don't need any
of it.

## Pre-commit hook (recommended)

Wire `tools/docker/git-hooks/pre-commit` into your clone so a commit that
edits `versions.env` / `docker/Dockerfile` / `docker/entrypoint.sh` without
recomputing `image-tag.txt` is rejected locally:

```sh
git config core.hooksPath tools/docker/git-hooks
```

This sets the hooks dir at the repo level only -- nothing leaks into
`~/.gitconfig`. The hook just calls `tools/docker/check-tag.sh`, which is
the same script the `build-tools` workflow runs server-side, so a
local pass means server-side will pass too.  Bypass for a single
commit with `git commit --no-verify`.

## Bumping a fork pin

1. Edit `tools/versions.env`. Replace the `*_REV=...` SHA with the
   commit you want pinned (or change a URL if you're pointing at a
   different fork).
2. If you changed `tools/docker/Dockerfile` or `tools/docker/entrypoint.sh` too, no
   extra step -- the tag picks them up.
3. Recompute the image tag:

   ```sh
   tools/docker/compute-tag.sh > tools/docker/image-tag.txt
   ```

   (The pre-commit hook above forces this if you forget.)
4. Commit `versions.env` + `image-tag.txt` (and any Dockerfile changes)
   in the same PR. Push.
5. The `build-tools` workflow runs on `tools/**` changes; it rebakes
   the image and pushes `ghcr.io/<owner>/uhdi-tools:<new-tag>` plus
   `:latest` (only on `main`).
6. Wait for `build-tools` to go green before merging. The `bench` CI
    job pulls the tag from `tools/docker/image-tag.txt`; if the build-tools
   workflow hasn't published it yet, the bench job fails loudly with
   a missing-manifest error -- by design, no silent stale image.

## Why a deterministic tag

The image tag is `sha256(versions.env + docker/Dockerfile + docker/entrypoint.sh)`,
truncated to 16 hex chars. That means:

- The CI bench job knows exactly which tag to `docker pull` by reading
  the committed `tools/docker/image-tag.txt`. No race against workflow latency.
- A bumper who forgets to update `image-tag.txt` is caught by the
  `build-tools` workflow's "image-tag.txt out of sync" check.
- Identical inputs always rebuild the same tag -- the GHA buildx cache
  short-circuits unchanged stages.

## Building locally / publishing by hand

CI publishes the image automatically when `tools/**` lands on `main`.
For first-time bootstrap (the package doesn't exist on GHCR yet,
so PRs can't pull it) or when iterating on the Dockerfile itself,
build + push directly:

```sh
# Build only -- no push, just verify the recipe compiles.
tools/docker/build-and-push.sh

# Build + push to ghcr.io/<owner>/uhdi-tools:<tag-from-image-tag.txt>.
# `--owner` defaults to the lowercased basename of `git remote get-url origin`.
echo $GHCR_TOKEN | docker login ghcr.io -u <username> --password-stdin
tools/docker/build-and-push.sh --push
```

Auth: GHCR accepts a personal access token with `write:packages` scope,
or `gh auth token` (when the user has `gh` configured with that scope).
First build is 30-60 minutes (CIRCT + LLVM compile from source).

## Maintainer release flow

Per-component release scripts live in `tools/release/`. Each one
operates on its own artifact and tags so the workflow scales:

| Script | Artifact | Tag convention |
|--------|----------|----------------|
| `release-firtool.sh build --from-docker --release <tag>` | `firtool-${platform}-${tag}.tar.gz` on `fkhaidari/uhdi` | `firtool-vX.Y.Z` |
| `release-hgdb-py.sh build --from-docker --release <tag>` | `hgdb-py-linux-x86_64-${tag}.tar.gz` on `fkhaidari/uhdi` | upload to firtool's tag |
| `release-tywaves.sh build --from-docker --release <tag>` | `tywaves-${platform}-${tag}.tar.gz` on `fkhaidari/uhdi` | upload to firtool's tag |
| `release-chisel.sh <tag>` | JitPack build at `https://jitpack.io/#fkhaidari/chisel/<tag>` | `vX.Y.Z-uhdi` |

`release-hgdb-py.sh` and `release-tywaves.sh` both use
`gh release upload --clobber` if the tag exists, so attaching
their tarballs to firtool's release is just running them with the
same tag.

`tools/test-install.sh` runs `install.sh all` against a throwaway
prefix and asserts each component landed correctly. Use it as a
post-release smoke test:

```sh
UHDI_TAG=firtool-v0.1.1 tools/test-install.sh
# UHDI_E2E=1 also scaffolds a tiny mill project, resolves chisel from
# JitPack, and runs firtool --emit-uhdi end-to-end (needs `mill`).
```
