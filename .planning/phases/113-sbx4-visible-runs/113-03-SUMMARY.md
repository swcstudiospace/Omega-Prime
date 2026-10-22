# Phase 113 Plan 03: Caller routing + W113-05 test proof Summary

`delegate_task` and `execute_ultrathink` now carry task context into the engine (parent-attribute getattr reads / `OMEGA_PRIME_TASKS_PATH`/`OMEGA_PRIME_TASK_ID` env reads), and the W113-05 suite proves the full n4 contract with fakes only: row working→done with pane and receipt, pinned herdr argv, tmux fallback, run_rows store units, the question round-trip with a mid-run responder, the stale clear-and-re-ask loop, and an answer-secret byte-scan — all green with NO deselect.

**Phase:** 113-sbx4-visible-runs
**Plan:** 03
**Requirements:** W113-04, W113-05 (W113-03's test half lands here)
**Status:** complete
**Date:** 2026-10-21
**Base:** b6fcf9e (`feat/sbx-4-routines-visible`, includes waves 1-2)
**Commits:** 4 (measured: `git rev-list --count b6fcf9e..HEAD`)
**plan_head_before:** b6fcf9e2cbf28811a89c413b575814bfa98d84dc
**plan_head_after:** 8f339ff9e7876b650b6f1370ea8c022412fc430f

## Commits

| Task | Commit | Subject |
| ---- | ------ | ------- |
| 113-03-T1 | 76bd38b | 00014 - callers: delegate_task and ultrathink_turn carry task context into the engine (W113-04) |
| 113-03-T2 | d930463 | 00014 - tests: fake herdr runner, hermetic backend env, requestId-holding fake wrapper (W113-05) |
| 113-03-T3 | 383033f | 00014 - tests: row lifecycle, pane argv, tmux fallback, run_rows units, secret scans (W113-05) |
| 113-03-T4 | 8f339ff | 00014 - tests: question round-trip and stale re-ask prove the n4 contract; boundary pin flipped (W113-03, W113-05) |
| summary | (this commit) | 00014 - phase 113: plan 113-03 summary (W113-04, W113-05) |

Diff scope: the four implementation commits touch ONLY the five owned files — `omega_prime/agent/delegate.py` (+4), `omega_prime/routines/ultrathink_turn.py` (+21/−15), `omega_prime/tests/test_engine_stage.py` (+96), `omega_prime/tests/test_dashboard_blocks.py` (+150), `omega_prime/tests/test_engine_omp_sandbox.py` (+607/−8). No engine, dashboard, agent-other, routine-other, or omp-sandbox edits (`git diff --name-only b6fcf9e..HEAD` = exactly those five). Working tree clean after the summary commit.

## Full-suite output (final acceptance, at HEAD 8f339ff)

```
python3 -m py_compile omega_prime/agent/delegate.py omega_prime/routines/ultrathink_turn.py   → OK
python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_engine.py \
  omega_prime/tests/test_engine_stage.py omega_prime/tests/test_dashboard_blocks.py \
  omega_prime/tests/test_engine_omp_sandbox.py
→ 1 failed, 96 passed in 11.29s
   FAILED omega_prime/tests/test_engine_stage.py::test_mcp_desk_parent_reads_the_stage_from_its_env
          (ModuleNotFoundError: No module named 'mcp' — the pre-existing failure
           recorded in 112-CONTEXT; untouched, everything else green)
```

NO deselect anywhere, no new skips. Task gates along the way: T1 `54 passed, 1 failed (same mcp)`; T2 `10 passed, 1 deselected`; T3 `39 passed, 1 deselected`; T4 full sweep green. The three threading-based question tests were repeat-run 3× (4.79-4.80s each) — stable.

## Documented cutover: `test_question_surfaces_as_event_only` → the n4 contract

The phase-112 pin asserted the n4 BOUNDARY (question = event only). It is REPLACED (not merely extended) by `test_question_round_trip_resumes_the_run` plus two companion tests. Assertion by assertion:

| # | Old pin assertion | Replacement |
| - | ----------------- | ----------- |
| 1 | `result.ok is True` | Kept: `result.ok is True, result.detail` (run still healthy) |
| 2 | exactly one `question` event in events.jsonl | Kept and strengthened: still exactly one, now `requestId == "req-1"` |
| 3 | `questions[0]["attestation"] == "mock"` | Kept verbatim |
| 4 | `data["waiting_on_ove"] == []` (no item ever) | INVERTED mid-run — exactly one item `gb:q:question:req-1` with `ask == "Which shape do you want?"` and `blocks == "sandbox GSD question (stage plan)"`, row `blocked` — and kept post-run (item cleared, `waiting_on_ove == []`) |
| 5 | `data["tasks"][0] == {"id": "gb:q", "state": "working"}` (exact row equality) | Strictly stronger: mid-run row is `blocked`; post-run row is `done` with the full visibility field set (its fields pinned by `test_tracked_run_lands_a_row_working_to_done`) |
| 6 | `"answer_question" not in _wrapper_tools(log)` (no round-trip) | INVERTED: exactly one `answer_question` receipt with args exactly `{"stage_id": "stage-1", "request_id": "req-1", "text": "the square one"}` |

Strictly MORE on every axis, never less — and the two companion tests add axes the pin never had: `test_stale_answer_reasks_under_a_new_item` (req-1 answered → stale receipt → req-1 item cleared → req-2 item raised as the only item → answered → two receipts with request ids `[req-1, req-2]`, texts `["first instinct", "the square one"]`, both asks in the event feed, run ok, store item-free, row done) and `test_answer_text_never_lands_in_durable_dashboard_artifacts`. The cutover rationale is documented in the replacement test's docstring.

## Fixture contracts (all stdlib-python scripts in tmp dirs, PATH/env-shadowed)

**Fake herdr runner — `_install_fake_herdr(tmp_path, mode="ok") -> (script, log)`.** Writes an executable `herdr` into `tmp_path/"bin"` (the dir `_env` puts FIRST on PATH — no test can reach a real herdr). Every invocation appends `{"argv": [...]}` to `tmp/bin/herdr-log.jsonl`; the ok-mode `workspace create` receipt also carries the exact pane id it printed. Mode `"ok"`: `workspace create` prints `workspace created pane-<pid>` (pane id = last whitespace token of the last non-empty line — the pinned parse contract) and exits 0; `pane run` exits 0; anything else exits 1. Mode `"fail"`: exits 1 on every call, receipts still written. `_backend_env` auto-installs the fail stub whenever no stub exists yet, so every backend test is hermetic regardless of the executor box; `OMEGA_PRIME_ENGINE_TMUX` stays `"0"` in the `_env` base, so the tmux fallback is opt-in per test.

**Fake tmux — `_install_fake_tmux(tmp_path)`.** Same bin dir; argv receipts to `tmp/bin/tmux-log.jsonl`; `has-session` exits 1 (always the spawn path), `new-session` exits 0. Used only by `test_tmux_fallback_records_the_session`, which sets `OMEGA_PRIME_ENGINE_TMUX="1"` explicitly.

**Fake Anda wrapper — question paths only.** `"question"`: after the `run_gsd_stage` reply, emits `{"event": "question", "stageId": stage_id, "requestId": "req-1", "question": "Which shape do you want?"}` and does NOT emit `stage_done` (pending = `{stage, request: "req-1", answered: False}`). A new `answer_question` handler: matching pending stage+request while unanswered → `ok {"answered": true}` + `stage_done completed`; anything else → `{"ok": false, "error": "stale answer (fake)", "code": "stale_answer"}` (server.ts errorReply shape). `"question_reask"`: same ask under `req-1`; the FIRST `answer_question` (any args) replies stale and re-emits the question under `"req-2"`; req-2's matching answer replies ok + `stage_done completed`; later answers go stale. While a question is pending, `abort` keeps its existing reply (ok, no stage_done). Every emitted line keeps `"attestation": "mock"`; every request still receipts before replying; happy/tier_error/hang and all other tools are byte-unchanged.

**Responder convention (W113-03's answer writing).** Fixtures are built in the MAIN thread; `run()` executes in a daemon thread; the responder polls `locked_tasks(store)` (shared lock, 10 s test-side bound) for the stable item id, then writes `item["answer"]` via one `locked_tasks(store, write=True)` + `_write_locked` block — the exact production responder convention. All bounds live in the TEST; the engine wait keeps no deadline (phase-112 lock). Thread errors are surfaced in the main thread through a shared failure list (`_check_round_trip`).

## Answer-secret scan scope

`test_answer_text_never_lands_in_durable_dashboard_artifacts` embeds a runtime-built secret-shaped token (`"".join(("sk-", "ans", "wer-", "not", "-for-", "disk"))` — never a literal) INSIDE the answer text and, after the run completes, byte-scans every product artifact: **tasks.json** (row, receipt, any surviving item), **events.jsonl** (`result.events_path`), and **herdr-log.jsonl** (the pane argv receipts — proving labels/commands never carry task/env text). Token absent from all three; the `answer_question` call itself demonstrably carried it. **Excluded: the fake wrapper's own `wrapper-log.jsonl`** — it receipts the `answer_question` call the test itself made; that call IS the product behavior under test (the client → wrapper hop), no production surface logs answers, and the fake is test code, not a product artifact. The existing `test_no_secret_shaped_value_lands_in_any_durable_artifact` keeps every assertion and only gains hermeticity (its whole-tmp_path scan now also covers the auto-installed stub's receipts).

## Visibility tests added (engine-visible, `test_engine_omp_sandbox.py`)

- `test_tracked_run_lands_a_row_working_to_done` — start CREATES the tracked row (seeded store holds only the unrelated row: `started_at` is stamped only on rows `start` creates — pinned 113-01 semantics), ends `done`, `herdr_pane == ` the create receipt's pane id, `started_at <= last_activity_at` (both ISO-8601), `last_event == "stage_done completed"`, receipt keys exactly `{job_id, stage_id, ok, status, export}` with the fake export summary keys; an unrelated pre-seeded row (owner/nested unknown fields) survives byte-for-byte.
- `test_pane_label_and_watch_argv_are_pinned` — create argv `["workspace","create","--cwd",<work>,"--label","gb:argv plan","--no-focus"]`; run argv `["pane","run",<pane>,sys.executable,"-m","omega_prime.engine.watch","job-argv","--follow","--state-dir",<tmp>/state]`.
- `test_tmux_fallback_records_the_session` — `herdr_pane == "tmux:omega-job-tmux"`; `has-session -t omega-job-tmux` then `new-session -d -s omega-job-tmux -- <the same pinned watch argv>`; the failed create is receipted, `pane run` never happened.
- `test_no_pane_leaves_the_key_absent_and_the_run_healthy` — auto fail stub + TMUX "0": run ok, row done, `herdr_pane` key absent, exactly one attempted create.
- `test_untracked_backend_run_writes_nothing` — no task context: phase-112 result shape (ok, `omp-sandbox`, stage, `final_text`), store file absent, herdr log empty, no `answer_question`.

Run_rows unit tests (`test_dashboard_blocks.py`, store-level, no engine): start creates the working row and pins `started_at` across re-start (later `pane=None` leaves the recorded pane); start never resurrects terminal rows (byte-identical store); start returns a blocked row to working; touch labels `question` / `stage_done <status>` (collapsed when empty) / plain-string-verbatim and leaves other shapes alone; touch is a no-op for missing/terminal rows; all three writers leave sibling rows byte-identical; finish upserts done/failed (missing row created terminal with `started_at`) and redacts a secret-shaped receipt string (`[REDACTED]` stored, raw token absent from file bytes). Routing tests (`test_engine_stage.py`): `test_delegate_task_threads_parent_task_context` (staged parent captures `cwd/engine_plan/tasks_path/task_id` + `stage "execute"`; bare parent captures all-None) and `test_execute_ultrathink_reads_task_context_from_env` (env values thread through; no-var and empty-env cases thread None).

## Verification

| Gate | Command | Result |
| ---- | ------- | ------ |
| T1 | py_compile delegate/ultrathink + `pytest test_engine_stage.py test_engine.py` | 54 passed, 1 failed (pre-existing mcp) |
| T2 | py_compile test file + sandbox suite `--deselect ...::test_question_surfaces_as_event_only` | 10 passed, 1 deselected |
| T3 | sandbox + dashboard suites, same deselect | 39 passed, 1 deselected |
| T4 | py_compile product files + FULL four-suite sweep, NO deselect | 1 failed (pre-existing mcp), 96 passed |
| Stability | question tests ×3 | 4 passed each run |
| Diff scope | `git diff --name-only b6fcf9e..HEAD` | exactly the five owned files |
| Secret/tracker scan | grep committed tests for `SPE-`/Notion/Linear/secret literals | clean (shaped values built at runtime only) |
| Tree | `git status --porcelain` | clean |

## Decisions Made

- Followed the plan's `<assumption_delta_decision>` verbatim: daemon responder (not pre-seed), `_backend_env` auto fail-stub, separate `question_reask` behavior, fake-tmux fallback tests opt-in via `OMEGA_PRIME_ENGINE_TMUX="1"`, run_rows units in `test_dashboard_blocks.py`.
- The ok-mode herdr create receipt carries the pane id it printed (alongside the pinned `{"argv": ...}` receipt) so `test_tracked_run_lands_a_row_working_to_done` can assert `herdr_pane == ` the exact parsed value — the plan's "read from the herdr log".
- `test_tracked_run_lands_a_row_working_to_done` seeds only the unrelated row: `run_rows.start` stamps `started_at` only on rows it creates (113-01 pinned semantics), and the plan requires `started_at <= last_activity_at` on the tracked row.
- Empty env values normalize: `mapping.get("OMEGA_PRIME_TASKS_PATH") or None` (plan: "a missing/empty value threads None").

## Deviations from Plan

None — plan executed exactly as written. (No auth gates, no package installs, no engine/dashboard/omp-sandbox edits, no CLAUDE.md conflicts.)

## Known Stubs

None. No stub patterns, no skipped tests, no unrun verifies; the n4-boundary pin flip is the planned cutover documented above, and the deselect bridge from wave 2 is fully retired.

## Notes

- Herdr/tmux/podman/gh/network/model were never touched: every binary the tests execute is a tmp-dir script (`omp`, `fake-anda`, `herdr`, `tmux`) resolved via `OMEGA_PRIME_OMP_BIN`, `OMEGA_PRIME_OMP_SANDBOX_ANDA`, and the first-on-PATH bin dir; `git` is used only inside tmp worktrees.
- The pre-existing `mcp` failure in `test_engine_stage.py` is the recorded 112-CONTEXT baseline, untouched by this plan.

## Self-Check: PASSED

- Files exist: `omega_prime/agent/delegate.py`, `omega_prime/routines/ultrathink_turn.py`, `omega_prime/tests/test_engine_stage.py`, `omega_prime/tests/test_engine_omp_sandbox.py`, `omega_prime/tests/test_dashboard_blocks.py`, this SUMMARY.
- Commits 76bd38b / d930463 / 383033f / 8f339ff are HEAD ancestors; `git status --porcelain` clean.
- No stubs, no deferred issues, no auth gates, no new dependencies (pytest/stdlib only).
