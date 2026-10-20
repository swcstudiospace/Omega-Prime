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
- **v20 Wiring** — Phases 97–103 (in progress)
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

<details>
<summary>v20 Wiring (Phases 97–103) — IN PROGRESS</summary>

Stage models/PR45 and the seven wiring phases are planning context only; this
milestone makes no claim that any phase is implemented. Requirements use local
traceability IDs (not tracker IDs). Phases 97–102 remain unplanned; phase 103
has five plans and no implementation is complete.

- [ ] Phase 97: Substrate direct connect + settings loader + no time caps (0 plans)
- [ ] Phase 98: Shared memory via substrate, Hindsight fallback (0 plans)
- [ ] Phase 99: Solo mode, Desk optional (0 plans)
- [ ] Phase 100: omp engine wiring (0 plans)
- [ ] Phase 101: Real U2 LocalExecutor (0 plans)
- [ ] Phase 102: Schedulers, dead modules, env docs (0 plans)
- [ ] Phase 103: Ultrathink integration and cleanup (5 plans; planning only)

**Goal:** Integrate the independently authored Ultrathink bridge without
vendoring its upstream implementation, preserving safe default-off ship
behavior, independently gated merge approval, explicit operator diagnostics,
and optional native test coverage.

**Requirements:**
- **WIRE-07-01:** `ULTRATHINK_SHIP` enables shipping only when its value is
  exactly `1` or `true`; unset, `0`, and every other value default off.
  When off, `ult_ship_pr`, `ult_ship_review`, and `ult_ship_merge` are blocked
  with a reason and fix hint; assess remains allowed. Merge retains its
  independent approval gate, and no `autoMerge` behavior changes.
- **WIRE-07-02:** Doctor checks executable `bin/ultrathink`,
  `bin/ultrathink-mcp`, and `bin/ultrathink-ship`; reports configured root,
  ship flag, and plan state directory; and distinguishes `not_configured`,
  `root_invalid`, `ready_ship_off`, and `ready_ship_on`.
- **WIRE-07-03:** Document `ULTRATHINK_ROOT`, `ULTRATHINK_SHIP`, and
  `ULTRATHINK_STATE_DIR` environment variables and the existing plan-discovery
  precedence. The Phase 102 environment checker remains unresolved until the
  actual checker is evidenced from that predecessor; do not invent one.
- **WIRE-07-04:** OpenShell tests locate an existing sandbox build binary
  through an explicit test environment variable or `PATH`, then probe the
  selected binary. They report a clear skip only when no binary is available;
  they do not build OpenShell. Native-extension tests remain explicitly
  optional and state their skip reasons.
- **WIRE-07-05:** Keep the stage models/PR45 context and all seven wiring
  phases traceable without representing planned work as implemented.

**Depends on:** Phase 102.

**Success Criteria:**
1. With a fake Ultrathink root, `ult_status` remains usable while shipping is
   disabled. Separately, setup/doctor reports configured root, effective ship
   state, preferred plan-discovery directory, and the required distinct
   readiness states for invalid and absent roots.
2. With `ULTRATHINK_SHIP` unset or `0`, ship PR/review/merge mutations are
   refused with a reason and fix hint, while assess is allowed. Only `1` or
   `true` enables shipping; merge still requires independent approval and no
   auto-merge behavior changes.
3. Doctor and environment documentation/checker coverage verifies the three
   required executables and documented environment names.
4. OpenShell test coverage locates and probes an existing binary through the
   explicit test environment or `PATH`; if no binary is available, it emits
   a clear skip. It does not build OpenShell. Optional native-extension tests
   report why they were skipped.
5. Gates are green, or any remaining failure is identified by name as a
   baseline failure.

</details>
