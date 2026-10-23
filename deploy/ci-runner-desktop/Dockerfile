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
    && chmod 0711 /var/lib/omega-ci /var/lib/omega-ci/work

# --- 4. Agent: stdlib package subset + hash-pinned uvicorn wheels (no pip) ----
RUN mkdir -p /opt/omega-ci/site /tmp/wheels && cd /tmp/wheels \
    && curl -fsSL -o "${UVICORN_WHL}" "${UVICORN_URL}" \
    && curl -fsSL -o "${CLICK_WHL}" "${CLICK_URL}" \
    && curl -fsSL -o "${H11_WHL}" "${H11_URL}" \
    && printf '%s  %s\n' "${UVICORN_SHA256}" "${UVICORN_WHL}" "${CLICK_SHA256}" "${CLICK_WHL}" "${H11_SHA256}" "${H11_WHL}" | sha256sum -c - \
    && for whl in *.whl; do python3 -m zipfile -e "$whl" /opt/omega-ci/site; done \
    && rm -rf /tmp/wheels
COPY omega_prime/__init__.py /opt/omega-ci/site/omega_prime/__init__.py
COPY omega_prime/credentials/__init__.py omega_prime/credentials/redact.py /opt/omega-ci/site/omega_prime/credentials/
COPY omega_prime/ci_runners /opt/omega-ci/site/omega_prime/ci_runners
COPY deploy/ci-runner-desktop/entrypoint.sh /opt/omega-ci/entrypoint.sh
RUN chown -R root:root /opt/omega-ci && chmod -R a+rX,go-w /opt/omega-ci \
    && chmod 0755 /opt/omega-ci/entrypoint.sh \
    && PYTHONPATH=/opt/omega-ci/site python3 -c "import sys; assert sys.version_info >= (3, 11); import omega_prime.ci_runners.agent.app, uvicorn"

ENV HOME=/config PORT=3000 VNC_RESOLUTION=1440x900 \
    OMEGA_CI_AGENT_RUNNER_ROOT=/opt/actions-runner \
    OMEGA_CI_AGENT_WORK_ROOT=/var/lib/omega-ci/work \
    OMEGA_CI_AGENT_JOB_UID=${JOB_UID} \
    OMEGA_CI_AGENT_JOB_GID=${JOB_GID}
WORKDIR /config
EXPOSE 3000 8787

# Wrapper starts the agent, then execs the upstream command below unchanged.
ENTRYPOINT ["/opt/omega-ci/entrypoint.sh"]
CMD ["python3", "/opt/railway-desktop.py"]
