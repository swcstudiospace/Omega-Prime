# CI runner fleet (autoscaling ephemeral GitHub runners)

Omega Prime can run GitHub Actions jobs on ephemeral, single-job runners that
boot on demand inside a serverless Railway Desktop and disappear afterwards.
A small always-on controller on the VPS receives GitHub's `workflow_job`
webhooks, decides how many runners are needed, mints a single-use JIT runner
config for each one, and asks the Desktop's runner agent to start it. When the
work is done the runners exit, and Railway puts the idle Desktop to sleep.

> **Status: built, not deployed.** Creating the GitHub App, installing the
> VPS service, adding the Caddy site, and redeploying the Railway Desktop are
> all operator steps that are **HELD** (D-120-10). Nothing in this repository
> performs them. Per-repository onboarding is in
> [CI runner repository setup](ci-runners-repo-setup.md).

## How it works

```text
GitHub ──workflow_job webhook (HMAC)──▶ controller (VPS, loopback :8788 behind Caddy TLS)
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
(default `self-hosted, linux, x64, omega-ci`). On `queued`:

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
| `OMEGA_CI_REPOS` | empty | Comma-separated `owner/repo` allowlist. Empty = every private repo the runner group allows. |
| `OMEGA_CI_LABELS` | `self-hosted,linux,x64,omega-ci` | Pool labels. |
| `OMEGA_CI_RUNNER_GROUP` | `omega-ci` | Runner group display name. |
| `OMEGA_CI_RUNNER_GROUP_ID` | — (org), `1` (repo) | Numeric group id; required in org scope and never inferred from the name. |
| `OMEGA_CI_MAX_RUNNERS` | `8` | Concurrent runner cap, 1–10. |
| `OMEGA_CI_IDLE_GRACE_S` | `180` | Idle grace before reaping, 0–3600 s. |
| `OMEGA_CI_WAKE_BUDGET_S` | `150` | Wake retry budget, 1–900 s. |
| `OMEGA_CI_GITHUB_APP_ID` | — | GitHub App id (App mode). |
| `OMEGA_CI_GITHUB_INSTALLATION_ID` | — | App installation id (App mode). |
| `OMEGA_CI_GITHUB_APP_KEY_FILE` | — | Path to the App private key (App mode). |
| `OMEGA_CI_GITHUB_PAT_FILE` | — | Path to a PAT; only when no App setting is present. |
| `OMEGA_CI_WEBHOOK_SECRET_FILE` | — | Path to the webhook HMAC secret. Required. |
| `OMEGA_CI_DESKTOP_URL` | — | `https://` URL of the agent's 8787 domain. Required. |
| `OMEGA_CI_DESKTOP_TOKEN_FILE` | — | Path to the agent bearer token. Required. |
| `OMEGA_CI_PUBLISHER` | `jsonl` | `jsonl`, `null` or `eventstream`. |
| `OMEGA_CI_EVENTS_PATH` | `~/.omega/ci-runners/events.jsonl` | JSONL event log. |
| `OMEGA_CI_DEMAND_PATH` | `~/.omega/ci-runners/demand.json` | Queued-job backlog replayed after a restart (non-secret fields, mode 0600, entries older than 24 h dropped). |

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
| `OMEGA_CI_CONTROLLER_URL` | — | Controller base URL for the exit callback. |

## Interfaces

Controller (`python -m omega_prime.ci_runners controller --host 127.0.0.1 --port 8788`):

| Route | Auth | Result |
|---|---|---|
| `GET /healthz` | none | 200, non-secret status. |
| `POST /github/webhook` | HMAC `X-Hub-Signature-256` | 202 once queued, before any GitHub/Desktop call; 503 when the queue is full (not committed, so GitHub can redeliver). |
| `POST /agent/events` | bearer | 202 for `{type:"agent.runner_exited",name,generation,exit_code}`. |

Agent (`python -m omega_prime.ci_runners agent --host 0.0.0.0 --port 8787`):

| Route | Result |
|---|---|
| `GET /healthz` (no auth) | 200 `{status, draining, runners, capacity}`. |
| `GET /v1/runners` | 200, list of `{name, generation, status, pid}`. |
| `POST /v1/runners` `{name, generation, jitconfig}` | 201 first launch; 200 same name+generation replay; 409 conflicting generation (the running child is kept) or draining; 429 full; 400 malformed; 401 unauthorized; 413 over 1 MiB. |
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
  treat the pool as same-trust: only your own private repositories.
- **Public repositories are excluded** by the matcher; self-hosted runners on
  public repositories are a known remote-code-execution vector.
- **Docker (D-120-08).** Railway is unprivileged: no Docker, DinD or
  privileged mode. Jobs using `container:`, `services:` or `docker build` stay
  on the VPS runner (`vps-docker`).
- **Redaction (D-120-07).** Every event passes through
  `omega_prime.credentials.redact` with the known transient secrets. Anything
  that still contains a secret, or is not plain JSON, is dropped.
- **Rate limits (D-120-09).** As a GitHub App installation, the controller has
  its own request budget, separate from any personal token, and makes about
  two ordinary API calls per job (generate JIT; idle check/delete). The
  installation token is cached and refreshed on demand once it is within five
  minutes of expiry, single-flight and without a daemon. Only real
  rate-limit responses are retried (3 attempts, 60 s budget).

## Events

CloudEvents with source `omega-prime/ci-runners`:
`omega.ci.runner.{demand,launch_requested,launched,busy,idle,finished,reaped,exited,error,launch_backoff,launch_retry,launch_circuit_open,launch_circuit_closed}`
and `omega.ci.desktop.{wake_requested,woke}`. The default publisher appends to
`OMEGA_CI_EVENTS_PATH`; `eventstream` publishes to the event stream once it is
available.

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
| Runner exits before taking a job | Counted as a launch failure (`exited_before_job`) and backed off as above. |
| Job completed, runner never exits | The runner moves to FINISHING with a grace deadline, then is reaped from the agent. A failed agent stop keeps it FINISHING and re-arms; a failed GitHub delete keeps it IDLE and re-arms; a runner found busy stays BUSY. |
| Controller restart | One-shot reconcile of GitHub runners and agent inventory (a partial snapshot never reaps), then queued jobs are replayed from `OMEGA_CI_DEMAND_PATH`. |
| Agent missing settings | Agent exits with a one-line error; the desktop still starts. |
| Emergency stop | `POST /v1/drain` on the agent (no new runners); revert `runs-on` (see rollback). |

## Offline replay

The whole path (signed webhook → scaler → JIT → agent → real fake `run.sh`
process → cleanup → exit callback → reap) can be rehearsed without network,
Docker, GitHub or Railway. The tests use injected fake transports and a
harmless fake runner executable:

```bash
python -m pytest omega_prime/tests/ci_runners/test_app.py omega_prime/tests/ci_runners/test_controller.py omega_prime/tests/ci_runners/test_agent.py omega_prime/tests/ci_runners/test_supervisor.py omega_prime/tests/ci_runners/test_desktop_entrypoint.py -q
```

The full package suite is `python -m pytest omega_prime/tests/ci_runners -q`.
This is a rehearsal only; it is not live validation.

## Verification

The Greptile round-1 fixes on PR #87 are backed by
`.receipts/bot-00-omega-prime/00050-pr87-greptile-r1.json`: the new tests
failing on the pre-fix source, then ruff, mypy, polyglot-check, the package
suite, scaler coverage (99%, gate 90%), the docs and inventory checks, and a
full-suite comparison against the base commit. Its `unverified` list names
what was not exercised (no live GitHub, Railway or VPS run).

## Held operations (D-120-10)

These stay with the operator and are never run by automation: creating the
GitHub App and runner group, installing the systemd unit, adding the Caddy
site, building or deploying the Railway image, adding domains or variables,
and any paid change. Templates: `deploy/ci-runner-controller/` (VPS) and
`deploy/ci-runner-desktop/` (Railway).

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
