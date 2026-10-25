# check=skip=FromPlatformFlagConstDisallowed
# ldraw-nova app image: LeoCAD (pinned released AppImage) + Python + full LDraw parts library,
# plus a web app (chat with LLM agents that build and render LDraw models).
#
# No compiling: downloads an official, tagged LeoCAD-Linux-*.AppImage release
# and unpacks its embedded squashfs (no FUSE / --device needed), and bakes
# in the complete official LDraw parts library.
#
# Build and run (see docker-compose.yml / README.md):
#   docker compose up -d --build        # web app on http://localhost:8765
#   docker compose exec ldraw-nova-app bash # log in
#
# Pin versions explicitly:
#   docker build --build-context nova=.. --build-arg LEOCAD_TAG=v25.09 -t ldraw-nova-app .

# Base images are pinned by digest (the tag is only for readers): node:24-slim, ubuntu:24.04, resolved 2026-10-24.
# To update: docker buildx imagetools inspect <tag>, review, and replace the digests below (4 places).

# --- Stage 1: the web UI (React + Vite) --------------------------------------
# Runs on the build host's own architecture (fast, no emulation); its output is
# plain static files, so it doesn't matter that the final image is amd64.
FROM --platform=$BUILDPLATFORM node:24-slim@sha256:55fb5f5a527ea2c5c4a95f963e48ef90aa38c7b346054fdc8ee9957a2b55d205 AS frontend
WORKDIR /src
COPY web/frontend/package.json web/frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/frontend/ ./
RUN npm run build

# --- Stage 1b: the 3D viewer's GLB decoders (Draco, meshopt) ----------------
# Lockfile-pinned three.js, the same package the viewer's browser test uses.
FROM --platform=$BUILDPLATFORM node:24-slim@sha256:55fb5f5a527ea2c5c4a95f963e48ef90aa38c7b346054fdc8ee9957a2b55d205 AS viewer-deps
WORKDIR /src
COPY web/viewer-deps/package.json web/viewer-deps/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

# --- Download stages ---------------------------------------------------------
# Everything that is only fetched and unpacked happens in throwaway stages, so
# the big downloads (a) never depend on the runtime's apt layer or on any app
# file, (b) run in parallel, and (c) leave no archives or tools in the image.
# Each only changes when its own pinned URL / version / checksum changes.
# amd64 because bun (run by the mpd2glb stage) is an x86_64 binary.
FROM --platform=linux/amd64 ubuntu:24.04@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac AS fetch
ENV DEBIAN_FRONTEND=noninteractive
# Keep downloaded .debs in the apt cache mount (the stock image deletes them).
RUN rm -f /etc/apt/apt.conf.d/docker-clean \
    && echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl unzip squashfs-tools python3

# --- The reference models: unpacked from the repo's compressed shards ----------
# The repo stores models-packed/*.tar.xz (tools/models_pack.py); the toolkit reads
# data/models-annotated/*.mpd. Unpacking (every file checked against the manifest's
# sha256) happens here, so the image holds the folder but never the archives.
# Only changes when the shards or the tool do.
FROM fetch AS models
COPY --from=nova tools/models_pack.py /src/tools/models_pack.py
COPY --from=nova models-packed/ /src/models-packed/
RUN python3 /src/tools/models_pack.py --packed /src/models-packed --target /out/models-annotated unpack \
    && python3 /src/tools/models_pack.py --packed /src/models-packed --target /out/models-annotated verify

# --- LeoCAD: the pinned release's AppImage, unpacked --------------------------
# Check https://github.com/leozide/leocad/releases for available tags.
FROM fetch AS leocad
ARG LEOCAD_TAG=v25.09
# Checksum of that release's x86_64 AppImage. When changing LEOCAD_TAG, update this too (GitHub lists it as the
# asset "digest" on the release; verify it by downloading the file once).
ARG LEOCAD_SHA256=135140a17cd5d701a5d758907d88bff59e28d1f35277ef3fcac4d0a82e0ca467
# An AppImage is an ELF runtime with a squashfs image appended right after its
# section headers. We unpack that squashfs directly instead of running
# `./LeoCAD.AppImage --appimage-extract`: the AppImage's "AI\x02" marker in the
# ELF header padding makes emulated (e.g. Apple Silicon) builds fail with
# "Exec format error", and this way nothing is executed at build time at all.
RUN set -eux; \
    asset_url="https://github.com/leozide/leocad/releases/download/${LEOCAD_TAG}/LeoCAD-Linux-${LEOCAD_TAG#v}-x86_64.AppImage"; \
    echo "Downloading: ${asset_url}"; \
    curl -fsSL "$asset_url" -o /tmp/LeoCAD.AppImage; \
    echo "${LEOCAD_SHA256}  /tmp/LeoCAD.AppImage" | sha256sum -c -; \
    offset="$(python3 -c 'import struct,sys; h=open(sys.argv[1],"rb").read(64); shoff,=struct.unpack_from("<Q",h,0x28); shentsize,shnum=struct.unpack_from("<HH",h,0x3A); print(shoff+shentsize*shnum)' /tmp/LeoCAD.AppImage)"; \
    echo "squashfs offset: ${offset}"; \
    mkdir -p /opt/leocad; \
    unsquashfs -q -no-progress -o "$offset" -d /opt/leocad/squashfs-root /tmp/LeoCAD.AppImage; \
    rm /tmp/LeoCAD.AppImage

# --- The complete official LDraw parts library --------------------------------
# Produces /opt/ldraw/ldraw/{parts,p,models,LDConfig.ldr,...}
FROM fetch AS ldraw-lib
# library.ldraw.org publishes only one complete archive, always the newest release (this one: update 2026-09, file
# dated 2026-09-30); there is no versioned URL for it. So the build is pinned by checksum: when LDraw publishes a
# new release the check fails on purpose. To move on, download complete.zip, review it, and update the checksum.
ARG LDRAW_COMPLETE_SHA256=f3e496d0ede69ca7942fa4aa198e661ee2d22723906101e2e339d8917a4dd5f2
RUN mkdir -p /opt/ldraw \
    && curl -fsSL https://library.ldraw.org/library/updates/complete.zip -o /tmp/complete.zip \
    && echo "${LDRAW_COMPLETE_SHA256}  /tmp/complete.zip" | sha256sum -c - \
    && unzip -q /tmp/complete.zip -d /opt/ldraw \
    && rm /tmp/complete.zip

# Parts index for the agent tools (find_parts: part descriptions).
# Only depends on the library above and build_index.py, so other app edits
# don't rebuild it.
FROM fetch AS ldraw-index
COPY --from=ldraw-lib /opt/ldraw /opt/ldraw
COPY web/backend/build_index.py /opt/index/build_index.py
RUN python3 /opt/index/build_index.py /opt/ldraw/ldraw /opt/index

# --- The 3D viewer's JavaScript, textures and licence ------------------------
# The 3D viewer: library.ldraw.org's own source (MIT), which vendors the
# buildinginstructions.js viewer (Unlicense) it runs on. Pinned commit.
# -> /opt/web/viewer-vendor/{ldbi/js,ldbi/textures,js/ldraworgscene.js,js/WebGL.js}
FROM fetch AS viewer-vendor
ARG LDRAWORG_REF=a38efc7aca5b1171e888687939d77b6c0b228c7f
RUN set -eux; \
    mkdir -p /tmp/lor /opt/web/viewer-vendor/js; \
    curl -fsSL "https://codeload.github.com/ldraw-org/ldraworg-library/tar.gz/${LDRAWORG_REF}" \
        | tar -xz -C /tmp/lor --strip-components=1 --wildcards '*/public/assets/*' '*/LICENSE'; \
    cp -r /tmp/lor/public/assets/ldbi /opt/web/viewer-vendor/ldbi; \
    cp /tmp/lor/public/assets/js/ldraworgscene.js /tmp/lor/public/assets/js/WebGL.js /opt/web/viewer-vendor/js/; \
    cp /tmp/lor/LICENSE /opt/web/viewer-vendor/LICENSE-ldraworg-library.txt; \
    rm -rf /tmp/lor

# GLB loaders must match ldbi's Three.js r139 globals. Decoder assets come from
# the viewer-deps stage's pinned three.js (no runtime CDN).
RUN set -eux; \
    mkdir -p /opt/web/viewer-vendor/gltf; \
    for loader in GLTFLoader DRACOLoader; do \
        curl -fsSL "https://raw.githubusercontent.com/mrdoob/three.js/r139/examples/js/loaders/${loader}.js" \
            -o "/opt/web/viewer-vendor/gltf/${loader}.js"; \
    done; \
    curl -fsSL https://raw.githubusercontent.com/mrdoob/three.js/r139/LICENSE \
        -o /opt/web/viewer-vendor/gltf/LICENSE-three.txt
COPY --from=viewer-deps /src/node_modules/three/examples/jsm/libs/draco/gltf/ /opt/web/viewer-vendor/gltf/draco/
COPY --from=viewer-deps /src/node_modules/three/examples/jsm/libs/meshopt_decoder.module.js /opt/web/viewer-vendor/gltf/meshopt_decoder.module.js

# --- Bun runtime (for mpd2glb) -------------------------------------------------
# mpd2glb supports Node.js and Bun; measured on this image, Bun converts up to
# ~3.7x faster than Node (Deno: no faster) with byte-identical .glb output.
# The *baseline* build: the default one needs AVX2, which the emulated x86 CPU
# on Apple Silicon doesn't have. Pinned and checksum-verified.
FROM fetch AS bun
ARG BUN_VERSION=1.4.2
ARG BUN_SHA256=c678040f14fe0440eb839d37cbd0ce4c051a32da72806ac97de6a6aab6bf728f
RUN set -eux; \
    curl -fsSL "https://github.com/oven-sh/bun/releases/download/bun-v${BUN_VERSION}/bun-linux-x64-baseline.zip" -o /tmp/bun.zip; \
    echo "${BUN_SHA256}  /tmp/bun.zip" | sha256sum -c -; \
    unzip -q /tmp/bun.zip -d /tmp/bun; \
    install -m 0755 /tmp/bun/bun-linux-x64-baseline/bun /usr/local/bin/bun; \
    rm -rf /tmp/bun /tmp/bun.zip; \
    bun --version

# --- mpd2glb: LDraw -> glTF binary (.glb), keeping LDraw metadata per node ----
# https://github.com/anteloc/mpd2glb — pinned release, checksum-verified.
# Run it through scripts/mpd2glb.sh (below), not bun + the .mjs directly:
#   mpd2glb.sh -c none -l /opt/ldraw/ldraw -o out.glb model.mpd
FROM bun AS mpd2glb
ARG MPD2GLB_VERSION=0.9.1
ARG MPD2GLB_SHA256=381fb275c9f974820620ef4e4ec8b5be84ea8306a9f1ccd95bc75beaa8139f34
RUN set -eux; \
    curl -fsSL "https://github.com/anteloc/mpd2glb/releases/download/v${MPD2GLB_VERSION}/mpd2glb-${MPD2GLB_VERSION}.zip" -o /tmp/mpd2glb.zip; \
    echo "${MPD2GLB_SHA256}  /tmp/mpd2glb.zip" | sha256sum -c -; \
    unzip -q /tmp/mpd2glb.zip -d /tmp/mpd2glb; \
    mv "/tmp/mpd2glb/mpd2glb-${MPD2GLB_VERSION}" /opt/mpd2glb; \
    rm -rf /tmp/mpd2glb /tmp/mpd2glb.zip; \
    cd /opt/mpd2glb && bun install --production; \
    rm -rf /root/.bun/install/cache

# --- The 3D player: ldraw-player (Rust -> WebAssembly) ------------------------
# https://github.com/anteloc/ldraw.rs-nova (tools/player), its release zip,
# checksum-verified -> /opt/web/player-vendor/{ldraw_player.js,ldraw_player_bg.wasm,...}
# For now a local build from vendor/: the v0.8.0 release doesn't start in any
# browser (its CI's old wasm-opt exported the wrong table; fixed in the fork's
# build). Once a fixed release is on GitHub, the COPY goes back to:
#   RUN curl -fsSL "https://github.com/anteloc/ldraw.rs-nova/releases/download/v${LDRAW_PLAYER_VERSION}/ldraw-player-${LDRAW_PLAYER_VERSION}.zip" -o /tmp/ldraw-player.zip
FROM fetch AS player
ARG LDRAW_PLAYER_VERSION=0.8.1
ARG LDRAW_PLAYER_SHA256=1c96d79764393ec702106b5da7a9131594510135704ff02cb808f1ec77a1154f
COPY vendor/ldraw-player-${LDRAW_PLAYER_VERSION}.zip /tmp/ldraw-player.zip
RUN set -eux; \
    echo "${LDRAW_PLAYER_SHA256}  /tmp/ldraw-player.zip" | sha256sum -c -; \
    mkdir -p /opt/web; \
    unzip -q /tmp/ldraw-player.zip -d /tmp/ldraw-player; \
    mv "/tmp/ldraw-player/ldraw-player-${LDRAW_PLAYER_VERSION}" /opt/web/player-vendor; \
    rm -rf /tmp/ldraw-player /tmp/ldraw-player.zip; \
    test -f /opt/web/player-vendor/ldraw_player_bg.wasm

# --- Stage 2: the runtime image ----------------------------------------------
# LeoCAD only publishes x86_64 Linux AppImages, so the image is always amd64.
# On Apple Silicon / arm64 hosts Docker Desktop runs it under emulation.
FROM --platform=linux/amd64 ubuntu:24.04@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac

ENV DEBIAN_FRONTEND=noninteractive
RUN rm -f /etc/apt/apt.conf.d/docker-clean \
    && echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache

# --- All apt packages in one layer (it only changes when this list does) ------
# Runtime deps: Xvfb/Mesa for headless GL, Qt's X11 plugin deps, Python;
# curl/unzip for agents and the uv/jev-rerank steps below;
# poppler-utils (pdftotext, pdfinfo, pdftoppm, ...), ripgrep (rg), git for agents.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl unzip \
        xvfb x11-utils \
        libgl1 libglx-mesa0 libegl1 libgl1-mesa-dri libosmesa6 \
        libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-image0 \
        libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-shape0 \
        libxcb-xinerama0 libnss3 libdbus-1-3 fontconfig \
        python3 python3-pip \
        poppler-utils ripgrep git \
        iptables
# iptables: the entrypoint uses it to keep the `agent` user away from the app's own API (see entrypoint.sh).

# --- Unpacked downloads from the stages above ---------------------------------
COPY --from=leocad /opt/leocad /opt/leocad
RUN ln -s /opt/leocad/squashfs-root/AppRun /usr/local/bin/leocad
COPY --from=ldraw-lib /opt/ldraw /opt/ldraw
COPY --from=ldraw-index /opt/index /opt/index
# LeoCAD reads LEOCAD_LIB as its default parts-library path (equivalent to
# always passing --libpath). Any call can still override it with -l/--libpath.
ENV LEOCAD_LIB=/opt/ldraw/ldraw
COPY --from=viewer-vendor /opt/web/viewer-vendor /opt/web/viewer-vendor
COPY --from=bun /usr/local/bin/bun /usr/local/bin/bun
COPY --from=mpd2glb /opt/mpd2glb /opt/mpd2glb
COPY --from=player /opt/web/player-vendor /opt/web/player-vendor
# Before the app's own files below, so editing code never re-copies the 390 MB.
COPY --from=models /out/models-annotated /opt/ldraw-nova/data/models-annotated

# --- uv, and Python 3.14 managed by it (jev-rerank, and tools to come) ---------
# https://github.com/astral-sh/uv — pinned release, checksum-verified. Python
# 3.14 (PYTHON_VERSION) is `python3.14`, and the Python uv picks. `python3`
# stays Ubuntu's: the app and agents' scripts run on it, with the app's packages.
# Everything uv installs is shared: under /opt/uv, commands in /usr/local/bin.
ARG UV_VERSION=0.12.19
ARG UV_SHA256=23bf5552d220e0842b65c862097b2ebaeba0064b74eda5e565e77fd25969d8c8
ARG PYTHON_VERSION=3.14.6
ENV UV_PYTHON_INSTALL_DIR=/opt/uv/python \
    UV_PYTHON_BIN_DIR=/usr/local/bin \
    UV_TOOL_DIR=/opt/uv/tools \
    UV_TOOL_BIN_DIR=/usr/local/bin
# Build-only (not in the runtime env): the cache mount is another filesystem, so hardlinks can't work.
ARG UV_LINK_MODE=copy
# /root/.cache/uv is a build cache mount: reused across builds, never in the image.
RUN --mount=type=cache,target=/root/.cache/uv \
    set -eux; \
    curl -fsSL "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz" -o /tmp/uv.tar.gz; \
    echo "${UV_SHA256}  /tmp/uv.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/uv.tar.gz -C /tmp; \
    install -m 0755 /tmp/uv-x86_64-unknown-linux-gnu/uv /tmp/uv-x86_64-unknown-linux-gnu/uvx /usr/local/bin/; \
    rm -rf /tmp/uv.tar.gz /tmp/uv-x86_64-unknown-linux-gnu; \
    uv python install "${PYTHON_VERSION}"; \
    test "$(python3.14 -c 'import platform; print(platform.python_version())')" = "${PYTHON_VERSION}"

# --- jev-rerank: ranks text files / SQLite text fields against a query ---------
# https://github.com/anteloc/jev-rerank — pinned release (its source bundle,
# checksum-verified), installed as a uv tool on Python 3.14 with the release's
# locked dependency versions. Needs TYPESAFE_API_KEY when it runs.
ARG JEV_RERANK_VERSION=0.5.0
ARG JEV_RERANK_SHA256=7a2bc86b68779e5f835026df708f4c7f38e2364f9d17bee58ff7f2c17e04f03a
RUN --mount=type=cache,target=/root/.cache/uv \
    set -eux; \
    curl -fsSL "https://github.com/anteloc/jev-rerank/releases/download/v${JEV_RERANK_VERSION}/jev-rerank-${JEV_RERANK_VERSION}-source.zip" -o /tmp/jev-rerank.zip; \
    echo "${JEV_RERANK_SHA256}  /tmp/jev-rerank.zip" | sha256sum -c -; \
    unzip -q /tmp/jev-rerank.zip -d /tmp/jev-rerank; \
    cd "/tmp/jev-rerank/jev-rerank-${JEV_RERANK_VERSION}"; \
    uv export --frozen --no-dev --no-emit-project --no-hashes --output-file /tmp/jev-rerank-locked.txt; \
    uv tool install --python "${PYTHON_VERSION}" --constraints /tmp/jev-rerank-locked.txt .; \
    cd /; \
    rm -rf /tmp/jev-rerank /tmp/jev-rerank.zip /tmp/jev-rerank-locked.txt; \
    jev-rerank --help > /dev/null

# Software (llvmpipe) OpenGL rendering — works on any host, GPU or not.
ENV LIBGL_ALWAYS_SOFTWARE=1

# entrypoint.sh starts Xvfb on this display. It's set as image ENV (rather
# than only exported by the entrypoint) so shells opened with `docker exec`,
# which bypass the entrypoint, can run leocad too.
ENV DISPLAY=:99

# Qt warns on every call without a (0700) runtime dir; give it one up front.
ENV XDG_RUNTIME_DIR=/tmp/runtime-root
RUN mkdir -p -m 0700 "${XDG_RUNTIME_DIR}"

# --- Users: the server runs as root; agent-written code runs as `agent` -------
# /config holds LLM API keys: root-only, so agent code can't read them.
RUN useradd --system --create-home --shell /bin/bash agent \
    && install -d -m 0700 -o agent -g agent /tmp/runtime-agent \
    && install -d -m 0700 /config

# --- Python side of the app ---------------------------------------------------
WORKDIR /app
# Installed from the hash-locked closure only (requirements.lock, generated from requirements.txt):
# pip refuses any file whose sha256 is not listed, so a re-uploaded or newer release cannot slip in.
COPY requirements.txt requirements.lock /app/
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --break-system-packages --require-hashes --no-deps -r requirements.lock
ENV PYTHONPATH=/app
# Keep the standalone builder intact. Only its distributable inputs enter the
# image: never the sibling checkout's output, virtualenv, credentials or cache.
COPY --from=nova pyproject.toml uv.lock /opt/ldraw-nova/
RUN --mount=type=cache,target=/root/.cache/uv \
    cd /opt/ldraw-nova && uv sync --frozen --no-dev --no-install-project --python "${PYTHON_VERSION}"
COPY --from=nova ldraw_tools/ /opt/ldraw-nova/ldraw_tools/
COPY --from=nova data/ /opt/ldraw-nova/data/
COPY --from=nova docs/ /opt/ldraw-nova/docs/
COPY --from=nova examples/ /opt/ldraw-nova/examples/
COPY --from=nova prompts/ /opt/ldraw-nova/prompts/
COPY --from=nova *.md *.sh LICENSE CC-BY-SA-4.0 ldraw-agent /opt/ldraw-nova/
# The set catalog builder: tests/test_sets.py loads it, and the root tests run in this image
COPY --from=nova tools/build_set_catalog.py /opt/ldraw-nova/tools/build_set_catalog.py
# The CaDA set-list fetcher: tests/test_fetch_cada_sets.py imports it (parsing only, no network)
COPY --from=nova tools/fetch_cada_sets.py /opt/ldraw-nova/tools/fetch_cada_sets.py
# tests/test_fetch_cada_sets.py reads .gitignore; tests/test_templates.py reads the backend's registry-dump source
COPY --from=nova .gitignore /opt/ldraw-nova/.gitignore
COPY --from=nova app/web/backend/generator_bridge.py /opt/ldraw-nova/app/web/backend/generator_bridge.py
RUN --mount=type=cache,target=/root/.cache/uv \
    cd /opt/ldraw-nova && uv sync --locked --no-dev --python "${PYTHON_VERSION}" \
    && mkdir -p .cache && chown agent:agent .cache \
    && ln -s /data/output output \
    && chmod -R a+rX /opt/ldraw-nova \
    && chmod a+x ldraw-agent setup.sh check-model.sh prepare-glb.sh
ENV LDRAW_DIR=/opt/ldraw/ldraw LDRAW_NOVA_TOOLKIT_DIR=/opt/ldraw-nova

# Build the discovery catalogue (about 45 s, 94 MB) now, not inside the first chat's first search. It has to be
# built by the agent user, who reads it later (a root-owned 0600 cache would lock agent code out), and with the
# same paths the app uses at run time: the cache directory is keyed by the models, database and library paths.
RUN cd /opt/ldraw-nova \
    && runuser -u agent -- env XDG_CACHE_HOME=/opt/ldraw-nova/.cache ./ldraw-agent discover search parts wing --engine fts --limit 1 > /dev/null

# Precompute every bundled model's parts list now, so "what can I build" answers on the first request instead of after a
# warm-up of minutes. Keyed by file content and part names/sizes, so the cache stays valid across restarts and rebuilds.
RUN cd /opt/ldraw-nova \
    && runuser -u agent -- ./ldraw-agent inventory buildable --warm --max-build-seconds 3000 > /dev/null

# --- scripts/: the whole folder, on everyone's PATH ----------------------------
# Whatever is in scripts/ runs from anywhere in the container, by name, for
# every user (agents too: web/backend/sandbox.py). mpd2glb.sh is the way to
# run mpd2glb.
COPY scripts/ /opt/scripts/
ENV PATH="/opt/scripts:${PATH}"
RUN set -eux; \
    chmod -R a+rX /opt/scripts; \
    find /opt/scripts -type f -exec chmod a+x {} +; \
    mpd2glb.sh --help > /dev/null

COPY leocad_render.py example.py /app/
COPY web/backend/ /app/web/backend/
COPY web/viewer/ /opt/web/viewer/
COPY --from=frontend /src/dist/ /opt/web/static/

# --- Shared folder with the host --------------------------------------------
# /data/generated: the model collection; /data/chats: chat history;
# /data/output: agents' work folders (one per chat) and the CLI's default output.
RUN mkdir -p /data/generated /data/chats /data/output
# Showcase models: Docker copies them into a new, empty data volume on first start.
COPY data/generated/ /data/generated/
VOLUME ["/data", "/config"]

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "main:app", "--app-dir", "/app/web/backend", "--host", "0.0.0.0", "--port", "8000"]
