---
phase: 111-block-surfacing
plan: '01'
subsystem: dashboard
tags: [dashboard, tasks-json, waiting-on-ove, blocks, redaction, engine, omp-driver]

requires:
  - phase: 110-sbx2-merge-finish
    provides: the clean tree this plan builds on; no omega_prime/dashboard package existed before (verified at cbf1dd2)
provides:
  - omega_prime.dashboard status store: locked_tasks + _write_locked implementing the box tasks.json contract (W111-01)
  - idempotent redacted raise_block/clear_block and the push_block hook (W111-02, W111-03, W111-05)
  - omp_driver.run raises one waiting_on_ove block and sets the task row blocked on plan_not_allowed / sandbox_unavailable when given task context (W111-04)
affects: [sbx-4-routines-visibility (n4 reuses this store), box executor push step (calls push_block), phase-111 audit item C10]

actuals:
  tasks: 3
  commits: 4

tech-stack:
  added: []
  patterns:
    - "sidecar <path>.lock fcntl.flock (LOCK_EX while write=True) held across the whole read-modify-write, persistence via _write_locked inside the with-block"
    - "deterministic item ids (f\"{task_id}:{reason}\", f\"{task_id}:push\") as the upsert mechanism: seq allocated once, since pinned at first raise"

key-files:
  created:
    - omega_prime/dashboard/__init__.py
    - omega_prime/dashboard/status_file.py
    - omega_prime/dashboard/blocks.py
    - omega_prime/tests/test_dashboard_blocks.py
  modified:
    - omega_prime/engine/omp_driver.py

key-decisions:
  - "Engine wiring is opt-in via explicit run(tasks_path=..., task_id=...) parameters; default None keeps behaviour byte-identical, which also keeps this parallel-safe with the sbx-3 backend (U3) that will pass the context later"
  - "The block write sits in run()'s existing except block, wrapped in one contextlib.suppress(Exception) together with the lazy raise_block import, so a visibility failure can never mask or replace the engine failure"
  - "Exception detail is not copied into the item (box shape has no detail field); ask/blocks are static per-reason texts naming the gate and the fix"
  - "Redaction reuses omega_prime.credentials.redact.redact_text; no dashboard-specific redactor; the API takes reason codes plus short text, never argv"

patterns-established:
  - "Engine tests that need a gated failure reuse test_engine.py's _env/_install_fake fixtures; empty allowlist forces plan_not_allowed, a missing OMEGA_PRIME_BWRAP_BIN with UNSANDBOXED=0 forces sandbox_unavailable"

requirements-completed: [W111-01, W111-02, W111-03, W111-04, W111-05]

coverage:
  - id: D1
    description: "Store primitives: seeding without creating, roundtrip without residue, exclusive sidecar lock, unknown-field survival"
    requirement: W111-01
    verification:
      - kind: test
        ref: "omega_prime/tests/test_dashboard_blocks.py::test_locked_tasks_seeds_a_missing_file_without_creating_it, ::test_write_roundtrip_persists_and_leaves_no_residue, ::test_write_lock_is_exclusive_against_a_second_writer, ::test_unknown_fields_survive_a_roundtrip_untouched, ::test_schema_constants_match_the_box_contract"
        status: pass
    human_judgment: false
  - id: D2
    description: "raise_block upserts by stable id (seq once, since pinned); clear flips only blocked->working; terminal rows untouched by foreign clears"
    requirement: W111-02
    verification:
      - kind: test
        ref: "::test_raise_block_upserts_by_stable_id_and_blocks_the_row, ::test_clear_restores_working_and_is_idempotent, ::test_clear_never_resurrects_terminal_rows"
        status: pass
    human_judgment: false
  - id: D3
    description: "Secret-shaped ask/blocks/detail never land in raw file bytes; unknown push reasons rejected without writing"
    requirement: W111-03
    verification:
      - kind: test
        ref: "::test_stored_text_is_redacted_and_never_the_input (asserts a ghp_-shaped value, a token-style assignment, and the secret value absent from raw bytes, REDACTED present), ::test_push_block_rejects_unknown_reasons_without_writing (file bytes unchanged)"
        status: pass
    human_judgment: false
  - id: D4
    description: "plan_not_allowed through the real run() writes its item and blocks the task in the same call; retry upserts; clear restores working"
    requirement: W111-04
    verification:
      - kind: test
        ref: "::test_engine_gated_failure_raises_one_block_in_the_same_call — item id gb:t1:plan_not_allowed exists the moment run() returns (no poll), repeat keeps one item with same seq/since, clear_block returns the row to working"
        status: pass
    human_judgment: false
  - id: D5
    description: "sandbox_unavailable raises its own block; context-free and ungated runs write nothing; EngineResult unchanged"
    requirement: W111-04
    verification:
      - kind: test
        ref: "::test_engine_tier_failure_raises_its_own_block, ::test_engine_writes_no_tasks_file_without_task_context, ::test_engine_ungated_failure_writes_no_item (not_configured)"
        status: pass
    human_judgment: false
  - id: D6
    description: "Push-step hook: one item per push, idempotent repeat, cleared by clear_block, exactly three reasons"
    requirement: W111-05
    verification:
      - kind: test
        ref: "::test_push_block_raises_one_item_and_blocks_the_row, ::test_push_block_repeat_is_idempotent (same seq and since)"
        status: pass
    human_judgment: false
  - id: D7
    description: "Scope discipline: only the five plan-owned files changed, pure additions, no existing test weakened/skipped/deleted"
    requirement: W111-05 (plan prohibitions)
    verification:
      - kind: other
        ref: "git diff cf5897c..HEAD --stat: exactly the 5 owned files, 663 insertions, 0 deletions"
        status: pass
    human_judgment: false
---

# 111-01 Summary: Immediate block surfacing

## Performance vs Plan

All three tasks executed as planned; no deviations from 111-01-PLAN.md.

- **T1 — store primitives (6f09c9a).** `omega_prime/dashboard/status_file.py`: `locked_tasks(path, write=False)` context manager (sidecar `<path>.lock`, `LOCK_EX` while `write=True` else `LOCK_SH`, released on exit; missing or empty file seeds `{"tasks": [], "waiting_on_ove": []}`; the manager never writes) and `_write_locked(data, path)` (same-directory temp file + `os.replace`, temp unlinked on any error; no key reordering, unknown fields pass through). `TASK_STATES` / `ITEM_FIELDS` pin the box schema. 5 tests.
- **T2 — raise/clear/push (ebacace).** `omega_prime/dashboard/blocks.py`: `raise_block` upserts one item keyed by the stable id — first call allocates `seq = max(existing, 0) + 1` and pins `since`; repeats update `ask`/`blocks` only — and sets the owning row `blocked`. `clear_block` removes the item by id and flips only a currently `blocked` row to `working`; missing item/row is an idempotent no-op. `push_block` accepts exactly `auto_review_blocked`, `gh_not_logged_in`, `approval_unanswered` (else `ValueError`, nothing written), writes item `f"{task_id}:push"`, optional short detail redacted and appended to ask. All stored text passes `redact_text`. 7 tests.
- **T3 — engine wiring (0828b30).** `omp_driver.run` gains `tasks_path` / `task_id` keyword parameters (default `None`, nothing threaded into `_run`). In the existing except block, after reason/detail are computed and before the `EngineResult` return, a failure with reason `plan_not_allowed` or `sandbox_unavailable` plus both parameters set raises exactly one item `f"{task_id}:{reason}"` and blocks the task — inside one `contextlib.suppress(Exception)` (the lazy `raise_block` import included) so a visibility failure can never mask the engine failure. Static per-reason ask/blocks texts; exception detail stays in `EngineResult.detail`. `probe()`, `attach.py`, `delegate_via_engine`, `_run` untouched. 4 tests.

## Evidence

```
$ python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_dashboard_blocks.py
16 passed in 0.58s

$ python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_dashboard_blocks.py \
    omega_prime/tests/test_engine.py omega_prime/tests/test_engine_stage.py \
    omega_prime/tests/test_credentials.py
1 failed, 73 passed in 4.22s
FAILED omega_prime/tests/test_engine_stage.py::test_mcp_desk_parent_reads_the_stage_from_its_env
  - ModuleNotFoundError: No module named 'mcp'   (pre-existing, see below)

$ python3 -m py_compile omega_prime/dashboard/{__init__,status_file,blocks}.py \
    omega_prime/engine/omp_driver.py   # ok
```

The outer-driver filter `-k "dashboard or engine or status"` resolves to exactly the three modules above (`test_dashboard_blocks.py`, `test_engine.py`, `test_engine_stage.py`); running it across all of `omega_prime/tests/` collects 49 unrelated ModuleNotFoundErrors (mcp ×35, apscheduler ×8, httpx2 ×3, tweepy, pyrit, aiogram) — this sandbox lacks those optional deps.

**Pre-existing failure (not this plan's):** `test_engine_stage.py::test_mcp_desk_parent_reads_the_stage_from_its_env` fails identically with the plan's changes stashed (run at HEAD, exit 1, `No module named 'mcp'`). The task rules anticipate missing `mcp` ("tests needing it may skip"); this one test lacks a skip guard, and `test_engine_stage.py` is outside this plan's five owned files, so it was left untouched per the plan's prohibition.

**Immediacy/idempotency (the audit C10 fix):** `test_engine_gated_failure_raises_one_block_in_the_same_call` asserts the item `gb:t1:plan_not_allowed` and the `blocked` row exist the moment `run()` returns the failed `EngineResult` — no poll, no second call — and that a retried `run()` leaves exactly one item with the same `seq` and `since`; `clear_block` then removes the item and returns the row to `working`.

## Blocked

None. All W111-01..W111-05 acceptance criteria are met and proven by the suite above.

## Notes

- The store ships in this repo because the tree had none; the API mirrors the box contract (`with locked_tasks(path, write=True) as data:` + `_write_locked(data, path)`, same states, same item keys) so the box tree and this repo read one tasks.json format.
- Deliberately not done (plan prohibitions): no items from `probe()` or `delegate_via_engine`, no Python `TierUnavailable` class, no `omp-sandbox/` changes, no reader/UI, no new dependencies (stdlib only: json, fcntl, os, datetime, contextlib, pathlib, tempfile, typing).
- The engine item's `ask` texts name the env var names (`OMEGA_PRIME_OMP_ALLOW_ZAI`, `OMEGA_PRIME_ENGINE_UNSANDBOXED`), never values; no secret or argv text exists in any stored row.
- Node n5 step 1 (list today's block sources) was recorded during planning in 111-CONTEXT.md `block_sources` (committed in cf5897c); steps 2–5 are the three commits above.
