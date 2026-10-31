# Phase 114: Sandbox steering end-to-end (sbx-5) — Context

**Gathered:** 2026-10-21
**Status:** Planning complete (this stage); two execution plans (114-01/114-02) staged, nothing implemented.
**Milestone:** v21 Sandbox wiring (task 00030 node n7 — `U5`, the last unit in node n7's build order; U1 = Phase 110 ✅, U2 = Phase 111 ✅, U3 = Phase 112 ✅, U4 = Phase 113 ✅).
**Requirements:** W114-01..W114-05 (below; also in the ROADMAP v21 section).
**Contract:** `/sandbox/home/refs/uplift2.xml` (task 00030, node n7 steps 3–5 and acceptance; `<ENGINE_BACKEND>`, `<VISIBILITY>`, `<GRACEFUL_DEGRADATION>`, `<ACCEPTANCE_CRITERIA>` AC3/AC4/AC5/AC6), `/sandbox/home/refs/got.json` (n7), `/sandbox/home/refs/AUDIT.md` (C1..C13 baseline; the re-run target), `/sandbox/home/refs/README-REFS.md` (box facts, branch stack, store contract).
**Tree:** `feat/sbx-5-e2e` @ 19b7bb9 (phase 113 complete: sbx-3 backend + sbx-4 visibility shipped; branch carries everything from sbx-2..sbx-4). Working tree clean before planning edits.

<goal>
## Goal

One command from Omega Prime's own loop drives plan → execute in omp-sandbox on Z.ai and the whole chain is proven together (node n7 acceptance, AC3 + AC6): a hermetic e2e test starts a throwaway GSD project through the `ultrathink_turn` routine (or the delegate path), plans on `zai/glm-5.3`, builds on `zai/glm-5.3-flash`, exports the commits back into the job worktree, finishes its tasks.json row and records a herdr pane id; a forced `PlanNotAllowed` shows in `waiting_on_ove` immediately. An opt-in marker runs the same harness against the real `omp-sandbox-anda` binary on the host. `docs/omp-sandbox-engine.md` documents backend selection, env names (never values) and the visibility contract; `114-AUDIT-RERUN.md` re-runs audit C1..C13 expecting every FAIL flipped PASS except C13 (push path, blocked on Ove). Tests stay hermetic here: fake Anda wrapper, fake omp work, fake herdr runner, temp tasks.json — no herdr, podman, gh, openshell, network or model calls.
</goal>

<requirements>
## Requirements (locked labels)

- **W114-01 hermetic e2e happy path.** `omega_prime/tests/test_sandbox_e2e.py` does not exist today (`ls omega_prime/tests/` — no `test_sandbox_e2e*`). Create it as the only test file this phase touches: it imports the established fixtures (`_env`/`_install_fake` from `test_engine.py`, `_install_fake_anda`/`_backend_env`/`_wrapper_tools`/`_read_tasks` from `test_engine_omp_sandbox.py` — cross-test-module imports are the established pattern, `test_engine_omp_sandbox.py` already imports from `test_engine`) and edits NO existing suite. The e2e drives `execute_ultrathink(env)` (the registered `ultrathink_turn` routine entrypoint) — or `delegate_task` if the plan documents why the ultrathink path cannot carry both stages — through two consecutive stages (`plan` then `execute`) against the fake Anda wrapper, in a temp workspace that is a seeded git repo under the allowlisted roots (the throwaway GSD project). The fake stage work creates GSD-shaped project files and commits them (the existing fake already does real `git` + `bundle` work via its `export_commit` helper, `test_engine_omp_sandbox.py:79-117`), so the test asserts: wrapper receipts show `run_gsd_stage` with model `zai/glm-5.3` (plan) and `zai/glm-5.3-flash` (execute); the exported commits land back in the job worktree (`git -C <work> log` grows, the created files exist); the tasks.json row finishes `done` with a receipt; the row records the `herdr_pane` id the fake herdr printed; the engine results report `sandbox="omp-sandbox"` with the per-stage model ids.
- **W114-02 forced block e2e.** Same file: with `OMEGA_PRIME_OMP_ALLOW_ZAI` absent from the run env, the same routine path returns `plan_not_allowed` and exactly one `waiting_on_ove` item exists in the temp store when the call returns (immediate — the phase-111 wiring raises in the same `run()` failure call, `omp_driver.py:376-394`), the owning row `blocked`; the item's `ask`/`blocks` text is redacted (no env value, no token-shaped text). Clearing the item is phase-111 behaviour, already covered — not re-proven here.
- **W114-03 real-binary opt-in.** Same file: a marker/env (exact name pinned by the plan — suggested shape `OMEGA_PRIME_SANDBOX_E2E_REAL=1`) gates a test that runs the same harness against the real `omp-sandbox-anda` binary on the host (located via the existing `OMEGA_PRIME_OMP_SANDBOX_ANDA` override or PATH resolution, `sandbox_omp.py:89-103`); without the marker the test `pytest.skip`s with a reason naming the marker. The opt-in path never prints consent/API env values (presence/truthiness only) and must never execute in this sandbox (herdr/gh/podman absent; the marker stays unset here).
- **W114-04 engine docs page.** `docs/omp-sandbox-engine.md` (new) documents: backend selection (`OMEGA_PRIME_OMP_BACKEND=omp-sandbox`, bwrap the unchanged default), every env name the backend and visibility wiring read — `OMEGA_PRIME_OMP_BACKEND`, `OMEGA_PRIME_OMP_SANDBOX_ANDA`, `OMP_SANDBOX_WORKSPACE_ROOTS`, `OMEGA_PRIME_OMP_ALLOW_ZAI`, `OMEGA_PRIME_TASKS_PATH`, `OMEGA_PRIME_TASK_ID`, `OMEGA_PRIME_ENGINE_TMUX` and the routine stage knobs — names and semantics only, NEVER values; stage→model routing for requested Z.ai (`plan` → `zai/glm-5.3`, `execute` → `zai/glm-5.3-flash`); the workspace allowlist; the result/export contract; and the visibility contract (pane label `"{task} {stage}"`, row lifecycle, question round-trip, `waiting_on_ove` blocks). The page joins `docs/SUMMARY.md` (the parity test requires every `docs/*.md` page linked exactly once, `test_docs.py:248-257`) and passes the docs suites (top-level heading `test_docs.py:143`, resolving relative links `:156`, fenced commands naming real modules `:198`).
- **W114-05 audit re-run table.** `.planning/phases/114-sbx5-e2e/114-AUDIT-RERUN.md` (new) lists C1..C13 with one runnable command per criterion and the expected result after phases 110-113 + this phase: PASS everywhere except the C13 push path (stays blocked until Ove unblocks gh login / push.sh — `AUDIT.md:24`, blockers list `:28-31`). Each row also names where the live proof lives: this phase's e2e (C3-per-run, C4, C7, C8, C9, C10), the phase 110-113 suites, or host-only evidence (C1/C2 box-env probes, C5 live stage, C6 mount smoke, C11 suites, C12 PR health, C13 push). The read-only refs under `/sandbox/home/refs/` stay untouched; the table's rows pass the dashboard redaction check (no env values, no argv of credentialed commands).
</requirements>

<acceptance>
## Acceptance criteria (execution stage)

1. `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_sandbox_e2e.py` green hermetically: one routine-path command drives plan on `zai/glm-5.3` then execute on `zai/glm-5.3-flash` through the backend, exported commits land in the job worktree, and the run is visible throughout (row `working`→`done` with `last_activity_at`, `herdr_pane` recorded) — node n7 acceptance, AC3 + AC4 shape.
2. A forced `PlanNotAllowed` run produces exactly one `waiting_on_ove` item in the same call with the owning row `blocked` (AC5 shape at the routine level); no poll delay.
3. Without the opt-in marker the real-binary test skips with a reason naming the marker; the marker stays unset in this sandbox; no consent/API env value is printed, logged or committed anywhere.
4. `docs/omp-sandbox-engine.md` passes the docs suites and documents backend selection, env names without values, and the visibility contract; `docs/SUMMARY.md` links it exactly once.
5. `114-AUDIT-RERUN.md` covers C1..C13 with runnable commands and expected results (all PASS except C13); `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_docs.py omega_prime/tests/test_public_repo.py` stays green; no existing test weakened, skipped or deleted.
</acceptance>

<source_map>
## Source map (verified at 19b7bb9 in this worktree, 2026-10-21)

**The chain the e2e drives (all shipped in phases 111-113):**
- `omega_prime/routines/ultrathink_turn.py` — `resolve_turn_plan` :56-118 (probes `<state>/ultrathink/last-plan.json` when `OMEGA_PRIME_STATE_DIR` is set, :27-29); `execute_ultrathink(env)` :134-153 reads `OMEGA_PRIME_TASKS_PATH`/`OMEGA_PRIME_TASK_ID` from the mapping and calls `_engine_annotation(plan, mapping, tasks_path=…, task_id=…)`; `main --state-dir` :156+ threads the same reads from `os.environ`. The e2e seeds a temp state dir with `last-plan.json` + spec file so the routine resolves a found plan.
- `omega_prime/engine/attach.py` — `ultrathink_engine(plan, env, *, stage=None, engine_plan=None, tasks_path=None, task_id=None)` :391-452: stage precedence `stage` arg → `plan["stage"]` → `OMEGA_PRIME_ENGINE_STAGE` (engine_stage :118); `engine_mode(env)` :22 gates ("off" → `{"used": False}`); `probe(env, stage=staged)` must be ready (`omp_driver.probe` :722); then `run("Carry out this ultrathink spec.…", env=…, system_prompt=…, stage=staged, plan=engine_plan, tasks_path=…, task_id=…)`. `delegate_via_engine` :272-315 carries the same keyword pair. So the e2e sets the per-call stage via the last-plan.json `stage` field or `OMEGA_PRIME_ENGINE_STAGE` — no product change needed.
- `omega_prime/engine/omp_driver.py` — `run(..., tasks_path=None, task_id=None, stage=None, interrupt=None)` :297-313; failure path raises the `waiting_on_ove` item in the same call for reasons `plan_not_allowed`/`sandbox_unavailable` when task context is present :376-394 (`_ENGINE_ASK`/`_ENGINE_BLOCKS` :54-70); `_run` :405-450 branches to `sandbox_omp.run_backend` at :435-450 WITH the task context threaded (phase 113).
- `omega_prime/engine/sandbox_omp.py` — `run_backend(prompt, *, job_id, directory, cwd, system_prompt, thinking, plan, mode, env, binary, omp_major, stage, interrupt, tasks_path=None, task_id=None)` :467-495; admission order: `allowed_worktree` (roots from `OMP_SANDBOX_WORKSPACE_ROOTS`, :66/:116/:122) → `_seed_ref` → `read_usage`/`select` (PlanNotAllowed raises PRE-spawn) → `invalid_stage` refusal → `anda_binary` (`OMEGA_PRIME_OMP_SANDBOX_ANDA` or PATH, :65/:89-103); `tracked = tasks_path and task_id` switches on pane/row/question wiring; `export_commits` runs after a completed stage :645-666 and the export dict rides the result/stats; backend selected only by `OMEGA_PRIME_OMP_BACKEND=omp-sandbox` (:63, `selected` :104).
- `omega_prime/engine/plan_router.py` — `_STAGE_MODELS` :44 (`zai`: plan `glm-5.3`, execute `glm-5.3-flash`); `select` :393 (requested plan + stage → model); Z.ai requires `flag(env, "OMEGA_PRIME_OMP_ALLOW_ZAI")` truthy else `PlanNotAllowed` :133-147; `read_usage` :492.
- `omega_prime/dashboard/` — `status_file.py` (`locked_tasks`/`_write_locked`, task states, unknown-field passthrough), `blocks.py` (`raise_block`/`clear_block`, the only block writer), `herdr_launch.py` (`open_pane` — herdr create/run, pane-id parse, tmux fallback), `run_rows.py` (`start`/`touch`/`finish` row lifecycle). The e2e only READS the store and the fake herdr log.

**Fixtures to reuse (no edits to these files):**
- `omega_prime/tests/test_engine_omp_sandbox.py` — `_ANDA_FAKE` :42-223 (behaviors `happy`/`tier_error`/`question`/`question_reask`/`hang`; one receipt line per request in `wrapper-log.jsonl`; every emitted line `"attestation": "mock"`; `export_commit` :79-117 makes a real commit + git bundle in the seeded worktree); `_install_fake_anda(tmp_path, behavior)` :231-245 (`OMEGA_PRIME_OMP_SANDBOX_ANDA` override); `_wrapper_tools` :263-264; `_read_tasks`/`_row`/`_seed` :267-329 (temp store helpers); `_backend_env` :297-314 (auto-installs the fail-mode herdr stub, so every backend test is hermetic); `_install_fake_herdr` (ok/fail modes, `herdr-log.jsonl` argv receipts, pane id = last whitespace token of the last non-empty line).
- `omega_prime/tests/test_engine.py` — `_env`/`_install_fake` :154-183 (tmp bin dir FIRST on PATH, `OMEGA_PRIME_ENGINE_TMUX="0"` base) — the shadowing discipline the e2e inherits by import.

**Docs constraints (verified):**
- `omega_prime/tests/test_docs.py` — every `docs/*.md` needs a top-level heading :143-154; relative links/images/anchors must resolve :156; fenced shell commands must name real modules/scripts :198+; `docs/SUMMARY.md` must link every docs page exactly once :248-257. New page ⇒ SUMMARY.md edit in the same plan.

**Environment:**
- This sandbox: herdr, gh, podman, openshell NOT available (contract rule); git present (worktree fixtures rely on it); pytest invocation `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/<file>` from the repo root. Baseline at 19b7bb9: engine suites green except the pre-existing `test_engine_stage.py::test_mcp_desk_parent_reads_the_stage_from_its_env` (ModuleNotFoundError: mcp — recorded in 112-CONTEXT, unchanged); docs/public-repo suites 31 passed.
</source_map>

<environment>
## Execution environment facts (verified 2026-10-21)

- The real-binary opt-in path (W114-03) CANNOT run here: herdr, gh, podman and openshell are absent, the box's real wrapper is host-side, and the contract forbids touching live containers. The marker stays unset in this sandbox; the plan's verify commands prove the skip path, not the live path (recorded as host-only evidence in the audit table).
- `omp-sandbox/` stays untouched this phase (no TypeScript changes; `npx tsc --noEmit`/`npm test` courtesy check only if the tree is somehow touched there — it is not planned to be).
- Python tests run from the repo root; `PYTHONPATH=.` breaks pytest here (phase 112/113 recorded the same quirk).
- No product code in this phase's plans: the e2e proves existing behaviour; if it exposes a product gap, the finding is recorded in the plan SUMMARY under "Blocked" (or a follow-up phase), NOT patched inside this phase's file set.
</environment>

<decisions>
## Locked scope and operator decisions

- Planning only in this stage: no product code, no commits under `omega_prime/` or `omp-sandbox/`, no push/PR (outer driver exports commits as a git bundle). Execution happens in plans 114-01/114-02 later.
- Exactly TWO file-disjoint plans: **114-01 owns `docs/omp-sandbox-engine.md` + `docs/SUMMARY.md` + `.planning/phases/114-sbx5-e2e/114-AUDIT-RERUN.md`** (W114-04, W114-05); **114-02 owns `omega_prime/tests/test_sandbox_e2e.py`** (W114-01..W114-03). Waves: 114-01 (wave 1) → 114-02 (wave 2). No plan edits another plan's files; 114-02 edits NO existing test file (imports only), so "never weaken existing tests" is structurally guaranteed.
- The e2e's primary path is `execute_ultrathink` (the registered routine); the delegate path is the documented fallback if (and only if) the ultrathink path cannot carry both stages — the plan pins which and why. No new product entrypoint is created.
- The throwaway GSD project is a temp seeded git repo under the allowlisted roots; the fake stage work (fake omp behaviour) creates GSD-shaped files and commits them so `export_commits` has real content to bundle back. The e2e carries its own fake-behaviour variant inside `test_sandbox_e2e.py` (self-contained fixture) rather than editing `_ANDA_FAKE` in the existing suite — existing behaviors stay byte-identical.
- The opt-in real-binary marker name is pinned by the plan (suggested `OMEGA_PRIME_SANDBOX_E2E_REAL`); the binary is located via the existing `OMEGA_PRIME_OMP_SANDBOX_ANDA`/PATH resolution. Without the marker: `pytest.skip` naming the marker.
- The audit re-run table lives in the phase directory (`.planning/phases/114-sbx5-e2e/114-AUDIT-RERUN.md`), NOT under `/sandbox/home/refs/` (read-only) and NOT in `docs/` (task evidence, not product docs). C13 stays FAIL/blocked until Ove; the table says so explicitly.
- Env values are never printed, logged or committed: docs and the audit table name envs and semantics only; consent/API values are presence/truthiness-only statements (uplift CONSTRAINTS).
- Z.ai stays a per-run explicit requested plan; the ChatGPT-first default is untouched; `OMEGA_PRIME_OMP_ALLOW_ZAI` is set only inside test env dicts (the documented in-process override, AUDIT.md C1/C2 precedent).
- Tracker identifiers (task 00030, node n7 ids) live in commit trailers and the PR body only, never in committed planning guidance files (repo rule).
- Commit title for this stage: `00015 - plan phase 114 sandbox e2e`.
</decisions>

<pitfalls>
## Pitfalls for the execution planner

- Do NOT edit any existing test file to build the e2e: import the fixtures (`_env`, `_install_fake`, `_install_fake_anda`, `_backend_env`, `_wrapper_tools`, `_read_tasks`, `_install_fake_herdr`); a self-contained fake behaviour for the two-stage GSD project lives in the new file. Editing existing suites risks the no-weakening rule for zero benefit.
- Do NOT let the e2e spawn anything real: no herdr, tmux (the `_env` base pins `OMEGA_PRIME_ENGINE_TMUX="0"`), podman, gh, openshell, network or model calls; the real-binary test is marker-gated and skipped here — its body must still never print env values when it DOES run on the host.
- The routine path blocks synchronously: `ultrathink_engine` → `run` → `run_backend` waits for `stage_done` with NO deadline (phase-112 lock). The e2e's fake completes promptly; any bounded wait lives in the TEST, never the engine.
- Two stages = two `run_backend` runs = two rows/panes only if the test uses two task rows; one task row across both stages is fine (run_rows re-start moves a non-terminal row back to working). The plan pins which and asserts the row timeline accordingly.
- Stage selection: `ultrathink_engine` precedence is stage arg → `plan["stage"]` → `OMEGA_PRIME_ENGINE_STAGE`; `execute_ultrathink` passes no stage arg, so the e2e sets the stage via the seeded `last-plan.json` `stage` field (or the env). Get this wrong and both stages run as the same stage — the model-id assertions catch it.
- `engine_mode(env)` must not be "off" and `probe(env, stage=…)` must be ready before the routine reaches `run` — reuse `_env`/`_backend_env` shapes from the existing suites, which already satisfy both.
- Docs page: `docs/SUMMARY.md` parity is a hard test (`test_docs.py:248-257`) — forgetting the SUMMARY link fails the suite; every relative link must resolve; fenced commands must name real modules (e.g. `python3 -m pytest … omega_prime/tests/test_sandbox_e2e.py`).
- The audit table's commands must be runnable and must not embed secrets or credentialed argv; expected results must match what phases 110-113 actually shipped (e.g. C3 is per-run reachable, not a default change; C13 blocked).
- Never print `OMEGA_PRIME_OMP_ALLOW_ZAI`/`ZAI_API_KEY` values or credentialed argv anywhere in tests, docs, the audit table, receipts or the SUMMARY — dashboard redaction is the backstop, not permission.
</pitfalls>

<out_of_scope>
## Out of scope

- Any product-code change under `omega_prime/` or `omp-sandbox/` (the e2e proves shipped behaviour; gaps are recorded, not patched, in this phase's file set).
- Running the real-binary path in this sandbox; touching the live 00025 sandbox or its containers; gh login, push.sh, pushing/merging anything (C13 stays blocked pending Ove).
- Setting consent flags on the box, Railway or any env change; changing the ChatGPT-first stage default.
- The hourly digest routine and the 00025 Command Center live terminal (uplift ALREADY_HANDLED).
- Phase-111 block clearing, phase-113 question round-trip re-proofs beyond what the e2e naturally exercises; dashboard readers/UI changes.
- Re-running the full audit on the box (host-side C1/C2/C5/C6/C12 evidence is named in the table, not fabricated).
</out_of_scope>

<tracker>
## Tracker

Task 00030 ("Audit Omega Prime loop and finish omp-sandbox wiring", Notion), node n7 (Linear issue + step subissues per the uplift ISSUES block). Identifiers and URLs are cited in commit trailers and the PR body by the parent; they are deliberately not written into this committed guidance file (repo rule).
</tracker>

<no_implementation>
## No implementation claim

This stage wrote planning artifacts only (`.planning/ROADMAP.md` v21 Phase 114 section, this context file, `114-01-PLAN.md`, `114-02-PLAN.md`). No `omega_prime/` or `omp-sandbox/` source, tests, or docs were modified; no push, PR, or remote was touched. Read-only evidence commands run here: `git status/log/branch`, ripgrep/grep source surveys (env names, signatures, docs test rules, receipt/ownership conventions), and targeted file reads at 19b7bb9 (engine: sandbox_omp.py, omp_driver.py, attach.py, plan_router.py, ultrathink_turn.py, delegate.py; dashboard: status_file.py, blocks.py, herdr_launch.py, run_rows.py; tests: test_engine_omp_sandbox.py, test_engine.py, test_docs.py; contract refs in /sandbox/home/refs).
</no_implementation>
