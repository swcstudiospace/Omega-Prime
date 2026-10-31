# Phase 120 CONTEXT — CI Runner Fleet (task 00050)

Smart-discuss run autonomously (rule 5b). All decisions **auto-accepted** with GSD's recommended option; rationale in DESIGN.md.

| ID | Decision | Status |
|---|---|---|
| D-120-01 | Custom event-driven autoscaler (webhook → pure scaler → JIT → ephemeral process). ARC rejected (needs k8s); actions/scaleset recorded as a future signal source. | auto-accepted |
| D-120-02 | Control plane always-on next to the VPS projects; Desktop is data plane only and never holds GitHub credentials. | auto-accepted |
| D-120-03 | Package `omega_prime/ci_runners/` in Omega-Prime; stdlib-only core; `cryptography` imported lazily for App JWT; optional extra `ci-runners = ["uvicorn>=0.30"]` for serving. | auto-accepted |
| D-120-04 | Default pool labels `self-hosted, linux, x64, omega-ci`; runner group `omega-ci`; max 8 runners; idle grace 180 s. | auto-accepted (Ove to confirm caps) |
| D-120-05 | Railway Serverless sleep is the teardown; wake is the first agent request with a 150 s retry budget. | auto-accepted |
| D-120-06 | Idle reap is a one-shot delayed event (asyncio call_later / Pulsar deliverAfter). No timers, cron, or sleep loops anywhere. | locked (standing rule) |
| D-120-07 | Events published as `omega.ci.runner.*` / `omega.ci.desktop.*` via a publisher port; JSONL default; 00046 EventStream adapter lazy. | auto-accepted |
| D-120-08 | Docker-needing jobs stay on the VPS runner (label `vps-docker`); Railway is unprivileged. | auto-accepted |
| D-120-09 | GitHub App auth (own rate-limit bucket) is primary; PAT file fallback. | auto-accepted |
| D-120-10 | Paid cutover (Railway redeploy of the Desktop with the overlay image, new domain, variables) and VPS install (systemd + Caddy) are HELD for Ove. | locked |

Auto-answered questions: repo scope (org, with repo-scope option), signal (webhook over scaleset long-poll), runtime home of controller (VPS), language (Python, matches Omega-Prime CI).
