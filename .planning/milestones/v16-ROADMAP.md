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
- 🔵 **v16 Enterprise Hardening and Continuous Verification** — Phases 79–84 (started 2026-10-13)

Archives: `milestones/v1-ROADMAP.md` through `milestones/v15-ROADMAP.md`
with phase directories under `milestones/v1-phases/` through
`milestones/v15-phases/`.

## Phases

<details>
<summary>✅ v15 Integrated Readiness and Repository Delivery (Phases 75–78) — SHIPPED 2026-10-12</summary>

- [x] Phase 75: Integrated local readiness (11/11 plans) — completed 2026-10-10
- [x] Phase 76: Clean CI/CD (5/5 plans) — completed 2026-10-11
- [x] Phase 77: Maintained docs and knowledge base (7/7 plans) — completed 2026-10-12
- [ ] Phase 77.1: Grok Bot import packaging (INSERTED) (8/8 plans) — executed; live import UAT deferred by the user (`/gsd:verify-work 77.1`)
- [x] Phase 78: Repository marketing (2/2 plans) — completed 2026-10-12

</details>

## v16 Enterprise Hardening and Continuous Verification

Seven evidence-grounded updates staged from the 2026-10-13 ultrathink run (Linear SPE-10591..10598, graph `ut-mv5xhkf8-8abb3c15`): per-update plans at `.planning/updates/plan-01.md`..`plan-07.md`, scoring and rejected candidates at `.planning/updates/scoring.md`, candidate catalog at `.planning/candidates.md`, evidence log at `.planning/recon-evidence.md`, sidecar gap table at `.planning/sidecar-audit.md`, autonomous-mode decision log at `.planning/decisions.md`. User decisions 2026-10-13: the purple-team sidecar ships BLOCKED (no live engagement until a signed rules-of-engagement artifact exists) and v16 executes all seven updates. Phase-to-update mapping: Phase 79 → U1, Phase 80 → U2, Phase 81 → U3, Phase 82 → U4+U7, Phase 83 → U5, Phase 84 → U6.

### Phase 79: Purple-team sidecar hardening

**Goal:** Make the pinned T3MP3ST red-team sidecar safe under constant upstream upgrades and gate every engagement behind a signed rules-of-engagement artifact, shipping fail-closed (BLOCKED) until the user signs.
**Requirements**: SIDE-01, SIDE-02, SIDE-03
**Depends on:** Phase 78
**Success Criteria:**
1. Pin freshness is automated: Dependabot `gitsubmodule` PRs; CI verifies the gitlink ≡ `VENDOR.md` ≡ `omega_prime/redteam/__init__.py` provenance; the vendored `omega_prime/redteam/pack/` is diffed against upstream at the pin; upstream `redteam/src/mcp-server.ts` tool surface is diffed against `REDTEAM_TOOL_NAMES`; a Node-dependency supply-chain audit job covers the redteam backend (CI-only submodule init).
2. The engagement harness refuses to start (nonzero BLOCKED exit) without a present, unexpired rules-of-engagement artifact; a kill switch halts a run immediately; every IDLE/RUNNING/DEGRADED/BLOCKED/ERROR transition is logged with timestamps. Per decision log 2026-10-13 the sidecar SHIPS BLOCKED: no live engagement until the user signs the artifact.
3. Simulation cases are versioned data (prompt-injection, jailbreak, abuse, malformed-input, auth-bypass, cost-abuse classes) tagged with technique/severity/expected behavior; a pass requires observed detection matching the expected detection; findings land in a deduplicated structured pipeline mapped to tracked work items; harness/bot version drift reports DEGRADED, never a silent pass.

**Plans:** 4/3 plans complete (1 wave-1, 2 wave-2), executed and verified.
- [x] 79-01 — Sidecar harness core: state machine, RoE fail-closed BLOCKED startup, kill switch (wave1)
- [x] 79-02 — Simulation case library, detection validation, findings pipeline, drift→DEGRADED (wave2)
- [x] 79-03 — Pin-freshness automation: provenance checker, Dependabot, CI jobs, bump runbook (wave2)

### Phase 80: Protected release pipeline

**Goal:** Activate the dead-closed image publication path behind a protected environment and add a staged rollout with a one-step rollback.
**Requirements**: REL-01, REL-02
**Depends on:** Phase 79
**Success Criteria:**
1. The `grokbot-image-publish` protected GitHub environment exists (required reviewers); a manual `workflow_dispatch publish:true` run publishes end-to-end with checksum/image-id binding and retained evidence; publication stays manual and default-off.
2. A rollback path (redeploy of the previous digest) is documented and exercised once against a non-production target.
3. Staged rollout produces `deploy.py check()` staging-first promotion evidence (approval/digest) before any promotion step.

**Plans:** 2/2 plans complete (sequential waves), executed and verified.
- [x] 80-01 — Protected environment payload + publish-gate digest/confirm binding (wave1)
- [x] 80-02 — Staged rollout wiring, staging-first evidence drill, local rollback drill + runbook (wave2)

### Phase 81: Telemetry export and SLO alerting

**Goal:** Ship opt-in telemetry export and define SLOs with alerting so regressions surface without weakening the zero-dependency default.
**Requirements**: OBS-01, OBS-02
**Depends on:** Phase 80
**Success Criteria:**
1. Optional OTLP export ships as an extra dependency, off by default; the existing zero-dep `/metrics` surface and its 401/token-leak guarantees are unchanged.
2. SLOs are defined for serve availability, tool-call p99 latency and denial-rate budget, with alert rules; a nightly scheduled readiness-profile run alerts on regression.
3. Export failure degrades gracefully: local metrics/logging/audit continue uninterrupted.

**Plans:** 2/2 plans complete (sequential waves), executed and verified.
- [x] 81-01 — telemetry.otlp config, optional otel extra, lazy exporter, serve wiring (wave1)
- [x] 81-02 — slo.json + loader, deterministic alert generator, nightly readiness workflow + docs (wave2)

### Phase 82: Token rotation and usage quotas

**Goal:** Automate host-token rotation with overlap windows and introduce soft-then-hard per-caller usage quotas.
**Requirements**: TOK-01, TOK-02
**Depends on:** Phase 81
**Success Criteria:**
1. Rotation mints a replacement token with a configurable overlap window, is schedulable via the existing cron heartbeat, writes rotation events into the audit hash chain, and honors live reload; the old token stops working at window end.
2. Quota enforcement is soft-then-hard per caller identity: report-only first (enforces nothing), then warnings, then hard caps; usage reporting is visible through `/metrics` series.
3. Revocation, expiry and rotation behaviors keep `FileTokenStore` fail-closed semantics.

**Plans:** 2/2 plans complete (sequential waves), executed and verified.
- [x] 82-01 — Token rotation: lineage store, rotate subcommand, overlap window, audit events, metrics (wave1)
- [x] 82-02 — Usage quotas: report/soft/hard ladder, token-identity caps, rollup CLI, docs (wave2)

### Phase 83: Sandboxed execution verification

**Goal:** Generate the OpenShell sandbox policy from the seat policy and run verification/execute paths inside sandboxes with receipts bound to executed commands.
**Requirements**: SAND-01, SAND-02
**Depends on:** Phase 82
**Success Criteria:**
1. The sandbox policy is generated from the seat policy (the hand-maintained `omega_prime/hosting/openshell/sandbox-policy.yaml` becomes generated and tested), with writable paths matching Omega home/root defaults.
2. Verification-gate paths run inside OpenShell sandboxes where available (execute-path coverage is the deferred SEED-012 follow-up per 83-CONTEXT.md); quality receipts bind to the recorded executed command and exit status rather than model-written claims.
3. When a sandbox is unavailable the outcome is an explicitly degraded receipt, never a silent pass.

**Plans:** 2/2 plans complete (sequential waves), executed and verified.
- [x] 83-01 — policy_gen renderer, cutover, snapshot golden, sandbox config, CI drift job (wave1)
- [x] 83-02 — fail-closed runner, gates-in-sandbox routing, argv/exit/policy-digest receipt binding, degraded mode (wave2)

### Phase 84: Live evals and regression tracking

**Goal:** Add scored eval cases with persistent trend history and a nightly schedule, keeping the deterministic suite as the push/PR gate.
**Requirements**: EVAL-01, EVAL-02
**Depends on:** Phase 83
**Success Criteria:**
1. Scored rubric cases exist alongside the deterministic runner, with persistent per-case results history showing trends across runs.
2. A nightly scheduled eval workflow runs the suite and surfaces regressions; the deterministic eval step remains the blocking push/PR gate.
3. Any live-model judge is strictly behind an explicit flag and makes no network calls when the flag is unset.

**Plans:** 1/1 plans complete (3 tasks), executed and verified.
- [x] 84-01 — scored rubric cases + history/trend/baseline; flag-gated judge; nightly-evals workflow
