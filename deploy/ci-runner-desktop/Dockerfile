# Railway "Ubuntu Desktop" + omega-ci runner agent overlay.
#
# NOT DEPLOYED. Building/deploying this image is the paid cutover that is HELD
# for the operator (D-120-10). See README.md.
#
# Build context: repository root, e.g.
#   docker build -f deploy/ci-runner-desktop/Dockerfile .
#
# Supply chain (every input immutable and checksum-verified; the build fails
# closed on any mismatch). Evidence is recorded in README.md.

# Upstream XavTo/Railway-Desktop-Pro ubuntu/Dockerfile @ a915efba pins this base.
FROM accetto/ubuntu-vnc-xfce-g3@sha256:de1b03759a37b78a62a0b4f25d1e29a54e7500bbc2101109b2430a5574e055b2

ARG UPSTREAM_REPO=XavTo/Railway-Desktop-Pro
ARG UPSTREAM_COMMIT=a915efba6d6a2c4b90f266f34fd8b56c1a446703
ARG UPSTREAM_ENTRYPOINT_SHA256=d1bc4e0373179a614979d6efdb31356aa95f0af37d896b5573010d4f3f7c8b82
ARG RUNNER_VERSION=2.338.1
ARG RUNNER_SHA256=08e9ccb3604d2462c24b5e33604dc30810296ca15cc65f23cffd4317eb144c37
ARG UVICORN_WHL=uvicorn-0.54.0-py3-none-any.whl
ARG UVICORN_URL=https://files.pythonhosted.org/packages/38/0c/b54a4fdd7f90a3af8b02ebc9ce6712c2c208b7926a2f7bad95c33ebbe943/uvicorn-0.54.0-py3-none-any.whl
ARG UVICORN_SHA256=505bdb0f318731d45f1f712071fc781a8981f6847a31c902c9f5e652d4f67faf
ARG CLICK_WHL=click-8.5.0-py3-none-any.whl
ARG CLICK_URL=https://files.pythonhosted.org/packages/58/50/6c0d534c5f134586a8e1ba4e330569e32f057e33372ae556463212fb4cd3/click-8.5.0-py3-none-any.whl
ARG CLICK_SHA256=255bc9599cf7748b4b1a446ccc735421bd08a2ae529a8b88597d3de5664ee360
ARG H11_WHL=h11-0.16.0-py3-none-any.whl
ARG H11_URL=https://files.pythonhosted.org/packages/04/4b/29cac41a4d98d144bf5f6d33995617b185d14b22401f75ca86f384e87ff1/h11-0.16.0-py3-none-any.whl
ARG H11_SHA256=63cf8bbe7522de3bf65932fda1d9c2772064ffb3dae62d55932da54b31cb6c86
ARG JOB_UID=1500
ARG JOB_GID=1500
# Toolchains (00056). Evidence for every pin: README.md "Toolchain provenance".
ARG APT_SNAPSHOT=20261023T000000Z
ARG GH_VERSION=2.103.1
ARG GH_SHA256=57500f347b5e84efb6578c9b644eeb3b4e54046762635a939dd0ee2cca1c1add
ARG GITLEAKS_VERSION=8.30.1
ARG GITLEAKS_SHA256=551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb
ARG TRIVY_VERSION=0.76.1
ARG TRIVY_SHA256=c7cbe3137fb8a48a17574bede3dd6beb16742d1e9772929940fc90082c39d9c0
ARG GREPTIME_VERSION=0.14.4
ARG GREPTIME_SHA256=50166e3f429327f6e4003d62d7b3eb9010345346d855a2d4442a7d73e900e787
ARG PY310_VERSION=3.10.22
ARG PY310_BUILD=36876129702
ARG PY310_SHA256=4c96c0e9380150d436c38be322bafa04a5632db7eb2def147b7553ed7e88fd16
ARG PY312_VERSION=3.12.15
ARG PY312_BUILD=36805895057
ARG PY312_SHA256=fcb672b0437c2d8df5565b7cc250b2b0d7455e2b8d6a4a107cfa6f2efe4f7ed8
ARG PY313_VERSION=3.13.17
ARG PY313_BUILD=39711892467
ARG PY313_SHA256=f0b66c1cb0614c6ab2b8ff7500e93c3b34bf3691eb5073050f9e4e56a513e932
ARG PY314_VERSION=3.14.9
ARG PY314_BUILD=39619022138
ARG PY314_SHA256=987af6a6858a59529d8f66a54d6484c7bc8127bafc19e651134ad3e01ea578d4
ARG CODEQL_VERSION=2.27.3
ARG CODEQL_SHA256=64518dc413c8e7b5733fee2489e830a899bb1e3f28c8873b5869d5cc90164c5c
ARG NODE22_VERSION=22.23.4
ARG NODE22_SHA256=1805bcd35e6815559bd2eb97db438d3fdb0eb3a5e6a977b7c1badca76337e9ce
ARG NODE24_VERSION=24.22.1
ARG NODE24_SHA256=6c172a9ab6718f7b801fb5a7bd486af2ee5e32758086a04b84fcce2aa77095e6
ARG DOTNET_SDK_VERSION=10.0.402
ARG DOTNET_SDK_SHA512=1904a98dda2db34ba78ba3c4fb1b224455329af91c341d83547843c1839e112b04ae699ee92fa2449a8f62249196a938723078c0c60b9f65c88539d5687729c2
ARG DOTNET_WORKLOAD_SET=10.0.402.1
ARG ANDROID_CMDLINE_TOOLS_BUILD=16111833
ARG ANDROID_CMDLINE_TOOLS_SHA256=0877a1d048fe4a24efe2eff536ca4223f7adeb58648bb81909d33c446918cfa8
ARG ANDROID_API=36
ARG ANDROID_BUILD_TOOLS=36.0.0
# Pins added by 00058 (Android archives, ILLink.Tasks) are declared just
# before section 3b (d2), so the layers above them keep their build cache.
ARG RUSTUP_VERSION=1.29.2
ARG RUSTUP_INIT_SHA256=5be191e8c70a5007074254cb74d78dbfdd25cd5a61c27de7b8e5410122ec24f4
ARG RUST_VERSION=1.99.1
# Second toolchain, never the default: Omega-Prime rust-parity builds the pinned
# upstream with the toolchain its contract pin names (prime-agent.pin.json).
ARG RUST_PARITY_VERSION=1.98.1
ARG CARGO_DENY_VERSION=0.20.2
ARG CARGO_DENY_SHA256=9f12ed4c49936e09b48bf862b595cde2fe64fcbd9d74dfacac6131ca824c8d5f
ARG PG_MAJOR=16

# Tool locations, shared by the build steps, the self-check and runner-env.
ENV RUNNER_TOOL_CACHE=/opt/hostedtoolcache \
    AGENT_TOOLSDIRECTORY=/opt/hostedtoolcache \
    DOTNET_ROOT=/usr/share/dotnet \
    DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1 DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1 \
    JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
    ANDROID_HOME=/opt/android-sdk ANDROID_SDK_ROOT=/opt/android-sdk \
    RUSTUP_HOME=/opt/rust/rustup \
    PATH=/opt/rust/cargo/bin:/usr/lib/postgresql/16/bin:/usr/share/dotnet:/opt/android-sdk/cmdline-tools/latest/bin:/opt/android-sdk/platform-tools:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

USER root

# --- 1. Upstream ubuntu/Dockerfile @ a915efba, reproduced step for step -------
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends nginx openssl dbus-x11 x11-utils \
    && rm -rf /var/lib/apt/lists/* \
    && cp -a /home/headless /opt/desktop-seed \
    && chmod 644 /etc/passwd /etc/group \
    && usermod -d /config headless \
    && rm -f /dockerstartup/.initial_sudo_password

# Upstream entrypoint fetched at the pinned commit and verified, never edited.
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates curl \
    && curl -fsSL -o /opt/railway-desktop.py \
       "https://raw.githubusercontent.com/${UPSTREAM_REPO}/${UPSTREAM_COMMIT}/ubuntu/entrypoint.py" \
    && echo "${UPSTREAM_ENTRYPOINT_SHA256}  /opt/railway-desktop.py" | sha256sum -c - \
    && chmod 0644 /opt/railway-desktop.py

# --- 2. actions/runner, published checksum, read-only shared install ----------
RUN curl -fsSL -o /tmp/actions-runner.tgz \
       "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz" \
    && echo "${RUNNER_SHA256}  /tmp/actions-runner.tgz" | sha256sum -c - \
    && mkdir -p /opt/actions-runner \
    && tar -xzf /tmp/actions-runner.tgz -C /opt/actions-runner \
    && rm -f /tmp/actions-runner.tgz \
    && /opt/actions-runner/bin/installdependencies.sh \
    && chown -R root:root /opt/actions-runner \
    && chmod -R a+rX,go-w /opt/actions-runner \
    && rm -rf /var/lib/apt/lists/*

# --- 3. Dedicated non-root job identity (distinct from root agent + headless) -
RUN groupadd --system --gid "${JOB_GID}" omega-ci-job \
    && useradd --system --uid "${JOB_UID}" --gid "${JOB_GID}" --no-create-home \
       --home-dir /nonexistent --shell /usr/sbin/nologin omega-ci-job \
    && mkdir -p /var/lib/omega-ci/work \
    && chown root:root /var/lib/omega-ci /var/lib/omega-ci/work \
    && chmod 0755 /var/lib/omega-ci /var/lib/omega-ci/work
# ^ 0755, not 0711: actions/runner refuses to start unless the job identity
#   can READ its directory and every directory above it ("Permission to read
#   the directory contents is required for ... and each directory up the
#   hierarchy"), and then exits 0 within a second. Each runner's own workdir
#   under work/ stays 0700 and owned by the job identity; nothing secret lives
#   in either directory (job-policy.json is 0644 repository names only).
# Fail the build unless the real runner, as the job identity, gets past that
# directory check in the real work root (it then stops at "Not configured",
# since no JIT config is given here).
RUN check=/var/lib/omega-ci/work/omega-ci-build-check \
    && cp -a /opt/actions-runner "$check" \
    && chown "${JOB_UID}:${JOB_GID}" "$check" \
    && chmod 0700 "$check" \
    && out="$(cd "$check" && env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME=/tmp \
         setpriv --reuid="${JOB_UID}" --regid="${JOB_GID}" --clear-groups ./run.sh 2>&1 || true)" \
    && rm -rf "$check" \
    && printf '%s\n' "$out" \
    && ! printf '%s\n' "$out" | grep -q 'Permission to read the directory contents' \
    && printf '%s\n' "$out" | grep -q 'Not configured'

# --- 3b. Toolchains every swcstudiospace workflow needs (00056) ---------------
# Every CI job in Ove's repositories runs on this pool, never on GitHub-hosted
# runners, so the image carries what ubuntu-latest would have provided. Jobs
# run as uid 1500 with no sudo and no Docker daemon (Railway is unprivileged),
# so nothing here may need root at job time. Shared trees are root-owned and
# read-only for the job: a job cannot change the tools the next job uses.
# Every download is version-pinned and checksum-verified (sha256sum/sha512sum
# -c, fail closed). apt comes from a dated snapshot.ubuntu.com view, so the
# package set is reproducible. Expected versions: toolchain-check.sh.

# (a) apt: build tools, CLIs, JDK 17, PostgreSQL 16 server, podman. No cluster
#     is created and no service starts; jobs run initdb/pg_ctl themselves.
#     fonts-liberation: faceless-desk's renderer loads
#     /usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf.
RUN mkdir -p /etc/postgresql-common/createcluster.d \
    && echo 'create_main_cluster = false' > /etc/postgresql-common/createcluster.d/00-omega-ci.conf \
    && apt-get update --snapshot "${APT_SNAPSHOT}" \
    && DEBIAN_FRONTEND=noninteractive apt-get install --snapshot "${APT_SNAPSHOT}" -y --no-install-recommends \
       git make build-essential pkg-config libssl-dev file \
       zip unzip xz-utils rsync jq age pipx python3-venv python3-pip \
       openjdk-17-jdk-headless postgresql-16 postgresql-client-16 \
       podman uidmap slirp4netns fuse-overlayfs \
       ripgrep ffmpeg fonts-liberation \
    && rm -rf /var/lib/apt/lists/* \
    && echo "omega-ci-job:100000:65536" >> /etc/subuid \
    && echo "omega-ci-job:100000:65536" >> /etc/subgid

# (b) Single-binary CLIs: gh, gitleaks, trivy, GreptimeDB (standalone mode),
#     cargo-deny (license gates; replaces EmbarkStudios/cargo-deny-action, a
#     Docker container action).
RUN mkdir -p /tmp/bin && cd /tmp/bin \
    && curl -fsSL -o gh.tgz "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.tar.gz" \
    && curl -fsSL -o gitleaks.tgz "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz" \
    && curl -fsSL -o trivy.tgz "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz" \
    && curl -fsSL -o greptime.tgz "https://github.com/GreptimeTeam/greptimedb/releases/download/v${GREPTIME_VERSION}/greptime-linux-amd64-v${GREPTIME_VERSION}.tar.gz" \
    && curl -fsSL -o cargo-deny.tgz "https://github.com/EmbarkStudios/cargo-deny/releases/download/${CARGO_DENY_VERSION}/cargo-deny-${CARGO_DENY_VERSION}-x86_64-unknown-linux-musl.tar.gz" \
    && printf '%s  %s\n' "${GH_SHA256}" gh.tgz "${GITLEAKS_SHA256}" gitleaks.tgz \
       "${TRIVY_SHA256}" trivy.tgz "${GREPTIME_SHA256}" greptime.tgz \
       "${CARGO_DENY_SHA256}" cargo-deny.tgz | sha256sum -c - \
    && tar -xzf gh.tgz --strip-components=2 -C /usr/local/bin "gh_${GH_VERSION}_linux_amd64/bin/gh" \
    && tar -xzf gitleaks.tgz -C /usr/local/bin gitleaks \
    && tar -xzf trivy.tgz -C /usr/local/bin trivy \
    && tar -xzf greptime.tgz --strip-components=1 -C /usr/local/bin "greptime-linux-amd64-v${GREPTIME_VERSION}/greptime" \
    && tar -xzf cargo-deny.tgz --strip-components=1 -C /usr/local/bin "cargo-deny-${CARGO_DENY_VERSION}-x86_64-unknown-linux-musl/cargo-deny" \
    && chown root:root /usr/local/bin/gh /usr/local/bin/gitleaks /usr/local/bin/trivy /usr/local/bin/greptime /usr/local/bin/cargo-deny \
    && chmod 0755 /usr/local/bin/gh /usr/local/bin/gitleaks /usr/local/bin/trivy /usr/local/bin/greptime /usr/local/bin/cargo-deny \
    && cd / && rm -rf /tmp/bin

# (c) Hosted tool cache, in the layout actions/setup-python and
#     actions/setup-node look up (<Tool>/<version>/x64 + x64.complete), at
#     the path GitHub's images use (the Python builds are compiled for it).
#     A cache hit needs no write, so the read-only tree serves uid 1500.
#     The Python step mirrors the build's own setup.sh, minus its unpinned
#     "pip install --upgrade pip" (the bundled pip is kept). setup.sh also
#     creates bin/pip; without it `pip install` after setup-python resolves to
#     the system /usr/bin/pip and fails with externally-managed-environment.
#     CodeQL: the bundle github/codeql-action pins (2.27.3 for action
#     6f2ac81), so init finds it here instead of trying to write the cache.
#     System Node: /usr/local/bin/{node,npm,npx,corepack} point at the cached
#     Node 22 (same pinned, checksum-verified tarball), ahead of the base
#     image's Node 20 on PATH. Jobs using setup-node still get their version.
RUN mkdir -p "${RUNNER_TOOL_CACHE}/Python" "${RUNNER_TOOL_CACHE}/node" /tmp/tc && cd /tmp/tc \
    && curl -fsSL -o py310.tgz "https://github.com/actions/python-versions/releases/download/${PY310_VERSION}-${PY310_BUILD}/python-${PY310_VERSION}-linux-24.04-x64.tar.gz" \
    && curl -fsSL -o py312.tgz "https://github.com/actions/python-versions/releases/download/${PY312_VERSION}-${PY312_BUILD}/python-${PY312_VERSION}-linux-24.04-x64.tar.gz" \
    && curl -fsSL -o py313.tgz "https://github.com/actions/python-versions/releases/download/${PY313_VERSION}-${PY313_BUILD}/python-${PY313_VERSION}-linux-24.04-x64.tar.gz" \
    && curl -fsSL -o py314.tgz "https://github.com/actions/python-versions/releases/download/${PY314_VERSION}-${PY314_BUILD}/python-${PY314_VERSION}-linux-24.04-x64.tar.gz" \
    && curl -fsSL -o node22.txz "https://nodejs.org/dist/v${NODE22_VERSION}/node-v${NODE22_VERSION}-linux-x64.tar.xz" \
    && curl -fsSL -o node24.txz "https://nodejs.org/dist/v${NODE24_VERSION}/node-v${NODE24_VERSION}-linux-x64.tar.xz" \
    && curl -fsSL -o codeql.tgz "https://github.com/github/codeql-action/releases/download/codeql-bundle-v${CODEQL_VERSION}/codeql-bundle-linux64.tar.gz" \
    && printf '%s  %s\n' "${PY310_SHA256}" py310.tgz "${PY312_SHA256}" py312.tgz \
       "${PY313_SHA256}" py313.tgz "${PY314_SHA256}" py314.tgz \
       "${NODE22_SHA256}" node22.txz "${NODE24_SHA256}" node24.txz \
       "${CODEQL_SHA256}" codeql.tgz | sha256sum -c - \
    && for spec in "${PY310_VERSION}:py310.tgz" "${PY312_VERSION}:py312.tgz" \
                   "${PY313_VERSION}:py313.tgz" "${PY314_VERSION}:py314.tgz"; do \
         ver="${spec%%:*}"; mm="${ver%.*}"; dest="${RUNNER_TOOL_CACHE}/Python/${ver}/x64"; \
         mkdir -p "$dest" && tar -xzf "${spec#*:}" -C "$dest" \
         && rm -f "$dest/setup.sh" "$dest/build_output.txt" "$dest/tools_structure.txt" \
         && ln -s "./bin/python${mm}" "$dest/python" \
         && ln -sf "python${mm}" "$dest/bin/python$(echo "$mm" | tr -d .)" \
         && { [ -e "$dest/bin/python" ] || ln -s "python${mm}" "$dest/bin/python"; } \
         && { "$dest/python" -c "import pip" 2>/dev/null || "$dest/python" -m ensurepip; } \
         && { [ -e "$dest/bin/pip" ] || ln -s pip3 "$dest/bin/pip"; } \
         && touch "${RUNNER_TOOL_CACHE}/Python/${ver}/x64.complete" || exit 1; \
       done \
    && for spec in "${NODE22_VERSION}:node22.txz" "${NODE24_VERSION}:node24.txz"; do \
         ver="${spec%%:*}"; dest="${RUNNER_TOOL_CACHE}/node/${ver}/x64"; \
         mkdir -p "$dest" && tar -xJf "${spec#*:}" --strip-components=1 -C "$dest" \
         && touch "${RUNNER_TOOL_CACHE}/node/${ver}/x64.complete" || exit 1; \
       done \
    && for tool in node npm npx corepack; do \
         ln -sfn "${RUNNER_TOOL_CACHE}/node/${NODE22_VERSION}/x64/bin/${tool}" "/usr/local/bin/${tool}" || exit 1; \
       done \
    && mkdir -p "${RUNNER_TOOL_CACHE}/CodeQL/${CODEQL_VERSION}/x64" \
    && tar -xzf codeql.tgz -C "${RUNNER_TOOL_CACHE}/CodeQL/${CODEQL_VERSION}/x64" \
    && touch "${RUNNER_TOOL_CACHE}/CodeQL/${CODEQL_VERSION}/x64.complete" \
    && cd / && rm -rf /tmp/tc \
    && chown -R root:root "${RUNNER_TOOL_CACHE}" && chmod -R a+rX,go-w "${RUNNER_TOOL_CACHE}"

# (d) .NET SDK per OmpOS global.json, installed where actions/setup-dotnet
#     looks on Linux (/usr/share/dotnet), so its install step finds the exact
#     version and writes nothing; plus the maui-android workload (pinned
#     workload set) for net10.0-android. Caches from the install are removed.
RUN curl -fsSL -o /tmp/dotnet.tgz "https://builds.dotnet.microsoft.com/dotnet/Sdk/${DOTNET_SDK_VERSION}/dotnet-sdk-${DOTNET_SDK_VERSION}-linux-x64.tar.gz" \
    && echo "${DOTNET_SDK_SHA512}  /tmp/dotnet.tgz" | sha512sum -c - \
    && mkdir -p "${DOTNET_ROOT}" && tar -xzf /tmp/dotnet.tgz -C "${DOTNET_ROOT}" && rm -f /tmp/dotnet.tgz \
    && ln -s "${DOTNET_ROOT}/dotnet" /usr/local/bin/dotnet \
    && export DOTNET_CLI_HOME=/tmp/dotnet-home NUGET_PACKAGES=/tmp/dotnet-nuget \
    && dotnet workload install maui-android --version "${DOTNET_WORKLOAD_SET}" \
    && rm -rf /tmp/dotnet-home /tmp/dotnet-nuget /tmp/NuGetScratch* /root/.nuget /root/.local/share/NuGet \
    && chown -R root:root "${DOTNET_ROOT}" && chmod -R a+rX,go-w "${DOTNET_ROOT}"

# --- 00058 pins (see the ARG note at the top) --------------------------------
# Android SDK packages as fixed archives from Google's repository (00058):
# exact revisions, sha256 recorded after checking the sha1 in
# repository2-3.xml. No sdkmanager resolution, so no drift between builds.
ARG ANDROID_PLATFORM_REV=2
ARG ANDROID_PLATFORM_ARCHIVE=platform-36_r02.zip
ARG ANDROID_PLATFORM_SHA256=37607369a28c5b640b3a7998868d45898ebcb777565a0e85f9acf36f29631d2e
ARG ANDROID_BUILD_TOOLS_ARCHIVE=build-tools_r36_linux.zip
ARG ANDROID_BUILD_TOOLS_SHA256=5d9ac77fb6ff43d9da518a337b4fcf8f9097113df531d99ccefe80ef7ce8250b
ARG ANDROID_PLATFORM_TOOLS_VERSION=37.0.1
ARG ANDROID_PLATFORM_TOOLS_SHA256=d230f13842f60f782a8645f9c813f8f845bf36089ea7289f28c48f17979313f1
# Microsoft.NET.ILLink.Tasks for the SDK's bundled runtime (10.0.13): every
# net10.0-android build restores it; shipped in the SDK's offline
# library-packs source so builds need no nuget.org for it (00058).
ARG ILLINK_TASKS_VERSION=10.0.13
ARG ILLINK_TASKS_SHA512=d17dd7dd51bf5be5263bd1224bcf0a94443cc29a517b46dcb6f7396faaaef2d141c5115f83f341c2b88d791ecb3186b403335a721a6fb669171c4e96c88e7326

# (d2) Microsoft.NET.ILLink.Tasks into the SDK's own offline package source
#      (library-packs, which every restore already lists). Pinned sha512 =
#      NuGet catalog packageHash. Separate layer so (d) stays cached.
RUN curl -fsSL -o "/tmp/microsoft.net.illink.tasks.${ILLINK_TASKS_VERSION}.nupkg" \
       "https://api.nuget.org/v3-flatcontainer/microsoft.net.illink.tasks/${ILLINK_TASKS_VERSION}/microsoft.net.illink.tasks.${ILLINK_TASKS_VERSION}.nupkg" \
    && echo "${ILLINK_TASKS_SHA512}  /tmp/microsoft.net.illink.tasks.${ILLINK_TASKS_VERSION}.nupkg" | sha512sum -c - \
    && [ -d "${DOTNET_ROOT}/shared/Microsoft.NETCore.App/${ILLINK_TASKS_VERSION}" ] \
    && install -o root -g root -m 0644 "/tmp/microsoft.net.illink.tasks.${ILLINK_TASKS_VERSION}.nupkg" "${DOTNET_ROOT}/library-packs/" \
    && rm -f /tmp/*.nupkg
# ^ The -d test fails the build if the SDK's runtime no longer matches the pin.

# (e) Android SDK for net10.0-android. cmdline-tools: pinned zip (sha256
#     recorded after checking Google's published sha1). The platform,
#     build-tools and platform-tools are fixed archives at exact revisions
#     (sha256-pinned above, sha1 cross-checked against repository2-3.xml),
#     unpacked where sdkmanager would put them; each keeps its
#     source.properties, which the self-check asserts. sdkmanager is used only
#     to record the licence acceptances.
RUN mkdir -p /tmp/android && cd /tmp/android \
    && curl -fsSL -o cmdline-tools.zip "https://dl.google.com/android/repository/commandlinetools-linux-${ANDROID_CMDLINE_TOOLS_BUILD}_latest.zip" \
    && curl -fsSL -o platform.zip "https://dl.google.com/android/repository/${ANDROID_PLATFORM_ARCHIVE}" \
    && curl -fsSL -o build-tools.zip "https://dl.google.com/android/repository/${ANDROID_BUILD_TOOLS_ARCHIVE}" \
    && curl -fsSL -o platform-tools.zip "https://dl.google.com/android/repository/platform-tools_r${ANDROID_PLATFORM_TOOLS_VERSION}-linux.zip" \
    && printf '%s  %s\n' "${ANDROID_CMDLINE_TOOLS_SHA256}" cmdline-tools.zip "${ANDROID_PLATFORM_SHA256}" platform.zip \
       "${ANDROID_BUILD_TOOLS_SHA256}" build-tools.zip "${ANDROID_PLATFORM_TOOLS_SHA256}" platform-tools.zip | sha256sum -c - \
    && mkdir -p "${ANDROID_HOME}/cmdline-tools" "${ANDROID_HOME}/platforms" "${ANDROID_HOME}/build-tools" \
    && unzip -q cmdline-tools.zip -d x-cmdline && mv x-cmdline/cmdline-tools "${ANDROID_HOME}/cmdline-tools/latest" \
    && unzip -q platform.zip -d x-platform && mv x-platform/* "${ANDROID_HOME}/platforms/android-${ANDROID_API}" \
    && unzip -q build-tools.zip -d x-build-tools && mv x-build-tools/* "${ANDROID_HOME}/build-tools/${ANDROID_BUILD_TOOLS}" \
    && unzip -q platform-tools.zip -d "${ANDROID_HOME}" \
    && grep -qx "Pkg.Revision=${ANDROID_PLATFORM_REV}" "${ANDROID_HOME}/platforms/android-${ANDROID_API}/source.properties" \
    && grep -qx "AndroidVersion.ApiLevel=${ANDROID_API}" "${ANDROID_HOME}/platforms/android-${ANDROID_API}/source.properties" \
    && grep -qx "Pkg.Revision=${ANDROID_BUILD_TOOLS}" "${ANDROID_HOME}/build-tools/${ANDROID_BUILD_TOOLS}/source.properties" \
    && grep -qx "Pkg.Revision=${ANDROID_PLATFORM_TOOLS_VERSION}" "${ANDROID_HOME}/platform-tools/source.properties" \
    && cd / && rm -rf /tmp/android \
    && export HOME=/tmp/android-home \
    && yes | "${ANDROID_HOME}/cmdline-tools/latest/bin/sdkmanager" --sdk_root="${ANDROID_HOME}" --licenses > /dev/null \
    && rm -rf /tmp/android-home \
    && chown -R root:root "${ANDROID_HOME}" && chmod -R a+rX,go-w "${ANDROID_HOME}"

# (f) Rust: rustup-init (published sha256), one pinned toolchain, minimal
#     profile + rustfmt + clippy, plus RUST_PARITY_VERSION (select it with
#     RUSTUP_TOOLCHAIN). RUSTUP_HOME is read-only for jobs; cargo's
#     registry and target dirs use the job's own HOME (CARGO_HOME unset).
RUN curl -fsSL -o /tmp/rustup-init "https://static.rust-lang.org/rustup/archive/${RUSTUP_VERSION}/x86_64-unknown-linux-gnu/rustup-init" \
    && echo "${RUSTUP_INIT_SHA256}  /tmp/rustup-init" | sha256sum -c - \
    && chmod 0755 /tmp/rustup-init \
    && CARGO_HOME=/opt/rust/cargo /tmp/rustup-init -y --no-modify-path --profile minimal \
       --default-toolchain "${RUST_VERSION}" -c rustfmt -c clippy \
    && CARGO_HOME=/opt/rust/cargo /opt/rust/cargo/bin/rustup toolchain install "${RUST_PARITY_VERSION}" \
       --profile minimal -c rustfmt -c clippy \
    && rm -f /tmp/rustup-init && rm -rf /opt/rust/cargo/registry /opt/rust/rustup/downloads/* /opt/rust/rustup/tmp/* \
    && chown -R root:root /opt/rust && chmod -R a+rX,go-w /opt/rust

# (g) Python CLIs, each in its own venv from a hash-locked requirements file
#     (pip --require-hashes --no-deps: every wheel, direct or transitive, must
#     match its sha256). Regenerate with the command in docs/ci-runners.md.
COPY deploy/ci-runner-desktop/requirements /opt/omega-ci/requirements
RUN for tool in semgrep bandit podman-compose; do \
         python3 -m venv "/opt/venvs/${tool}" \
         && "/opt/venvs/${tool}/bin/pip" install --no-cache-dir --disable-pip-version-check \
            --require-hashes --no-deps --only-binary=:all: -r "/opt/omega-ci/requirements/${tool}.txt" \
         && ln -s "/opt/venvs/${tool}/bin/${tool}" "/usr/local/bin/${tool}" || exit 1; \
       done \
    && chown -R root:root /opt/venvs /opt/omega-ci/requirements && chmod -R a+rX,go-w /opt/venvs /opt/omega-ci/requirements

# (h) The job environment. The agent hands each runner an allowlisted env
#     (PATH, locale, HOME), so the toolchain variables reach jobs through the
#     runner's own root file, which Runner.Listener loads at startup and the
#     agent hard-links into every per-runner work dir.
COPY deploy/ci-runner-desktop/runner-env /opt/actions-runner/.env
RUN chown root:root /opt/actions-runner/.env && chmod 0644 /opt/actions-runner/.env

# --- 4. Agent: stdlib-only omega_prime package + hash-pinned uvicorn (no pip) -
RUN mkdir -p /opt/omega-ci/site /tmp/wheels && cd /tmp/wheels \
    && curl -fsSL -o "${UVICORN_WHL}" "${UVICORN_URL}" \
    && curl -fsSL -o "${CLICK_WHL}" "${CLICK_URL}" \
    && curl -fsSL -o "${H11_WHL}" "${H11_URL}" \
    && printf '%s  %s\n' "${UVICORN_SHA256}" "${UVICORN_WHL}" "${CLICK_SHA256}" "${CLICK_WHL}" "${H11_SHA256}" "${H11_WHL}" | sha256sum -c - \
    && for whl in *.whl; do python3 -m zipfile -e "$whl" /opt/omega-ci/site; done \
    && rm -rf /tmp/wheels
# The whole package (tests excluded by .dockerignore), not a hand-picked file
# list: the agent's credential reads go through omega_prime.credentials.broker,
# whose import chain spans several subpackages. It is stdlib-only at import.
COPY omega_prime /opt/omega-ci/site/omega_prime
COPY deploy/ci-runner-desktop/entrypoint.sh /opt/omega-ci/entrypoint.sh
COPY deploy/ci-runner-desktop/smoke.sh /opt/omega-ci/smoke.sh
COPY deploy/ci-runner-desktop/job-started.sh /opt/omega-ci/job-started.sh
# Fail the build unless the real agent app builds from this site alone.
RUN chown -R root:root /opt/omega-ci && chmod -R a+rX,go-w /opt/omega-ci \
    && chmod 0755 /opt/omega-ci/entrypoint.sh /opt/omega-ci/smoke.sh /opt/omega-ci/job-started.sh \
    && /opt/omega-ci/smoke.sh uvicorn \
    && ! /opt/omega-ci/job-started.sh
# ^ The pre-job guard runs under the image's own interpreter and, with no
#   policy written yet and no job context, refuses (fails closed).

# Toolchain self-check (00056): every tool at its pinned version, run as the
# job identity with the job's environment (runner-env), so a tool that only
# works as root fails the build. Re-runnable on a live Desktop.
# omega-ci-db (00058): per-job PostgreSQL/GreptimeDB on free loopback ports
# with data under RUNNER_TEMP, so parallel jobs on one Desktop never collide.
COPY deploy/ci-runner-desktop/omega-ci-db /usr/local/bin/omega-ci-db
COPY deploy/ci-runner-desktop/omega-ci-ports /usr/local/bin/omega-ci-ports
COPY deploy/ci-runner-desktop/toolchain-check.sh /opt/omega-ci/toolchain-check.sh
RUN printf '%s=%s\n' GH "${GH_VERSION}" GITLEAKS "${GITLEAKS_VERSION}" TRIVY "${TRIVY_VERSION}" \
       GREPTIME "${GREPTIME_VERSION}" PY310 "${PY310_VERSION}" PY312 "${PY312_VERSION}" \
       PY313 "${PY313_VERSION}" PY314 "${PY314_VERSION}" CODEQL "${CODEQL_VERSION}" \
       NODE22 "${NODE22_VERSION}" NODE24 "${NODE24_VERSION}" DOTNET_SDK "${DOTNET_SDK_VERSION}" \
       ANDROID_API "${ANDROID_API}" ANDROID_BUILD_TOOLS "${ANDROID_BUILD_TOOLS}" \
       ANDROID_PLATFORM_REV "${ANDROID_PLATFORM_REV}" ANDROID_PLATFORM_TOOLS "${ANDROID_PLATFORM_TOOLS_VERSION}" \
       ILLINK_TASKS "${ILLINK_TASKS_VERSION}" \
       RUST "${RUST_VERSION}" RUST_PARITY "${RUST_PARITY_VERSION}" CARGO_DENY "${CARGO_DENY_VERSION}" \
       PG_MAJOR "${PG_MAJOR}" > /opt/omega-ci/toolchain-versions \
    && chown root:root /usr/local/bin/omega-ci-db /usr/local/bin/omega-ci-ports \
    && chmod 0755 /usr/local/bin/omega-ci-db /usr/local/bin/omega-ci-ports \
    && chmod 0644 /opt/omega-ci/toolchain-versions && chmod 0755 /opt/omega-ci/toolchain-check.sh \
    && /opt/omega-ci/toolchain-check.sh --as-job "${JOB_UID}" "${JOB_GID}"

ENV HOME=/config PORT=3000 VNC_RESOLUTION=1440x900 \
    OMEGA_CI_AGENT_RUNNER_ROOT=/opt/actions-runner \
    OMEGA_CI_AGENT_WORK_ROOT=/var/lib/omega-ci/work \
    OMEGA_CI_AGENT_JOB_UID=${JOB_UID} \
    OMEGA_CI_AGENT_JOB_GID=${JOB_GID} \
    OMEGA_CI_AGENT_JOB_HOOK=/opt/omega-ci/job-started.sh \
    OMEGA_CI_AGENT_JOB_POLICY=/var/lib/omega-ci/job-policy.json
WORKDIR /config
EXPOSE 3000 8787

# Wrapper starts the agent, then execs the upstream command below unchanged.
ENTRYPOINT ["/opt/omega-ci/entrypoint.sh"]
CMD ["python3", "/opt/railway-desktop.py"]
