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
- **v20 Wiring** — Phases 97–103 (in progress; Phase 100 complete; later phases remain open)

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

Wiring phases 1–7 map to GSD phases 97–103. This milestone registration does
not verify or re-execute earlier wiring work. The operator explicitly
authorizes Phase 100 to proceed in parallel without Phase 97–99 verification
or merge gates.

- [ ] Phase 97: Substrate direct connect + settings loader + no time caps
- [ ] Phase 98: Shared memory via substrate, Hindsight fallback
- [ ] Phase 99: Solo mode, Desk optional
- [x] Phase 100: omp engine wiring — six reviewed plans (6/6) — completed 2026-10-20; exact 27-test `prime-agent` baseline only
- [ ] Phase 101: Real U2 LocalExecutor
- [ ] Phase 102: Schedulers, dead modules, env docs
- [ ] Phase 103: Ultrathink integration and cleanup

### Phase 100: omp engine wiring

**Goal:** Resolve uplift gaps G4/G5: attach Omega Prime MCP tools and real
skills to the omp engine session, and bridge rpc-ui asks to Ove live through
clarify without stale replies or user-config damage. Preserve the existing
outer turn timeout and add no new session/turn caps.

**Depends on:** Phase 99 (wiring phase 3) in the original milestone
structure, but the operator authorizes this Phase 100 run to proceed without
Phase 97–99 verification or merge gates. Phase 97 owns changes to the existing
turn timeout; preserve it here and add no new caps.

**Requirements** (local planning labels, not tracker identifiers):

- **ENG-100-01 — MCP discovery:** Place generated MCP configuration where
  omp actually reads it; resolve the server command without
  `CURSOR_PLUGIN_ROOT`. Emit `attached` or `attach_failed`; attachment
  failure is reported while the session continues without Omega tools.
- **ENG-100-02 — Ownership and cleanup:** Merge safely or back off on existing
  project configuration. Restore the prior work-tree `.omp/mcp.json` state;
  remove only run-owned artifacts, including on error/cancellation. Never
  mutate global `~/.omp/agent/mcp.json` or copy/print the shared login store.
  Generated configuration contains no secret values.
- **ENG-100-03 — Skills:** Expose real GSD and Omega skills through omp's
  discovery paths; `--skills` is a name glob, never a directory path.
- **ENG-100-04 — Live HITL:** In rpc-ui, receive `extension_ui_request`,
  route ask/select/confirm to Ove through clarify, validate answers against
  each request's schema, and emit `extension_ui_response` with its id.
  Live clarify is the decided default, not policy-file answers.
- **ENG-100-05 — Request lifecycle:** Track job/session/request identity;
  answer once, reject stale/duplicate answers, expose `ui_pending`,
  `ui_answered`, and `ui_rejected_stale`. No separate bridge deadline is
  added; the existing outer turn timeout may end a pending ask until Phase 97
  merges. Cancellation/abort remains usable.
- **ENG-100-06 — Compatibility and safety:** Accept `max`, confirmed by
  observed installed omp 18.12.3 (`18.x`) `--help`; `--help` lists `max`.
  No model session is required to establish support. Preserve shared login and
  model-role behavior. No new packages or caps, live-config changes,
  deployment, or LocalExecutor work. No policy-file auto-approval of sends,
  merges, or deploys.
- **ENG-100-07 — Verification and docs:** Document that `/skill:` needs
  rpc or rpc-ui and ask needs rpc-ui. Prove discovered tools, loaded skills,
  request lifecycle, and config restoration; run exact CI gates and name
  any independently established pre-existing baseline failures.

**Success criteria** (implementation acceptance, not achieved by planning):

1. A session tool-list test (`get_state` or fake omp) contains Omega Prime tools.
2. A test proves real GSD and Omega skills load, with `--skills` a glob, not a path.
3. A fake rpc-ui session answers an ask once, rejects a stale answer, and
   completes without hanging; validation, duplicate, and cancellation cases
   preserve the request lifecycle.
4. Work-tree `.omp/mcp.json` returns to its prior state after a run, with
   existing user configuration and shared login untouched.
5. Docs state mode requirements. Ruff check, Ruff format check, mypy, and
   the complete Omega Prime test suite are green or baseline-only, with
   prompt assembly checked if prompts change.

**Plans:** Six reviewed execution plans; all six executed and verified. Phase 100 is complete. The v20 milestone remains in progress.

- [x] 100-00 — Installed-contract evidence and prerequisite decisions (wave 1)
- [x] 100-01 — MCP attachment and ownership-aware cleanup (wave 2)
- [x] 100-03 — UI protocol and clarification lifecycle service (wave 2, parallel with 01)
- [x] 100-02 — Real skills discovery and name-glob selection (wave 3)
- [x] 100-04 — Production rpc-ui/HITL routing and operator controls (wave 4)
- [x] 100-05 — Docs, integrated verification, and execution receipt (wave 5)

**Out of scope:** Phase 101 LocalExecutor; the external agentOS runner
(note only); Phase 97 time-cap removal; deployments and connector restarts.
