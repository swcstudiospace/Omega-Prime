# Roadmap: Omega Prime

## Milestones

- ✅ **v1 One Omega Prime agent** — Phases 1-12 (shipped 2026-10-03)
- ✅ **v2 Enterprise hardening** — Phases 13-16 (shipped 2026-10-03)
- ✅ **v3 Grok Bot** — Phases 17-20 (shipped 2026-10-03)
- ✅ **v4 Third-party integrations** — Phases 21-25 (shipped 2026-10-03)
- ✅ **v5 Desk packs** — Phases 26-33 (shipped 2026-10-03)
- ✅ **v6 Grok ship** — Phases 34-38 (shipped 2026-10-03)
- ✅ **v7 Substrate surface** — Phases 39-43 (shipped 2026-10-03)
- ✅ **v8 Public launch** — Phases 44-45 (shipped 2026-10-03)
- ✅ **v9 SOTA upgrade** — Phases 46-52 (shipped 2026-10-08)
- ✅ **v10 Prime merge** — Phases 53-61 (shipped 2026-10-09; audit passed)
- ✅ **v11 Grok Bot native** — Phases 62-63 (shipped 2026-10-09; audit passed)
- ✅ **v12 Programming Desk merge** — Phases 64-67 (shipped 2026-10-09; audit passed)
- ✅ **v13 Grok Bot specialisation** — Phases 68-70 (shipped 2026-10-09; audit passed)
- ✅ **v14 Boundary alignment** — Phases 71-74 (shipped 2026-10-10; audit passed)
- ✅ **v15 Integrated Readiness and Repository Delivery** — Phases 75–78 (shipped 2026-10-12; audit 21/22, GROK-03 live import UAT deferred by the user)
- ✅ **v16 Enterprise Hardening and Continuous Verification** — Phases 79–84 (shipped 2026-10-13; audit PASS 13/13, sidecar ships BLOCKED pending signed RoE)
- ✅ **v17 XML Prompt Standard** — Phases 85–88 (shipped 2026-10-14; every first-party prompt wrapped in XML tags like Programming Desk, enforced by a gate)
- ✅ **v18 Operator posture** - Phases 89-95 (shipped 2026-10-14). Seven upgrades accepted when the operator said to continue.
- ✅ **v19 Installer bindings** - Phase 96 (shipped 2026-10-15). A fresh install binds its own Linear and Notion, treats the existing gateways as optional, and fails setup when `prime-agent` is empty.
- **v20 Wiring** — Phases 97–103 (in progress 2026-10-21). Staged from the 2026-10-21 read-only wiring audit (`/workspace/omes-bot-wiring-audit/AUDIT.md`): explicit substrate configuration with a safe settings loader, streamable-HTTP/SSE transport and argument/lease/memory contracts, shared memory, solo mode, engine wiring with no time caps, a real local executor, schedulers and dead modules, and Ultrathink integration.
- 🔵 **v21 Task Dashboard** — Phase 104 (started 2026-10-21)

Archives: `milestones/v1-ROADMAP.md` through `milestones/v16-ROADMAP.md`
with phase directories under `milestones/v1-phases/` through
`milestones/v16-phases/`.

## Phases

<details>
<summary>✅ v15 Integrated Readiness and Repository Delivery (Phases 75–78) — SHIPPED 2026-10-12</summary>

- [x] Phase 75: Integrated local readiness (11/11 plans) — completed 2026-10-10
- [x] Phase 76: Clean CI/CD (5/5 plans) — completed 2026-10-11
- [x] Phase 77: Maintained docs and knowledge base (7/7 plans) — completed 2026-10-12
- [ ] Phase 77.1: Grok Bot import packaging (INSERTED) (8/8 plans) — executed; live import UAT deferred by the user (`/gsd:verify-work 77.1`)
- [x] Phase 78: Repository marketing (2/2 plans) — completed 2026-10-12

</details>

<details>
<summary>✅ v16 Enterprise Hardening and Continuous Verification (Phases 79–84) — SHIPPED 2026-10-13</summary>

Seven evidence-grounded updates staged from the 2026-10-13 ultrathink run (Linear SPE-10591..10598, graph `ut-mv5xhkf8-8abb3c15`); full archive in `milestones/v16-ROADMAP.md` with phase directories under `milestones/v16-phases/`.

- [x] Phase 79: Purple-team sidecar hardening (3/3 plans) — completed 2026-10-13; sidecar ships BLOCKED pending signed RoE
- [x] Phase 80: Protected release pipeline (2/2 plans) — completed 2026-10-13; `grokbot-image-publish` environment live
- [x] Phase 81: Telemetry export and SLO alerting (2/2 plans) — completed 2026-10-13
- [x] Phase 82: Token rotation and usage quotas (2/2 plans) — completed 2026-10-13
- [x] Phase 83: Sandboxed execution verification (2/2 plans) — completed 2026-10-13
- [x] Phase 84: Live evals and regression tracking (1/1 plan) — completed 2026-10-13

</details>

<details>
<summary>✅ v17 XML Prompt Standard (Phases 85–88) — SHIPPED 2026-10-14</summary>

Every first-party prompt in the application is wrapped in XML tags like
Programming Desk (`prompts/*.xml` + `_shared/core-directives.xml` there; the
same seat-prompt shape here), and a CI gate keeps it that way. Requirements
PROM-01..PROM-08 in `milestones/v17-REQUIREMENTS.md`.

- [x] Phase 85: XML prompt standard and gate (1/1 plan) — completed 2026-10-14
- [x] Phase 86: Runtime-built prompts in XML (1/1 plan) — completed 2026-10-14
- [x] Phase 87: Static prompt documents in XML (1/1 plan) — completed 2026-10-14
- [x] Phase 88: Grok Bot import readiness re-proof (1/1 plan) — completed 2026-10-14

</details>

<details>
<summary>✅ v18 Operator posture (Phases 89-95) - shipped 2026-10-14</summary>

Accepted set from STATE.md after the operator said to continue. Requirements: milestones/v18-REQUIREMENTS.md.

- [x] Phase 89: Purple-cycle schedule (1/1 plan) - completed 2026-10-14
- [x] Phase 90: Cycle and sync backlog on durable_status (1/1 plan) - completed 2026-10-14
- [x] Phase 91: Grant conflicts slash command (1/1 plan) - completed 2026-10-14
- [x] Phase 92: Findings JSONL export (1/1 plan) - completed 2026-10-14
- [x] Phase 93: Purple-cycle lock and deadline (1/1 plan) - completed 2026-10-14
- [x] Phase 94: Readiness slash command (1/1 plan) - completed 2026-10-14
- [x] Phase 95: Audit-chain slash command (1/1 plan) - completed 2026-10-14

</details>

<details>
<summary>✅ v19 Installer bindings (Phase 96) - shipped 2026-10-15</summary>

Requirements: milestones/v19-REQUIREMENTS.md. Phase 77.1 live UAT stays user-owned.

- [x] Phase 96: Per-install Linear, Notion, and optional gateways (1/1 plan) - completed 2026-10-15

</details>

### v20 Wiring (Phases 97–103) — in progress 2026-10-21

Source evidence: `/workspace/omes-bot-wiring-audit/AUDIT.md` (read-only audit of this tree at `44b28df`). Phase 97 has offline implementation and test changes; schema/path preflight tasks in plans 97-02/97-03 remain blocked because current upstream evidence was unavailable. Phase 97 and all other v20 phases remain incomplete.

**Phase 97 goal:** Connect the Omega Prime connector to Agent Substrate safely and without turn-duration caps: explicit configuration instead of a silent localhost default, streamable-HTTP/SSE transport, exact tool argument/lease/memory contracts, a guarded settings loader, honest diagnostics, and uncapped engine/lane behavior. Prove behavior against fake servers locally, with no live network dependency.

**Requirements:**
- **W97-01 transport:** MCP streamable POST, SSE `data:` parsing, and session ID continuity.
- **W97-02 arguments/leases/memory:** exact docs-search, graph lease, memory-write version/idempotency, and memory-search repository arguments; server schema remains unverified pending execution preflight.
- **W97-03 explicit configuration/auth/URL:** unset substrate is `not_configured` with no localhost request; preserve ready binding precedence; process `SUBSTRATE_TOKEN` override precedes `SUBSTRATE_TOKEN_GROK_BOT`; warn when anonymous; validate HTTPS URLs except explicit literal-local opt-in; redact credentials.
- **W97-04 safe settings:** fixed `~/.omega-prime/settings.json`, override `OMEGA_PRIME_SETTINGS_FILE`, exact five-key allowlist, env precedence, and safe rejection of malformed, secret-shaped, tracked, worktree-internal, or group/world-writable settings files.
- **W97-05 diagnostics/degradation:** actionable failure when substrate is unset and visible tool errors rather than silent degradation.
- **W97-06 uncapped engine/liveness/cancel:** no turn/session deadline or env timeout override; use request/socket liveness detection and explicit cancellation while preserving ordinary bounded I/O waits.
- **W97-07 uncapped lanes:** `max_minutes` remains unset unless explicitly configured; preserve local routing and resource caps; persist local unset as JSON `null` without changing fleet serialization.
- **W97-08 docs/local verification:** fake-server and contract tests, execution-preflight schema confirmation, settings/auth/local-opt-in/no-cap documentation, and parent-run gates.

**Success criteria:**
1. Fake SSE MCP server verifies POST headers, `text/event-stream` data-frame parsing, and session ID continuity.
2. Tool-call tests verify `question`, end-to-end `lease_id`, `expected_version`, UUID `idempotency_key`, and optional `repo`; server-specific assertions are pinned only after schema preflight.
3. Unset configuration makes no request to `127.0.0.1:7410`, while doctor/setup fails with an actionable configuration hint.
4. Settings tests cover missing/loaded/env-precedence/secret-name/malformed/tracked/worktree-internal/loose-permission cases; rejected or missing settings leave startup degraded but operational.
5. An eligible 600-minute spec remains local without a max-minutes spill reason. Separately, an unspecified local maximum persists as `max_minutes: null`; supplied duration metadata and fleet omission semantics remain unchanged.
6. A turn longer than 120 seconds completes; dead-process liveness and explicit cancellation are detected without an inference deadline.
7. Parent-run gates pass and same-phase docs cover settings, precedence, allowlist, local opt-in, uncapped semantics, and degradation.

- [ ] Phase 97: Substrate direct connect + settings loader + no time caps (local implementation; upstream MCP schema/path verification deferred)
- [ ] Phase 98: Shared memory via substrate, Hindsight fallback
- [ ] Phase 99: Solo mode, Desk optional
- [ ] Phase 100: omp engine wiring
- [ ] Phase 101: Real U2 LocalExecutor
- [ ] Phase 102: Schedulers, dead modules, env docs
- [ ] Phase 103: Ultrathink integration and cleanup

## v21 Task Dashboard

One phase staged from the 2026-10-21 task-dashboard uplift (operator-approved
01:51 AEDT). The full uplift spec is
`.planning/phases/104-task-dashboard/104-UPLIFT.xml`. Locked decisions: the
dashboard lives in the omes-bot package `omega_prime/dashboard/` on
`feat/task-dashboard`; the fleet collector is implemented but OFF by default
(enabled only via a non-secret setting); the omega-job `final_text` first line
is shown redacted with a 120-character cap; the dashboard runs in a Herdr
workspace "dashboard" on 127.0.0.1:7421 and the operator starts it. This
session plans only. U7 covers docs/dashboard.md + scripts/dashboard-start.sh +
tests; the live box run, the PR and the merge yes are operator steps.

### Phase 104: Task Dashboard

**Goal:** A local, read-only view of what Omega Prime is actually working on: a new `omega_prime/dashboard/` package with one collector per live work source on the box, a normaliser joining them into one Task model with redaction at every boundary, a starlette app exposing a GET-only JSON API plus an SSE stream, and one static HTML/CSS/JS page (no build step) with four sections — Active work, PR / Greptile / CI gate, Waiting on Ove, Recently finished — bound to 127.0.0.1 only, plus the `~/.omega-prime/dashboard/tasks.json` writer helper, tests, docs and a start script.
**Requirements**: TD-01, TD-02, TD-03, TD-04, TD-05, TD-06, TD-07, TD-08
**Success Criteria:**
1. (a1/TD-01) `ss -ltn` shows the dashboard only on `127.0.0.1:<port>`; `--host 0.0.0.0` or any non-loopback host exits non-zero with a clear message.
2. (a2/TD-02) With the box as observed, `/api/snapshot` lists the Herdr pane w2:p1 "swarm-smoke", the live wiring phases p1/p2/p3/p4/p6/p7 with branch and stage, the omp-sandbox e2e run with its two podman containers, and recent omega jobs, each with started and last-activity times.
3. (a3/TD-03) The PR gate lists open omes-bot PRs with CI and Greptile columns, and later shows `fix/wiring-*` PRs once they open, without a restart.
4. (a4/TD-04) Writing a sample `tasks.json` via the writer helper makes its tasks, `waiting_on_ove` and `finished` items appear in the page within 5s with no reload.
5. (a5/TD-05) The seeded-secret test passes: no fake token appears in any response; `rg` over the served HTML/JS finds no innerHTML with data.
6. (a6/TD-06) Killing herdr, stopping podman, or removing the PAT turns that source amber or red in the health strip while the other sections keep updating.
7. (a7/TD-07) Every non-GET method returns 405, and the Host-header guard rejects a foreign Host.
8. (a8/TD-08) ruff, mypy and pytest are green or baseline-only (recorded baseline: `test_desk_wiring`, `test_grokbot_oneclick`, `test_omega_commands` doctor tests, `test_prime_kernel_*`); all new dashboard tests pass. Greptile 5/5 + green CI + the operator's merge yes follow the phase.

**Plans:** 7 plans

Plans:
- [x] Plan 104-01 (wave 1): U4 — Task model, joins, redaction wrapper, atomic tasks.json writer + model/redaction tests (tracer) — completed 2026-10-21
- [x] Plan 104-02 (wave 2, after 104-01): U1 — file collectors (omp_sessions, wiring, sandbox_files, omega_jobs, command_log, tasks_json, agentos) — completed 2026-10-21
- [x] Plan 104-03 (wave 2, after 104-01): U2 — process collectors (procs, herdr, podman) — completed 2026-10-21
- [x] Plan 104-04 (wave 2, after 104-01): U3 — GitHub and opt-in fleet collectors — completed 2026-10-21
- [x] Plan 104-05 (wave 2, after 104-01): U5 — starlette app, GET-only API + SSE, poller, CLI, loopback guard — completed 2026-10-21
- [x] Plan 104-06 (wave 3, after 104-05): U6 — static four-section UI with health strip and SSE/polling fallback — completed 2026-10-21
- [x] Plan 104-07 (wave 4, after 104-02, 104-03, 104-04, 104-06): U7 — integration tests, docs/dashboard.md, scripts/dashboard-start.sh (live run and PR are operator steps) — completed 2026-10-21

Phase 104 implemented on `feat/task-dashboard` (commits beeae56, d5505cd,
c20a0ee): dashboard tests green, ruff/mypy/format green, full suite at the
recorded baseline. The live box run (a1-a7 observation), rebase, push, PR,
Greptile 5/5 and the merge yes remain operator steps.
