# 114-02 SUMMARY — sandbox e2e (test_sandbox_e2e.py)

**Status:** complete. One new file (`omega_prime/tests/test_sandbox_e2e.py`); no existing file edited, no product code touched, nothing pushed.

## Verification (run in this sandbox, 2026-10-21)

- Full file: `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_sandbox_e2e.py` → **2 passed, 1 skipped**; skip reason names `OMEGA_PRIME_SANDBOX_E2E_REAL` ("host-only: set OMEGA_PRIME_SANDBOX_E2E_REAL=1 to run the real omp-sandbox-anda binary").
- `-k real` (audit C5): **1 skipped, 2 deselected**, same named reason.
- Neighbour sweep (audit C3 context): full file + `test_engine.py` + `test_engine_omp_sandbox.py` + `test_dashboard_blocks.py` + `test_engine_stage.py` with the single documented deselect (`test_engine_stage.py::test_mcp_desk_parent_reads_the_stage_from_its_env`) → **98 passed, 1 skipped, 1 deselected**. The one skip is this file's marker gate.
- Negative redaction grep on the new file (consent/credential NAME=value shapes): clean. Env overrides ride runtime-built dicts; the Z.ai token is `_shaped_zai_key()` output, byte-scanned out of every durable artifact in the gate test.

## Pinned decisions (executed as planned)

- **Routine path:** `execute_ultrathink(env)` once per stage; the stage rides the freshly seeded `<state>/ultrathink/last-plan.json` `"stage"` field; the worktree is the process cwd (`monkeypatch.chdir(work)`) because `ultrathink_engine` threads no cwd and `run_backend` admits `Path.cwd()` (`sandbox_omp.py:515-517`).
- **Two task rows, one per stage** (`gb:sbx5-plan`, `gb:sbx5-exec`): a shared row would freeze between stages (`run_rows.start` never resurrects a terminal row; `finish` overwrites the receipt), so each stage keeps its own pane/job/stage/receipt timeline.
- **Self-contained two-stage fake** (`_E2E_ANDA_FAKE`, installed under `tmp_path/"anda-e2e"` via `OMEGA_PRIME_OMP_SANDBOX_ANDA`): receipt-per-request, `attestation: "mock"` on every emitted line, id-matched replies, down-then-exit; `run_gsd_stage` does real GSD-shaped git work in the seeded worktree (plan → `.planning/phases/999-demo/PLAN.md` + `gsd(plan): demo plan`; execute → `src/demo/greeting.txt` + `gsd(execute): demo greeting`); `export_commits` makes a real bundle. `_ANDA_FAKE` untouched.
- **Marker:** `OMEGA_PRIME_SANDBOX_E2E_REAL` (module constant `_REAL_MARKER`); skip reasons name it; the real test double-gates (marker AND wrapper resolution, each with its own named skip) and reads consent/API envs presence-only.

## W114-02: the two-layer proof and the drift note

Shipped code cannot surface the plan gate as a `waiting_on_ove` item through the routine call itself: `ultrathink_engine` probes first (`attach.py:416-418`) and the probe's `PlanNotAllowed` short-circuits into a clean `{"used": False, "reason": "plan_not_allowed"}` (`_required_miss`) — `run()`'s same-call `raise_block` wiring (`omp_driver.py:378-396`) is never reached on this path. The test therefore proves both layers honestly: (1) `execute_ultrathink` refuses with zero writes (store still has no items, row still `working`); (2) the exact engine call the routine wraps (`run("Carry out this ultrathink spec.\n\n"+spec, system_prompt="Ultrathink turn. Stay inside the spec.", stage="plan", tasks_path, task_id`, no cwd kwarg) refuses pre-spawn and raises **exactly one** item `gb:sbx5-gate:plan_not_allowed` in the same returning call, row `blocked`, canary wrapper log empty, token absent from every scanned artifact.

**Product finding (recorded, not patched):** the routine layer never reaches the block wiring — a `plan_not_allowed` from the loop's own entrypoint is silent at the visibility layer. If the routine should surface the gate as an item, that is a product change (`omega_prime/routines/ultrathink_turn.py` / `omega_prime/engine/attach.py`), out of this phase's file set.

## W114-03: real-binary PASS is host-only evidence

The opt-in test is complete (minimal env, no fake overrides, `omp`/wrapper resolution skips, engine-side assertions only: annotation stage/model/plan, rows done with export receipts, worktree git growth, `session.json` sandbox `omp-sandbox`), structurally cannot run here (marker unset; no herdr/podman/gh), and carries **no test-side deadline** on the stage wait. The real-binary PASS has NOT been run in this sandbox; it is named host-only evidence for audit C5, never claimed as executed.

## Commits

- T1 harness: fixtures, two-stage fake, seeding, marker-gated skip
- T2 routine-driven plan→execute e2e (W114-01)
- T3 two-layer forced gate + redaction (W114-02)
- T4 sweep/keyword-shape fix keeping the mandated redaction grep clean (W114-03 body landed with the T1 commit; the sweep is T4's verify)

All commit subjects `00015 - …` with `Refs: 00030` / `Node: n7 steps 3,4` trailers; `git status --porcelain` clean after each. Tracker identifiers appear in trailers only.
