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
- **v21 Sandbox wiring (task 00030)** — Phase 110+ (in progress 2026-10-21). Finish the omp-sandbox sbx-2 merge follow-ups (tsc clean, all still-applying Greptile #55 comments fixed with regression tests) before sbx-3 engine-backend work starts. Source contract: `/sandbox/home/refs/uplift2.xml` (node n2) and `/sandbox/home/refs/PR55-greptile-comments.md`.

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

### v21 Sandbox wiring (Phase 110) — in progress 2026-10-21

Task 00030 node n2 (`/sandbox/home/refs/uplift2.xml`): the merge of sbx-0's round-1 fixes into `feat/sbx-2-workspace-mount` is committed (3e7be33) but leaves follow-ups, all inside `omp-sandbox/`. Planning only in this stage; execution is one plan (110-01).

**Phase 110 goal:** Make the merged sbx-2 tree typecheck clean and fix every Greptile PR #55 inline comment that still applies at 3e7be33 — each with a regression test — while keeping both parents' behavior (sbx-0 guards and sbx-2 mount variant) intact.

**Requirements:**
- **W110-01 tsc clean:** `openshell-podman.ts` `#otherSandboxes` calls bare `os(...)` where `this.#os(...)` is meant; two implicit-any lambdas at the same site must be gone. `npx tsc --noEmit` in `omp-sandbox/` exits 0.
- **W110-02 merge preservation:** `omp-sandbox/package.json`, `src/core/core.ts`, `src/core/providers/tiers.ts` keep sbx-0's guards (single-flight `up`, `validateTiers`, guards suite in `npm test`) and sbx-2's mount variant (`workspace_host_path`, `MOUNT_TIERS`), re-checked against both parents 40338ec and 3ada70b.
- **W110-03 mounted bwrap sees GSD:** `ensureGsd` (and the same-assumption `gsdCommandsOnHost`) must target `workspaceHostPath` when a workspace is mounted, not the hidden `hostRoot/work` copy.
- **W110-04 podman uid mapping:** the mount variant must not require host uid 1000; a workspace owned by a non-1000 host user stays writable (map the host user to the policy uid or derive the policy user from the host identity).
- **W110-05 path_refused reaches callers:** `server.ts` reply mapping carries `PathRefused.code` ("path_refused") instead of collapsing it to "error".
- **W110-06 probe hygiene:** `vmRoundTrip` must not leave `.agentos-probe.txt` (or any probe artifact) in the mounted host job directory.
- **W110-07 seed streaming:** `seedFiles` must not hold every seed file's bytes in memory; copy one file at a time.
- **W110-08 valid dotdot names:** `allowedDir` must accept strictly-inside names like `..job` while still refusing `..` itself, `../` prefixes, and escapes.

**Success criteria:**
1. `cd omp-sandbox && npx tsc --noEmit` reports 0 errors.
2. `cd omp-sandbox && npm test` is all green, including one new regression test per W110-03..W110-08.
3. W110-02 verified by diffing the three merged paths against 40338ec and 3ada70b: guards and mount variant both present.
4. No test is weakened, skipped, or deleted to make anything pass; all new tests run under Node's built-in runner with fakes only (no podman, openshell, herdr, or network).

- [x] Phase 110: omp-sandbox sbx-2 merge finish (tsc clean + Greptile #55 round 2)
