# Phase 113 Plan 02: Engine visibility wiring Summary

The sbx-3 backend (`sandbox_omp.run_backend`) now carries task context: when `tasks_path` AND `task_id` are both set it opens one Herdr pane labelled `f"{task_id} {stage}"` running the existing watch command, drives one locked tasks.json row (start after the `up` reply, touch per wrapper event, finish with a redacted receipt first in the `finally`), and surfaces GSD questions as exactly one `waiting_on_ove` item whose polled answer resumes the stage through `answer_question` — all best-effort under `contextlib.suppress`, so visibility can never kill a run. `omp_driver._run` threads the pair from `run()`, and `delegate_via_engine`/`ultrathink_engine` gain the same keyword-only pair. Everything is additive: with either param absent (or `OMEGA_PRIME_OMP_BACKEND` unset) every path is byte-identical to phase 112.

**Phase:** 113-sbx4-visible-runs
**Plan:** 02
**Requirements:** W113-03, W113-04 (W113-01/W113-02 consumed)
**Status:** complete
**Date:** 2026-10-21
**Base:** c195b4f (`feat/sbx-4-routines-visible`, includes 113-01's merged dashboard API)

## Commits (measured: `git rev-list --count c195b4f..HEAD` = 4; plan_head_after = 38a8d28)

| Task | Commit | Subject |
| ---- | ------ | ------- |
| 113-02-T1 | 20c885e | 00014 - engine: thread tasks_path/task_id from run into the omp-sandbox backend (W113-04) |
| 113-02-T2 | de3ef69 | 00014 - engine: herdr pane and tasks.json row ride every tracked backend run (W113-01, W113-02) |
| 113-02-T3 | 07a6cf7 | 00014 - engine: GSD questions surface in waiting_on_ove and answers resume the stage (W113-03) |
| 113-02-T4 | 38a8d28 | 00014 - engine: tasks_path/task_id on delegate_via_engine and ultrathink_engine (W113-04) |
| summary | (this commit) | 00014 - phase 113: plan 113-02 summary (W113-03, W113-04) |

Diff scope: the four implementation commits touch ONLY `omega_prime/engine/{sandbox_omp.py,omp_driver.py,attach.py}` (`git diff --name-only c195b4f..HEAD` = exactly those three; +313/−14). No dashboard, test, agent, routine, or omp-sandbox edits.

## Tracker engagement point (load-bearing for existing tests)

The tracker block sits **between the `up` reply and `run_gsd_stage`** (after `client.call("up", ...)` returns, before the stage call), and every tracker write is gated on `row_started` — a flag set only where `run_rows.start` actually ran (inside its own suppress block, immediately after the start call; deliberately NOT the existing `started`, which is set only after `run_gsd_stage` returns and would strand a started row on a stage-call failure). Consequences, verified against the phase-112 tests:

- **`test_tier_unavailable`** fails AT the `up` reply → `row_started` stays False → zero tracker writes → run()'s own `raise_block` flips the seeded row to `blocked`; its exact-row assertion `{"id": "gb:tier", "state": "blocked"}` holds verbatim. **`test_plan_not_allowed`** refuses pre-spawn (before the try) → same zero-write guarantee.
- **The interrupt test** passes no task context → `tracked` False → the tracker never engages; its wrapper-tool/event assertions are unchanged.
- **The secret byte-scan test** is the tracked happy path: its row gains visibility fields, but its assertions are byte-scan only → still green.
- `run_rows.finish` is the first statement of the `finally` (before `client.down()`), gated on `row_started` not `tracked`, with `stage_id` initialised to None before the try so the receipt `{job_id, stage_id, ok, status, export}` is buildable on every exit path — a cancelled stage (EngineError propagating, outcome None) finishes `failed` via the outcome-None branch.

## Pinned question-helper signatures (cross-plan contract; consumed by `_wait_stage_done`, exercised by 113-03 through `run()`, not by import)

```python
# sandbox_omp.py (module constant: _ANSWER_POLL = 1.0 — answer store-read
# cadence; the question/stage wait itself has NO deadline, phase-112 lock)

def _question_item_id(task_id: str, request_id: str) -> str
    # returns f"{task_id}:question:{request_id}"

def _raise_question(tasks_path: str | Path | None, task_id: str | None,
                    stage: str | None, request_id: str, question_text: Any) -> str
    # raise_block(tasks_path, item_id, ask=str(question_text or ""),
    #             blocks=f"sandbox GSD question (stage {stage})", task_id=task_id);
    # returns the item id

def _answer_from_store(tasks_path: str | Path | None, item_id: str
                       ) -> str | Mapping[str, Any] | None
    # one locked_tasks(path) shared-lock snapshot; returns the item's `answer`
    # only when it is a str or a Mapping containing "selected", else None

def _answer_args(answer: str | Mapping[str, Any]) -> dict[str, Any]
    # {"text": answer} for a str; {"selected": list(answer["selected"])} otherwise

def _clear_question(tasks_path: str | Path | None, task_id: str | None,
                    item_id: str) -> None
    # clear_block(tasks_path, item_id, task_id=task_id)
```

Round-trip semantics wired in `_wait_stage_done` (signature `(client, stage_id, interrupt, events_path, *, tasks_path=None, task_id=None, row_started=False, stage=None)`): a `question` event with a non-empty requestId and a started row supersedes any open item (one question at a time) and raises the new one; the poll reads the store at most once per `_ANSWER_POLL` seconds riding the `_POLL_TICK` 0.2 s loop; a conforming answer fires `answer_question {stage_id, request_id, text|selected}` then clears the item; `stale_answer` (sniffed from the SandboxUnavailable detail — the only channel, per the frozen `AndaClient.call` ok:false contract) clears the dead item and keeps waiting; any other `answer_question` failure re-raises. requestId-less questions are informational (append/touch only) — that leniency's last user is the un-flipped pin test's fake.

## Verification (all run in this environment, no PYTHONPATH prefix)

| Gate | Command | Result |
| ---- | ------- | ------ |
| Baseline (before any edit) | `python3 -m pytest -q -p no:cacheprovider <four suites>` | 1 failed, **79 passed** — the failure is `test_engine_stage.py::test_mcp_desk_parent_reads_the_stage_from_its_env` (ModuleNotFoundError: No module named 'mcp'; pre-existing, recorded in 112-CONTEXT) |
| T1 gate (params inert) | `python3 -m py_compile sandbox_omp.py omp_driver.py` + four suites, NO deselect | py_compile pass; 1 failed (same pre-existing mcp), **79 passed** — the n4 pin still passes with inert params |
| T2/T3/T4 gate | `python3 -m py_compile sandbox_omp.py omp_driver.py attach.py` + four suites `--deselect omega_prime/tests/test_engine_omp_sandbox.py::test_question_surfaces_as_event_only` | py_compile pass; 1 failed (same pre-existing mcp), **78 passed, 1 deselected** |
| Final acceptance | same final command at HEAD (38a8d28) | identical: 1 failed (pre-existing mcp), 78 passed, 1 deselected |
| Diff scope | `git diff --name-only c195b4f..HEAD` | exactly the three engine files |
| Working tree | `git status --porcelain` | clean after the summary commit |

**The deselect is the planned 113-03 cutover bridge, not a dodge:** the phase-112 pin `test_question_surfaces_as_event_only` fails on exactly ONE assertion — its exact-row equality `data["tasks"][0] == {"id": "gb:q", "state": "working"}` — because the row is now finished `done` with the visibility fields (verified: the failure diff shows the row carrying the receipt; every earlier assertion in that test — `result.ok`, one question event with `attestation: "mock"`, `waiting_on_ove == []`, no `answer_question` call — still passes). 113-03 (wave 3) extends the fake wrapper with `requestId` + answer-hold and flips the pin to the strictly stronger n4 contract (item raised under the stable id, answer piped with matching ids, item cleared, run completes), and the deselect disappears.

Additional spot-checks beyond the plan's gates (evidence for 113-03, nothing committed):

- **Row lifecycle through the real `run()`** (fake Anda `happy`, seeded row, `tasks_path`/`task_id` set): row ends `done` with `job_id`/`stage` stamped, `last_activity_at` present, `herdr_pane` correctly ABSENT (no herdr/tmux in this sandbox — the locked field shape), receipt keys exactly `["export", "job_id", "ok", "stage_id", "status"]` with `status="completed"`, `waiting_on_ove` empty, wrapper tools unchanged `["up", "run_gsd_stage", "sandbox_status", "export_commits", "down"]`. (`started_at` absent on a pre-seeded row is 113-01's pinned `start` semantics — it stamps only rows it creates.)
- **Question-helper contract smoke** (temp store, direct calls): stable id `gb:x:question:req-7`; raise blocks the row with ask/blocks verbatim; re-raise upserts (seq/since pinned, no duplicate); `_answer_from_store` returns None until a conforming `answer` appears (str or `{"selected": [...]}`; non-conforming values like `42` stay None); `_answer_args` maps str→`{"text": ...}`, mapping→`{"selected": [...]}`; `_clear_question` is idempotent and returns the row to `working`.

## Decisions Made

- As locked in the plan's `<assumption_delta_decision>`: tracker after the `up` reply (honest visibility + the tier/gate exact-row contracts); pane opened before `run_rows.start` so `herdr_pane` rides the row's creating write; time-gated store reads inside the one event loop (`_ANSWER_POLL = 1.0`, no second thread, no deadline); stale_answer sniffed from the detail string (no client surface change); requestId-less questions informational (keeps the un-flipped pin finite).
- `_touch_row` helper: one module-level suppress wrapper used by `_tool` and `_wait_stage_done` (3 call sites) instead of three inline copies — same contract (gate on `row_started`, suppress everything).
- `pane` local is annotated `str | None` inside the tracked block; `row_started = True` sits in the same suppress block as `run_rows.start`, immediately after it, so a failed start leaves the run un-finished.

## Deviations from Plan

None — plan executed exactly as written. (No CLAUDE.md conflicts; ownership resolves all touched paths to bot-00-omega-prime; no auth gates; no package installs.)

## Known Stubs

None. No stub patterns, no skipped tests, no unrun verifies. (The deselect excludes an EXISTING test from the run command per the plan; the test itself is untouched and its one moved assertion is the documented 113-03 cutover.)

## Notes for 113-03 (wave 3)

- Consume `delegate_via_engine(goal, *, env, cwd, interrupt, stage, engine_plan, tasks_path=None, task_id=None)` and `ultrathink_engine(plan, env, *, stage, engine_plan, tasks_path=None, task_id=None)` exactly as pinned; the callers (parent attributes / env) land in 113-03.
- The fake herdr runner must print the pane id as the last whitespace token of its last non-empty stdout line (`_parse_pane_id` contract) and the fake Anda `question` behavior must emit `requestId` and hold `stage_done` until `answer_question` (stale re-answer → `ok:false` code `stale_answer`, `attestation: "mock"` on every line).
- The answer convention for tests: `with locked_tasks(store, write=True) as data: item["answer"] = ...; _write_locked(data, store)`.
- After the pin flip, the four suites run with NO deselect and the pre-existing mcp failure remains the only red.

## Self-Check: PASSED

- Files exist: `omega_prime/engine/sandbox_omp.py`, `omega_prime/engine/omp_driver.py`, `omega_prime/engine/attach.py`, `113-02-SUMMARY.md`, `phase113-plan02-execution.json` (all FOUND on disk).
- Commits 20c885e / de3ef69 / 07a6cf7 / 38a8d28 / 7b1e85f are all HEAD ancestors (`git merge-base --is-ancestor`); `git status --porcelain` clean.
- No stubs, no deferred issues, no auth gates, no new dependencies (stdlib `sys`/`pathlib` only; dashboard imports are the existing engine→dashboard direction).
- Final acceptance command re-run at the implementation HEAD (38a8d28) before the summary commit: py_compile pass; 1 failed (pre-existing mcp), 78 passed, 1 deselected.
