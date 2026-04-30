# syntax=docker/dockerfile:1.7
# uhdi-tools: prebuilt bench toolchain. Multi-stage; ARGs come from
# tools/versions.env so layer cache invalidates on SHA bumps.

# ---- shared build base ---------------------------------------------------
FROM ubuntu:24.04 AS base-build
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake ninja-build git ccache \
        python3 python3-dev python3-pip \
        openjdk-21-jdk \
        curl ca-certificates unzip xz-utils \
    && rm -rf /var/lib/apt/lists/*

# git-pin helper (clone+checkout doesn't see SHAs on non-default branches).
# Standalone file (not heredoc -- podman/buildah rejects the BuildKit form).
COPY git-pin /usr/local/bin/git-pin
RUN chmod +x /usr/local/bin/git-pin

# ---- circt (firtool with --emit-uhdi) ------------------------------------
FROM base-build AS circt-build
ARG CIRCT_URL
ARG CIRCT_REV
ARG LLVM_URL
ARG LLVM_REV
WORKDIR /src
RUN git-pin circt "$CIRCT_URL" "$CIRCT_REV" && \
    git-pin circt/llvm "$LLVM_URL" "$LLVM_REV"
# Unified LLVM+CIRCT cmake: CIRCT as an external project under LLVM so
# `--target firtool` walks the real dep graph and pulls all required
# MLIR static libs (the two-step form omitted them and ninja errored).
RUN --mount=type=cache,target=/root/.ccache cd /src/circt && \
    cmake -G Ninja -S llvm/llvm -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_C_COMPILER_LAUNCHER=ccache \
        -DCMAKE_CXX_COMPILER_LAUNCHER=ccache \
        -DLLVM_ENABLE_PROJECTS=mlir \
        -DLLVM_EXTERNAL_PROJECTS=circt \
        -DLLVM_EXTERNAL_CIRCT_SOURCE_DIR=/src/circt \
        -DLLVM_TARGETS_TO_BUILD=host \
        -DLLVM_ENABLE_ASSERTIONS=OFF \
        -DLLVM_BUILD_EXAMPLES=OFF \
        -DLLVM_INCLUDE_EXAMPLES=OFF \
        -DLLVM_INCLUDE_TESTS=OFF \
        -DLLVM_INCLUDE_BENCHMARKS=OFF \
        -DCIRCT_LLHD_SIM_ENABLED=OFF \
        -DCIRCT_BINDINGS_PYTHON_ENABLED=OFF \
        -DCMAKE_INSTALL_PREFIX=/opt/circt && \
    cmake --build build --target firtool && \
    (cmake --install build --component firtool 2>/dev/null || \
        (mkdir -p /opt/circt/bin && cp build/bin/firtool /opt/circt/bin/)) && \
    strip /opt/circt/bin/firtool

# ---- hgdb-circt (legacy --hgdb=<file>) -----------------------------------
FROM base-build AS hgdb-circt-build
ARG HGDB_CIRCT_URL
ARG HGDB_CIRCT_REV
ARG HGDB_CIRCT_LLVM_URL
ARG HGDB_CIRCT_LLVM_REV
WORKDIR /src
RUN git-pin hgdb-circt "$HGDB_CIRCT_URL" "$HGDB_CIRCT_REV" && \
    git-pin hgdb-circt/llvm "$HGDB_CIRCT_LLVM_URL" "$HGDB_CIRCT_LLVM_REV"
# Same unified-build pattern as circt-build (LLVM-16-era fork; same cmake vars).
RUN --mount=type=cache,target=/root/.ccache cd /src/hgdb-circt && \
    cmake -G Ninja -S llvm/llvm -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_C_COMPILER_LAUNCHER=ccache \
        -DCMAKE_CXX_COMPILER_LAUNCHER=ccache \
        -DLLVM_ENABLE_PROJECTS=mlir \
        -DLLVM_EXTERNAL_PROJECTS=circt \
        -DLLVM_EXTERNAL_CIRCT_SOURCE_DIR=/src/hgdb-circt \
        -DLLVM_TARGETS_TO_BUILD=host \
        -DLLVM_ENABLE_ASSERTIONS=OFF \
        -DLLVM_BUILD_EXAMPLES=OFF \
        -DLLVM_INCLUDE_TESTS=OFF \
        -DLLVM_INCLUDE_BENCHMARKS=OFF \
        -DCMAKE_INSTALL_PREFIX=/opt/hgdb-circt && \
    cmake --build build --target firtool && \
    (cmake --install build --component firtool 2>/dev/null || \
        (mkdir -p /opt/hgdb-circt/bin && cp build/bin/firtool /opt/hgdb-circt/bin/)) && \
    strip /opt/hgdb-circt/bin/firtool

# ---- hgdb-firrtl (Scala FIRRTL 1.x assembly) -----------------------------
FROM base-build AS hgdb-firrtl-build
ARG HGDB_FIRRTL_URL
ARG HGDB_FIRRTL_REV
# hgdb-firrtl pins sbt 1.4.x which calls setSecurityManager() in
# launcher init -- broken on JDK 18+. Install JDK 17 just for this stage.
RUN apt-get update && apt-get install -y --no-install-recommends \
        openjdk-17-jdk-headless \
    && rm -rf /var/lib/apt/lists/*
ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
ENV PATH="${JAVA_HOME}/bin:${PATH}"
RUN curl -fsSL https://raw.githubusercontent.com/sbt/sbt/v1.10.1/sbt -o /usr/local/bin/sbt && \
    chmod +x /usr/local/bin/sbt
WORKDIR /src
RUN git-pin hgdb-firrtl "$HGDB_FIRRTL_URL" "$HGDB_FIRRTL_REV"
# build.sbt's `assemblyOutputPath` puts the fat jar at bin/, not target/.
RUN cd /src/hgdb-firrtl && sbt assembly && \
    mkdir -p /opt/hgdb-firrtl/bin && \
    cp bin/hgdb-firrtl.jar /opt/hgdb-firrtl/bin/hgdb-firrtl.jar

# ---- hgdb (toml2hgdb + _hgdb python C extension) -------------------------
FROM base-build AS hgdb-py-build
ARG HGDB_URL
ARG HGDB_REV
WORKDIR /src
RUN git-pin hgdb "$HGDB_URL" "$HGDB_REV" && \
    cd hgdb && git submodule update --init --recursive
# Layout matches bench/runner.py expectations:
#   /opt/hgdb/bindings/python/{hgdb/, build/lib.<plat>-cpython-XYZ/, scripts/toml2hgdb}
# build/lib.* must be kept as a wrapper dir (not flattened) so
# runner.py's `(build).glob("lib.*")` detection fires.
RUN cd /src/hgdb/bindings/python && \
    pip install --break-system-packages --no-cache-dir \
        pybind11 setuptools wheel && \
    python3 setup.py build_ext --inplace && \
    mkdir -p /opt/hgdb/bindings/python/build && \
    cp -r build/lib.* /opt/hgdb/bindings/python/build/ && \
    cp -r hgdb /opt/hgdb/bindings/python/ && \
    mkdir -p /opt/hgdb/bindings/python/scripts && \
    cp scripts/toml2hgdb /opt/hgdb/bindings/python/scripts/

# ---- chisel forks (mill publishLocal) ------------------------------------
# Fully-qualified image ref: podman refuses short names without
# unqualified-search-registry config.
FROM docker.io/eclipse-temurin:21-jdk AS chisel-publish
ARG CHISEL_TYWAVES_URL
ARG CHISEL_TYWAVES_REV
ARG CHISEL_UHDI_URL
ARG CHISEL_UHDI_REV
ARG CHISEL_STOCK_VERSION
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://github.com/com-lihaoyi/mill/releases/download/0.11.12/0.11.12 \
        -o /usr/local/bin/mill && chmod +x /usr/local/bin/mill && \
    curl -fsSL https://github.com/coursier/coursier/releases/download/v2.1.10/cs-x86_64-pc-linux.gz \
        | gunzip > /usr/local/bin/cs && chmod +x /usr/local/bin/cs && \
    curl -fsSL https://raw.githubusercontent.com/sbt/sbt/v1.9.7/sbt \
        -o /usr/local/bin/sbt && chmod +x /usr/local/bin/sbt

# git-pin again -- eclipse-temurin doesn't share base-build's overlay.
COPY git-pin /usr/local/bin/git-pin
RUN chmod +x /usr/local/bin/git-pin

WORKDIR /src
# rameloni/chisel publishes via sbt (not mill); pinned SHA carries the
# git tag `v6.4.3-tywaves-SNAPSHOT` and emitVersion derives the publish
# coord via `git describe`. git-pin's shallow fetch strips tags, so we
# re-create the tag below.
#
# sbt repositories file overrides plugins.sbt's dead resolver; the
# `-Dsbt.override.build.repos=true` flag makes sbt ignore project resolvers.
RUN mkdir -p /root/.sbt && \
    printf '%s\n' \
        '[repositories]' \
        '  local' \
        '  local-preloaded-ivy: file:///root/.sbt/preloaded/, [organization]/[module]/(scala_[scalaVersion]/)(sbt_[sbtVersion]/)[revision]/[type]s/[artifact](-[classifier]).[ext]' \
        '  local-preloaded: file:///root/.sbt/preloaded/' \
        '  maven-central' \
        '  sbt-plugin-releases: https://repo.scala-sbt.org/scalasbt/sbt-plugin-releases/, [organization]/[module]/(scala_[scalaVersion]/)(sbt_[sbtVersion]/)[revision]/[type]s/[artifact](-[classifier]).[ext]' \
        '  typesafe-ivy-releases: https://repo.typesafe.com/typesafe/ivy-releases/, [organization]/[module]/(scala_[scalaVersion]/)(sbt_[sbtVersion]/)[revision]/[type]s/[artifact](-[classifier]).[ext]' \
        > /root/.sbt/repositories && \
    printf '%s\n' \
        'updateClassifiers := skip' \
        'update / classifiers := Nil' \
        'Global / update / classifiers := Nil' \
        'Global / updateClassifiers := skip' \
        > /root/.sbt/global.sbt && \
    mkdir -p /root/.config/coursier && \
    printf '%s\n' \
        'credentials += {"https://scala.jfrog.io/artifactory/sbt-plugin-releases/" -> ""}' \
        > /root/.config/coursier/credentials.properties 2>/dev/null || true
RUN git-pin chisel-tywaves "$CHISEL_TYWAVES_URL" "$CHISEL_TYWAVES_REV" && \
    cd chisel-tywaves && \
    git tag v6.4.3-tywaves-SNAPSHOT HEAD && \
    sbt -Dsbt.override.build.repos=true -Dsbt.ivy.home=/root/.ivy2 -Dsbt.coursier.home=/root/.cache/coursier \
        -Dsbt.log.format=true \
        "unipublish / publishLocal"
# chisel-uhdi uses Mill 1.0.6-jvm with build.mill (not build.sc); the
# repo's bundled ./mill launcher auto-downloads the matching jar.
# `unipublish[2.13]` bundles every public module into one artifact.
RUN git-pin chisel-uhdi "$CHISEL_UHDI_URL" "$CHISEL_UHDI_REV" && \
    cd chisel-uhdi && \
    ./mill unipublish[2.13].publishLocal
# Seed coursier cache with stock chisel for offline bench resolution.
RUN cs fetch "org.chipsalliance::chisel:${CHISEL_STOCK_VERSION}" \
        "org.chipsalliance:::chisel-plugin:${CHISEL_STOCK_VERSION}" \
        --scala-version 2.13.14 || true

# ---- surfer-tywaves (waveform viewer; binary renamed to `tywaves`) -------
# Mirrored from gitlab.com/rameloni/surfer-tywaves-demo to fkhaidari/surfer-tywaves.
FROM docker.io/rust:1.85-slim-bookworm AS tywaves-build
ARG TYWAVES_URL
ARG TYWAVES_REV
# Use system git for cargo fetches: libgit2 chokes on non-default-branch SHAs.
ENV CARGO_NET_GIT_FETCH_WITH_CLI=true
RUN apt-get update && apt-get install -y --no-install-recommends \
        git pkg-config libssl-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# spade's Cargo.lock pins tracing-tree at a deleted fork; the same SHA lives
# on the upstream. Rewrite transparently so `cargo --locked` still resolves.
RUN git config --global \
        url."https://github.com/davidbarsky/tracing-tree".insteadOf \
        "https://github.com/jonathanmorley/tracing-tree"
COPY git-pin /usr/local/bin/git-pin
RUN chmod +x /usr/local/bin/git-pin
WORKDIR /src
# 5 path-dep submodules (spade, fzcmd, f128, instruction-decoder, tywaves-rs);
# git-pin's shallow fetch leaves them empty, init explicitly.
RUN git-pin surfer-tywaves "$TYWAVES_URL" "$TYWAVES_REV" && \
    cd surfer-tywaves && git submodule update --init --recursive
# Cargo.toml package name is `surfer-tywaves`; rename to `tywaves` for users.
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    cd /src/surfer-tywaves && \
    cargo build --release --locked && \
    mkdir -p /opt/tywaves/bin && \
    cp target/release/surfer-tywaves /opt/tywaves/bin/tywaves && \
    strip /opt/tywaves/bin/tywaves

# ---- runtime image (what gets pushed) ------------------------------------
FROM ubuntu:24.04 AS runtime
ARG SCALA_CLI_VERSION
ENV DEBIAN_FRONTEND=noninteractive

# Ubuntu 24.04 ships python 3.12; bench deps work on any 3.11+.
RUN apt-get update && apt-get install -y --no-install-recommends \
        openjdk-21-jre-headless \
        python3 python3-venv python3-pip \
        git curl ca-certificates xz-utils \
    && rm -rf /var/lib/apt/lists/* && \
    ln -sf /usr/bin/python3 /usr/local/bin/python

# scala-cli — official tarball, pinned by version arg.
RUN curl -fsSL "https://github.com/VirtusLab/scala-cli/releases/download/v${SCALA_CLI_VERSION}/scala-cli-x86_64-pc-linux.gz" \
        | gunzip > /usr/local/bin/scala-cli && \
    chmod +x /usr/local/bin/scala-cli

COPY --from=circt-build      /opt/circt        /opt/circt
COPY --from=hgdb-circt-build /opt/hgdb-circt   /opt/hgdb-circt
COPY --from=hgdb-firrtl-build /opt/hgdb-firrtl /opt/hgdb-firrtl
COPY --from=hgdb-py-build    /opt/hgdb         /opt/hgdb
COPY --from=tywaves-build    /opt/tywaves      /opt/tywaves
COPY --from=chisel-publish   /root/.ivy2/local /opt/ivy2-local
COPY --from=chisel-publish   /root/.cache/coursier /opt/coursier-cache

# Default env -- discover_toolchain() picks these baked paths.
ENV FIRTOOL=/opt/circt/bin/firtool \
    HGDB_CIRCT_FIRTOOL=/opt/hgdb-circt/bin/firtool \
    HGDB_FIRRTL_JAR=/opt/hgdb-firrtl/bin/hgdb-firrtl.jar \
    HGDB_PY=/opt/hgdb/bindings/python \
    TYWAVES=/opt/tywaves/bin/tywaves \
    COURSIER_CACHE=/opt/coursier-cache \
    PATH=/opt/circt/bin:/opt/hgdb-circt/bin:/opt/tywaves/bin:/usr/local/bin:/usr/bin:/bin

# scala-cli reads ~/.ivy2/local; entrypoint symlinks /opt/ivy2-local
# at container start so the host UID's $HOME doesn't matter.
COPY entrypoint.sh /usr/local/bin/uhdi-tools-entrypoint
RUN chmod +x /usr/local/bin/uhdi-tools-entrypoint

WORKDIR /work
ENTRYPOINT ["/usr/local/bin/uhdi-tools-entrypoint"]
CMD ["bash"]
