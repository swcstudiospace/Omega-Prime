# Phase 120 — CI Runner Fleet: autoscaling ephemeral GitHub runners on the serverless Railway Desktop (task 00050)

Status: decided 2026-10-23 12:05 AEDT (checkpoint 1). Authoritative spec for plans 120-01..120-04.

## 1. Problem (evidence, 2026-10-23 11:55 AEDT)
- One org runner serves everything: `actions.runner.swcstudiospace.srv1778002` on the Hostinger VPS (`/opt/actions-runner`, systemd). 5–8 GSD projects (test-per-file) queue behind it.
- "Rate limited": the shared PAT on the box had **4451/5000** core requests used this hour (reset 12:22 AEDT). Every tool (gh, dashboards, listeners, CI helpers) draws from one PAT bucket. The autoscaler must NOT add to it → it authenticates as a **GitHub App installation** (own 5k+/h bucket, scales with repos) and spends ~2 calls per job.
- Railway `Linux Containers` project (`d8ba2d6d-…`), env `production` (`c2cfb1e7-…`), service **`Ubuntu Desktop`** (`86cc34ce-…`), source `XavTo/Railway-Desktop-Pro` (`ubuntu/`, MIT), `sleepApplication: true` (= Railway "Serverless"), 1 replica, domains `ubuntu-desktop-production-be9d.up.railway.app` and `…-cd2c…` both → port 3000 (nginx → noVNC).
- Latest deployment `e84cc9c2` (2026-10-23 02:53 AEDT) **FAILED**: `PASSWORD must contain at least 16 characters` → healthcheck never passed. The service is on the previous successful deployment. Ove must set a ≥16-char `PASSWORD` before the cutover.

## 2. Options evaluated
| Option | Fit | Verdict |
|---|---|---|
| actions-runner-controller (ARC) | Needs Kubernetes + controller + listener pods. Railway gives one container, no k8s, no privileged pods. | Rejected (wrong substrate). |
| `actions/scaleset` Go client (public preview, Feb 2026) | No k8s; long-poll message session; JIT runners. Requires an always-on listener **and** a Go toolchain/binary; long-poll is a held connection, not a webhook event; preview API. | Not chosen now; recorded as the v2 signal source (the scaler core is signal-agnostic, so a scaleset listener can feed the same `Demand` events later). |
| philips-labs / runs-on style (webhook → JIT → ephemeral) | Exactly our shape, but bound to AWS Lambda/EC2. | Pattern adopted, implementation custom. |
| **Custom event-driven autoscaler (chosen)** | `workflow_job` webhook → pure scaler → JIT config → ephemeral runner process on the Desktop; Railway Serverless provides scale-to-zero for free. Python stdlib core, same EventBus subjects as 00046. | **Chosen.** |

## 3. Architecture
```
GitHub org swcstudiospace ──workflow_job webhook (HMAC)──▶ ci-autoscaler (always-on: VPS, behind Caddy TLS)
                                                          │  pure Scaler (event-sourced ledger)
                                                          │  GitHub App → POST /orgs/{org}/actions/runners/generate-jitconfig
                                                          │  publishes omega.ci.runner.* (EventBus; JSONL fallback)
                                                          ▼  HTTPS + bearer (wake: first request may 502 → bounded retry)
                         Railway "Ubuntu Desktop" (Serverless, 24 vCPU / 24 GB, Singapore, static egress IP)
                           runner-agent :8787 (2nd Railway domain) ─ spawns ./run.sh --jitconfig <cfg> per job
                           each runner = 1 job then exit (ephemeral, auto-deregistered by GitHub)
                           no runners + no outbound traffic for 5–10 min ⇒ Railway sleeps the container ($0 compute)
```
- **Control plane (`ci-autoscaler`)** runs always-on next to the VPS projects (the VPS already hosts the current runner). It holds every credential (GitHub App key, webhook secret, desktop bearer). Mirrors coding-fleet's coordinator/worker split: the worker side (Desktop) never holds a GitHub credential — it only receives single-use JIT configs.
- **Data plane (`runner-agent`)** runs inside the Desktop image (wrapper entrypoint starts the agent, then execs the upstream desktop entrypoint unchanged). Stdlib-only, bearer-authenticated, capacity-capped.
- **Why not receive webhooks on the Desktop directly?** A sleeping service may 502 the first request and GitHub does not auto-redeliver; GitHub also expects a response within 10 s. The always-on autoscaler ACKs in <50 ms (202) and owns wake retries.

## 4. Event model (no timers — hard rule)
Inputs: `workflow_job.queued | in_progress | completed` (+ `waiting` ignored), `agent.runner_exited`, delayed `runner.idle_deadline`, `controller.started` (one-shot startup reconcile).
- **Match**: a job is ours iff its `labels` ⊆ pool labels (default `self-hosted, linux, x64, omega-ci`) and its repo is in the allowlist (`OMEGA_CI_REPOS`, empty = all org repos in the runner group).
- **Scale up (on `queued`)**: `launch = min(max_runners, queued_unmatched + busy) − (starting + idle + busy)`, clamped ≥0. Each launch = mint JIT config (runner name `omega-ci-<job_id>-<rand4>`) → POST to agent (wake). One runner per queued job, never more than `OMEGA_CI_MAX_RUNNERS`.
- **Assignment (`in_progress`)**: payload `runner_name` marks that runner busy; jobs picked up by other runners (e.g. the VPS runner) free a starting/idle slot.
- **Scale down (`completed`)**: the busy runner exits by itself (ephemeral). If `queued == 0`, every idle/starting runner gets a **delayed event** `runner.idle_deadline` after `OMEGA_CI_IDLE_GRACE_S` (default 180 s) — a one-shot `loop.call_later` handle (or Pulsar `deliverAfter` when the mesh is live), cancelled if the runner becomes busy. On deadline: `DELETE /orgs/{org}/actions/runners/{id}` (only if still idle) + agent `DELETE /v1/runners/{name}` ⇒ process exits.
- **Desktop sleep**: when the agent has zero runners it sends nothing; Railway sleeps it. No stop API call needed; no cron.
- **Idempotency**: `X-GitHub-Delivery` dedupe (bounded LRU 4096); state transitions are monotonic per job id (a late `queued` after `in_progress` is ignored).
- **Restart safety**: on `controller.started`, list org runners with the pool prefix and agent `/v1/runners`, rebuild the ledger, reap orphans (one-shot, event-triggered).

## 5. Capacity policy (24 vCPU / 24 GB)
- Per runner budget: 2 vCPU / 2.5 GB (GSD suites are pytest/vitest heavy, I/O light). Desktop + agent reserve: 2 vCPU / 3 GB.
- **Default `OMEGA_CI_MAX_RUNNERS=8`** (16 vCPU / 20 GB), hard ceiling 10. Agent enforces its own `OMEGA_CI_AGENT_MAX_RUNNERS` independently (defence in depth) and returns 429 when full; controller keeps the job queued and retries on the next `completed` event.
- Each runner: own workdir `/config/ci-runners/<name>` (deleted on exit), `nice 10`, `RLIMIT_NPROC`/`RLIMIT_NOFILE` caps, env scrubbed (no agent token, no desktop PASSWORD).
- Kill switch: `POST /v1/drain` on the agent (no new runners, finish in-flight).

## 6. Railway wake / teardown (concrete)
- Serverless = `sleepApplication: true` on the service instance (already on). It applies at container creation, so the cutover redeploy keeps it.
- **Wake**: any inbound HTTP to a service domain. The autoscaler's first `POST /v1/runners` is the wake; it retries 502/503/connect errors with jittered backoff inside a 150 s wake budget (event-scoped retry, not polling).
- **Teardown**: automatic after 5–10 min without outbound packets once all runners have exited. Runners long-poll GitHub while alive, so the container cannot sleep under a running job (correct behaviour).
- **Static IP caveat (Railway docs)**: "Services with Static Outbound IPs enabled may remain awake even when idle." The static Singapore IP is outbound-only (useful for allowlisting the runners' egress at third parties); it plays no role in webhook ingress. If it keeps the Desktop awake, disable it on this service or accept ~idle cost — Ove decision at cutover.
- **Docker caveat**: Railway containers are unprivileged → no Docker-in-Docker. Jobs that use `container:`, `services:`, or `docker build` must keep targeting the VPS runner (label `vps-docker`).

## 7. EventBus integration (00046)
Subjects (CloudEvents `type`): `omega.ci.runner.demand`, `.launch_requested`, `.launched`, `.busy`, `.idle`, `.reaped`, `.exited`, `.error`, `omega.ci.desktop.wake_requested`, `.woke`. Publisher port with three adapters: `NullPublisher`, `JsonlPublisher` (default, `~/.omega/ci-runners/events.jsonl`), `EventStreamPublisher` (lazy import of `omega_prime.eventstream` once 00046 lands; no hard dependency). Delayed events use the same port (`DelayedEvents`) with asyncio and Pulsar-`deliverAfter` adapters.

## 8. Security
- Secrets only by file path env vars: `OMEGA_CI_WEBHOOK_SECRET_FILE`, `OMEGA_CI_GITHUB_APP_KEY_FILE`, `OMEGA_CI_DESKTOP_TOKEN_FILE` (agent: `OMEGA_CI_AGENT_TOKEN_FILE` or Railway variable `OMEGA_CI_AGENT_TOKEN`). Never logged; repr-redacted types; `omega_prime.credentials.redact` on every log/event payload.
- JIT configs are single-use and never persisted or logged; passed to `run.sh` through the env var `ACTIONS_RUNNER_INPUT_JITCONFIG` (the runner's documented arg-from-env form, as ARC does), never argv (visible in `ps`).
- Webhook: HMAC-SHA256 `X-Hub-Signature-256`, constant-time compare, 1 MiB body cap, event allowlist (`workflow_job`, `ping`).
- Agent API: bearer (constant-time), body cap, bound to `0.0.0.0:8787` only inside Railway; runner names validated `^omega-ci-[a-z0-9-]{1,48}$`.
- Org runner group `omega-ci` restricted to selected repos; public repos excluded (self-hosted runners on public repos are a known RCE vector).

## 9. Cost projection (Railway usage pricing: $20/vCPU-month, $10/GB-month, per minute)
- Busy, 8 runners ≈ 16 vCPU + 20 GB ≈ $0.71/h. Idle-awake desktop ≈ 0.2 vCPU + 1.5 GB ≈ $0.03/h. Asleep ≈ $0 compute (volume storage only).
- 3 h/day full-pool CI ≈ **$65/month**; 6 h/day ≈ **$130/month**; if the static IP prevents sleep, add ≈ **$22/month** idle.
- Autoscaler on the VPS: $0 incremental.

## 10. Config inputs Ove must confirm
1. Repo list for the runner group (proposal: the active swcstudiospace repos built on the VPS — Omega-Prime, substrate, PumpGrok, realm, autonogrammer-ai, swarm, manifolded, ultrathink, coding-fleet …). Any personal `SomeRandmGuyy/*` repos need repo-scope registration (supported: `OMEGA_CI_SCOPE=repo`).
2. GitHub App: create "Omega CI Autoscaler" on swcstudiospace with **Organization → Self-hosted runners: Read & write**, **Repository → Actions: Read**, webhook event **Workflow job**; install on the selected repos; give App ID, installation ID, private key file path.
3. Caps: `MAX_RUNNERS=8`, `IDLE_GRACE_S=180`, label `omega-ci`.
4. Static IP keep / drop on the Desktop; `PASSWORD` ≥16 chars.
5. Where the autoscaler listens: `https://ci.<domain>/github/webhook` on the VPS via Caddy (VPS change → explicit go).
