---
phase: 106-command-center-polish
plan: "01"
subsystem: dashboard
tags: [python, github, model, snapshot, task-numbering]
requires: []
provides:
  - Bounded GitHub closed-PR pagination and explicit oversize degradation with stale merge retention.
  - Finished-only terminal task projection and an idle_sessions snapshot/API bucket.
  - Durable identity numbering and history across active/idle transitions.
affects: [106-03, 106-04, 106-05]
actuals:
  tokens: not-measured
  tasks: 3
  commits: 11
tech-stack:
  added: []
  patterns: [bounded page-specific collector policy, additive snapshot bucket, shared canonical identity registry]
key-files:
  created:
    - omega_prime/tests/test_dashboard_github_merges_polish.py
    - omega_prime/tests/test_dashboard_model_buckets.py
  modified:
    - omega_prime/dashboard/collectors/github.py
    - omega_prime/dashboard/github_data.py
    - omega_prime/dashboard/collectors/herdr.py
    - omega_prime/dashboard/model.py
    - omega_prime/dashboard/task_ids.py
    - omega_prime/dashboard/app.py
requirements-completed: [AC1, AC2, AC6, AC8]
coverage:
  - id: D1
    description: Bounded closed-PR scans, limit+1 response detection and stale merge fallback.
    requirement: AC1
    verification:
      - kind: unit
        ref: omega_prime/tests/test_dashboard_github_merges_polish.py
        status: pass
    human_judgment: true
    rationale: Focused offline tests passed as reported by the integration owner; live host validation remains unverified.
  - id: D2
    description: Finished and idle bucket projection retains task detail, waiting relations and worktree wiring.
    requirement: AC6
    verification:
      - kind: unit
        ref: omega_prime/tests/test_dashboard_model_buckets.py
        status: pass
    human_judgment: true
    rationale: Focused offline tests passed as reported by the integration owner.
  - id: D3
    description: Idle task identities survive bucket transitions, restart, snapshot hashing and API publication.
    requirement: AC8
    verification:
      - kind: integration
        ref: omega_prime/tests/test_dashboard_snapshot_contract.py
        status: pass
      - kind: integration
        ref: omega_prime/tests/test_dashboard_app.py
        status: pass
    human_judgment: true
    rationale: Focused offline tests passed as reported by the integration owner; broader dashboard gate remains pending.
duration: not-measured
completed: 2026-10-21
status: complete
---

# Phase 106 Plan 01 Summary

**Bounded GitHub merge collection and backend-only Finished/Idle task projection now flow through durable numbering and the existing snapshot/API pipeline.**

## Performance

- **Duration:** Not measured.
- **Started:** Not recorded.
- **Completed:** 2026-10-21.
- **Tasks:** 3.
- **Files modified:** 14 (including two new regression test files).

## Accomplishments

- Closed-PR scans use `CLOSED_PAGE_SIZE=30`, at most `MAX_CLOSED_PAGES=10` (300 raw candidates per target), stop at 100 actual merged rows per target, then sort globally and cap at `MAX_MERGES=100`. Existing open-list constants remain `PAGE_SIZE=100`, `MAX_PAGES=3`.
- GitHub response bodies are bounded at `MAX_RESPONSE_BYTES=1_000_000`; transport reads at most cap+1, injected transports are independently bounded, and oversize failures report `too_large`. Failed merge refreshes keep repository-local cached rows and their original successful timestamps/stale evidence.
- `tasks_json` done/failed tasks appear only in Finished with `ok`/`failed`, retaining available owner, model, timestamps, progress, links, note/detail and wiring/worktree evidence. Non-live, unknown, stopped and quiet sessions are projected into `idle_sessions`; live working/idle/blocked panes and working sandbox E2E remain Active, with blocked-pane Waiting relations retained.
- Added serialized `Snapshot.idle_sessions` after the four existing positional fields and included it in content hashing and the cold snapshot shape. Idle tasks share the existing identity registry, sequence allocator and timeline.

## Active-consumer audit

- `poller.py` was intentionally unchanged: `_build_snapshot()` already runs `normalize_tasks()` → `number_snapshot()` → `Snapshot.to_dict()` → redaction → canonical content hash/publication. Adding Idle serialization and numbering automatically carries Idle into current API snapshots and SSE payloads without duplicate assembly.
- `task_ids.py` now numbers/observes `active`, `idle_sessions`, `waiting_on_ove` and `finished` in its single existing registry; no second counter or stored idle array was added.
- `app.py` changed only `_empty_snapshot()` to include `idle_sessions: []`. Existing GET/Host/CSP/static guards and original array shapes are otherwise unchanged.
- Legacy static `app.js` remains intentionally Active-only. Frontend Idle rendering/count behavior belongs to plan 04. `status_file.py`, poller, frontend/generated assets and external services were not changed.

## Task Commits

1. **Task 1: Restore bounded recent merges** — `c495f6e`.
2. **Task 2: Separate finished tasks and idle sessions** — `45e3ad9`.
3. **Task 3: Carry Idle through numbering, hashing and APIs** — `febadb4`.

**Regression/compatibility corrections:** `36c3d41`, `a7c622e`, `7225337`, `a21ad25`.  
**Summary commits:** `608fa0b`, `cb4c61c`, `c27524c`; final evidence update follows.
## Files Created/Modified

- `omega_prime/dashboard/collectors/github.py`
- `omega_prime/dashboard/github_data.py`
- `omega_prime/dashboard/collectors/herdr.py`
- `omega_prime/dashboard/model.py`
- `omega_prime/dashboard/task_ids.py`
- `omega_prime/dashboard/app.py` (only `_empty_snapshot`)
- `omega_prime/tests/test_dashboard_github_merges_polish.py` (new)
- `omega_prime/tests/test_dashboard_model_buckets.py` (new)
- `omega_prime/tests/test_dashboard_model.py`
- `omega_prime/tests/test_dashboard_process_collectors.py`
- `omega_prime/tests/test_dashboard_task_ids.py`
- `omega_prime/tests/test_dashboard_snapshot_contract.py`
- `omega_prime/tests/test_dashboard_app.py`
- `omega_prime/tests/test_dashboard_integration.py`

## Decisions Made

- Kept the shared general GitHub page constants unchanged and introduced a closed-only page policy.
- Used the existing normalized Herdr pane record to add optional `has_live_agent` evidence; recognized agent status remains compatibility evidence only when explicit agent metadata is absent. No process requirement was added.
- Followed the approved model-bucket addendum without touching frontend plan-04 files or plan-05 model extensions.

## Deviations from Plan

None intended. No poller changes, generated asset changes, external dependency changes or unrelated security changes.

## Verification and Evidence

The implementation agent did not run tests, builds, lint or formatters. The integration owner subsequently reported these focused results:

- `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_dashboard_github_merges_polish.py` — PASS, 4/4.
- `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_dashboard_model_buckets.py omega_prime/tests/test_dashboard_model.py omega_prime/tests/test_dashboard_process_collectors.py` — PASS, 19/19 after restoring the test fixture assignment and preserving standalone OMP classification.
- `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_dashboard_model_buckets.py omega_prime/tests/test_dashboard_task_ids.py omega_prime/tests/test_dashboard_snapshot_contract.py omega_prime/tests/test_dashboard_app.py omega_prime/tests/test_dashboard_integration.py` — PASS, 35 tests.
- Focused dashboard family suite — PASS, 158 passed / 1 skipped (integration-owner report; exact invocation was not supplied).
- `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/ -k dashboard` — BLOCKED at collection, exit 2 (integration-owner report); supplied baseline identifies missing `mcp` and `apscheduler` prerequisites.
No baseline fixture transplant was executed, so no baseline assertion failure is claimed. Regression tests have focused passing results, but no red-on-base evidence. The integration owner reports the broad gate remains blocked during collection; supplied baseline identifies missing `mcp` and `apscheduler` prerequisites. No dependency was changed.

**Host-unverified:** Live GitHub parity and all 7421/live-data behavior remain unverified by design. The assignment prohibited network, Herdr, real task-store/HOME and 7421 access. No claim is made about these live acceptance conditions.

## User Setup Required

None.

## Next Phase Readiness

Plan 03 can extend GitHub collector behavior after 01/02. Plan 04 can add the frontend Idle group/count; plan 05 can extend model/task-pane projection. The focused offline suites reported by the integration owner pass; the broad dashboard gate and live host evidence remain pending.

---
*Phase: 106-command-center-polish*
*Plan: 01*
