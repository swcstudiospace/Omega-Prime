---
phase: 113-sbx4-visible-runs
subsystem: sandbox run visibility (sbx-4)
tags: [engine, dashboard, herdr, tasks-json, waiting_on_ove, routines, tests, node-n4]
requires: [112 (sbx-3 omp-sandbox backend), 111 (waiting_on_ove blocks)]
provides:
  - omega_prime/dashboard/herdr_launch.py — never-raising pane launcher (herdr → tmux fallback → None)
  - omega_prime/dashboard/run_rows.py — locked tasks.json row lifecycle start/touch/finish
  - tasks_path/task_id threading: run() → _run → run_backend; delegate_via_engine / ultrathink_engine; delegate_task parent attrs; ultrathink_turn env knobs
  - GSD question round-trip: one waiting_on_ove item per question, answer polled and piped through answer_question, stale answers re-asked
  - 18 new tests (row/pane/argv/fallback/untracked units + question round-trip + secret scans + routing)
affects: []
tech-stack:
  added: []
  patterns:
    - best-effort visibility — every pane/row/item write suppress-wrapped so it can never fail a run
    - single-writer store discipline — rows via locked_tasks+_write_locked, items via blocks.raise_block/clear_block only
    - mid-run responder round-trip on the existing event loop (≤1 s store poll, no wall-clock deadline)
key-files:
  created:
    - omega_prime/dashboard/herdr_launch.py
    - omega_prime/dashboard/run_rows.py
  modified:
    - omega_prime/dashboard/__init__.py (module exports only)
    - omega_prime/engine/sandbox_omp.py (tracker + question round-trip)
    - omega_prime/engine/omp_driver.py (tasks_path/task_id pass-through)
    - omega_prime/engine/attach.py (keyword pair on the two entry points)
    - omega_prime/agent/delegate.py (four parent-attribute reads)
    - omega_prime/routines/ultrathink_turn.py (two env reads)
    - omega_prime/tests/{test_engine_stage.py,test_engine_omp_sandbox.py,test_dashboard_blocks.py}
decisions:
  - open_pane gains keyword-only session=None (deviation documented in W113-01/113-01-SUMMARY): the tmux fallback session name is an engine convention the dashboard must not derive
  - tracker engages only after the up reply and every tracker write is gated on row_started — tier/pre-spawn failures perform zero tracker writes and their exact-row tests stay green verbatim
  - question items use id f"{task_id}:question:{request_id}" through raise_block (the only block writer); stale answers are sniffed from the SandboxUnavailable detail and cleared, a re-ask arrives under a new id
  - test_question_surfaces_as_event_only (phase-112 n4-boundary pin) replaced by strictly stronger round-trip tests — the one sanctioned existing-test change
status: complete
---

# Phase 113: sandbox runs visible in Herdr and tasks.json (sbx-4) — Summary

Task 00030 node n4. A routine- or tool-launched run through the sbx-3 backend is now visible and
answerable: audit C8 (Herdr pane) FAIL → PASS, C9 (tasks.json row) FAIL → PASS, C10's question
half (surfacing + resuming) FAIL → PASS — all proven with a fake herdr runner, a fake tmux, a
fake Anda wrapper and a temp tasks.json (no herdr, podman, gh, network or model calls).

## Plans executed (strictly sequential waves — each consumes the previous API)

| Plan | Scope | Tasks | Commits | Result |
| ---- | ----- | ----- | ------- | ------ |
| 113-01 | omega_prime/dashboard/** (W113-01, W113-02) | 3/3 | af12b22, 2cab136, fa0d4f8 (+ summary c195b4f) | complete |
| 113-02 | omega_prime/engine/** (W113-03, W113-04 engine half) | 4/4 | 20c885e, de3ef69, 07a6cf7, 38a8d28 (+ summary b6fcf9e) | complete |
| 113-03 | callers + tests (W113-04 caller half, W113-05) | 4/4 | 76bd38b, d930463, 383033f, 8f339ff (+ summary c2fc48c) | complete |

Plus this summary/ROADMAP/receipt commit. 15 commits total (14 above + closeout), all local,
no push/PR/remote; diff scope per wave verified against each plan's files_modified.

## New tests → AUDIT capability map

18 new tests; the read-only-audit capability (AUDIT.md C1..C13) each proves:

### omega_prime/tests/test_engine_omp_sandbox.py (engine-visible, fake wrapper + fake herdr)

| Test | Proves |
| ---- | ------ |
| test_tracked_run_lands_a_row_working_to_done | C9 — row working→done within one poll of each event; last_activity_at advancing; herdr_pane recorded; receipt {job_id, stage_id, ok, status, export}; sibling rows byte-untouched (node n4 acceptance, AC1/AC4) |
| test_pane_label_and_watch_argv_are_pinned | C8 — pane label exactly f"{task_id} {stage}"; `herdr pane run` argv pinned to the watch invocation (sys.executable, job_id, --follow, --state-dir) (AC4) |
| test_tmux_fallback_records_the_session | C8 — missing herdr + OMEGA_PRIME_ENGINE_TMUX=1 records herdr_pane "tmux:omega-{job}" with the same watch argv (AC3) |
| test_no_pane_leaves_the_key_absent_and_the_run_healthy | C8/C9 — a pane failure never fails the run; the row simply has no herdr_pane key (AC3) |
| test_untracked_backend_run_writes_nothing | C9 — without task context the backend is byte-identical to phase 112 (zero tracker calls, store untouched) (AC4) |
| test_question_round_trip_resumes_the_run | C10 — question → exactly one waiting_on_ove item (stable id, ask/blocks pinned, row blocked) → answer written under the flock → answer_question with matching {stage_id, request_id, text} → item cleared → row done → run completes (AC2/AC5) |
| test_stale_answer_reasks_under_a_new_item | C10 — stale answer clears the dead item; re-ask raises under req-2; two ordered answer_question receipts; final store item-free (AC5) |
| test_answer_text_never_lands_in_durable_dashboard_artifacts | C10/security — a runtime-built secret inside the answer is absent from tasks.json, events.jsonl and herdr receipts (the answer travels only into answer_question) |
| (extended) test_no_secret_shaped_value_lands_in_any_durable_artifact | unchanged assertions; now hermetic via the auto-installed failing herdr stub |
| (replacement of the phase-112 pin) | the old boundary assertions (no item ever, no answer_question, exact {"id","state"} row) are inverted/strengthened per the cutover map in 113-03-SUMMARY.md |

### omega_prime/tests/test_dashboard_blocks.py (run_rows units, store level)

| Test | Proves |
| ---- | ------ |
| test_run_rows_start_creates_the_working_row_and_pins_started_at | C9 — documented field set, ISO-8601 UTC, herdr_pane only when a pane opened |
| test_run_rows_start_never_resurrects_terminal_rows | C9 — done/failed rows are write-protected |
| test_run_rows_start_returns_a_blocked_row_to_working | C9 — re-start of a non-terminal row re-enters working, started_at pinned |
| test_run_rows_touch_labels_question_and_stage_done_events | C9 — last_event labels ("question", "stage_done <status>", verbatim str) |
| test_run_rows_touch_is_a_noop_for_missing_and_terminal_rows | C9 — late events neither resurrect nor churn |
| test_run_rows_writes_leave_other_rows_byte_identical | C9 — single-writer RMW preserves unknown fields on sibling rows byte-for-byte |
| test_run_rows_finish_upserts_terminal_states | C9 — done/failed with receipt; a missing row is created terminal |
| test_run_rows_finish_redacts_secret_shaped_receipt_strings | C10/security — receipt strings pass redact_text recursively; raw token absent from file bytes (dashboard redaction check) |

### omega_prime/tests/test_engine_stage.py (routing)

| Test | Proves |
| ---- | ------ |
| test_delegate_task_threads_parent_task_context | W113-04 — delegate_task forwards cwd/engine_plan/tasks_path/task_id via the getattr convention; bare parent ⇒ all None (defaults unchanged) |
| test_execute_ultrathink_reads_task_context_from_env | W113-04 — OMEGA_PRIME_TASKS_PATH/OMEGA_PRIME_TASK_ID threaded; absent env ⇒ unchanged call |

## Verification (parent-executed at final state)

- Baseline before any edit: `python3 -m pytest -q -p no:cacheprovider` over the four suites →
  **1 failed, 79 passed**; the failure is the pre-existing
  `test_engine_stage.py::test_mcp_desk_parent_reads_the_stage_from_its_env`
  (ModuleNotFoundError: No module named 'mcp'; recorded in 112-CONTEXT; identical after).
- Final, NO deselect: same four suites → **1 failed, 96 passed** (+17 net passing; the only
  failure is the same pre-existing mcp gap). No new skips.
- Dashboard redaction/terminal/unknown-field filter: `pytest test_dashboard_blocks.py -k "redact
  or resurrect or unknown"` → **6 passed**.
- Layer gate: `! grep -rn "omega_prime\.engine" omega_prime/dashboard/` → clean.
- Docs/public-repo suites: `pytest omega_prime/tests/test_docs.py omega_prime/tests/test_public_repo.py`
  → **31 passed**.
- py_compile clean on all eight product/test files touched.
- Wave-2 gate (during 113-02, before 113-03 flipped the pin): four suites with
  `--deselect ...::test_question_surfaces_as_event_only` → 1 failed (pre-existing mcp), 78 passed,
  1 deselected; the pin failed ONLY its exact-row assertion (planned cutover bridge).
- Diff hygiene: wave scopes exactly match each plan's files_modified (dashboard 3 files; engine
  3 files; callers+tests 5 files); blocks.py and status_file.py byte-identical throughout; no
  omp-sandbox/** changes; no new dependencies (stdlib only); `git status --porcelain` empty.

## Acceptance criteria → status

1. Routine-launched run shows a Herdr pane and a row moving working → done/failed within one poll
   of each event — **PASS** (test_tracked_run_lands_a_row_working_to_done + argv pin test; fake
   herdr runner per the pinned parse contract).
2. GSD question appears in waiting_on_ove and its answer resumes the run (answer_question with
   matching ids, item clears, stage completes) — **PASS** (round-trip + stale re-ask tests).
3. herdr missing ⇒ tmux fallback + row still track; pane failure never fails the run — **PASS**
   (fallback/None-path tests).
4. OMEGA_PRIME_OMP_BACKEND unset ⇒ existing suites unchanged; no task context ⇒ phase-112
   behaviour — **PASS** (full four-suite green modulo the pre-existing mcp gap; untracked no-op test).
5. All new tests green with fakes only; no existing test weakened/skipped/deleted; the one pin
   flipped to strictly more — **PASS** (cutover map in 113-03-SUMMARY.md; secret test assertions
   untouched).

## Blocked / pre-existing (not caused by this phase, unchanged by it)

- `test_engine_stage.py::test_mcp_desk_parent_reads_the_stage_from_its_env` — pre-existing
  ModuleNotFoundError: No module named 'mcp' (optional dep absent; no network). Identical at
  baseline and after.
- herdr/tmux binaries are absent in this sandbox (contract): every pane proof uses PATH-shadowed
  fakes; visual proof against a real herdr is node n7 (sbx-5 e2e), out of scope here.
- omp-sandbox TypeScript suites were not re-run: no file under omp-sandbox/** changed (verified
  by diff); phase 112 recorded tsc 0 / npm test 27/27 at this base.
- Push path stays blocked pending Ove (gh login / push.sh yes) — commits are local only, as ordered.
