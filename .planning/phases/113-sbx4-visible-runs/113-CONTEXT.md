# Phase 113: Sandbox runs visible in Herdr and tasks.json (sbx-4) — Context

**Gathered:** 2026-10-21
**Status:** Planning complete (this stage); three execution plans (113-01/113-02/113-03) staged, nothing implemented.
**Milestone:** v21 Sandbox wiring (task 00030 node n4 — `U4` in node n7's file-disjoint build order; U1 = Phase 110 ✅, U2 = Phase 111 ✅, U3 = Phase 112 ✅).
**Requirements:** W113-01..W113-05 (below; also in the ROADMAP v21 section).
**Contract:** `/sandbox/home/refs/uplift2.xml` (task 00030, node n4 steps 1–5 and acceptance; `<VISIBILITY>` HERDR/TASKS_JSON/BLOCKS; `<ACCEPTANCE_CRITERIA>` AC4 + AC5), `/sandbox/home/refs/got.json` (n4), `/sandbox/home/refs/AUDIT.md` (C8 Herdr pane FAIL, C9 tasks.json row FAIL, C10 block/question surfacing), `/sandbox/home/refs/README-REFS.md` (box facts; herdr present on the box but NOT in this sandbox — fakes only).
**Tree:** `feat/sbx-4-routines-visible` @ 6c88fe2 (phase 112 complete: sbx-3 backend `omega_prime/engine/sandbox_omp.py` merged, `omega_prime/dashboard/blocks.py` phase-111 helper merged). Working tree clean before planning edits.

<goal>
## Goal

Routine- and tool-launched sandbox runs are visible and answerable (audit C8 FAIL → PASS, C9 FAIL → PASS, C10's question half → PASS): a run through the sbx-3 backend opens one Herdr pane labelled with the task number and stage, writes one live tasks.json row (working on start, `last_activity_at` on each event, done/failed with a receipt on finish, `herdr_pane` recorded), and forwards GSD follow-up questions to `waiting_on_ove` with the answer piped back through the wrapper's `answer_question` into the same live session. `delegate_task` and the ultrathink_turn routine reach the backend with task context when it is selected. Tests use a temp tasks.json, a fake herdr runner and the fake Anda wrapper — no herdr, podman, gh, network or model calls.
</goal>

<requirements>
## Requirements (locked labels)

- **W113-01 Herdr pane launcher (dashboard).** `omega_prime/dashboard/herdr_launch.py` does not exist today (rg "herdr" over `omega_prime/` + `omp-sandbox/`: zero product hits; only a test docstring mentions it). Create it: `open_pane(env, *, cwd, label, command) -> str | None` resolves the herdr binary (`shutil.which`), runs `herdr workspace create --cwd <cwd> --label <label> --no-focus`, parses the pane id from stdout (last whitespace-separated token of the last non-empty line), then `herdr pane run <pane> <command…>`; any herdr failure or a missing binary falls back to the engine's tmux pattern (`omega_prime/engine/jobs.py` `maybe_tmux` :77-125: session `omega-<job>`, `python -m omega_prime.engine.watch <job> --follow --state-dir <state>`, skip when `OMEGA_PRIME_ENGINE_TMUX` is falsy) and returns the tmux session as `"tmux:<session>"`; both failing returns None and NEVER raises into the run. The pane command for sandbox runs is the same watch invocation against the run's job id. Tests drive a fake herdr runner from PATH (a script writing a pane id and argv receipts), never the real binary.
- **W113-02 tasks.json row per run (dashboard).** `omega_prime/dashboard/run_rows.py` (new) owns the row lifecycle, every write through `locked_tasks(path, write=True)` + `_write_locked` (`omega_prime/dashboard/status_file.py` :46-95; box contract, README-REFS): `start(path, task_id, *, job_id, stage, pane)` creates the row when missing (or moves a non-terminal row back to `working`) with `herdr_pane`, `stage`, `job_id`, `started_at`, `last_activity_at` (ISO-8601 UTC); `touch(path, task_id, event)` stamps `last_activity_at` and (for `question`/`stage_done`-shaped events) a short `last_event` label — one locked read-modify-write per call, unknown fields on other rows untouched; `finish(path, task_id, ok, receipt)` sets `done`/`failed` with a redacted receipt `{job_id, stage_id, ok, status, export}` (export = the `stats["omp_sandbox"]["export"]` summary shape from phase 112; secrets excluded, `redact_text` backstop). Terminal rows (`done`/`failed`) are never resurrected by a later `start`.
- **W113-03 question round-trip (dashboard + engine).** On a `question` wrapper event mid-stage the engine raises exactly one `waiting_on_ove` item through the phase-111 helper `raise_block(path, f"{task_id}:question:{request_id}", ask=<redacted question text>, blocks="sandbox GSD question (stage <stage>)", task_id=task_id)` (`omega_prime/dashboard/blocks.py` :38-74; the ONLY block writer — no second writer). The stage wait then polls the store (bounded tick, same magnitude as the backend's `_POLL_TICK` 0.2 s; answer poll at ≤1 s cadence) for an `answer` field on that item (the dashboard-side answer convention this phase defines: the responder adds `answer` — a text string or `{selected: [...]}` — to the item); when present it calls the wrapper's `answer_question` with `{stage_id, request_id, text|selected}` (`omp-sandbox/src/core/server.ts` :44; stale answers surface as code `stale_answer` → treated as a fresh ask, item re-raised), then `clear_block(path, item_id, task_id)` (:77-92) and the stage wait continues. Row state during a question: `blocked` (raise_block's semantics, phase-111 locked); back to `working` on clear.
- **W113-04 product-caller routing (engine + agent + routine).** `omp_driver.run` already accepts `tasks_path`/`task_id` (:309-310) but `_run` does not pass them into `sandbox_omp.run_backend` and `run_backend` has no task params — thread them through. `delegate_via_engine` (`omega_prime/engine/attach.py` :271-315) and `ultrathink_engine` (:379-439) gain keyword-only `tasks_path`/`task_id` threaded into `run(...)`. `omega_prime/agent/delegate.py` :51-63 reads the parent's `engine_tasks_path`/`engine_task_id`/`engine_plan`/`engine_cwd` attributes (the established getattr convention — `route_turn` already reads `agent.engine_plan`/`agent.engine_cwd`, attach.py :171-176) and passes them; `omega_prime/routines/ultrathink_turn.py` `execute_ultrathink` (:130-153) reads `OMEGA_PRIME_TASKS_PATH` + `OMEGA_PRIME_TASK_ID` from its env and threads them (plus the existing stage machinery) into `ultrathink_engine`. All new parameters default to None/absent: without task context the runs behave exactly as today (no row, no pane, no item — the phase-112 contract), and the bwrap path is untouched.
- **W113-05 fake-visible tests.** `omega_prime/tests/test_engine_omp_sandbox.py` `_ANDA_FAKE` "question" behavior (:183-189) currently emits `{"event":"question","stageId",question}` with NO `requestId` and completes without waiting — extend it to emit `requestId` (real wrapper shape: `e["requestId"]`, e2e.py :104; client.rs/server.ts) and hold `stage_done` until an `answer_question` call arrives (stale re-answer → `ok:false` code `stale_answer`, matching server.ts :59). New/updated tests with a temp tasks.json and a fake herdr runner: row working→done with `last_activity_at` advancing and `herdr_pane` recorded; pane label `"{task_id} {stage}"` and `herdr pane run` argv pinned; question → exactly one item → write `answer` → `answer_question` called with the right `{stage_id, request_id}` → item cleared → run completes; tmux fallback when the herdr binary is absent (fake tmux or skip-gated); no secret-shaped value in rows/receipts/events (byte-scan pattern from :726-765); `test_question_surfaces_as_event_only` (:768-795) — the phase-112 pin that asserted the n4 boundary ("No waiting_on_ove item, no answer_question round-trip (node n4)") — is updated to assert the n4 contract with STRONGER assertions (item raised then cleared, `answer_question` in `_wrapper_tools(log)` with correct ids). Documented cutover in the SUMMARY, not a weakening.
</requirements>

<acceptance>
## Acceptance criteria (execution stage)

1. A routine-launched sandbox run (backend selected, task context present) shows a Herdr pane labelled `"{task_id} {stage}"` and a tasks.json row that moves `working` → `done`/`failed` within one poll of each event (node n4 acceptance; AC4).
2. A GSD question appears in `waiting_on_ove` immediately (same event tick) and its answer resumes the run: `answer_question` fires into the same live session with the matching `stage_id`/`request_id`, the item clears, the row returns to `working`, and the stage completes (node n4 acceptance; AC5).
3. When herdr is missing, the tmux fallback still shows progress and the row still tracks; a pane/tmux failure never fails the run itself.
4. With `OMEGA_PRIME_OMP_BACKEND` unset, every existing engine suite passes unchanged (bwrap default byte-identical); without task context the backend path behaves exactly as phase 112 shipped it.
5. All new tests green via `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/<files>` with fakes only; no existing test weakened, skipped or deleted (the one n4-boundary pin flips to the stronger n4 contract, documented).
</acceptance>

<source_map>
## Source map (verified at 6c88fe2 in this worktree, 2026-10-21)

**Engine (Python):**
- `omega_prime/engine/sandbox_omp.py` — the sbx-3 backend this phase wires visibility into: `run_backend(...)` :459-640 (params: prompt, job_id, directory, cwd, system_prompt, thinking, plan, mode, env, binary, omp_major, stage, interrupt — NO task params yet); flow: `allowed_worktree` :495 → `_seed_ref` :498 → `read_usage`/`select` :499-500 (PlanNotAllowed pre-spawn) → pre-spawn `session.json` :519-534 → `AndaClient` :535 → `up` (seed_repo) :542-548 → `run_gsd_stage` :549-562 → `started=True` :563 → `_wait_stage_done` :567 → `sandbox_status` :569-571 → `export_commits` (completed only) :572-574 → finally `down` + `_append_down_event` :586-590 → frozen `EngineResult` + `stats["omp_sandbox"]` :594-640. `_tool` :643-656 appends drained events via `append_event`; `_wait_stage_done` :659-698 is the no-deadline event loop (`client.next_event(_POLL_TICK)` :676, buffered drains :677-678, interrupt→`abort` :692-698) — the natural hook points for row touch + question handling. `AndaClient.call` :266-314 sends any tool incl. `answer_question`; `next_event` :316-341; `drain_events` :343-346. Constants: `BACKEND_ENV`/`ANDA_BIN_ENV`/`WORKSPACE_ROOTS_ENV` :59-64, `_POLL_TICK = 0.2` :75.
- `omega_prime/engine/omp_driver.py` — `run(..., tasks_path=None, task_id=None, stage=None, interrupt=None)` :297-313 (task context accepted; failure path raises the waiting_on_ove item :376-394 via `raise_block` with `_ENGINE_ASK`/`_ENGINE_BLOCKS` :54-70); `_run` :405-450 branches to `sandbox_omp.run_backend` at :435-450 WITHOUT passing tasks_path/task_id — the threading gap; bwrap path calls `maybe_tmux(job_id, env, enabled=tmux)` :514 (backend path never does — pane launch is net-new there).
- `omega_prime/engine/attach.py` — `delegate_via_engine(goal, *, env, cwd, interrupt, stage, engine_plan)` :271-315 (probe→`run(goal, env, cwd, interrupt, stage, plan)` :297-299 — no tasks_path/task_id); `ultrathink_engine(plan, env, *, stage, engine_plan)` :379-439 (spec file read → `run("Carry out this ultrathink spec.…", …)` :415-421 — no task context); `route_turn` :129-186 reads `agent.engine_plan`/`engine_cwd`/`engine_job_id` via getattr :171-176 — the attribute convention W113-04 reuses.
- `omega_prime/agent/delegate.py` — `delegate_task` :19-78; engine-first branch :51-63 calls `delegate_via_engine(goals[0], interrupt=…, stage=child_stage)` only; `_child_stage` :81-97 (staged parent → children get `execute`).
- `omega_prime/routines/ultrathink_turn.py` — `resolve_turn_plan` :56-118 (probes last-plan.json; `{found, plan_path, spec_path, …}`); `_engine_annotation` :121-127 → `ultrathink_engine(plan, env)`; `execute_ultrathink(env)` :130-153 — the routine entrypoint the XML registers (`routines/ultrathink.md`); `main` :156-175.
- `omega_prime/engine/jobs.py` — `append_event` :60-66 (redacting, the only events write path); `maybe_tmux` :77-125 (tmux fallback pattern: `omega-<job>` session running `python -m omega_prime.engine.watch <token> --follow --state-dir <state>`; `OMEGA_PRIME_ENGINE_TMUX=0` skips; missing binary skips; returns session name or None).
- `omega_prime/engine/watch.py` — `omega watch <job>`: `tail`/`format_event` :17-92 — the pane command herdr runs for sandbox runs (events.jsonl lives in the job dir for backend runs too, sandbox_omp :511-513).

**Dashboard (Python):**
- `omega_prime/dashboard/status_file.py` — `TASK_STATES` :25 (`queued, working, blocked, waiting_on_ove, done, failed`), `ITEM_FIELDS` :26 (`id, seq, ask, blocks, since`), `locked_tasks(path, write=False)` :46-66 (sidecar flock), `_write_locked(data, path)` :69-95 (atomic temp+replace). Unknown fields pass through untouched (:22-24) — `herdr_pane`, `last_activity_at`, `receipt`, `answer` are additive.
- `omega_prime/dashboard/blocks.py` — `raise_block(path, item_id, ask, blocks, task_id)` :38-74 (upsert by stable id, seq allocated once, since pinned, owning row → `blocked`, `redact_text` on ask/blocks); `clear_block(path, item_id, task_id)` :77-92 (idempotent, row → `working`, terminal rows untouched); `push_block` :95-108. The only block writer (phase-111 decision) — W113-03 consumes it, never adds a second.
- `omega_prime/dashboard/herdr_launch.py` — DOES NOT EXIST (verified: rg "herdr" → only a test docstring). W113-01 creates it.

**omp-sandbox (read-only reference for the wrapper contract):**
- `src/core/server.ts` — op dispatch :41-51; `answer_question` args `{stage_id, request_id, selected?, text?, per_question?}` :44; question events unsolicited :4; `StaleAnswerError` code `stale_answer` (:59 per docs/ARCHITECTURE.md :57-60: an `extension_ui_request` becomes a `question` event; `answer_question` sends the `extension_ui_response`; stale/unknown/cancelled → `stale_answer`).
- `crates/omp-sandbox-anda/src/tools.rs` — TOOLS :11-21 incl. `answer_question` ("Answer a pending omp ask-dialog question (stale answers are rejected)").
- `tests/e2e.py` :103-123 — the reference round-trip: on `event == "question"` call `answer_question` with `{stage_id, request_id}` (+ `selected`/`text`); a repeat answer goes stale.

**Tests (fixtures to reuse/extend):**
- `omega_prime/tests/test_engine_omp_sandbox.py` — `_ANDA_FAKE` :42-223 (behavior.json-selected; "question" :183-189 emits the question event then `stage_done` immediately, NO `requestId`, never waits); `_install_fake_anda(tmp_path, behavior)` :231-245 (chmod +x, `OMEGA_PRIME_OMP_SANDBOX_ANDA` override, `wrapper-log.jsonl` receipts); `_wrapper_tools(log)` :263-264; `_read_tasks`/`_row`/`_seed` :267-329 (temp tasks.json helpers); `_backend_env` :297-314; `_git_worktree` :281-294; secret byte-scan pattern :726-765; the n4-boundary pin `test_question_surfaces_as_event_only` :768-795.
- `omega_prime/tests/test_dashboard_blocks.py` — store/block test shapes (`_seed`, `_row`, flock exclusivity, unknown-field roundtrip :98-132) — the template for run_rows tests.
</source_map>

<environment>
## Execution environment facts (verified 2026-10-21)

- This sandbox: herdr, gh, podman, openshell are NOT available (contract rule) — every pane test uses a fake herdr runner script on a tmp PATH (same discipline as the fake omp / fake bwrap / fake Anda in `test_engine.py` / `test_engine_omp_sandbox.py`); tmux-fallback tests use a fake tmux or assert the None path.
- Python: `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/<file>` from the repo root (cwd puts `omega_prime` on sys.path here; the `PYTHONPATH=.` prefix breaks pytest in this sandbox — executor envs may differ, phase 112 recorded the same quirk). Baselines at 6c88fe2: docs/public-repo suites and the engine suites green (one pre-existing `mcp`-import skip/failure in `test_engine_stage.py`, recorded in 112-CONTEXT).
- `git` binary present (worktree fixtures already rely on it).
- No TypeScript changes in this phase: `omp-sandbox/` is untouched (the fake wrapper is a Python script the tests write); `cd omp-sandbox && npx tsc --noEmit && npm test` stays green untouched — run it once as a no-regression courtesy check only if the tree is touched there (it is not).
</environment>

<decisions>
## Locked scope and operator decisions

- Planning only in this stage: no product code, no commits under `omega_prime/` or `omp-sandbox/`, no push/PR (outer driver exports commits as a git bundle). Execution happens in plans 113-01/02/03 later.
- Exactly THREE file-disjoint plans: **113-01 owns `omega_prime/dashboard/**`** (herdr_launch.py, run_rows.py, `__init__.py` exports); **113-02 owns `omega_prime/engine/**`** (sandbox_omp.py, omp_driver.py, attach.py); **113-03 owns `omega_prime/agent/delegate.py` + `omega_prime/routines/ultrathink_turn.py` + `omega_prime/tests/**`**. Waves: 113-01 (wave 1) → 113-02 (wave 2) → 113-03 (wave 3); each later wave consumes the earlier wave's imported API but no plan edits another plan's files.
- Dependency direction stays engine→dashboard (omp_driver already imports dashboard.blocks): the dashboard modules NEVER import from `omega_prime/engine/**` — the pane command string is passed in by the engine caller, and herdr_launch's tmux fallback re-implements the session-spawn shape rather than importing `maybe_tmux` (or accepts it as an injected callable — 113-01 picks the boring one: re-implement, ~15 lines, keeps the layer clean).
- One block writer: question items go through `blocks.raise_block`/`clear_block` with stable id `f"{task_id}:question:{request_id}"`; row state during a question is `blocked` (raise_block semantics, phase-111 locked); no new writer, no new task states.
- The answer convention this phase defines: the responder adds an `answer` field (text string or `{selected: [...]}`) to the waiting_on_ove item; the engine polls the locked store, pipes it via `answer_question`, clears the item. Additive field — `status_file` passes unknown fields through by design.
- `herdr_pane` on the row holds the herdr pane id; when tmux backs the pane it holds `"tmux:<session>"`; when neither opened, the key is absent. Dashboard readers treat it as opaque.
- Pane label: `f"{task_id} {stage}"` (uplift VISIBILITY: "000NN stage"). The pane command is the existing watch invocation (`python -m omega_prime.engine.watch <job_id> --follow --state-dir <state>`); no new streamer is built.
- Pane id parse contract (herdr CLI is absent everywhere testable, so this phase pins it): last whitespace-separated token of the last non-empty stdout line of `herdr workspace create`; the fake herdr runner follows it. A create/run failure or unparseable output falls back to tmux — never raises into the run.
- Task context carriers: parent attributes `engine_tasks_path`/`engine_task_id` (delegate, alongside the existing `engine_stage`/`engine_plan`/`engine_cwd` getattr convention) and env `OMEGA_PRIME_TASKS_PATH`/`OMEGA_PRIME_TASK_ID` (ultrathink routine — it has no parent object). Two explicit knobs, documented; nothing is inferred.
- Z.ai stays a per-run explicit requested plan (`engine_plan`), threaded from the same attributes/env; the ChatGPT-first stage default is untouched; `OMEGA_PRIME_OMP_ALLOW_ZAI`/`ZAI_API_KEY` values are never printed, logged or stored (presence/truthiness only).
- The sbx-5 e2e (real herdr, real wrapper, real models, audit re-run) is node n7 — out of scope here.
- Tracker identifiers (task 00030, node n4 Linear ids and Notion URLs) live in commit trailers and the PR body only, never in committed planning guidance files.
- No secrets or token values anywhere; receipts and rows must pass the dashboard redaction check.
</decisions>

<pitfalls>
## Pitfalls for the execution planner

- Do NOT add a second block writer, a second events writer, or a second redactor: rows/items go through `locked_tasks` + `_write_locked` (+ `redact_text`), question items through `raise_block`/`clear_block`, events through `append_event`. The dashboard redaction check is the backstop, not permission.
- Do NOT let visibility failures kill runs: every pane/row/item write is best-effort with the same suppress discipline as `omp_driver.run`'s raise_block wiring (:385 `contextlib.suppress(Exception)`); a missing herdr, an unwritable tasks.json or a corrupt store must never turn a healthy sandbox stage into a failure.
- Do NOT change the frozen `EngineResult`, `run()`'s existing parameters/except behaviour, or the bwrap path: task-context threading is additive keyword-only parameters defaulting to None; with them absent the backend behaves exactly as phase 112 shipped.
- The stage wait has NO deadline (phase-112 lock): the answer poll rides the existing event-loop ticks; never add a wall-clock cap on the question wait — liveness is the event stream plus the row's `last_activity_at`.
- One question at a time per stage is the wrapper's contract (server.ts stale_answer); the item id embeds `request_id` so a re-ask after a stale answer re-raises under a new id rather than duplicating.
- The fake wrapper's question behavior MUST gain `requestId` and hold `stage_done` until answered — without that the round-trip is untestable; keep the `attestation: "mock"` stamp on every emitted line (client.rs contract).
- `test_question_surfaces_as_event_only` is the deliberate n4 boundary pin from phase 112: updating it is the planned cutover, and its replacement must assert MORE (item raised with stable id, answer piped with matching ids, item cleared, run completes), never less. No other existing assertion may weaken.
- Keep `omega_prime/dashboard/**` free of engine imports (layer direction); the tmux fallback must not import `maybe_tmux` from engine — copy the shape.
- herdr argv appears in fake-runner receipts: never put secret-shaped values in labels or commands; the label is `f"{task_id} {stage}"` only.
- `OMP_SANDBOX_STATE`/env-capture discipline and "never touch the live 00025 containers" carry over from phase 112 unchanged.
</pitfalls>

<out_of_scope>
## Out of scope

- sbx-5 (node n7): the live e2e (real herdr, real Anda wrapper, real models), export verification against a live worktree, the audit re-run C1..C13.
- The hourly Session progress digest routine and the 00025 Command Center live terminal (uplift ALREADY_HANDLED).
- Any dashboard reader/UI/collector changes (rows are written for the existing dashboard to read).
- Setting `OMEGA_PRIME_OMP_ALLOW_ZAI`, `ZAI_API_KEY`, gh login, push.sh approval, pushing/merging anything.
- Any change under `omp-sandbox/` (the wrapper is consumed as-is; the fake wrapper is test code).
- Phase-111 store/block helpers beyond consuming them; `route_turn`'s attach-turn path (n4 names delegate_task and ultrathink_turn only).
- New task states, new waiting_on_ove item fields beyond the additive `answer`, any EngineResult field change.
</out_of_scope>

<tracker>
## Tracker

Task 00030 ("Audit Omega Prime loop and finish omp-sandbox wiring", Notion), node n4 (Linear issue + five step subissues, SPE-18326..SPE-18330 range per the uplift ISSUES block). Identifiers and URLs are cited in commit trailers and the PR body by the parent; they are deliberately not written into this committed guidance file (repo rule).
</tracker>

<no_implementation>
## No implementation claim

This stage wrote planning artifacts only (`.planning/ROADMAP.md` v21 Phase 113 section, this context file, `113-01-PLAN.md`, `113-02-PLAN.md`, `113-03-PLAN.md`). No `omega_prime/` or `omp-sandbox/` source, tests, or docs were modified; no push, PR, or remote was touched. Read-only evidence commands run here: `git status/log`, ripgrep source surveys (herdr, last_activity_at, answer_question, run_backend, maybe_tmux, raise_block/clear_block/push_block, tasks.json), and targeted file reads at 6c88fe2 (engine: sandbox_omp.py, omp_driver.py, attach.py, jobs.py, watch.py, delegate.py, ultrathink_turn.py; dashboard: status_file.py, blocks.py, __init__.py; tests: test_engine_omp_sandbox.py, test_dashboard_blocks.py; contract refs in /sandbox/home/refs).
</no_implementation>
