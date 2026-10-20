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
- **v20 Wiring** — Phases 97–103 (in progress). Seven wiring phases; Phase 99 planning is current. Implementation remains pending.

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

## v20 Wiring (in progress)

Wiring phases 1–7 map to GSD phases 97–103. They are built in parallel in
separate worktrees; integration/rebase and merge order remains 97 through 103.
Phase 99 implementation is recorded here without marking Phases 97 or 98 shipped.

- [ ] Phase 97: Substrate direct connect + settings loader + no time caps
- [ ] Phase 98: Shared memory via substrate, Hindsight fallback
- [x] Phase 99: Solo mode, Desk optional — implemented locally; rebase integration pending
- [ ] Phase 100: omp engine wiring
- [ ] Phase 101: Real U2 LocalExecutor
- [ ] Phase 102: Schedulers, dead modules, env docs
- [ ] Phase 103: Ultrathink integration and cleanup

### Phase 99: Solo mode, Desk optional

**Goal:** Omega Prime runs as a solo bot without requiring the Desk. Preserve
explicitly configured Desk planes as opt-in compatibility paths; do not delete them.

**Planned dependency:** Phase 98 and Phase 97's substrate docs-search wire contract
(`question`). The operator authorized parallel phase implementation on the
current checkout; this phase deliberately leaves `SubstrateClient.docs_search`
unchanged. Rebase and integrate with Phase 97 before merging this phase.
**Requirements:** G3 and G11-desk-vars from the supplied uplift (audit gap labels,
not tracker identifiers).

1. Resolve `OMEGA_PRIME_SOLO` once during runtime construction: default on for
   bot-00; `=0` restores configured Desk planes. Do not change other seats' defaults.
2. Route solo `lead_docs_search` through the shared substrate client and
   `docs_search(question=...)`; route events through substrate emit. Unsupported
   bus operations report `not_configured: solo` with an actionable hint.
3. Use Desk bus operations only with solo off and explicit, permitted Desk
   configuration. Remove the literal `127.0.0.1` bus fallback, including blocked
   binding construction. Doctor reports solo state and disabled Desk planes.
4. Make desk-gateway skill loading on-demand in source prompts/core directives;
   regenerate assembled prompts with the existing assembly tooling.
5. Replace programming-desk skill-document dependencies with verified
   substrate-hosted or existing in-repo references. If neither exists, remove
   the link with a note; fix the dead `docs/quality-gates.md` reference.
6. Document all five Desk environment variables as optional/legacy, plus solo
   flag semantics. Preserve the separate child/delegation behavior unless a
   Desk-plane dependency requires a scoped change.

**Success criteria:**

1. No code path connects to a literal `127.0.0.1` bus URL.
2. Deterministic tests prove solo docs search and events use a fake substrate,
   and solo off with Desk configuration uses the Desk planes. A substrate
   outage reports unreachable clearly without a localhost fallback.
3. The assembled bot-00 prompt no longer loads desk-gateway always.
4. Skill link validation finds no dead in-repo references; Desk environment
   variables are documented as optional.
5. Exact repository gates are green or only named, evidenced baseline failures:
   `ruff check omega_prime/`; `ruff format --check omega_prime/`;
   `mypy omega_prime/`; `pytest omega_prime/tests -q`; and, when prompts change,
   `bash omega_prime/scripts/assemble-prompts.sh --check`.

**Out of scope:** Deleting Desk code; the separate user-desk-lead connector; the
full environment table generator (Phase 102); connector restarts, deployment,
live configuration, pushes, PRs, or merges. No new packages or session/turn time
caps.

**Plans and execution:**

- [x] [99-01 — Solo routing and regression proof](phases/99-solo-mode-desk-optional/99-01-PLAN.md)
- [x] [99-02 — On-demand gateway prompt](phases/99-solo-mode-desk-optional/99-02-PLAN.md)
- [x] [99-03 — Repository-grounded skill links](phases/99-solo-mode-desk-optional/99-03-PLAN.md)
- [x] [99-04 — Optional Desk docs and link validation](phases/99-solo-mode-desk-optional/99-04-PLAN.md)

Phase execution summary: [99-SUMMARY.md](phases/99-solo-mode-desk-optional/99-SUMMARY.md).
Verification: [99-VERIFICATION.md](phases/99-solo-mode-desk-optional/99-VERIFICATION.md).
