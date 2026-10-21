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

### v21 Sandbox wiring (Phases 110-113) — in progress 2026-10-21

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

**Phase 111 goal:** Surface every blocked push, approval or plan gate in `waiting_on_ove` the moment it happens (task 00030 node n5; audit C10: a blocked push sat ~10 h with no item). One helper in `omega_prime/dashboard` writes the item through `locked_tasks(path, write=True)` + `_write_locked` with a stable id (repeats update, never duplicate), sets the owning task to `blocked` on raise and back to `working` on clear, and passes `ask`/`blocks` text through the secret redaction. The engine's plan/tier gates (`PlanNotAllowed`, sandbox/tier unavailable) and a push-step hook (blocked push, gh not logged in, unanswered approval) call it; execution is one plan (111-01). This is U2 in node n7's build order (U1 was Phase 110).

**Requirements:**
- **W111-01 store primitives:** `omega_prime/dashboard/status_file.py` with `locked_tasks(path, write=False)` (exclusive sidecar lock while `write=True`, yields the parsed tasks.json) and `_write_locked(data, path)` (atomic write-back) implementing the box contract: task states `queued, working, blocked, waiting_on_ove, done, failed`; `waiting_on_ove` items `{id, seq, ask, blocks, since}`; a missing file starts as `{"tasks": [], "waiting_on_ove": []}`.
- **W111-02 raise/clear helper:** `omega_prime/dashboard/blocks.py` with `raise_block(path, item_id, ask, blocks, task_id)` and `clear_block(path, item_id, task_id)`: upsert by stable `item_id` (one item per id, `seq` allocated once, `since` pinned at first raise), owning task `blocked` on raise / back to `working` on clear (a `done`/`failed` row is not resurrected).
- **W111-03 redaction:** `ask` and `blocks` pass through `redact_text` (`omega_prime/credentials/redact.py`) before writing; no token-shaped or credentialed-argv text lands in a row.
- **W111-04 engine failure wiring:** `omp_driver.run`'s failure path (reason `plan_not_allowed` or `sandbox_unavailable` — the Python face of `PlanNotAllowed` and of the sandbox tier's TS `TierUnavailable` gate) raises one block item in the same call that returns the failed `EngineResult` when the caller passed task context; no item for other reasons; without task context, behaviour is byte-identical to today.
- **W111-05 push-step hook + tests:** a push-step hook (`push_block(path, task_id, reason, detail="")` with reasons `auto_review_blocked`, `gh_not_logged_in`, `approval_unanswered`, cleared via `clear_block`) any caller can call; tests with a temp tasks.json cover: blocked push -> exactly one item in one call, repeat idempotent, `PlanNotAllowed` -> item + task blocked immediately, clear -> item gone and task working, a secret-looking string never lands in the row.

**Success criteria:**
1. A simulated blocked push creates exactly one `waiting_on_ove` item within one call; a repeat updates it (same id, same `seq`, same `since`) instead of duplicating.
2. An engine run failing with `PlanNotAllowed` writes its item and sets the owning task `blocked` in the same call (no poll delay); the `EngineResult` shape is unchanged.
3. `clear_block` removes the item and returns the task to `working`.
4. A secret-looking string passed into `ask`/`blocks`/`detail` never appears verbatim in tasks.json (dashboard redaction check).
5. No existing test weakened, skipped or deleted; tests use a temp tasks.json and fakes only (no gh, herdr, podman, network, or model calls).

- [x] Phase 111: Immediate block surfacing in waiting_on_ove

**Phase 112 goal:** Add the sbx-3 engine backend (task 00030 node n3; audit C4 + C7): a new `omega_prime/engine/sandbox_omp.py` — a small stdio client for the omp-sandbox Anda wrapper (`omp-sandbox-anda client`: newline JSON `{id, tool, args}` -> `{id, ok, result|error, code}` plus `{event: question|fallback|stage_done, stageId}` events; tools `up`, `run_gsd_stage`, `sandbox_status`, `answer_question`, `abort`, `run_check`, `export_commits`, `down`) — selected only by `OMEGA_PRIME_OMP_BACKEND=omp-sandbox` with bwrap kept as the unchanged default, mounting the job worktree only from the allowlisted workspace roots, mapping every result into the existing `EngineResult` contract (sandbox label `omp-sandbox`, model ids, exported commits), and passing stage plus requested plan through `delegate_via_engine`/`ultrathink_engine`. Execution is three file-disjoint plans: 112-01 (`omp-sandbox/` folded-ops test coverage), 112-02 (the backend under `omega_prime/engine/`), 112-03 (`omega_prime/tests/` with a fake Anda wrapper).

**Requirements:**
- **W112-01 folded-ops coverage (omp-sandbox/):** `npx tsc --noEmit` stays at 0 errors (verified green at 52ce785) and `npm test` gains coverage of the folded standalone ops against the fake omp/fake tier: `seed_repo` (git-bundle seed), `run_check` (CHECK_BINS allowlist + receipt fields), `export_commits` (bundle out plus the `no seeded repo` refusal), `py_deps` staging, and `runStage` stage/plan/model routing (`stageRoute` with a zai primary; message `/{gsdCommand} {MAGIC}\n\n{task}`).
- **W112-02 backend selection + Anda client:** `omega_prime/engine/sandbox_omp.py` speaks the wrapper stdio protocol and is selected only when `OMEGA_PRIME_OMP_BACKEND=omp-sandbox`; bwrap remains the default and is byte-unchanged when not selected; the job worktree must resolve under the allowlisted workspace roots (`OMP_SANDBOX_WORKSPACE_ROOTS`) with an engine-side pre-check before anything starts, and the wrapper's `path_refused`/tier failures map to `sandbox_unavailable`.
- **W112-03 stage + requested plan pass-through:** with the backend selected, `run` still routes through `plan_router.select`, so `stage=plan` with Z.ai requested/selected yields `zai/glm-5.3` and `stage=execute` yields `zai/glm-5.3-flash`; `delegate_via_engine` and `ultrathink_engine` accept a `plan` argument threaded through to `run`, and the chosen plan/model reach `run_gsd_stage`.
- **W112-04 one result contract:** the backend maps `stage_done`/`sandbox_status`/`export_commits` into the existing `EngineResult` (ok, final text from the stage's `lastText`, `stage`, `sandbox: "omp-sandbox"`, plan/provider/model ids, exported commits recorded without changing the dataclass shape); `PlanNotAllowed` and wrapper/tier unavailability surface as today's `plan_not_allowed`/`sandbox_unavailable` reasons so `run()`'s phase-111 wiring raises the one `waiting_on_ove` block; `probe()` reports the backend's readiness honestly.
- **W112-05 fake-Anda tests:** `omega_prime/tests/` gains a fake Anda wrapper script (no podman, no Rust build, no model calls) and tests for: plan->execute ordering on one job, abort mid-stage, tier unavailable, plan not allowed, results landing back in the job worktree (export applied), no secret in receipts/events, and bwrap behaviour unchanged when the env is unset.

**Success criteria:**
1. With `OMEGA_PRIME_OMP_BACKEND=omp-sandbox`, an engine `run` drives a plan stage then an execute stage through the (fake) Anda wrapper on the chosen models, and the returned `EngineResult` reports `sandbox="omp-sandbox"`, the stage, the model ids, and the exported commits (node n3 acceptance).
2. With the env unset, every existing engine test passes unchanged and `run` still wraps omp in bwrap.
3. `PlanNotAllowed` and tier/wrapper-unavailable runs return the existing reasons and raise exactly one `waiting_on_ove` item each when task context is present (phase-111 helper, unchanged).
4. A worktree outside the allowlisted roots is refused before any sandbox starts.
5. `cd omp-sandbox && npx tsc --noEmit` exits 0 and `npm test` is green including the new folded-ops tests; `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/<new file>` is green; no existing test weakened, skipped or deleted; fakes only (no podman, openshell, herdr, gh, network, or model calls).

- [x] Phase 112: omp-sandbox engine backend (sbx-3) — completed 2026-10-21 (audit C4 + C7 → PASS; C3 reachable per-run via engine_plan; default ChatGPT-first stage routing unchanged)

**Phase 113 goal:** Make sandbox runs visible and steerable (task 00030 node n4; audit C8 + C9, and C10's question half): routine- and tool-launched engine runs through the sbx-3 backend open one Herdr pane per run (labelled "{task} {stage}", live event stream; tmux fallback when herdr is missing), write one tasks.json row per run through `locked_tasks` + `_write_locked` (state `working` on start, `last_activity_at` on each event, `done`/`failed` with a receipt on finish, `herdr_pane` recorded), and forward GSD follow-up questions to `waiting_on_ove` through the phase-111 helper with the answer piped back via the wrapper's `answer_question` into the same live session. `delegate_task` (`omega_prime/agent/delegate.py`) and the ultrathink_turn routine (`omega_prime/routines/ultrathink_turn.py`) thread task context and the requested plan onto the backend when it is selected; planning only in this stage, execution is three file-disjoint plans (113-01/02/03). This is U4 in node n7's build order (U1 = Phase 110 ✅, U2 = Phase 111 ✅, U3 = Phase 112 ✅).

**Requirements:**
- **W113-01 Herdr pane launcher:** a new `omega_prime/dashboard/herdr_launch.py` runs `herdr workspace create --cwd DIR --label LABEL --no-focus` then `herdr pane run PANE CMD` (CMD streams the run's events), parses the pane id from stdout, and falls back to the engine's tmux pattern when herdr is missing or fails; a missing pane never fails the run. No `omega_prime/dashboard/herdr_launch.py` exists today, so this stage creates it with that CLI contract.
- **W113-02 tasks.json row per run:** a new `omega_prime/dashboard/run_rows.py` writes the run's row through `locked_tasks(path, write=True)` + `_write_locked`: row created/moved to `working` with `herdr_pane` on start, `last_activity_at` stamped on each event, terminal `done`/`failed` with a redacted receipt (job id, stage id, ok, export summary) on finish; unknown fields on other rows pass through untouched.
- **W113-03 question round-trip:** a `question` wrapper event raises exactly one `waiting_on_ove` item through the phase-111 helper (stable id per question, ask redacted, owning row blocked); the run then polls the store, and when the item carries an answer pipes it back with the wrapper's `answer_question` (`stage_id` + `request_id`) into the same live session and clears the item.
- **W113-04 product-caller routing:** `delegate_task` reads the parent's task context and requested plan attributes and `execute_ultrathink` reads them from its env, both threaded through `delegate_via_engine`/`ultrathink_engine` into `run(tasks_path=…, task_id=…, plan=…)`; the backend wires those into the pane label, the row and the question items; bwrap default behaviour and entry-point defaults are unchanged.
- **W113-05 fake-visible tests:** the fake Anda wrapper's question behavior waits for `answer_question` (with `requestId`) before `stage_done`, and tests with a temp tasks.json and a fake herdr runner cover: row created working → done/failed with `last_activity_at` advancing, pane opened with the "{task} {stage}" label and recorded on the row, question → one item → answered → `answer_question` called with the right ids → item cleared → run completes, tmux fallback when the fake herdr is absent, no secret-shaped value in rows/receipts, and the phase-112 question-event pin flipped to the n4 contract (stronger assertions, documented cutover).

**Success criteria:**
1. A routine-launched sandbox run shows a Herdr pane and a tasks.json row that moves `working` → `done`/`failed` within one poll of each event (node n4 acceptance).
2. A GSD question appears in `waiting_on_ove` and its answer resumes the run through `answer_question` into the same live session; the item clears on answer.
3. When herdr is missing, tmux and the tasks.json row still show progress; a pane failure never fails the run itself.
4. With `OMEGA_PRIME_OMP_BACKEND` unset, every existing engine test passes unchanged (bwrap default untouched); `delegate_task`/`ultrathink_turn` defaults are unchanged without task context.
5. `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/<files>` green with a temp tasks.json, a fake herdr runner and the fake Anda wrapper only (no herdr, podman, gh, network, or model calls); no existing test weakened, skipped or deleted — the one phase-112 pin that asserted the n4 boundary is updated to assert the n4 contract instead.

- [x] Phase 113: Sandbox runs visible in Herdr and tasks.json
