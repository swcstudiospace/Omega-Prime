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
| `OMEGA_CI_REPOS` | empty | Comma-separated `owner/repo` allowlist. Empty = every private repo the runner group allows. |
| `OMEGA_CI_PUBLIC_REPOS` | empty | Public repositories explicitly opted in. Each must also be in a non-empty `OMEGA_CI_REPOS`. Empty = every public repo is refused. See [Public repositories](#public-repositories). |
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
| `OMEGA_CI_AGENT_REPOS` | empty | **Required for jobs to run on the image.** Comma-separated `owner/repo` list the runner-side pre-job guard lets run; set it to the controller's `OMEGA_CI_REPOS`. Empty → the guard refuses every job (fails closed). Repository names only, no secret. |
| `OMEGA_CI_AGENT_JOB_HOOK` | unset (image: `/opt/omega-ci/job-started.sh`) | Pre-job guard the agent sets as `ACTIONS_RUNNER_HOOK_JOB_STARTED` for every runner. Must be a regular file the job identity cannot modify, or the agent refuses to start. |
| `OMEGA_CI_AGENT_JOB_POLICY` | `/var/lib/omega-ci/job-policy.json` | Root-owned allowlist file the agent writes at startup from `OMEGA_CI_AGENT_REPOS`; the guard reads it. |
| `OMEGA_CI_CONTROLLER_URL` | — | **Required: the agent will not start without it.** Controller base URL for the exit callback. Set it at deploy time, even before the controller exists: the agent only calls it after a job ends. Without it, the agent exits with `OMEGA_CI_CONTROLLER_URL: required callback base URL`, so nothing listens on 8787 and Railway answers 502 Bad Gateway. |

## Interfaces

Controller (`python -m omega_prime.ci_runners controller --host 127.0.0.1 --port 8788`;
8788 is the default, and the production VPS uses `--port 8798` because 8788 is
taken there):

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
  treat the pool as same-trust: only your own private repositories.
- **Public repositories are refused by default.** Self-hosted runners on
  public repositories are a known remote-code-execution vector. A public
  repository is served only through the explicit opt-in and fork guard below.
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
- **Fork-guard calls.** The fork guard adds one
  `GET /repos/{repo}/actions/runs/{run_id}` per workflow run of an opted-in
  public repository (the verdict is cached for the run's other jobs, and
  concurrent jobs of one run share the lookup). Private repositories make no
  extra call.

## Public repositories

Anyone can open a pull request against a public repository. If that pull
request's workflow ran on these runners, a stranger's code would execute on
your machine, next to other jobs that share the job UID. That is why the
matcher refuses public repositories unless you opt one in, and why opting in
is not enough on its own.

**Opt-in.** List the repository in `OMEGA_CI_PUBLIC_REPOS`. It must also be in
`OMEGA_CI_REPOS`, and `OMEGA_CI_REPOS` must not be empty. The default is empty,
so behaviour for every repository is unchanged until you set it.

**Fork guard.** The `workflow_job` webhook alone cannot say where the code
came from. For each new (`queued`) job from an opted-in public repository the
controller fetches the job's workflow run
(`GET /repos/{repo}/actions/runs/{run_id}`, needs **Actions: Read**) before the
scaler sees the job, and serves it only when all of these hold:

- the run belongs to the same repository the webhook named;
- `head_repository` is that same repository and is not a fork;
- the trigger (`event`) is `push`, `workflow_dispatch` or `pull_request`
  (which, given the check above, means a same-repository branch).
  `pull_request_target` is refused by name, and so is every other trigger
  (`workflow_run`, `issue_comment`, `schedule`, `merge_group` and so on).

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

**Restart during a lookup.** An accepted public job is written to the demand
file *before* its run lookup, so a restart mid-lookup replays it, and the
replay re-runs the fork guard. A refused job is removed from the file.

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
- `pull_request_target`, always;
- any pull request whose head repository is a fork, is another repository,
  or cannot be determined (private repositories included);
- for a repository the event payload does not mark `private: true`, any
  event other than `push`, `workflow_dispatch` or `pull_request`;
- missing context or an unreadable or mismatched event payload.

The allowlist reaches the hook through a root-owned policy file the agent
writes at startup, not through the job's environment, and the hook runs as
`python3 -I` with absolute paths, so the job cannot redirect it. Its rules
and tests are `omega_prime/ci_runners/agent/jobhook.py` and
`test_jobhook.py`. A refused job fails in GitHub with
`omega-ci job guard refused this job (<reason>)`. The runner then exits, and
the controller treats that like any other finished job.

**Defence in depth.** Also set the repository's Actions fork policy to
**Require approval for all outside collaborators** (Settings → Actions →
General → "Approval for running fork pull request workflows from
contributors"). Then a fork pull request cannot start any workflow until a
maintainer approves it. Keep the runner group's repository access limited to
the opted-in repositories, and only turn on its "Allow public repositories"
option for this case.

## Events

CloudEvents with source `omega-prime/ci-runners`:
`omega.ci.runner.{demand,launch_requested,launched,busy,idle,finished,reaped,exited,error,launch_backoff,launch_retry,launch_circuit_open,launch_circuit_closed,refused}`
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
| Controller restart | Each job saved in `OMEGA_CI_DEMAND_PATH` is first checked once against GitHub (`GET /repos/{repo}/actions/jobs/{id}`, needs **Actions: Read**): only jobs GitHub still reports as queued are replayed. Jobs taken, finished or deleted meanwhile are dropped, so they cannot keep idle runners (and the Desktop) awake. A job that cannot be checked is dropped too and reported as `omega.ci.runner.error` `demand_unverified`; re-run it if it stays queued. The `demand` event with `reconciled: true` gives the counts. Then comes the one-shot reconcile of GitHub runners and agent inventory (a partial snapshot never reaps). Agent exit callbacks that arrive before startup finishes are held (up to 64) and applied once the runners are restored, so a runner that exits mid-startup is never restored as idle. |
| Launch retry after the runner already exited | The agent answers the retried launch with `finished` + exit code (the single-use JIT is never rerun); the controller treats it as that exit and frees the slot. |
| Agent missing settings | Agent exits with a one-line error; the desktop still starts. |
| Emergency stop | `POST /v1/drain` on the agent (no new runners); revert `runs-on` (see rollback). |

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
