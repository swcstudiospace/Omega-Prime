# CI runner fleet (autoscaling ephemeral GitHub runners)

The Autonomous Event-Driven ADLC Programming Bot (Omega Prime) can run GitHub Actions jobs on ephemeral, single-job runners that
boot on demand inside a serverless Railway Desktop and disappear afterwards.
A small always-on controller on the VPS receives GitHub's `workflow_job`
webhooks, decides how many runners are needed, mints a single-use JIT runner
config for each one, and asks the Desktop's runner agent to start it. When the
work is done the runners exit, and Railway puts the idle Desktop to sleep.

> **Status: built; deployment in progress (2026-10-24).** "Built" means the
> code and its offline checks pass, with command-and-exit-code receipts: see
> [Verification](#verification). Operator-reported and **unverified** by any
> receipt in this repository: the Railway Desktop overlay is deployed and its
> agent answers `/healthz`, and the VPS controller is installed (nginx site,
> systemd unit, settings) but not started. Creating the GitHub App, installing
> the VPS service, adding the proxy site, and redeploying the Railway Desktop
> are operator steps that are **HELD** (D-120-10). Nothing in this repository
> performs them. Per-repository onboarding is in
> [CI runner repository setup](ci-runners-repo-setup.md).

## How it works

```text
GitHub ──workflow_job webhook (HMAC)──▶ controller (VPS, loopback port behind nginx/Caddy TLS)
                                          │ pure Scaler: event-sourced ledger → decisions
                                          │ GitHub App → generate-jitconfig (single-use)
                                          │ publishes omega.ci.* events (redacted)
                                          ▼ HTTPS + bearer; the first POST is the wake
                          Railway Desktop (serverless) — runner agent :8787
                            one run.sh per reservation, one job, then exit
                            no runners + no traffic ⇒ Railway sleeps it
```

- **Event-driven, no timers (D-120-01, D-120-06).** Inputs are
  `workflow_job` `queued` / `in_progress` / `completed`, the agent's
  `agent.runner_exited` callback, a one-shot `controller.started` reconcile,
  and a per-runner idle deadline. The idle deadline is a single cancellable
  `loop.call_later` handle (or the event stream's delayed delivery), not a
  schedule or loop.
- **Control plane vs data plane (D-120-02).** The controller holds every
  credential (GitHub App key, webhook secret, agent bearer). The Desktop holds
  no GitHub credential; it only ever receives single-use JIT configs.
- **Stdlib core (D-120-03).** The scaler, controller, agent and supervisor use
  the Python standard library. Serving needs the optional `ci-runners` extra
  (`uvicorn`); App auth lazily imports `cryptography`. If it is missing,
  the controller fails with `CryptoUnavailable` and an install hint, and it
  never silently falls back to a PAT.
- **Wake and sleep (D-120-05).** The controller's first `POST /v1/runners` is
  the wake request; there is no preflight health poll. Connect errors and
  502/503 are retried with jittered backoff (capped at 15 s, at most 16
  attempts) inside a 150 s budget that includes request time. 429 is never
  retried. The one-shot startup reconcile does one `GET /v1/runners`, which
  may wake the Desktop once. Once runners exit, the agent sends nothing, and
  Railway sleeps the service after roughly 5–10 minutes without outbound
  packets.

### Scaling rule

A job belongs to the pool when it runs in an allowlisted **private**
repository and its `runs-on` labels are a subset of the pool labels
(default `self-hosted, linux, x64, omega-ci`). A **public** repository is
served only when it is opted in and the job passes the fork guard (see
[Public repositories](#public-repositories)). On `queued`:

```text
launch = min(max_runners, queued + busy) − (starting + idle + busy), never below 0
```

Each launch mints a JIT config for a runner named `omega-ci-<job id>-<suffix>`
and posts it to the agent. `in_progress` marks the named runner busy (a job
taken by another runner frees a slot). When nothing is queued, idle runners
get an idle deadline after `OMEGA_CI_IDLE_GRACE_S`. At the deadline the
controller checks GitHub first, deletes the runner only if it is still idle,
then stops it on the agent. Webhook deliveries are de-duplicated by
`X-GitHub-Delivery`, and job state only moves forward.

### Identity: name + generation

Every reservation is identified by the pair `name` + `generation`. The
generation is an opaque string nonce issued by the controller, unique across
controller restarts. It is not an integer, timestamp or counter. Retries,
callbacks and stale-event checks always compare the exact pair, so a late
event for an old reservation can never act on a new one.

## Settings

All settings are validated by `omega_prime/ci_runners/settings.py`; nothing
else defines configuration. Secrets are referenced **by file path** (`*_FILE`).
Every secret is read through the `omega_prime.credentials` broker, bound to
the one host it is sent to (GitHub API, the webhook origin, the desktop or
controller URL). The file must be a non-empty regular file with mode 0600;
errors name the variable, never the path contents.
The single exception is `OMEGA_CI_AGENT_TOKEN`, a Railway service variable on
the agent. Secret values are never logged, put in a repr, or placed on argv.

### Controller (VPS)

| Variable | Default | Meaning |
|---|---|---|
| `OMEGA_CI_SCOPE` | `org` | `org` or `repo` registration scope. |
| `OMEGA_CI_ORG` | — | Organisation login (derived from `OMEGA_CI_REPO` in repo scope). |
| `OMEGA_CI_REPO` | — | `owner/repo`, repo scope only. |
| `OMEGA_CI_REPOS` | empty | Comma-separated allowlist of `owner/repo` names or `owner/*` (every repository of that owner that is not itself a GitHub fork). Empty = every private repo the runner group allows. See [Org-wide setup](#org-wide-setup-every-repository-of-an-owner). |
| `OMEGA_CI_PUBLIC_REPOS` | empty | Public repositories explicitly opted in, as `owner/repo` or `owner/*`. Each entry must be covered by a non-empty `OMEGA_CI_REPOS` (`owner/*` only by `owner/*`). Empty = every public repo is refused. See [Public repositories](#public-repositories). |
| `OMEGA_CI_LABELS` | `self-hosted,linux,x64,omega-ci` | Pool labels. |
| `OMEGA_CI_RUNNER_GROUP` | `omega-ci` | Runner group display name. |
| `OMEGA_CI_RUNNER_GROUP_ID` | — (org), `1` (repo) | Numeric group id; required in org scope and never inferred from the name. |
| `OMEGA_CI_MAX_RUNNERS` | `8` | Concurrent runner cap, 1–10. |
| `OMEGA_CI_IDLE_GRACE_S` | `180` | Idle grace before reaping, 0–3600 s. |
| `OMEGA_CI_WAKE_BUDGET_S` | `150` | Wake retry budget, 1–900 s. |
| `OMEGA_CI_GITHUB_APP_ID` | — | GitHub App id (App mode). |
| `OMEGA_CI_GITHUB_INSTALLATION_ID` | — | App installation id (App mode). |
| `OMEGA_CI_GITHUB_APP_KEY_FILE` | — | Path to the App **private key** `.pem` (App mode). It comes from the "Private keys" section at the bottom of the App's General settings page. A client secret is a different value and does not work. |
| `OMEGA_CI_GITHUB_PAT_FILE` | — | Path to a PAT; only when no App setting is present. |
| `OMEGA_CI_WEBHOOK_SECRET_FILE` | — | Path to the webhook HMAC secret. Required. |
| `OMEGA_CI_DESKTOP_URL` | — | `https://` URL of the agent's 8787 domain. Required. |
| `OMEGA_CI_DESKTOP_TOKEN_FILE` | — | Path to the agent bearer token. Required. |
| `OMEGA_CI_PUBLISHER` | `jsonl` | `jsonl`, `null` or `eventstream`. |
| `OMEGA_CI_EVENTS_PATH` | `~/.omega/ci-runners/events.jsonl` | JSONL event log. |
| `OMEGA_CI_DEMAND_PATH` | `~/.omega/ci-runners/demand.json` | Queued-job backlog replayed after a restart (non-secret fields, mode 0600, entries older than 24 h dropped, each entry re-checked against GitHub before replay). |

Auth selection fails closed. A complete App configuration selects App mode. No
App settings plus a PAT file selects PAT mode. A partial App configuration, or
both modes at once, is an error. Ambient tokens such as `GH_TOKEN` are never
read. A sanitized template is in `deploy/ci-runner-controller/env.example`.

### Agent (Railway Desktop)

| Variable | Default | Meaning |
|---|---|---|
| `OMEGA_CI_AGENT_TOKEN_FILE` | — | Path to the bearer token (must be private to the agent). |
| `OMEGA_CI_AGENT_TOKEN` | — | Railway-only alternative, at least 32 characters. Exclusive with the file form. |
| `OMEGA_CI_AGENT_MAX_RUNNERS` | `8` | Agent's own cap, 1–10, enforced independently of the controller. |
| `OMEGA_CI_AGENT_RUNNER_ROOT` | `/opt/actions-runner` | Read-only shared runner install. |
| `OMEGA_CI_AGENT_WORK_ROOT` | `/config/ci-runners` | Per-runner workdirs. The image sets `/var/lib/omega-ci/work`, because upstream re-owns `/config` at boot. |
| `OMEGA_CI_AGENT_JOB_UID` / `OMEGA_CI_AGENT_JOB_GID` | unset (image: 1500) | Dedicated non-root job identity, distinct from the agent's. |
| `OMEGA_CI_AGENT_REPOS` | empty | **Required for jobs to run on the image.** Comma-separated `owner/repo` or `owner/*` list the runner-side pre-job guard lets run; set it to the controller's `OMEGA_CI_REPOS` (org-wide: `swcstudiospace/*`). Empty → the guard refuses every job (fails closed). Repository names only, no secret. |
| `OMEGA_CI_AGENT_JOB_HOOK` | unset (image: `/opt/omega-ci/job-started.sh`) | Pre-job guard the agent sets as `ACTIONS_RUNNER_HOOK_JOB_STARTED` for every runner. Must be a regular file the job identity cannot modify, or the agent refuses to start. |
| `OMEGA_CI_AGENT_JOB_POLICY` | `/var/lib/omega-ci/job-policy.json` | Root-owned allowlist file the agent writes at startup from `OMEGA_CI_AGENT_REPOS`; the guard reads it. Only this path is accepted: the shipped `job-started.sh` hard-codes it (a runner hook takes no arguments, and the job's environment is not agent-controlled), so any other value stops the agent at startup with a `SettingsError` instead of failing every job. |
| `OMEGA_CI_CONTROLLER_URL` | — | **Required: the agent will not start without it.** Controller base URL for the exit callback. Set it at deploy time, even before the controller exists: the agent only calls it after a job ends. Without it, the agent exits with `OMEGA_CI_CONTROLLER_URL: required callback base URL`, so nothing listens on 8787 and Railway answers 502 Bad Gateway. |

## Interfaces

Controller (`python -m omega_prime.ci_runners controller --host 127.0.0.1 --port 8788`;
8788 is the default, and the production VPS uses `--port 8798` because 8788 is
taken there):

| Route | Auth | Result |
|---|---|---|
| `GET /healthz` | none | 200, non-secret status. |
| `POST /github/webhook` | HMAC `X-Hub-Signature-256` | 202 once queued and written to the demand file, before any GitHub/Desktop call; 503 when the queue is full or the demand file cannot be written (not committed, so GitHub can redeliver). |
| `POST /agent/events` | bearer | 202 for `{type:"agent.runner_exited",name,generation,exit_code}`. |

Agent (`python -m omega_prime.ci_runners agent --host 0.0.0.0 --port 8787`):

| Route | Result |
|---|---|
| `GET /healthz` (no auth) | 200 `{status, draining, runners, capacity}`. |
| `GET /v1/runners` | 200, list of `{name, generation, status, pid}`. |
| `POST /v1/runners` `{name, generation, jitconfig}` | 201 first launch; 200 same name+generation replay (`{"status": "replayed"}` while the child runs, `{"status": "finished", "exit_code": N}` once it has exited, so a retry settles a lost exit callback instead of holding a dead slot); 409 conflicting generation (the running child is kept) or draining; 429 full; 400 malformed; 401 unauthorized; 413 over 1 MiB. |
| `DELETE /v1/runners/{name}` | 202 stopping; 404 unknown. |
| `POST /v1/drain` | 200 `{draining: true}`: no new runners, in-flight jobs finish. |

The JIT config reaches `run.sh` only as `ACTIONS_RUNNER_INPUT_JITCONFIG` in an
allowlisted child environment. It never appears on argv, on disk, in logs,
in responses or in events. After the child's own `process.wait()` returns and
its workdir is removed, the agent sends exactly one exit callback.

## Security model

- **Trust boundaries.** GitHub → controller is HMAC-verified with a 1 MiB cap
  and an event allowlist. Controller → agent and agent → controller use a
  constant-time bearer check. Agent → job is a UID boundary.
- **Job identity.** Jobs run as a dedicated non-root UID/GID, distinct from the
  agent (root, which owns the credentials). They get `nice 10` and
  `RLIMIT_NPROC` / `RLIMIT_NOFILE` caps. Concurrent jobs share that UID, so
  treat the pool as same-trust: only your own organisation's repositories,
  with public ones behind the fork guard.
- **Public repositories are refused by default.** Self-hosted runners on
  public repositories are a known remote-code-execution vector. A public
  repository is served only through the explicit opt-in and fork guard below.
- **Docker (D-120-08, superseded 2026-10-24).** Railway is unprivileged: no
  Docker, DinD or privileged mode. Ove's rule since 2026-10-24: every job runs
  on this pool anyway, so jobs that used `container:`, `services:` or
  `docker run` switch to the binaries in the
  [runner image toolchain](#runner-image-toolchain) instead of a Docker host.
- **Redaction (D-120-07).** Every event passes through
  `omega_prime.credentials.redact` with the known transient secrets. Anything
  that still contains a secret, or is not plain JSON, is dropped.
- **Rate limits (D-120-09).** As a GitHub App installation, the controller has
  its own request budget, separate from any personal token, and makes about
  two ordinary API calls per job (generate JIT; idle check/delete). The
  installation token is cached and refreshed on demand once it is within five
  minutes of expiry, single-flight and without a daemon. Only real
  rate-limit responses are retried (3 attempts, 60 s budget).
- **Fork-guard calls.** The fork guard adds one
  `GET /repos/{repo}/actions/runs/{run_id}` per workflow run of an opted-in
  public repository (the verdict is cached for the run's other jobs, and
  concurrent jobs of one run share the lookup). Private repositories make no
  extra call.

## Runner policy: every job on the pool (2026-10-24)

Ove's rule, also a standing directive in the Autonomous Event-Driven ADLC
Programming Bot's prompt (`ci_runner_scope` in
`omega_prime/prompts/_shared/core-directives.xml`): every CI/CD job in his
repositories runs on the Omega CI runner pool,
`runs-on: [self-hosted, linux, x64, omega-ci]`, never on GitHub-hosted
runners (`ubuntu-latest` and friends). When a job needs a tool, service or
capacity the pool lacks, extend the runner image
(`deploy/ci-runner-desktop/Dockerfile`) or the pool instead of falling back to
a GitHub-hosted runner. There is no carve-out for side runners either. The
pool has no Docker (see [Security model](#security-model)), so a job that
needs a service container or a Docker build is rewritten to use binaries baked
into the runner image (Postgres, GreptimeDB and similar, started as ordinary
processes in the job; task 00056) and runs on the pool like everything
else.

## Org-wide setup (every repository of an owner)

As of 2026-10-24 (Ove's decision), the Autonomous Event-Driven ADLC
Programming Bot's runners serve **every `swcstudiospace` repository**, not only
Omega-Prime. Listing 100+ repositories one by one does not scale and misses
new ones, so each allowlist (`OMEGA_CI_REPOS`, `OMEGA_CI_PUBLIC_REPOS`,
`OMEGA_CI_AGENT_REPOS`) accepts the owner wildcard `owner/*`:

| Where | Setting | Value |
|---|---|---|
| GitHub org → Actions → Runner groups → `omega-ci` | Repository access | **All repositories**, and tick **Allow public repositories** |
| GitHub org → Actions → General | Fork pull request workflows from outside collaborators | **Require approval for all outside collaborators** |
| VPS `/etc/omega-ci/controller.env` | `OMEGA_CI_REPOS` | `swcstudiospace/*` |
| VPS `/etc/omega-ci/controller.env` | `OMEGA_CI_PUBLIC_REPOS` | `swcstudiospace/*` |
| Railway → Ubuntu Desktop → Variables | `OMEGA_CI_AGENT_REPOS` | `swcstudiospace/*` |

How entries are read (one shared matcher, `repo_allowed` / `patterns_cover`
in `omega_prime/ci_runners/agent/jobhook.py`, used by the controller's
settings, webhook admission, demand replay and fork guard and by the
runner-side hook, so the two sides can never disagree):

- An entry is `owner/repo` or `owner/*`, matched case-insensitively. There is
  **no bare `*`** and no partial glob (`owner/foo*`, `*/repo`, `owner/**`):
  those are rejected at startup with a `SettingsError` that names the
  variable. Opening the pool to every owner is not expressible.
- `owner/*` matches that owner only (`swcstudiospace-evil/x` does not match
  `swcstudiospace/*`) and covers repositories created later automatically.
- Empty still means nothing for the guard: an empty `OMEGA_CI_AGENT_REPOS`
  refuses every job, and an empty `OMEGA_CI_REPOS` cannot opt public
  repositories in.
- Subset rule: an `OMEGA_CI_PUBLIC_REPOS` entry must be covered by
  `OMEGA_CI_REPOS`. `owner/repo` is covered by itself or by `owner/*`;
  `owner/*` only by `owner/*` (a list of names never covers a wildcard).
- Every fork-guard rule stays: public repositories still get the run lookup,
  and only `push`, `workflow_dispatch`, same-repository `pull_request`, and
  `schedule` on that repository's default branch are served. Fork pull
  requests, `pull_request_target` and unknown heads are refused by both the
  controller and the runner-side hook.

**Org repositories that are themselves GitHub forks.** Some `swcstudiospace`
repositories are forks of upstream projects. Their workflow files come from
upstream (a "Sync fork" pushes them in), and an upstream workflow that asks
for plain `runs-on: self-hosted` would match this pool. So an `owner/*`
entry **never** admits a repository whose `repository.fork` is `true`, or
whose fork status is missing from the payload (fails closed):

- The controller ignores its jobs (no runner is launched for them).
- The runner-side hook refuses them with `fork_repo_not_listed` if GitHub
  assigns one to a runner anyway.
- To serve such a repository on purpose it must be **private**: add its
  exact `owner/repo` name to `OMEGA_CI_REPOS` and `OMEGA_CI_AGENT_REPOS`
  (for example `OMEGA_CI_REPOS=swcstudiospace/*,swcstudiospace/some-fork`).
  Even then, pull requests inside it are refused, because GitHub reports
  every head repository of a fork as `fork: true`, so only `push`,
  `workflow_dispatch` and other non-pull-request triggers run there.
- A **public** fork can never be served, by design. Listing it exactly in all
  three lists (including `OMEGA_CI_PUBLIC_REPOS`) does not help: the
  controller's fork guard checks the workflow run's `head_repository`, which
  for a fork is always `fork: true`, so every trigger (`push` and
  `workflow_dispatch` included) is refused with `fork`. There is no
  exception for public forks.

Opening the runners to any other organisation or owner needs Ove's explicit
decision.

## Public repositories

Anyone can open a pull request against a public repository. If that pull
request's workflow ran on these runners, a stranger's code would execute on
your machine, next to other jobs that share the job UID. That is why the
matcher refuses public repositories unless you opt one in, and why opting in
is not enough on its own.

**Opt-in.** List the repository in `OMEGA_CI_PUBLIC_REPOS`, or opt in every
repository of an owner with `owner/*` (see
[Org-wide setup](#org-wide-setup-every-repository-of-an-owner)). Each entry
must be covered by `OMEGA_CI_REPOS`, and `OMEGA_CI_REPOS` must not be empty.
The default is empty, so behaviour for every repository is unchanged until you
set it.

**Fork guard.** The `workflow_job` webhook alone cannot say where the code
came from. For each new (`queued`) job from an opted-in public repository the
controller fetches the job's workflow run
(`GET /repos/{repo}/actions/runs/{run_id}`, needs **Actions: Read**) before the
scaler sees the job, and serves it only when all of these hold:

- the run belongs to the same repository the webhook named;
- `head_repository` is that same repository and is not a fork;
- the trigger (`event`) is `push`, `workflow_dispatch`, `pull_request`
  (which, given the check above, means a same-repository branch), or
  `schedule` whose `head_branch` is the repository's `default_branch`.
  GitHub runs scheduled workflows from the default branch only, so that
  run is the same trust as a push of it. A schedule run that does not say
  so is refused. `pull_request_target` is refused by name, and so is every
  other trigger (`workflow_run`, `issue_comment`, `merge_group` and so on).

Everything unknown fails closed: a lookup error, a 404, a missing field or an
unexpected shape refuses the job. Lookup errors are not cached, so a
redelivered or re-run job is checked again; definite verdicts are cached per
run (up to 512 runs). Trust is never persisted: after a restart each saved
public job is checked again before it is replayed. Each refusal emits
`omega.ci.runner.refused` with `job_id`, `run_id`, `repo` and a `reason`:
`public_not_opted_in`, `fork`, `pull_request_target`, `event_not_allowed`,
`head_unknown`, `run_repo_mismatch`, `run_not_found` or `run_lookup_failed`.
A refused job is not cancelled; it waits for another runner or times out.

**State only moves forward.** A `queued` delivery for a job the controller
has already seen `in_progress` or `completed` is acknowledged and ignored, as
is a verified `queued` event whose job started or finished while its run
lookup was in flight. Stale demand therefore never keeps the queue non-empty
(which would keep an unused runner and the Desktop awake).

**Restart after a 202 (durable before acknowledging).** A `202` means the job
is on disk. Every accepted `queued` pool job, public or private, is written
to the demand file *before* the webhook handler commits the delivery and
answers, so a restart at any later point replays it: during a public job's
run lookup, while it waits in the worker queue, or while the controller is
still starting up. During startup the write keeps the saved demand that is
still being reconciled, so it is never overwritten. Replay re-checks each job
against GitHub and re-runs the fork guard for public ones. A refused job is
removed from the file. If the write fails, the job is not acknowledged: the
handler answers `503`, does not commit the delivery, and emits
`omega.ci.runner.error` `demand_persist`, so the delivery shows as failed in
GitHub and can be redelivered.

**Runner-side pre-job guard (the enforcement point).** Refusing a webhook
only decides whether the controller *launches* a runner. A JIT runner
registers into the shared runner group with the pool labels, so GitHub may
assign it *any* matching job, including one the controller refused (an
approved fork pull request, say). The Desktop image therefore installs
`/opt/omega-ci/job-started.sh` as every runner's
`ACTIONS_RUNNER_HOOK_JOB_STARTED`. The runner executes it after a job is
assigned and before the first workflow step; a non-zero exit fails the job, so
no user code runs. It reads `GITHUB_EVENT_NAME`, `GITHUB_REPOSITORY` and
`GITHUB_EVENT_PATH` and refuses:

- a repository not in `OMEGA_CI_AGENT_REPOS` (empty list → every job);
- a repository that is itself a GitHub fork (or whose fork status is
  missing) when only an `owner/*` entry covers it (`fork_repo_not_listed`);
- `pull_request_target`, always;
- any pull request whose head repository is a fork, is another repository,
  or cannot be determined (private repositories included);
- for a repository the event payload does not mark `private: true`, any
  event other than `push`, `workflow_dispatch`, `pull_request`, or
  `schedule` when `GITHUB_REF` is `refs/heads/<default_branch>` from the
  payload;
- missing context or an unreadable or mismatched event payload.

The allowlist reaches the hook through a root-owned policy file the agent
writes at startup, not through the job's environment, and the hook runs as
`python3 -I` with absolute paths, so the job cannot redirect it. Its rules
and tests are `omega_prime/ci_runners/agent/jobhook.py` and
`test_jobhook.py`. A refused job fails in GitHub with
`omega-ci job guard refused this job (<reason>)`. The runner then exits, and
the controller treats that like any other finished job.

**Defence in depth.** Set the Actions fork policy to **Require approval for
all outside collaborators**. For the org-wide setup do it once at org level
(Organisation settings → Actions → General → "Approval for running fork pull
request workflows from contributors"), so every current and future
repository inherits it; for a single repository it is the same option under
the repository's Settings → Actions → General. Then a fork pull request
cannot start any workflow until a maintainer approves it. The runner group's
repository access should match the allowlists: **All repositories** with
"Allow public repositories" for the org-wide setup, or only the opted-in
repositories otherwise.

## Events

CloudEvents with source `omega-prime/ci-runners`:
`omega.ci.runner.{demand,launch_requested,launched,busy,idle,finished,reaped,exited,error,launch_backoff,launch_retry,launch_circuit_open,launch_circuit_closed,refused}`
and `omega.ci.desktop.{wake_requested,woke}`. The default publisher appends to
`OMEGA_CI_EVENTS_PATH`; `eventstream` publishes to the event stream once it is
available.

## Runner image toolchain

**Rule (Ove, 2026-10-24): every CI job in his repositories runs on this pool**
(`[self-hosted, linux, x64, omega-ci]`), never on GitHub-hosted runners. When
a job needs a tool, the answer is to add it to the image, not to send the job
elsewhere. Task 00056 added the toolchains the swcstudiospace workflows use
(PumpGrok, claude-ultrathink, OmpOS, agent-substrate, agent-realm,
agent-swarm, autonogrammer, faceless-desk, spectrumwebco-marketing).

### What is in the image

Base: `accetto/ubuntu-vnc-xfce-g3` (digest-pinned), **Ubuntu 24.04.5 LTS
(noble)**. apt packages come from the dated `snapshot.ubuntu.com` view
`APT_SNAPSHOT` (`20261023T000000Z`), so a rebuild gets the same package set.
Everything else is version-pinned and checksum-verified in the Dockerfile.

| Tool | Version | Where | Used by |
|---|---|---|---|
| git | 2.43.0 (apt) | `/usr/bin` | everything (`actions/checkout` needs ≥ 2.18) |
| gh | 2.103.1 | `/usr/local/bin` | scripts |
| jq, make, build-essential (gcc 13), pkg-config, libssl-dev, file, zip, unzip, xz-utils, rsync, curl | apt | `/usr/bin` | general; `unzip` is required by `oven-sh/setup-bun` |
| age | 1.1.1 (apt) | `/usr/bin` | ultrathink off-host round-trip tests |
| pipx 1.4.3, python3-venv, python3-pip | apt (system Python 3.12.3) | `/usr/bin` | `pipx install ...` (lands in the job's `HOME`) |
| Python | 3.10.22, 3.12.15, 3.13.17, 3.14.9 (each with `bin/pip`) | tool cache | `actions/setup-python` (Omega-Prime matrix 3.12–3.14) |
| CodeQL bundle | 2.27.3 | tool cache `CodeQL/2.27.3/x64` | `github/codeql-action/init@6f2ac81` (ultrathink); the cache is read-only, so the bundle must match the action pin |
| Node.js (+ npm) | 22.23.4, 24.22.1 | tool cache | `actions/setup-node` (`22.x`, `24.x`) |
| Bun | not pre-installed | job `HOME` | `oven-sh/setup-bun` downloads into `~/.bun/bin` (any version) |
| .NET SDK | 10.0.402 (OmpOS `global.json`) + `maui-android` workload (workload set 10.0.402.1) | `/usr/share/dotnet` | `actions/setup-dotnet` (`global-json-file`) |
| JDK | OpenJDK 17 (apt) | `JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64` | Android builds |
| Android SDK | cmdline-tools 23.0 (build 16111833), `platforms;android-36`, `build-tools;36.0.0`, platform-tools | `ANDROID_HOME=/opt/android-sdk` | OmpOS `net10.0-android` |
| PostgreSQL | 16.x (apt, server + client; no cluster, no service) | `/usr/lib/postgresql/16/bin` on `PATH` | agent-substrate (replaces the `postgres:16` service) |
| GreptimeDB | 0.14.4 (`greptime standalone`) | `/usr/local/bin` | autonogrammer (replaces `docker run greptime/greptimedb`) |
| podman / podman-compose | 4.9.3 (apt) / 1.6.0 | `/usr/bin`, `/usr/local/bin` | OmpOS compose validation (`config` only, see limits) |
| gitleaks | 8.30.1 | `/usr/local/bin` | secret scans |
| trivy | 0.76.1 | `/usr/local/bin` | vulnerability scans |
| semgrep | 1.182.1 (hash-locked venv) | `/usr/local/bin` | SAST |
| bandit | 1.9.4 (hash-locked venv) | `/usr/local/bin` | Python SAST |
| Rust | rustup 1.29.2, toolchains 1.99.1 (default) and 1.98.1 (minimal + rustfmt + clippy) | `RUSTUP_HOME=/opt/rust/rustup`, proxies in `/opt/rust/cargo/bin` | faceless-desk (1.99.1); Omega-Prime rust-parity (1.98.1, via `RUSTUP_TOOLCHAIN`) |
| cargo-deny | 0.20.2 | `/usr/local/bin` | Omega-Prime license gates (replaces `cargo-deny-action`, a Docker action) |
| ripgrep | 14.x (apt) | `/usr/bin/rg` | autonogrammer branding gate |
| ffmpeg / ffprobe | 6.1 (apt) | `/usr/bin` | faceless-desk CLI integration tests |

The authoritative list is `deploy/ci-runner-desktop/Dockerfile` (the `ARG`
block and section 3b) and `deploy/ci-runner-desktop/requirements/*.txt`.

### How jobs see the tools

- **The tool cache** is `/opt/hostedtoolcache`, the same path GitHub's images
  use (the Python builds are compiled for it). It follows the layout the
  `setup-*` actions look up: `<Tool>/<version>/x64/` plus an
  `<version>/x64.complete` marker. A cache hit writes nothing, so the
  root-owned, read-only tree works for uid 1500. **Ask for a version the
  image has** (`3.12`, `3.10`, `22.x`, `24.x`): any other version makes the
  action try to download into the read-only cache, and the job fails. Add
  the version to the image instead.
- **The job environment.** The agent gives each runner only an allowlisted
  environment (`PATH`, locale, `HOME`). The toolchain variables
  (`RUNNER_TOOL_CACHE`, `AGENT_TOOLSDIRECTORY`, `DOTNET_ROOT`,
  `DOTNET_INSTALL_DIR`, `JAVA_HOME`, `ANDROID_HOME`, `RUSTUP_HOME`, `PATH`)
  reach jobs through the runner's own root env file,
  `/opt/actions-runner/.env`, built from `deploy/ci-runner-desktop/runner-env`.
  `Runner.Listener` loads it at startup, and the agent hard-links it into
  every per-runner work dir.
- **Installs land in the job's `HOME`.** `HOME` is the runner's private work
  dir. `pip install` on a tool-cache Python cannot write the read-only
  site-packages, so pip falls back to `--user` (`~/.local`). `cargo` keeps its
  registry in `~/.cargo` (`CARGO_HOME` is unset on purpose). The pre-job
  guard, after allowing a job, appends `~/.local/bin` and `~/.cargo/bin` to
  `GITHUB_PATH`, so `pip install uv && uv ...` works as on hosted runners.
- **pip is the one each Python build bundles** (3.10.22: pip 23.0.1,
  3.12.15: pip 25.0.1). The image does not run the builds' unpinned
  `pip install --upgrade pip`. A job that needs a newer pip can upgrade it
  into its own `--user` site.
- **No root at job time.** There is no `sudo`. A step like
  `sudo apt-get install age rsync` must go; the tool is either already in the
  image or belongs in it.

### Limits (real technical constraints)

- **No Docker, no running containers.** Railway runs the container
  unprivileged: there is no Docker daemon, and rootless podman needs user
  namespaces that Railway is not known to grant (not yet tried on the live
  Desktop), so treat running containers as unavailable. podman and
  podman-compose are installed for `podman-compose ... config` validation,
  which needs no engine. Databases
  that used to come from containers come from the image binaries instead:

  ```bash
  # PostgreSQL 16 (agent-substrate): a throwaway cluster as the job user
  initdb -D "$RUNNER_TEMP/pg" -U postgres --auth=trust
  pg_ctl -D "$RUNNER_TEMP/pg" -l "$RUNNER_TEMP/pg.log" -w \
    -o "-k $RUNNER_TEMP -p 54321 -c listen_addresses=127.0.0.1" start
  # GreptimeDB (autonogrammer)
  greptime standalone start --http-addr 127.0.0.1:4000 \
    --data-home "$RUNNER_TEMP/greptime" --log-dir "$RUNNER_TEMP/greptime/logs" &
  ```
- **macOS jobs** (OmpOS `shells` on `macos-15`, ultrathink's
  `macos-latest` matrix leg) cannot run on a Linux pool. The Android part of
  OmpOS's MAUI head (`-f net10.0-android`) builds here; the iOS and
  MacCatalyst heads need macOS and Xcode. That is Ove's call: drop the macOS
  legs, or add a macOS runner to the swarm later.
- **Rust channel names.** The image has toolchain 1.99.1 as the default. A
  `rust-toolchain.toml` that names `stable` (or an action that runs
  `rustup toolchain install`) needs a write to the read-only `RUSTUP_HOME`
  and fails. Pin `1.99.1`, or bump `RUST_VERSION` in the image.
- **CPU budget versus visible CPUs.** Each runner is budgeted 2 vCPU but
  sees every host CPU (48 measured), and the Desktop's pid cgroup
  (`pids.max` 1000, measured 2026-10-24; Railway's limit, well below the job
  uid's `RLIMIT_NPROC` 4096) is shared by every concurrent job and the agent. Tools that size their worker pools from the CPU count exhaust it
  ("Cannot acquire required number of nodes", "can't spawn worker thread").
  Cap them at the budget in the workflow: `DOTNET_PROCESSOR_COUNT: 2`
  (MSBuild), `CARGO_BUILD_JOBS: "2"` (cargo), Next.js
  `experimental.cpus`, vitest `--maxWorkers=2`, `dotnet test
  --max-parallel-test-modules 2` (task 00057). These keep one job inside its
  share; the 1000-task ceiling itself is a Railway service limit, so many
  concurrent thread-heavy jobs can still exhaust it.
- **Omega-Prime's own container jobs (task 00057).** This page does not
  claim `unshare -r` exited 0. Two image jobs on the pool exited 1, and
  their logs are the record. Job 120657880461 (runner
  `omega-ci-120656411281-w6to`) logged `unshare: cannot change root
  filesystem propagation: Permission denied` and the step exited 1. Job
  120662745206 (runner `omega-ci-120662861496-8hh0`) logged `podman info`
  output `rootless=true graph=vfs` and then `marking filesystem ... as
  private: mount ... flags: 0x1000: permission denied`; the job's
  conclusion is failure. `CapBnd` in that log is `00000000800405fb` (no
  `CAP_SYS_ADMIN`, bit 21). `/dev/fuse` was not a device node, so
  fuse-overlayfs was not selected. setuid `newuidmap` is not called; PATH
  shims write the one mapping the job user is allowed to write. Storage
  uses `ignore_chown_errors` with vfs under `RUNNER_TEMP`, and the build
  asks for chroot isolation. That does not finish a build.
  `deploy/ci-runner-desktop` has to be started with `CAP_SYS_ADMIN` in the
  bounding set and run `mount --make-rslave /` before the job user starts.
  Until then `grokbot-image` cannot build or run a container. The
  container's single mapped uid is presented as 10001, and
  nothing is published on a host port. `ci.yml` `eventstream-it` still uses a
  `services:` Pulsar container. That needs a Docker daemon at job setup, and
  the owned-secure harness execs as root and as uid 10000 inside one
  container. A single-ID namespace cannot name both, so the job stays blocked
  until the pool can apply a subordinate uid range or the runner image ships
  a Pulsar binary and the harness stops using `services:`.
- **`dotnet workload install`** needs a writable SDK directory. `maui-android`
  is pre-installed, so on this pool skip the step (the `maui` workload is
  macOS/Windows-only anyway).

### Size and disk

Measured on the local build (rootless `podman build`, 2026-10-24 AEDT): the
image is **9.6 GB uncompressed** (the base is 0.7 GB). Expect roughly a third
of that as the compressed pull (estimate, not measured). Biggest parts:

| Tree | Size |
|---|---|
| `/usr/share/dotnet` (.NET SDK 10.0.402 + `maui-android` packs) | 4.0 GB |
| `/opt/hostedtoolcache` (2 Pythons, 2 Nodes) | 0.94 GB |
| `/opt/rust` | 0.64 GB |
| `/usr/local/bin` (gh, gitleaks, trivy, greptime, ...) | 0.55 GB |
| `/opt/android-sdk` | 0.49 GB |
| `/opt/venvs` (semgrep, bandit, podman-compose) | 0.42 GB |
| `/usr/lib/jvm` (JDK 17) | 0.26 GB |
| apt build tools, PostgreSQL 16, podman, the desktop | the rest |

Plan for:

- **Build host:** about 25 GB free (layers, plus the downloads inside each
  layer). The local build used about 11 GB of container storage.
- **Railway:** the image is pulled once per deploy and cold-starts a little
  slower. Each running job also needs its own scratch disk under
  `/var/lib/omega-ci/work` (checkout, `~/.nuget`, `~/.cargo`, `target/`):
  budget 2–5 GB per concurrent runner for .NET/Rust jobs.

### Self-check

`deploy/ci-runner-desktop/toolchain-check.sh` runs at the end of every image
build **as the job identity (uid 1500) with the job's environment**. It
asserts each pinned version, that the shared trees are not writable by the
job, and it really uses the tools: creates a venv, starts and queries a
throwaway PostgreSQL cluster and a GreptimeDB standalone instance, runs
`podman-compose config`, and compiles and runs a Rust crate. Any failure fails the build. On a live Desktop:

```bash
/opt/omega-ci/toolchain-check.sh --as-job 1500 1500   # as root
```

`omega_prime/tests/ci_runners/test_desktop_toolchain.py` keeps the inputs
honest in normal CI: every checksum pin is a full digest and is actually
verified, apt uses the snapshot, the Python CLIs are hash-locked, and
`runner-env` matches the image `PATH`.

### Adding a tool to the runner image

1. **Check you need it** (`omega_prime/skills/security-supply-chain`):
   name, publisher, maintenance, licence.
2. **Pick the source, in this order:** an apt package (it is pinned by the
   snapshot; to move the snapshot, bump `APT_SNAPSHOT`); an official release
   binary with a published checksum; a PyPI tool in its own hash-locked
   venv. Never `curl | sh`, never an unpinned `latest`.
3. **Pin it.** Add `ARG <TOOL>_VERSION` and `ARG <TOOL>_SHA256` (or
   `_SHA512`) next to the others. Copy the digest from the publisher's
   checksum file or release API, download once and confirm the digest
   matches, and record the source in the README's toolchain provenance
   table. Never hand-type a hash.
4. **Install it in section 3b**, in the layer it belongs to, with
   `sha256sum -c -` before use. Leave it root-owned and not writable by the
   job (`chmod -R a+rX,go-w`). For a PyPI tool, add
   `requirements/<tool>.txt` generated with hashes:

   ```bash
   echo '<tool>==<version>' > /tmp/<tool>.in
   uv pip compile --generate-hashes --python-version 3.12 \
     --python-platform x86_64-manylinux_2_39 --no-header \
     /tmp/<tool>.in -o deploy/ci-runner-desktop/requirements/<tool>.txt
   ```

   and add `<tool>` to the venv loop.
5. **Wire the environment.** If jobs need a variable or a `PATH` entry, add
   it to both the image `ENV` and `runner-env` (the test checks they agree).
   For a new `setup-*` version, add it to the tool cache with its
   `x64.complete` marker.
6. **Assert it** in `toolchain-check.sh` (version, and a real use if it is
   cheap), add its pin key to the `toolchain-versions` block, and run
   `pytest omega_prime/tests/ci_runners/test_desktop_toolchain.py`.
7. **Build locally** (`podman build -f deploy/ci-runner-desktop/Dockerfile .`
   from the repo root, or `docker build`). The build only succeeds when the
   self-check passes as uid 1500. Then update the table above, the README and
   `CHANGELOG.md`.

## Capacity and cost

The Railway Desktop has 24 vCPU / 24 GB. Each runner is budgeted
2 vCPU / 2.5 GB, and 2 vCPU / 3 GB is reserved for the desktop and agent, so
the default cap is 8 (hard ceiling 10). Railway usage pricing:

| Situation | Estimate |
|---|---|
| Full pool (8 busy runners) | ≈ $0.71 / hour |
| 3 h/day of full-pool CI | ≈ $65 / month |
| 6 h/day of full-pool CI | ≈ $130 / month |
| Idle but awake (e.g. a static outbound IP prevents sleep) | ≈ $22 / month extra |
| Asleep | ≈ $0 compute (volume storage only) |
| Controller on the existing VPS | $0 incremental |

## Failure handling

| Symptom | Behaviour / action |
|---|---|
| Desktop asleep | First launch POST wakes it; retried within 150 s. |
| Wake budget exhausted | The controller asks the agent inventory: present → launched, confirmed absent → rolled back, unknown → kept (never deleted blindly). Look for `omega.ci.runner.error`. |
| Agent full (429) | Capacity hold: the job stays queued and a retry deadline re-evaluates after 240 s, or sooner when a runner is released. Not counted as a failure. |
| Launch rejected or ambiguous | Exponential backoff (15 s, 30 s, 60 s, 120 s, capped at 240 s) via a retry deadline; new demand does not bypass it. After 5 consecutive failures the circuit opens (`launch_circuit_open`) until a runner registers or is assigned (`launch_circuit_closed`). |
| Runner exits before taking a job | Counted as a launch failure (`exited_before_job`, with its `exit_code`) and backed off as above, **whatever the exit code**: actions/runner exits 0 on fatal startup errors. A runner that did run a job but whose `in_progress` webhook was lost is corrected by the job's `completed` webhook, which resets the failure count. See [Troubleshooting](#troubleshooting). |
| Job completed, runner never exits | The runner moves to FINISHING with a grace deadline, then is reaped from the agent. A failed agent stop keeps it FINISHING and re-arms; a failed GitHub delete keeps it IDLE and re-arms; a runner found busy stays BUSY. |
| Controller restart | Each job saved in `OMEGA_CI_DEMAND_PATH` is first checked once against GitHub (`GET /repos/{repo}/actions/jobs/{id}`, needs **Actions: Read**): only jobs GitHub still reports as queued are replayed. Jobs taken, finished or deleted meanwhile are dropped, so they cannot keep idle runners (and the Desktop) awake. A job that cannot be checked is dropped too and reported as `omega.ci.runner.error` `demand_unverified`; re-run it if it stays queued. Rows saved by a controller older than 00054 have no `fork` field; when only that unknown fork status keeps one out of an `owner/*` entry (for example right after switching `OMEGA_CI_REPOS` from exact names to `swcstudiospace/*`), the controller first reads `repository.fork` from the job's workflow run (`GET /repos/{repo}/actions/runs/{run_id}`, **Actions: Read**). A repository confirmed not to be a fork is replayed as usual (public ones still go through the fork guard); a confirmed fork is dropped (`dropped_fork_repo`). If the flag stays unknown (lookup error, 404, missing field) the job is kept in the demand file but never launched, reported as `retained_fork_unknown` in the `demand` event and as `omega.ci.runner.error` `demand_fork_unknown`; a redelivery, the next restart or the job finishing clears it (the 24 h limit still applies). The `demand` event with `reconciled: true` gives the counts. Then comes the one-shot reconcile of GitHub runners and agent inventory (a partial snapshot never reaps). Agent exit callbacks that arrive before startup finishes are held (up to 64) and applied once the runners are restored, so a runner that exits mid-startup is never restored as idle. |
| Launch retry after the runner already exited | The agent answers the retried launch with `finished` + exit code (the single-use JIT is never rerun); the controller treats it as that exit and frees the slot. |
| Agent missing settings | Agent exits with a one-line error; the desktop still starts. |
| Emergency stop | `POST /v1/drain` on the agent (no new runners); revert `runs-on` (see rollback). |

## Troubleshooting

### Every runner exits within a second: "Permission to read the directory contents is required"

**Symptom** (smoke run 39999949179, 2026-10-24): the job stays queued; the
agent log shows each JIT runner printing
`An error occurred: Permission to read the directory contents is required for
'/var/lib/omega-ci/work/omega-ci-…' and each directory up the hierarchy` and
`Runner listener exit with terminated error, stop the service, no retry
needed`, then exiting **0**. Before 00055 the controller treated exit 0 as a
clean exit, relaunched immediately and looped (285 launches, each leaving an
offline `omega-ci-<job>-*` registration in the runner group).

**Cause:** actions/runner checks that its user can *read* (not only search)
its directory and every directory above it. The image made
`/var/lib/omega-ci` and `/var/lib/omega-ci/work` 0711, and the agent's
`prepare()` re-applied 0711, so the non-root job identity could not list them.

**Fix (00055):** both directories are root-owned 0755 in the image and the
agent re-applies 0755 at startup; it refuses to start (`UnsafeWorkdir`, in the
Railway log) if the work root or any parent is not listable. Per-runner
workdirs stay 0700 and owned by the job identity, and the hook and policy
files stay root-owned. The image build runs the real `run.sh` as the job
identity and fails on this error. On the controller, any exit before a job
backs off (15 s doubling to a 240 s cap) and after 5 in a row the circuit opens
(`omega.ci.runner.launch_circuit_open`): no more launches until a pool runner
takes a job or the controller restarts.

**Where to look:** the agent now relays each runner's stdout/stderr
(`runner <name>: …`) and, on a pre-job exit, the tail of its `_diag` log
(`runner <name> diag: …`) to the Railway service log, bounded and redacted.
Controller-side, look for `exited_before_job`, `launch_backoff` and
`launch_circuit_open` in `OMEGA_CI_EVENTS_PATH`.

**Cleanup after a loop:** delete the offline `omega-ci-<job>-*` registrations
(org → Settings → Actions → Runners, or
`DELETE /orgs/{org}/actions/runners/{id}`) and remove the dead job from
`OMEGA_CI_DEMAND_PATH` while the controller is stopped.

## Offline replay

The whole path (signed webhook → scaler → JIT → agent → real fake `run.sh`
process → cleanup → exit callback → reap) can be rehearsed without network,
Docker, GitHub or Railway. The tests use injected fake transports and a
harmless fake runner executable:

```bash
python -m pytest omega_prime/tests/ci_runners/test_app.py omega_prime/tests/ci_runners/test_controller.py omega_prime/tests/ci_runners/test_agent.py omega_prime/tests/ci_runners/test_supervisor.py omega_prime/tests/ci_runners/test_desktop_entrypoint.py omega_prime/tests/ci_runners/test_desktop_image.py -q
```

The full package suite is `python -m pytest omega_prime/tests/ci_runners -q`.
This is a rehearsal only; it is not live validation.

## Verification

Every "built" or "passing" claim on this page and in PR #87 rests on a
receipt that records each command, its exit code and the tail of its output:

| Receipt | Covers |
|---|---|
| `.receipts/bot-00-omega-prime/00050-pr87-greptile-r3.json` | Current head: exit callbacks held during startup (the new test fails on the pre-fix controller) and the deterministic agent replay tests, plus ruff, mypy, polyglot-check, the package suite, coverage, the docs checks and the full suite. |
| `.receipts/bot-00-omega-prime/00050-pr87-greptile-r2.json` | Round 2. The Desktop image smoke check failing on the pre-fix COPY set (`ModuleNotFoundError: omega_prime.credentials.broker`) and passing on the new one; restart-replay, finished-launch and token-failure tests; ruff, ruff format, mypy and polyglot-check; the `ci_runners` package suite; package coverage (gate 90%); the docs and env-inventory checks; the full suite. |
| `.receipts/bot-00-omega-prime/00050-pr87-greptile-r1.json` | Round-1 fixes: the new tests failing on the pre-fix source, then the same lint, type, suite, coverage and docs checks, plus a full-suite comparison against the base commit. |
| `.receipts/bot-00-omega-prime/00050-phase-120-plans.json` | Phase-120 plan checks only. |

Each receipt's `unverified` list names what was not exercised: no live
GitHub, Railway, Pulsar or VPS run, and no `docker build` of the Desktop
image (the smoke check runs the image's own `smoke.sh` against the exact
COPY set instead).

## End-to-end smoke test

`.github/workflows/omega-ci-smoke.yml` is the live end-to-end check of the
pool. It is manual only (`on: workflow_dispatch`, no schedule and no
pull-request trigger), has `permissions: contents: read`, and runs one job on
`[self-hosted, linux, x64, omega-ci]` with `timeout-minutes: 10`. Its steps
are `uname -a`, `whoami` and `echo omega-ci ok`: no checkout, no secrets.
`.github/actionlint.yaml` declares the `omega-ci` label so
actionlint accepts the custom `runs-on`, and
`omega_prime/tests/ci_runners/test_smoke_workflow.py` keeps the workflow this
small.

Run it from the Actions tab ("omega-ci-smoke" → Run workflow) or with
`gh workflow run omega-ci-smoke.yml --repo swcstudiospace/Omega-Prime`. A
green run proves the whole path: the `workflow_job` webhook reaches the
controller, the controller mints a JIT runner and wakes the Railway Desktop,
the runner-side pre-job guard admits the job (`workflow_dispatch` in an
allowlisted repository), and the job runs as `omega-ci-job` on a runner named
`omega-ci-…`. Expect `omega.ci.runner.demand → launch_requested → launched →
busy → exited` in the controller's events. To smoke-test another repository,
copy the file there; with the org-wide setup no allowlist change is needed.

## Held operations (D-120-10)

These stay with the operator and are never run by automation: creating the
GitHub App and runner group, installing the systemd unit, adding the proxy
site (Caddy or nginx), building or deploying the Railway image, adding domains or variables,
and any paid change. Templates: `deploy/ci-runner-controller/` (VPS) and
`deploy/ci-runner-desktop/` (Railway).

## Deployment lessons (2026-10-24)

The first real deploy hit these. Each is now in the relevant how-to.

- **Railway built the wrong Dockerfile.** With the config-as-code path set,
  Railway still built the repository's root `Dockerfile` (the MCP server),
  which failed with `cannot read secret file /run/secrets/omega_prime_token`.
  Fix: leave **Root Directory blank** (the build context is the repo root) and
  set the service variable
  `RAILWAY_DOCKERFILE_PATH=deploy/ci-runner-desktop/Dockerfile`. A correct
  build log shows the `accetto/ubuntu-vnc-xfce-g3` base and the actions-runner
  download.
- **`OMEGA_CI_CONTROLLER_URL` is required.** The agent refuses to start
  without it, and the 8787 domain then answers 502. Set it to the planned
  controller URL at deploy time.
- **`PASSWORD` needs 16+ characters**, or the upstream desktop fails its
  health check.
- **The VPS runs nginx, not Caddy, and 8788 is taken.** The controller uses
  `--port 8798` behind an nginx site
  (`deploy/ci-runner-controller/nginx-site.conf.example`).
- **A client secret is not the App private key.** The `.pem` is generated in
  the "Private keys" section at the bottom of the App's General page.

## Source audit

No formal phase-120 requirement IDs exist in the requirements document, and
none are invented. There is no research document; the phase design document is
the technical evidence.

| Source item | Implemented by | Documented by |
|---|---|---|
| Phase goal: webhook → scaler → JIT → ephemeral runner → events, plus repo guide | 120-01 Tasks 1–3; 120-02 Tasks 1–4; 120-03 Tasks 1–3 | 120-04 Tasks 1–4 |
| Formal requirement IDs | none exist (gap recorded) | 120-04 Task 4 |
| Research evidence | none; design document is authoritative | 120-04 Tasks 1, 4 |
| D-120-01 event-driven scaler / JIT / ephemeral | 120-01 Task 1; 120-02 Tasks 1, 3 | 120-04 Tasks 1, 2 |
| D-120-02 VPS controller, credential-free Desktop | 120-02 Tasks 1, 3; 120-03 Tasks 1–3 | 120-04 Tasks 1–4 |
| D-120-03 stdlib core, lazy crypto/uvicorn | 120-01 Task 1; 120-02 Tasks 1, 4; 120-03 Tasks 1, 3 | 120-04 Tasks 1–3 |
| D-120-04 labels / group / capacity / grace | 120-01 Tasks 1–2; 120-02 Tasks 2–3; 120-03 Task 2 | 120-04 Tasks 2–4 |
| D-120-05 wake / sleep | 120-02 Task 3; 120-03 Tasks 1–3 | 120-04 Tasks 1–2 |
| D-120-06 one-shot delayed event, no timers | 120-01 Tasks 2–3; 120-02 Task 3 | 120-04 Tasks 1–4 |
| D-120-07 publisher and redaction | 120-01 Task 3; 120-02 Tasks 1, 3 | 120-04 Tasks 1–2 |
| D-120-08 Docker work stays on VPS | 120-01 Task 1; 120-02 Task 2; 120-03 Task 3 | 120-04 Tasks 2, 4 |
| D-120-09 App primary, PAT file fallback | 120-02 Tasks 1–2 | 120-04 Tasks 1–2 |
| D-120-10 paid cutover and VPS install held | 120-02 Task 4; 120-03 Task 3 | 120-04 Tasks 1–4 |
| n1 webhook / HMAC / dedupe / pure scaler | 120-01 Task 1; 120-02 Task 1 | 120-04 Tasks 1, 4 |
| n2 App / PAT / JIT / rate limits | 120-02 Tasks 1–2 | 120-04 Tasks 1–2 |
| n3 Railway wake / sleep / static IP / PASSWORD / cutover | 120-02 Task 3; 120-03 Task 3 | 120-04 Tasks 1–3 |
| n4 agent API / supervisor / image | 120-03 Tasks 1–3 | 120-04 Tasks 1–2 |
| n5 capacity / grace / reconcile | 120-01 Tasks 2–3; 120-02 Task 3 | 120-04 Tasks 2, 4 |
| n6 publisher / delayed events | 120-01 Task 3; 120-02 Tasks 1, 3 | 120-04 Tasks 1–2 |
| n7 guide / workflow / templates / rollout / cost | 120-04 Tasks 1–4 | 120-04 Tasks 1–4 |
