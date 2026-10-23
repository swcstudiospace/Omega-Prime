# CI runner Desktop overlay (Railway "Ubuntu Desktop")

This directory turns the existing Railway **Ubuntu Desktop** service into the
data plane for the omega-ci autoscaling runner pool of the Autonomous
Event-Driven ADLC Programming Bot. The image is the upstream
desktop, unchanged, plus three additions:

- the omega-ci **runner agent** on port 8787,
- a read-only **actions/runner 2.338.1** install at `/opt/actions-runner`,
- a dedicated non-root **job identity** (`omega-ci-job`, UID/GID 1500).

> **Status: operator-reported as deployed to Railway on 2026-10-24**, with
> the agent answering `/healthz`. That is **unverified**: no receipt in this
> repository covers it. Building and deploying this image, adding the agent
> domain and setting variables is the paid cutover, an operator step
> (D-120-10). Nothing in this repository runs it.

## How it fits

```
controller (VPS) ──HTTPS POST /v1/runners──▶ agent :8787 (this image)
       ▲                                         │ spawns run.sh as omega-ci-job
       └──── POST /agent/events (exit) ◀─────────┘ (JIT config in env only)
```

- **Wake.** The controller's first launch request wakes the sleeping service. It
  retries 502/503 and connection failures for up to 150 s.
- **Sleep.** When the jobs finish, the children exit and the agent sends one exit
  callback per child. Nothing then generates outbound traffic, so Railway can
  sleep the service. There are no timers or keep-alives.
- **Credentials.** The Desktop never holds a GitHub credential. It only receives
  single-use JIT configs.

## Files

| File | Purpose |
|---|---|
| `Dockerfile` | Pinned overlay. The build context is the repo root. |
| `entrypoint.sh` | Starts the agent (`python3 -m omega_prime.ci_runners agent --host 0.0.0.0 --port 8787`), then `exec`s the upstream command (`CMD ["python3", "/opt/railway-desktop.py"]`) unchanged. |
| `job-started.sh` | Runner-side pre-job guard, installed as every runner's `ACTIONS_RUNNER_HOOK_JOB_STARTED`. Fails the job before any step for `pull_request_target`, fork pull requests, repositories not in `OMEGA_CI_AGENT_REPOS`, and (for non-private repositories) any event but `push` / `workflow_dispatch` / `pull_request`. Logic: `omega_prime/ci_runners/agent/jobhook.py`. The build runs it once and requires a refusal (fail closed). |
| `smoke.sh` | Build-time check: builds the real agent app (settings, credential broker, token read, supervisor) from `/opt/omega-ci/site` alone with an isolated interpreter, so a module missing from the image fails the build, not the first boot. |
| `railway.toml` | Dockerfile builder, desktop `/healthz` readiness, `sleepApplication = true`, `/config` mount. |

## Supply-chain provenance (verified 2026-10-23 AEDT)

Every input is immutable and checked in the Dockerfile with `sha256sum -c`, so
the build fails closed on any mismatch.

| Input | Pin | Evidence |
|---|---|---|
| Upstream source | `XavTo/Railway-Desktop-Pro` commit `a915efba6d6a2c4b90f266f34fd8b56c1a446703` (2026-10-18) | HEAD of a fresh `git clone` |
| Upstream base image | `accetto/ubuntu-vnc-xfce-g3@sha256:de1b03759a37b78a62a0b4f25d1e29a54e7500bbc2101109b2430a5574e055b2` | pinned by upstream `ubuntu/Dockerfile`; the manifest resolved (HTTP 200) on Docker Hub |
| Upstream `ubuntu/Dockerfile` | sha256 `39c400900a55b7166b13fad3d3a55440786120082677175fcce846f76f88eeff` | its steps are reproduced verbatim in section 1 of our Dockerfile |
| Upstream `ubuntu/entrypoint.py` | sha256 `d1bc4e0373179a614979d6efdb31356aa95f0af37d896b5573010d4f3f7c8b82` | identical from the clone and from `raw.githubusercontent.com/…/a915efba…/ubuntu/entrypoint.py` |
| actions/runner | `actions-runner-linux-x64-2.338.1.tar.gz` sha256 `08e9ccb3604d2462c24b5e33604dc30810296ca15cc65f23cffd4317eb144c37` | published on the v2.338.1 GitHub release page, and matches the downloaded tarball |
| uvicorn | `uvicorn-0.54.0-py3-none-any.whl` sha256 `505bdb0f318731d45f1f712071fc781a8981f6847a31c902c9f5e652d4f67faf` | PyPI JSON digest, matches the download |
| click | `click-8.5.0-py3-none-any.whl` sha256 `255bc9599cf7748b4b1a446ccc735421bd08a2ae529a8b88597d3de5664ee360` | PyPI JSON digest, matches the download |
| h11 | `h11-0.16.0-py3-none-any.whl` sha256 `63cf8bbe7522de3bf65932fda1d9c2772064ffb3dae62d55932da54b31cb6c86` | PyPI JSON digest, matches the download |

There is no pip in the image. The wheels are pure Python and are unzipped
into `/opt/omega-ci/site`, next to the `omega_prime` package (tests excluded
by `.dockerignore`; stdlib-only at import). The whole package is copied, not a
file list, because the agent reads its token through
`omega_prime.credentials.broker`, whose imports span several subpackages;
`smoke.sh` proves the copied set is complete on every build.

Upstream's apt steps (`nginx openssl dbus-x11 x11-utils`, plus our
`ca-certificates curl` and the runner's `installdependencies.sh`) still take
current Ubuntu packages at build time, exactly as upstream's own README notes.

To update a pin, re-derive it from upstream evidence and change the
corresponding `ARG`. Never hand-type a hash.

## Isolation and its limits

- **Agent.** It runs as root, because it must switch identities. It gets neither
  the desktop `PASSWORD` (the wrapper strips it) nor any GitHub credential.
- **Jobs.** They run as `omega-ci-job` (UID/GID 1500, no supplementary groups)
  with `nice 10`, `RLIMIT_NPROC` 4096 and `RLIMIT_NOFILE` 65536. The child
  environment is an allowlist (`PATH`, locale, `TZ`, `TERM`), plus `HOME` and
  `ACTIONS_RUNNER_INPUT_JITCONFIG`, plus `ACTIONS_RUNNER_HOOK_JOB_STARTED`,
  which the agent sets itself (never inherited). The JIT config is never on
  argv or disk.
- **Pre-job guard.** A JIT runner sits in a shared runner group with shared
  labels, so GitHub may assign it any matching job, not only the one the
  controller launched it for. `job-started.sh` runs before the job's first
  step and fails refused jobs (see [Files](#files)). It reads its allowlist
  from `/var/lib/omega-ci/job-policy.json`, which the root agent writes at
  startup from `OMEGA_CI_AGENT_REPOS` (mode 0644, in a 0711 root directory),
  and runs `/usr/bin/python3 -I` with absolute paths, so a job cannot
  redirect it. The agent refuses to start if the hook is missing or
  modifiable by the job identity.
- **Agent token.** If you use `OMEGA_CI_AGENT_TOKEN_FILE`, the agent refuses to
  start unless the file is private (no "other" access, not owned by the job
  identity, not group-readable by it). The Railway variable
  `OMEGA_CI_AGENT_TOKEN` lives only in root-owned process environments, and
  `/proc/<pid>/environ` is unreadable across UIDs.
- **Shared runner files.** Each runner's tree under
  `/var/lib/omega-ci/work/<name>` hard-links the read-only, root-owned files from
  `/opt/actions-runner`. A job can create its own files but cannot modify the
  shared install.
- **Concurrent jobs.** They share one UID, so the per-runner 0700 workdirs are
  hygiene, **not** a confidentiality boundary between jobs. Treat the pool as
  running same-trust (your own repositories only). The controller enforces
  that with its repo allowlist plus `private: true` (or the public opt-in and
  fork guard), and the pre-job guard enforces it again on the runner.
- **Docker.** There is no Docker, no DinD and no privileged mode; Railway is
  unprivileged. Workflows that need Docker must stay on the VPS runner
  (`runs-on: [self-hosted, vps-docker]`) (D-120-08).
- **Runner logs.** Runner stdout/stderr go to `/dev/null`, so nothing JIT-adjacent
  is logged. Read job logs in GitHub; the runner's `_diag` is deleted with the
  workdir.
- **Untested here.** The UID-denial test
  (`test_job_uid_cannot_read_agent_token_or_environ`) needs root, so it runs
  only inside the image or a root CI. It is skipped in unprivileged test
  environments.

### Why the work root is not on `/config`

The settings default is `/config/ci-runners`, but the image sets
`OMEGA_CI_AGENT_WORK_ROOT=/var/lib/omega-ci/work`. Upstream's entrypoint runs
`chown -R headless:headless /config` on every boot. That would hand runner trees
to the desktop user and could race a launch. Runner trees are ephemeral anyway,
so container-local disk is the right place, and nothing CI-related ends up in
the backed-up `/config` volume.

## Variables (set on the Railway service at cutover; HELD)

| Variable | Value |
|---|---|
| `PASSWORD` | **≥16 characters, required.** A recent deploy failed health checks on exactly this. |
| `OMEGA_CI_AGENT_TOKEN` | Random value of at least 32 characters. It must equal the controller's `OMEGA_CI_DESKTOP_TOKEN_FILE` contents. |
| `OMEGA_CI_CONTROLLER_URL` | **Required.** `https://<autoscaler domain>` (the nginx or Caddy site on the VPS). Set it at deploy time even if the controller is not up yet: the agent only calls it after a job ends, but refuses to start without it (the 8787 domain then answers 502). |
| `RAILWAY_DOCKERFILE_PATH` | `deploy/ci-runner-desktop/Dockerfile`. Needed because Railway ignored the config-as-code path on the first real deploy and built the root MCP-server `Dockerfile`, which failed on `/run/secrets/omega_prime_token`. |
| `OMEGA_CI_AGENT_MAX_RUNNERS` | `8` (default; hard ceiling 10). Keep it ≥ the controller's `OMEGA_CI_MAX_RUNNERS`. |
| `OMEGA_CI_AGENT_REPOS` | **Required for jobs to run.** The controller's `OMEGA_CI_REPOS`, e.g. `swcstudiospace/Omega-Prime`. Repository names only, not a secret. Unset → the pre-job guard fails every job (fail closed). |
| `SERVERLESS_FRIENDLY` | `1` (recommended). Upstream's own knob: it stops Chromium's background/update/GCM traffic, which would otherwise keep an idle Desktop awake and defeat sleep-as-teardown. |

The image already sets `OMEGA_CI_AGENT_RUNNER_ROOT`, `OMEGA_CI_AGENT_WORK_ROOT`,
`OMEGA_CI_AGENT_JOB_UID`, `OMEGA_CI_AGENT_JOB_GID`, `OMEGA_CI_AGENT_JOB_HOOK`
and `OMEGA_CI_AGENT_JOB_POLICY`.

If the agent variables are missing (including `OMEGA_CI_CONTROLLER_URL`), the
agent exits with a one-line error and the desktop still starts normally, but
nothing listens on 8787. The wrapper does not supervise the agent: if
it crashes, launches fail until the next restart or redeploy, and the controller
reports `wake_failed`.

On container stop, upstream's supervisor (PID 1) signals only its own desktop
processes, so the agent and any in-flight runner end with the container. That
is fine for sleep, which only happens once no job traffic remains. A redeploy
while jobs run fails those jobs; drain first with `POST /v1/drain`.

## Cutover checklist (operator only, HELD)

1. Set `PASSWORD` (≥16 characters) and confirm that the current desktop deploy
   is healthy.
2. Point the service at this repo (Settings → Source: the **source** repo is
   this one; leave the upstream repo alone). Leave **Root Directory blank**.
   Set config-as-code `/deploy/ci-runner-desktop/railway.toml` and the variable
   `RAILWAY_DOCKERFILE_PATH=deploy/ci-runner-desktop/Dockerfile`, then deploy.
   The build log must show the `accetto/ubuntu-vnc-xfce-g3` base and the
   actions-runner download; an error about `/run/secrets/omega_prime_token`
   means Railway built the root MCP-server `Dockerfile` instead.
3. Keep the existing desktop domain on port **3000**. Add a **second** domain
   targeting port **8787** (the agent). Its readiness check is
   `GET https://<agent domain>/healthz` → `{"status":"ok",…}`.
4. Set the agent variables above, and set the controller's
   `OMEGA_CI_DESKTOP_URL` to the 8787 domain.
5. **Static outbound IP.** Railway says services with Static Outbound IPs
   enabled *may remain awake even when idle*. Decide whether to keep it (only
   useful for third-party egress allowlists; it plays no part in the webhook
   path) or drop it so the Desktop can sleep.

## Local validation (optional, never deploys)

```
bash -n deploy/ci-runner-desktop/entrypoint.sh
python -c "import pathlib,tomllib; tomllib.loads(pathlib.Path('deploy/ci-runner-desktop/railway.toml').read_text())"
python -m pytest omega_prime/tests/ci_runners/test_desktop_entrypoint.py omega_prime/tests/ci_runners/test_desktop_image.py -q
# Optional, heavy (~2 GB), local only:
# docker build -f deploy/ci-runner-desktop/Dockerfile -t omega-ci-desktop:local .
```
