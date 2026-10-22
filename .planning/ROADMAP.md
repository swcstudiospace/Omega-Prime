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
- **v20 Wiring** — Phases 97–103 (in progress 2026-10-21). Staged from the 2026-10-21 read-only wiring audit (`/workspace/omes-bot-wiring-audit/AUDIT.md`): explicit substrate configuration with a safe settings loader, streamable-HTTP/SSE transport and argument/lease/memory contracts, shared memory, solo mode, engine wiring with no time caps, a real local executor, schedulers and dead modules, and Ultrathink integration. Phase 98 implementation was executed on its phase-specific branch; phase 97 remains authoritative for transport, endpoint resolution, source-aware search failures, and integration order.

Phase 98 requirements MEM-98-01..MEM-98-10 and four executable plans are in `.planning/phases/98-shared-memory-substrate-hindsight/`. Branch execution and verification are recorded in that phase directory's `SUMMARY.md`.

Phase 99 (solo mode, Desk optional) is implemented on its own branch and integrated onto main after Phases 97 and 98; see `.planning/phases/99-solo-mode-desk-optional/`.

Phase 100 (omp engine wiring) is complete and integrated after Phase 99 (PR #50); see `.planning/phases/100-omp-engine-wiring/`.

Phase 102 (Schedulers, dead modules, env docs) is integrated in wiring order (PR #52); see `.planning/phases/` for its summary.
- **v21 Event Stream** — Phases 104–108 (started 2026-10-23, task 00046). Apache Pulsar event backbone for the Autonomous Event-Driven ADLC; timer routines banned (event-driven only). Runs in parallel with v20; see `phases/104-event-stream-broker-decision-and-railway-spec/DESIGN.md`.

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

### Phase 98: Shared memory via substrate, Hindsight fallback

**Depends on:** Phase 97

**Goal:**

Deliver shared memory through local storage, Substrate, and optional Hindsight.
Recall queries local memory and configured Substrate and Hindsight independently,
then merges results with canonical-content deduplication and complete source
provenance. Local memory remains available when a remote source is missing or
unavailable; source failures remain visible instead of becoming empty results
or silent fallback. Retain is local-first, then writes to configured Substrate
for every selector, and writes to Hindsight only when Hindsight is selected.
Backend selection is a write preference, not a recall opt-out.

**Requirements:** MEM-98-01 through MEM-98-10

- [ ] **MEM-98-01 — Selector and resolution:** validate `local|substrate|hindsight`; default to Substrate only when phase 97 explicitly resolves a configured URL, otherwise local; never probe loopback defaults or silently fall back from explicit missing configuration.
- [ ] **MEM-98-02 — Merged recall and provenance:** independently query local, configured Substrate, and configured Hindsight; deduplicate canonical content while preserving every source, native score, and stable source order; preserve legacy local banks for unscoped recall.
- [ ] **MEM-98-03 — Local-first stable-identity retain:** validate before IO, persist locally first, then publish to every configured Substrate regardless of selector; use one stable operation identity and report remote refusal/failure without rolling back local data.
- [ ] **MEM-98-04 — Selected-only Hindsight writes:** always recall configured Hindsight; write to it only when selected and report failures truthfully without claiming unsupported idempotency.
- [ ] **MEM-98-05 — Truthful backend states:** report `answered`, `empty`, `not_configured`, `unreachable`, or `unauthorized` with sanitized structured diagnostics; never infer outage or authorization from empty results or exception text.
- [ ] **MEM-98-06 — Command and doctor reporting:** both memory commands and the actual lead doctor report all three named backend states, selection, and configuration; retain command failure reflects failure of any required configured remote publication, regardless of selector.
- [ ] **MEM-98-07 — Security, scope, repo, and bounds:** validate before IO, enforce repo isolation across all sources, keep Hindsight HTTPS-only and secrets broker-backed, and redact every key/error path.
- [ ] **MEM-98-08 — Runtime Hindsight and bridge cutover:** compose the real optional Hindsight service; remove the service-first bridge; retain local-only profile, replace, and remove behavior.
- [ ] **MEM-98-09 — Fake-backend tests and runtime-path smoke:** cover source states, merge, security, isolation, and partial failures through real tool/command/doctor paths against loopback fakes, never live services.
- [ ] **MEM-98-10 — Gates, docs, and receipt:** update affected docs/consumers; run the exact gates after integration and record evidence, including `rule adherence by future agents` under receipt `unverified`.

**Success Criteria:**

- [ ] Fake three-source recall merges local, Substrate, and Hindsight hits, deduplicates by canonical content, and preserves all contributing source provenance without fabricating or comparing unlike scores.
- [ ] Retain writes locally first and publishes to configured Substrate regardless of selector, using a stable idempotency key; required remote failure is explicit even when `local_saved` is true.
- [ ] Runtime composes Hindsight when configured, and the real doctor path reports all backend states.
- [ ] Both `/omega-recall` and `/omega-retain` name all three backends and expose truthful states and participation.
- [ ] Required gates are green after integration, or any remaining failures are explicitly identified as named baseline-only failures; no false green is claimed.

Phase 98 implementation was executed on its phase-specific parallel branch under the operator decision. Its selector, merged recall/provenance, local-first retention, Hindsight runtime, doctor/reporting, and docs work is being integrated after phase 97; phase 97's endpoint resolver and source-aware `memory_search` errors remain authoritative. Phase 77.1's human-owned live UAT remains open and unchanged.

- [x] Phase 99: Solo mode, Desk optional — implemented; integrated onto main after Phases 97/98
- [x] Phase 100: omp engine wiring — six reviewed plans (6/6) — completed 2026-10-20; integrated after Phase 99
- [ ] Phase 101: Real U2 LocalExecutor
- [x] Phase 102: Schedulers, dead modules, env docs — executed and verified; integrated in wiring order (PR #52)
- [x] Phase 103: Ultrathink integration and cleanup — executed and verified; integrated in wiring order (PR #46)
Phase 98 plan: [CONTEXT](phases/98-shared-memory-substrate-hindsight/CONTEXT.md),
[research](phases/98-shared-memory-substrate-hindsight/RESEARCH.md), and
executable plans [98-01](phases/98-shared-memory-substrate-hindsight/98-01-PLAN.md),
[98-02](phases/98-shared-memory-substrate-hindsight/98-02-PLAN.md),
[98-03](phases/98-shared-memory-substrate-hindsight/98-03-PLAN.md),
[98-04](phases/98-shared-memory-substrate-hindsight/98-04-PLAN.md).

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

**Integration note:** Phase 100 was planned against the old outer turn timeout. After integration with Phase 97 (W97-06) turns are uncapped; a cancel interrupts the turn and an operator abort gives omp one command timeout to reach `agent_end`.

**Out of scope:** Phase 101 LocalExecutor; the external agentOS runner
(note only); Phase 97 time-cap removal; deployments and connector restarts.

### Phase 102: Schedulers, dead modules, and environment documentation

Goal: close uplift gaps G9–G11 by wiring existing watchdog and pin-bump schedules, routing due prompt jobs through a delegate or blocked receipt, removing unreachable/dead modules, documenting supported environment variables, and reporting wiring status without executing scheduled work. Phase 102 was executed and verified locally and is integrated in wiring order (PR #52).

Requirements: G9-SCHED, G10-EXEC, G10-PLUGINS, G10-X, G10-REACH, G11-ENV, and G9-DOCTOR.

Acceptance criteria:
1. Tests show that the driver ticks watchdog and pin-bump schedules; due prompt work uses a fake delegate parent, or records an explicit blocked receipt when no executable delegate is available.
2. No tools module is unreachable except documented, explicitly flag-gated modules.
3. The committed environment-name/purpose table is AST-derived, and CI fails when a supported environment name is missing from the table.
4. Doctor reports disabled families and scheduled work whose runner was not observed without executing schedules; the exact requested gates are green, or each baseline failure is explicitly named.

Additional safety constraints: exec-job operations remain approval-gated; plugins require exact `OMEGA_PRIME_PLUGINS=1` opt-in and remain off otherwise; `x_tweepy` is removed while urllib X behavior is retained. Preserve approval gates for sends, posts, merges, deploys, and pin-bump mutation. Do not activate live providers or integrations.


### v21 Event Stream (Phases 104–108) — in progress 2026-10-23

Source: Ove's `/gsd-autonomous` prompt (00046), 2026-10-23 AEDT. Standing rule: Omega Prime may not create timer/schedule routines; everything is event-triggered (deadlines are delayed events). Decision and spec: `phases/104-event-stream-broker-decision-and-railway-spec/{CONTEXT,DESIGN,RESEARCH,UPLIFT}.md`.

- [ ] Phase 104: Event stream broker decision + Railway deploy spec
- [ ] Phase 105: Event stream core library (Python + TypeScript)
- [ ] Phase 106: Box hub + VPS leaf live feed
- [ ] Phase 107: ADLC taxonomy wiring + timer retirement
- [ ] Phase 108: Event stream ops + verification

### Phase 104: Event stream broker decision + Railway deploy spec

**Goal:** Decide the broker (Pulsar vs NATS vs other) with verified SDK/Railway evidence and write the enterprise-grade spec: topology, CloudEvents contract, tenancy/topics/schemas, delivery semantics, security, FMEA, test strategy, Railway deploy spec.

**Requirements:** ES-104-01 decision with evidence; ES-104-02 DESIGN.md complete; ES-104-03 topology-as-code draft (`topology.yaml` shape); ES-104-04 HITL list with defaults.

**Success criteria:** CONTEXT.md records the decision and auto-accepted answers; DESIGN.md covers §1–§18; RESEARCH.md verifies every pasted claim.

### Phase 105: Event stream core library (Python + TypeScript)

**Depends on:** Phase 104

**Goal:** `omega_prime/eventstream/` (envelope, taxonomy, schemas, ports, memory + Pulsar adapters, SQLite outbox with broker dedup, consumer runtime with Key_Shared/Failover/Exclusive/Reader, retry topics + DLQ, idempotency ledger, reconnect/backoff/circuit, graceful shutdown, admin REST reconcile, CLI) and `packages/eventstream-ts/` with the same semantics; Pulsar Functions scaffold.

**Success criteria:** Integration tests against pinned `apachepulsar/pulsar:5.0.0` prove no loss across broker restart, dedup of resends, per-key ordering, delayed delivery, retry→DLQ, schema-compat rejection and ACL denial; ruff/format/mypy --strict/pytest/vitest/polyglot-check green.

### Phase 106: Box hub + VPS leaf live feed

**Depends on:** Phase 105

**Goal:** Pulsar standalone on the box (podman, loopback, JWT, persistent volume, topology applied) and the VPS leaf publisher (systemd unit, outbox, box-initiated reverse SSH tunnel) emitting auth/login, job, CI and Hindsight events as a live feed.

**Success criteria:** VPS events reach the box hub within seconds; a tunnel outage loses nothing (outbox drains on reconnect); all sources are push streams (no polling).

### Phase 107: ADLC taxonomy wiring + timer retirement

**Depends on:** Phase 105 (106 for VPS-sourced types)

**Goal:** Wire 00043's handlers and listeners L1–L23 onto the bus; express deadlines as delayed events; retire every timer routine (`omega_prime/cron`, routine schedules, heartbeats, collector loops); Command Center bridge via Reader → `poller.nudge`.

**Success criteria:** Every former timer is event-triggered, on-demand, or removed; Command Center updates from events; gate/blocked/review flows pass end-to-end against the hub.

### Phase 108: Event stream ops + verification

**Depends on:** Phases 106, 107

**Goal:** OTel metrics/traces, structured logs, `/healthz` + `/readyz`, `omega eventstream {status,tail,replay,dlq}`, chaos tests, no-timer CI gate, Railway Dockerfile + cutover runbook + tiered storage config.

**Success criteria:** Chaos suite shows zero loss and zero duplicate effects; no-timer gate in CI; runbook reviewed; Railway artefacts ready for cutover.
