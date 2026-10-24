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
  retries 502/503 and connection failures for up to 150 s. In a multi-agent
  swarm, the controller sends `GET /healthz` first to an agent with none of
  its runners and waits for it to be ready. It moves to the next agent if
  that one does not wake. See [Serverless runner swarm](../../docs/ci-runners.md#serverless-runner-swarm-multi-agent-pool-00064).
- **Sleep.** When the jobs finish, the children exit and the agent sends one exit
  callback per child. Nothing then generates outbound traffic, so Railway can
  sleep the service. There are no timers or keep-alives.
- **Credentials.** The Desktop never holds a GitHub credential. It only receives
  single-use JIT configs.

## Files

| File | Purpose |
|---|---|
| `Dockerfile` | Pinned overlay. The build context is the repo root. |
| `entrypoint.sh` | Starts the agent (`python3 -m omega_prime.ci_runners agent --host 0.0.0.0 --port 8787`) and the upstream command (`CMD ["python3", "/opt/railway-desktop.py"]`, argv unchanged) side by side, and forwards SIGTERM/SIGINT to both. Upstream only stops its own children, so without the forward a redeploy SIGKILLed the agent and its runners with no exit callbacks, and the controller held their slots until GitHub timed the jobs out (00062). On SIGTERM the agent drains. Admission closes, and after a 2 s settle (so a runner whose "Running job:" line was printed just before or during the drain is seen as busy, not cancelled) runners that have not taken a job get SIGTERM. Runners mid-job may finish for up to `RAILWAY_DEPLOYMENT_DRAINING_SECONDS` minus a 30 s stop margin (00065: with the pool's `3900` that covers the longest 60-minute job, so a redeploy no longer cancels running jobs, which was the cause of the `types (3.12)` "operation was canceled" and pip-audit exit 143 failures on 24 Oct 2026). Any runner still running when that budget is spent gets SIGTERM, then SIGKILL after `TERM_GRACE_S` (10 s, in parallel). Its output is drained (up to the same grace) and its work tree is removed before its exit callback is sent. A runner whose callback misses Railway's SIGKILL falls back to the old path (GitHub's job-completed webhook, then the controller's 180 s finish deadline). While the agent drains it does not listen on 8787, so the controller places new jobs on the other agents. The volume-backed service cannot start its new deployment until the old one exits, and the agent exits as soon as its last job finishes. The wrapper exits with the upstream status. |
| `job-started.sh` | Runner-side pre-job guard, installed as every runner's `ACTIONS_RUNNER_HOOK_JOB_STARTED`. Fails the job before any step for `pull_request_target`, fork pull requests, repositories not in `OMEGA_CI_AGENT_REPOS` (an `owner/*` entry skips repositories that are themselves GitHub forks: `fork_repo_not_listed`), and (for non-private repositories) any event but `push` / `workflow_dispatch` / `pull_request`. Logic: `omega_prime/ci_runners/agent/jobhook.py`. The build runs it once and requires a refusal (fail closed). |
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
The toolchain layer (00056) instead installs from a dated
`snapshot.ubuntu.com` view, so its package set is reproducible.

To update a pin, re-derive it from upstream evidence and change the
corresponding `ARG`. Never hand-type a hash.

## Toolchain (00056)

Every CI job in Ove's repositories runs on this pool, never on GitHub-hosted
runners (2026-10-24), so section 3b of the Dockerfile adds the toolchains the
swcstudiospace workflows need: build tools, gh, a read-only hosted tool cache
(Python 3.10/3.12, Node 22/24) at `/opt/hostedtoolcache`, .NET SDK 10.0.402
with `maui-android`, JDK 17, Android SDK (API 36), PostgreSQL 16, GreptimeDB,
podman/podman-compose, gitleaks, trivy, semgrep, bandit and Rust. The tool
list, how jobs see the tools, the limits (no running containers, no macOS)
and how to add a tool are in
[docs/ci-runners.md](../../docs/ci-runners.md#runner-image-toolchain).

Extra files: `runner-env` (copied to `/opt/actions-runner/.env`, which
`Runner.Listener` loads, so the toolchain variables reach jobs through the
agent's allowlisted environment), `toolchain-check.sh` (run at build time as
uid 1500; fails the build if any tool is missing, at the wrong version, or
needs root; it stops any database it started on every exit, and builds a
`net10.0-android` app offline), `omega-ci-db` (installed as
`/usr/local/bin/omega-ci-db`: per-job PostgreSQL/GreptimeDB on free loopback
ports with data under `RUNNER_TEMP`, 00058; its port primitives are
`omega-ci-ports`, installed beside it) and `requirements/*.txt`
(hash-locked venvs).

### Toolchain provenance (verified 2026-10-24 AEDT)

| Input | Pin | Evidence |
|---|---|---|
| apt (section 3b) | `snapshot.ubuntu.com` view `20261023T000000Z` | `apt-get update --snapshot` against the pinned base; package versions asserted by `toolchain-check.sh` |
| gh | `gh_2.103.1_linux_amd64.tar.gz` sha256 `57500f34…1add` | `gh_2.103.1_checksums.txt` on the v2.103.1 release |
| gitleaks | `gitleaks_8.30.1_linux_x64.tar.gz` sha256 `551f6fc8…70eb` | `gitleaks_8.30.1_checksums.txt`; same pin as OmpOS CI |
| trivy | `trivy_0.76.1_Linux-64bit.tar.gz` sha256 `c7cbe313…9c0` | `trivy_0.76.1_checksums.txt` on the v0.76.1 release |
| GreptimeDB | `greptime-linux-amd64-v0.14.4.tar.gz` sha256 `50166e3f…e787` | the release's `.sha256sum` file and the GitHub asset digest (the version autonogrammer CI pins) |
| Python 3.10.22 / 3.12.15 | `actions/python-versions` builds `-linux-24.04-x64` sha256 `4c96c0e9…fd16` / `fcb672b0…7ed8` | GitHub release asset digests; versions from `versions-manifest.json` |
| Python 3.13.17 / 3.14.9 (00057) | `actions/python-versions` builds `-linux-24.04-x64` sha256 `f0b66c1c…e932` / `987af6a6…78d4` | GitHub release asset digests (releases `3.13.17-39711892467`, `3.14.9-39619022138`); versions from `versions-manifest.json` |
| CodeQL bundle 2.27.3 (00057) | `codeql-bundle-linux64.tar.gz` sha256 `64518dc4…4c5c` | `codeql-bundle-linux64.tar.gz.checksum.txt` on `codeql-bundle-v2.27.3`; the bundle `github/codeql-action@6f2ac81` (v4) resolves |
| cargo-deny 0.20.2 (00057) | `cargo-deny-0.20.2-x86_64-unknown-linux-musl.tar.gz` sha256 `9f12ed4c…8d5f` | the release's `.sha256` file and the GitHub asset digest |
| ripgrep, ffmpeg (00057) | apt, same snapshot | asserted by `toolchain-check.sh` (`rg`, `ffmpeg`, `ffprobe`) |
| Node.js 22.23.4 / 24.22.1 | `node-v*-linux-x64.tar.xz` sha256 `1805bcd3…e9ce` / `6c172a9a…95e6` | `nodejs.org/dist/v*/SHASUMS256.txt` |
| .NET SDK 10.0.402 | `dotnet-sdk-10.0.402-linux-x64.tar.gz` sha512 `1904a98d…29c2` | `release-metadata/10.0/releases.json` (runtime 10.0.13); version from OmpOS `global.json` |
| `maui-android` workload | workload set `10.0.402.1` | NuGet `Microsoft.NET.Workloads.10.0.400` (packages are signed and verified by the SDK) |
| Android cmdline-tools 23.0 | `commandlinetools-linux-16111833_latest.zip` sha256 `0877a1d0…cfa8` | matches Google's published sha1 `e025545c…5f60` in `repository2-3.xml`; used only to record licence acceptance |
| Android platform `android-36` r2 (00058) | `platform-36_r02.zip` sha256 `37607369…1d2e` | sha1 `2c1a80dd…f576` and size 65878410 in `repository2-3.xml` (checked 2026-10-24 AEDT) |
| Android build-tools 36.0.0 (00058) | `build-tools_r36_linux.zip` sha256 `5d9ac77f…250b` | sha1 `b0b63769…c6fb` in `repository2-3.xml` |
| Android platform-tools 37.0.1 (00058) | `platform-tools_r37.0.1-linux.zip` sha256 `d230f138…13f1` | sha1 `477254aa…53e0` in `repository2-3.xml` |
| Microsoft.NET.ILLink.Tasks 10.0.13 (00058) | `microsoft.net.illink.tasks.10.0.13.nupkg` sha512 `d17dd7dd…7326` | equals the NuGet catalog `packageHash` (SHA512, base64 `0X3X3VG/…yI5zJg==`); version = the SDK's bundled runtime 10.0.13 |
| rustup-init 1.29.2 | sha256 `5be191e8…f4` | `static.rust-lang.org/rustup/archive/1.29.2/…/rustup-init.sha256`; rustup verifies toolchains 1.99.1 (default) and 1.98.1 (00057, Omega-Prime rust-parity pin) against their channel manifests |
| semgrep 1.182.1, bandit 1.9.4, podman-compose 1.6.0 | `requirements/*.txt`, every wheel `--hash=sha256` | `uv pip compile --generate-hashes` against PyPI |

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
  startup from `OMEGA_CI_AGENT_REPOS` (mode 0644, in a root-owned 0755 directory),
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
  unprivileged. Since 2026-10-24 every job runs on this pool regardless
  (Ove's rule), so the image carries the binaries Docker used to provide
  (PostgreSQL 16, GreptimeDB, podman/podman-compose for `config` checks).
  Jobs start the databases with `omega-ci-db`, which picks free ports, so
  parallel jobs on one Desktop never collide.
  See [Toolchain](#toolchain-00056).
- **Directory modes (00055).** `/var/lib/omega-ci` and `/var/lib/omega-ci/work`
  are root-owned **0755**, and the agent re-applies 0755 to the work root at
  startup and refuses to start (`UnsafeWorkdir`) if the work root or any
  directory above it is not listable by others. actions/runner needs read
  permission on its directory and every directory up the hierarchy; with the
  earlier 0711 it exited 0 within a second on every launch. Each runner's own
  `work/<name>` stays 0700 and owned by the job identity. Listing these
  directories shows only runner names and `job-policy.json` (repository
  names); nothing secret lives there. The image build runs the real
  `run.sh` as the job identity (`setpriv`) in the real work root and fails
  unless it gets past that directory check.
- **Runner logs (00055).** Each runner's stdout/stderr go to the agent log
  (Railway service logs), prefixed `runner <name>:`, capped at 200 lines /
  64 KiB per runner, every line redacted (GitHub token shapes, `Bearer`
  values, `token=` assignments and any long opaque blob, so a JIT config is
  never echoed). When a runner exits before taking a job, the agent also logs
  the redacted tail of its newest `_diag` log (at most 60 lines / 8 KiB)
  before the workdir is deleted. Job logs themselves stay in GitHub.
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
| `OMEGA_CI_AGENT_REPOS` | **Required for jobs to run.** The controller's `OMEGA_CI_REPOS`: comma-separated `owner/repo` or `owner/*`. Org-wide (since 2026-10-24): `swcstudiospace/*`, which covers every `swcstudiospace` repository except ones that are themselves GitHub forks (list those by exact name). No bare `*`, no partial globs (the agent refuses to start). Repository names only, not a secret. Unset → the pre-job guard fails every job (fail closed). |
| `SERVERLESS_FRIENDLY` | `1` (recommended). Upstream's own knob: it stops Chromium's background/update/GCM traffic, which would otherwise keep an idle Desktop awake and defeat sleep-as-teardown. |

The image already sets `OMEGA_CI_AGENT_RUNNER_ROOT`, `OMEGA_CI_AGENT_WORK_ROOT`,
`OMEGA_CI_AGENT_JOB_UID`, `OMEGA_CI_AGENT_JOB_GID`, `OMEGA_CI_AGENT_JOB_HOOK`
and `OMEGA_CI_AGENT_JOB_POLICY`. Do not override `OMEGA_CI_AGENT_JOB_POLICY`:
the pre-job guard only reads `/var/lib/omega-ci/job-policy.json`, so the
agent refuses any other value at startup.

If the agent variables are missing (including `OMEGA_CI_CONTROLLER_URL`), the
agent exits with a one-line error and the desktop still starts normally, but
nothing listens on 8787. The wrapper does not supervise the agent: if
it crashes, launches fail until the next restart or redeploy, and the controller
reports `wake_failed`.

On container stop, upstream's supervisor (PID 1) signals only its own desktop
processes, so the agent and any in-flight runner end with the container. That
is fine for sleep, which only happens once no job traffic remains. A redeploy
while jobs run fails those jobs; drain first with `POST /v1/drain`.

## Adding swarm agents 2 and 3 (operator only, HELD on spend)

Each extra agent is a copy of this service: same source repo, config as code,
`RAILWAY_DOCKERFILE_PATH` and variables, except for **its own**
`OMEGA_CI_AGENT_TOKEN`. Keep `sleepApplication = true` (from `railway.toml`)
and no static outbound IP, so it scales to zero. Add a `/config` volume like
agent 1's and a custom domain `runnerN.ego.engineer` on port 8787, then add
the Hostinger `CNAME` Railway shows. On the VPS, write the token to
`/etc/omega-ci/secrets/desktop-N-token` (0600), add
`OMEGA_CI_DESKTOP_N_URL` / `_TOKEN_FILE` to `controller.env`, leave
`OMEGA_CI_MAX_RUNNERS` unset (sum = 24), and restart the controller. The full
variable table is in [docs/ci-runners.md](../../docs/ci-runners.md#every-variable-and-value-3-agent-go-live).

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
4. Set the agent variables above (org-wide: `OMEGA_CI_AGENT_REPOS=swcstudiospace/*`),
   and set the controller's `OMEGA_CI_DESKTOP_URL` to the 8787 domain.
   `OMEGA_CI_CONTROLLER_URL` points at the VPS controller's public site
   (nginx on the VPS, proxying to the controller on `127.0.0.1:8798`).
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
