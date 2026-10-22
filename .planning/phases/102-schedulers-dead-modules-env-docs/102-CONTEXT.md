# Phase 102 Context — Schedulers, Dead Modules, Environment Docs

## Scope and authority

This is planning-only work for GSD phase 102, wiring phase 6, milestone v20 Wiring. The eventual execution belongs exclusively in `/workspace/omes-bot-wt/p6` on its current branch. This planning session updates planning roadmap/state artifacts and authors executable plans; it does not implement source, tests, CI, or runtime behavior. The target path is owned by `bot-00-omega-prime` under the last-match `**` rule in `omega_prime/ownership.yaml`.

The requested uplift is limited to the original acceptance areas: **G9 schedulers**, **G10 dead modules**, **G11 environment documentation**. Required end state for execution: live driver ticks watchdog and pin-bump schedules; due prompt work uses the real delegate parent or records a blocked receipt; no tool module is unreachable except documented flag-gated families; an AST-derived committed environment-name/purpose inventory has a CI check that fails for missing names; doctor reports disabled families and unrun schedules; exact gates are green or explicitly report only the unchanged documented baseline failures. Add no scheduled jobs, enable no Prime families, change no X posting behavior, and add no packages. No session/turn caps. Preserve send/post/merge/deploy approval gates; pin-bump mutation requires explicit approval for exact tool identity `pin_bump_apply`, not merely a verdict or semver eligibility.

### Locked choices and constraints

- `exec_jobs` is wired as approval-gated `exec_job_start`, `exec_job_poll`, `exec_job_wait`, and `exec_job_kill`, unless semantic overlap with a **VERIFIED** phase-101 executor is demonstrated. `omega_prime/engine/lanes.py` `StubLocalExecutor` is not that proof. Current static research found no equivalence; phase 101 must be verified on the eventual execution base, and phase97–101 completion must never be inferred from this worktree.
- Plugins load only for exact `OMEGA_PRIME_PLUGINS=1`; absent/other values are off. Keep path containment and do not widen roster or policy automatically. Loader enabled means trusted local Python execution, not isolation or approval.
- Remove `x_tweepy`; retain the existing `urllib` X transport and existing X tools/approval behavior.
- Stale action jobs outside configurable lateness grace are skipped with receipts. Grace and cadence are not execution, session, or turn caps. Do not create a second scheduler owner or allow generic prompt execution to consume specialized kinds.
- Pin-bump authorization is identity-specific: `pin_bump_apply` must be covered by the existing admin action-grant metadata and checked against the same live `ApprovalLog` used by the served tool. Grant metadata is distinct from the served-tool roster; do not widen or conflate the roster to authorize it. Validate grant, expiry, revoke, and unrelated-tool grants; none except a live exact-name grant authorizes mutation.
- Before any later out-of-session connector restart, the operator must confirm stale jobs and their drop disposition. This planning work performs no live inventory, configuration, or restart; that unresolved operator confirmation does not block planning.
- The only host/roster/catalog integration owner is plan 102-03. Disjoint core ownership: 102-01 scheduler and 102-02 dead modules, both wave 1. 102-04 environment generator/docs/CI follows integration. 102-05 doctor and full verification/docs/receipt is last.
- No external paths, audits, workflow/CLI, secrets, credentials, or values may be read. The parent is required to commit on the current branch; workers must not commit. No pushes, PRs, merges, deploys, restarts, or configuration. No package installs. Main agent runs verification; workers edit only.

## Environment-name coverage contract

The inventory must include all relevant concrete names proven by source and explicitly required below, without recording values/defaults. At execution, rediscover current consumers and phase 1–5 names on the verified execution base before committing the generated inventory. Names currently evidenced include `OMEGA_PRIME_DESK_CHILD_MODEL`, `OMEGA_PRIME_DESK_CHILD_PROVIDER`, `OMEGA_PRIME_WORK_ROOT`, `OMEGA_PRIME_DESK_WORK_ROOT`, `OMEGA_PRIME_STATE_DIR`, `OMEGA_PRIME_AUDIT_LOG`, `OMEGA_PRIME_ENGINE`, `OMEGA_PRIME_ENGINE_TMUX`, `OMEGA_PRIME_ENGINE_UNSANDBOXED`, `OMEGA_PRIME_OMP_BIN`, `OMEGA_PRIME_OMP_HOME`, `OMEGA_PRIME_BWRAP_BIN`, `OMEGA_PRIME_APPROVALS_FILE`, `OMEGA_PRIME_APPROVALS_SHA256`, `OMEGA_PRIME_COMMAND_LOG`, `OMEGA_PRIME_RAILWAY_PROJECTS`, `OMEGA_PRIME_SWEEP_MENTIONS`, `FLEET_COORDINATOR_URL`, `FLEET_COORDINATOR_TOKEN`, `FLEET_COORDINATOR_TOKEN_FILE`, `ULTRATHINK_STATE_DIR`, `GROK_HOME`, `GROK_PLUGIN_DATA`, `HERMES_HOME`, `PI_CODING_AGENT_DIR`, and `PRIME_AGENT_CODING_AGENT_DIR`. Also rediscover provider/integration credentials, their genuinely supported `_FILE` forms, finite plan/stage and Prime-family flags, CI-only/provenance inputs, ambient variables, fixture forwarding, and operator-selected token-name selectors; document exclusions and dynamic families explicitly. The supplied list includes future phase 1–5 names, which must be gathered from the eventual source, not guessed here. No secret values are needed or permitted.

## GOT coverage and traceability (not an ISSUES graph)

There is no `<ISSUES>` block; the GOT items below are supplied coverage requirements, not a second uplift. Do not rerun an uplift graph or create trackers. All seven GOT nodes are mapped to the five executable plans:

1. **n1 — read baseline:** inspect current scheduler/storage/import graph/environment collectors and baseline. Plan 01 covers schedule state/owners; 02 inventories module reachability and locked overlap; 03 checks host/roster/catalog integration; 04 derives environment inventory; 05 checks doctor and records verification. The only baseline execution evidence available to planning is parent-reported below.
2. **n2 — split ownership:** U1 schedulers→01; U2 dead modules→02; U3 env→04; U4 doctor→05. Plan 03 is the sole host/roster/catalog integration and shared-test/docs owner. U1/U2 files remain disjoint.
3. **n3 — scheduler failure modes:** isolate per-kind/per-job failures; use real parent dispatch or blocked receipts; preserve cadence configurability without execution limits. Configure stale action handling with explicit grace and receipts. Covered by 01, host wiring by 03, doctor visibility by 05.
4. **n4 — overlap/defaults:** compare executor semantics only on verified phase101 base; retain approval-gated `exec_jobs` without proof; exact opt-in plugins; remove Tweepy while retaining urllib. Covered by 02 and its integration in 03.
5. **n5 — environment docs/CI:** AST-derived names and purpose table, explicit ignore/dynamic policy, generated docs and a CI drift failure. Covered by 04, after added runtime names land in 03.
6. **n6 — risks:** stale-job/restart semantics, dynamic AST false positives/omissions, and approval boundaries are explicit; pin mutation requires operator approval. Covered by 01/02/04 and integrated/checked by 03/05.
7. **n7 — execution completion:** implementation, behavior tests, doctor, exact gates and receipt are owned by plans 01–05; main agent executes verification once per green wave and final gates. Planning here establishes no implementation completion.

### Original GOT numbered substeps → plan/evidence mapping

- **n1.1 — desk/untriggered ticks:** determine whether the live driver invokes scheduler work; planning evidence in research, implementation in 01/03, visibility in 05.
- **n1.2 — `cron_admin`/`SchedulerService`:** trace ownership and callsites; planning evidence in research, scheduler behavior in 01, host integration in 03.
- **n1.3 — import graph post101:** inspect reachability on the execution base; planning evidence in research, dead-module cutover in 02 and integration/reachability proof in 03/05. Phase 97–101 completion is not presumed.
- **n1.4 — env scan:** inspect literal, aliased, helper, and dynamic sources; planning evidence in research, inventory in 04.
- **n1.5 — baseline:** preserve the parent-reported pre-edit baseline and exact failures in this context and research; no gate rerun is needed for this planning task.
- **n2.1 — scheduler unit:** 01.
- **n2.2 — dead-module unit:** 02.
- **n2.3 — env unit:** 04.
- **n2.4 — doctor:** 05.
- **n3.1 — watchdog/pin isolated:** isolate failures by kind/job; 01, with host wiring in 03.
- **n3.2 — parent:** dispatch due prompt work through the real delegate parent; 01, host integration in 03.
- **n3.3 — block:** persist an explicit blocked receipt when real dispatch is unavailable; 01/03.
- **n3.4 — intervals:** keep cadence configurable and separate from stale-job grace; 01/03.
- **n4.1 — executor overlap:** verify semantic overlap only on a verified phase-101 executor; planning evidence in research, locked behavior in 02/03.
- **n4.2 — plugins:** exact `OMEGA_PRIME_PLUGINS=1` opt-in, default off, containment retained; 02/03.
- **n4.3 — Tweepy:** remove `x_tweepy` and active dependency/policy references while retaining urllib transport; 02/03.
- **n4.4 — receipt decisions:** preserve evidence limits and receipt requirements; 05; planning artifacts do not claim execution receipt completion.
- **n5.1 — AST:** derive environment names from source without product imports; 04.
- **n5.2 — table compare:** generate and validate complete name/purpose documentation; 04.
- **n5.3 — CI ignore:** define justified exclusions/dynamic supplements and fail on inventory drift; 04.
- **n5.4 — gate:** specify deterministic read-only validation and final exact gates; 04/05.
- **n6.1 — stale grace:** stale action jobs outside configured grace skip with receipts; 01/03.
- **n6.2 — hidden imports/full tests:** audit transitive/lazy reachability and verify behavior/full gates; planning evidence in research, implementation 02/03, verification 05.
- **n6.3 — dynamic ignores:** document dynamic-name supplements and precise source exclusions; 04.
- **n6.4 — approvals:** retain send/post/merge/deploy gates and require explicit approval for pin mutation; 01/03.
- **n7.1 — sched/tests:** scheduler behavior and tests in 01/03.
- **n7.2 — dead:** dead-module behavior and tests in 02/03.
- **n7.3 — env/CI:** generated inventory, docs, and CI check in 04.
- **n7.4 — doctor/gates/receipt:** doctor and final verification/docs/receipt in 05. No completion is claimed before execution.

## Parent planning-session fallback plan

1. **Understand**
   1. Read the phase scope, constraints, and locked decisions.
   2. Read the four static contract research reports.
   3. Read the parent-reported baseline artifact without rerunning gates.
   4. Inspect the planning precedent and establish the allowed worktree/ownership boundary.
2. **Establish context**
   1. Separate current evidence, requested outcomes, and unresolved execution prerequisites.
   2. Record the execution graph, wave/dependency order, and disjoint file ownership.
   3. Map all GOT substeps to planning evidence or executable plans.
   4. Preserve approval, no-secret, no-external-path, and no-source-implementation limits.
3. **Author executable plans**
   1. Specify scheduler-core plan 102-01 and dead-module-core plan 102-02.
   2. Specify the sole shared host/roster/catalog integration plan 102-03.
   3. Specify environment generator/docs/CI plan 102-04 after runtime names land.
   4. Specify doctor and final verification/docs/receipt plan 102-05 last.
4. **Critique**
   1. Check every plan for bounded behavior, verification, `fails_when`, and `done` criteria.
   2. Check the dependency graph, wave order, ownership disjointness, and no racing edits.
   3. Check locked decisions, approval boundaries, stale-job grace, and evidence qualifiers.
   4. Check all GOT substeps and acceptance requirements for traceability.
5. **Synthesize**
   1. Update context and research to reflect verified planning evidence and unresolved work.
   2. Preserve all 27 full baseline test nodeids and their reported causes.
   3. Keep roadmap/state handling within the authorized planning scope and avoid unsupported CLI claims.
   4. Record tracker/Notion absence and execution receipt requirements without claiming execution completion.


## Baseline reported by parent (pre-edit)

Correct-interpreter baseline: `ruff check omega_prime/` exit 0; `ruff format --check omega_prime/` reported 589 already formatted, exit 0; `mypy omega_prime/` checked 462 files, exit 0; full `pytest omega_prime/tests -q` exit 1 with 4,777 passed, 27 failed, 11 skipped in 234.85s. Exact failures are listed individually in 102-RESEARCH.md. Failures were observed before planning edits with empty `prime-agent`/missing `rlm`/native source. No submodule init or package install was performed. Earlier wrong-interpreter attempts (ruff/mypy 127; pytest 63 collection errors) are discarded and are not the baseline. No prompts changed, therefore prompt assembly check was conditional and not run. The parent owns the required commit on the current branch and any verification decisions.

## Delivery metadata

No tracker IDs provided. No Notion URLs supplied. Planning artifacts do not assert GSD CLI validation: no in-worktree workflow/CLI was available, and external locations were not accessed. `.planning/state.json` remains a stale navigation artifact and is intentionally unchanged pending a validated schema/workflow. Future agent rule adherence remains unverified.
