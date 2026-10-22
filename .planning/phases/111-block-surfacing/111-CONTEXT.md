# Phase 111: Immediate block surfacing — Context

**Gathered:** 2026-10-21
**Status:** Planning complete (this stage); one execution plan (111-01) staged, nothing implemented.
**Milestone:** v21 Sandbox wiring (task 00030 node n5 — `U2` in node n7's file-disjoint build order, parallel-safe with U1/Phase 110 which is complete).
**Requirements:** W111-01..W111-05 (below; also in the ROADMAP v21 section).
**Contract:** `/sandbox/home/refs/uplift2.xml` (task 00030, node n5 steps 1–5 and acceptance; `<VISIBILITY><BLOCKS>`; `<GRACEFUL_DEGRADATION>`), `/sandbox/home/refs/got.json` (n5), `/sandbox/home/refs/AUDIT.md` (C10, C13), `/sandbox/home/refs/README-REFS.md` (box `locked_tasks` contract).
**Tree:** `feat/sbx-3-engine-backend` @ cbf1dd2 (sbx-2 merge + sbx-1 stage plumbing + folded standalone omp-sandbox ops; no `omega_prime/dashboard` package exists yet). Working tree clean before planning edits.

<goal>
## Goal

Every block that needs Ove — a blocked push, gh not logged in, an unanswered approval, `PlanNotAllowed` (Z.ai flag off the allowlist), or a sandbox tier being unavailable — writes exactly one `waiting_on_ove` item into tasks.json at the moment it happens, through one idempotent helper (`raise_block`/`clear_block` in `omega_prime/dashboard/blocks.py`) that also flips the owning task to `blocked` and back to `working`, with `ask`/`blocks` text passing the secret redaction. Audit C10 is the failure being fixed: a push sat blocked ~10 h with no item and no code path wrote one.
</goal>

<block_sources>
## Today's block sources (node n5 step 1, from the audit)

1. **Auto-review blocked push** — `push.sh` with `GITHUB_PAT_DESK_LEAD` is blocked by Auto-review; not retried, not routed around (AUDIT C13).
2. **gh not logged in** — `gh auth status`: not logged in on the box (AUDIT C13); every ship path that needs `gh` stalls.
3. **PlanNotAllowed** — Z.ai stages raise it while `OMEGA_PRIME_OMP_ALLOW_ZAI` is not truthy (AUDIT C1/C2; value never printed).
4. **TierUnavailable** — omp-sandbox TS (`providers/types.ts`) when a tier cannot start; the Python engine face of that gate is `SandboxUnavailable` → reason `sandbox_unavailable` (bwrap missing behaves the same way today).
5. **Approval card unanswered** — an approval left waiting has no `waiting_on_ove` item today.
</block_sources>

<requirements>
## Requirements (locked labels)

- **W111-01 store primitives.** This tree has NO dashboard store (verified: `locked_tasks`, `_write_locked`, `waiting_on_ove`, `tasks.json` — zero code hits on this branch; `docs/notion-task-dashboard.md` is an unrelated Notion template). Create `omega_prime/dashboard/status_file.py` implementing the box contract verbatim (README-REFS.md): `locked_tasks(path, write=False)` context manager — sidecar exclusive lock while `write=True`, yields the parsed JSON (missing file → `{"tasks": [], "waiting_on_ove": []}`) — plus `_write_locked(data, path)` atomic write-back called inside the `with` block. Task states: `queued, working, blocked, waiting_on_ove, done, failed`. Items: `{id, seq, ask, blocks, since}`.
- **W111-02 raise/clear helper.** `omega_prime/dashboard/blocks.py`: `raise_block(path, item_id, ask, blocks, task_id)` upserts one item keyed by stable `item_id` (`seq` allocated once as max+1, `since` pinned at first raise, repeats update `ask`/`blocks` only) and sets the owning task row state to `blocked`; `clear_block(path, item_id, task_id)` removes the item and returns the row to `working` (only a `blocked` row flips; `done`/`failed` rows are not resurrected).
- **W111-03 redaction.** `ask` and `blocks` pass through `redact_text` from `omega_prime/credentials/redact.py` (sk-/xai-/ghp-/Bearer/`TOKEN=value` patterns, `test_credentials.py:84-97` pins them) before anything is written. No argv of credentialed commands enters a row — the push hook takes a reason code plus short text, never argv. A secret-looking input string must never appear verbatim in tasks.json.
- **W111-04 engine failure wiring.** `omega_prime/engine/omp_driver.py:323-353` — the existing `except (EngineError, PlanNotAllowed, SandboxUnavailable, OSError)` — computes `reason` (`plan_not_allowed` at 333, `sandbox_unavailable` at 335). When the caller passed task context and `reason` is one of those two, the same call that returns the failed `EngineResult` must already have written one item (id `f"{task_id}:{reason}"`) and set the task `blocked`. No item for any other reason (`process`, `not_configured`, `invalid_mode`, …); with no task context, behaviour and `EngineResult` are byte-identical to today. The block write is best-effort (`contextlib.suppress`) and never masks the original failure.
- **W111-05 push-step hook + tests.** `push_block(path, task_id, reason, detail="")` with exactly three reasons — `auto_review_blocked`, `gh_not_logged_in`, `approval_unanswered` (`ValueError` otherwise) — writing one item with id `f"{task_id}:push"` through `raise_block`, cleared via `clear_block`. No executor push step exists in this repo (only `grokbot/pin_bump.py` ships `git push`, a different flow), so the hook IS the push-step integration point; the box executor calls it. Tests with a temp tasks.json: blocked push → exactly one item in one call; repeat idempotent; `PlanNotAllowed` through the real `run()` failure path → item + task `blocked` immediately; clear → item gone and task `working`; a secret-looking string never lands in the row.
</requirements>

<acceptance>
## Acceptance criteria (execution stage)

1. Simulated blocked push and `PlanNotAllowed` each produce exactly one `waiting_on_ove` item immediately — same call, no poll delay — and the owning task shows `blocked` (node n5 acceptance).
2. Resolution clears the item and returns the task to `working`.
3. Repeats are idempotent: same id, one item, `seq` and `since` unchanged.
4. Rows pass the dashboard redaction check: no token-shaped or argv text in tasks.json.
5. `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_dashboard_blocks.py` green, plus the touched engine suites still green; no existing test weakened, skipped, or deleted; fakes and `tmp_path` only.
</acceptance>

<source_map>
## Source map (verified at cbf1dd2 in this worktree, 2026-10-21)

- **Store absence.** `locked_tasks` / `_write_locked` / `waiting_on_ove` / `tasks.json`: 0 hits in this tree (rg over `omega_prime/`, `sidecar/`, `prime-agent/`, `scripts/`). `git log --all -S locked_tasks` — empty. The box checkout (`/workspace/omes-bot`, local branch) owns the live dashboard; README-REFS.md documents its contract: `with locked_tasks(path, write=True) as data:` + `_write_locked(data, path)`; states `queued, working, blocked, waiting_on_ove, done, failed`; items `{id, seq, ask, blocks, since}`. Phase 111 recreates that contract API-compatibly here so both trees read the same file.
- `omega_prime/engine/omp_driver.py` — `:275-289` `run()` signature (goal, env, cwd, plan, mode, stage, …); `:323-353` the failure catch: reason `plan_not_allowed` (333) / `sandbox_unavailable` (335) / `process` for OSError, returns `EngineResult(False, token, reason, detail, events_path, started)`; `:364-386` `_run` gate order: `resolve_omp` → `omp_version` → `sandbox_ready` → `select`; `:645-672` `probe()` (advisory: returns `(False, "plan_not_allowed"|"sandbox_unavailable")` without raising); `:675-679` `state_root_for(env)` → `state_dir` (`engine/jobs.py:36-41`: `OMEGA_PRIME_STATE_DIR` else `~/.omega-prime`).
- `omega_prime/engine/plan_router.py:53-56` — `PlanNotAllowed(detail)`; raised at `:98` (unknown stage), `:421-423` (requested plan off the allowlist), `:427-432` (no allowlisted plan for the stage). `omega_prime/engine/sandbox.py:20-23` — `SandboxUnavailable(detail)`.
- **TierUnavailable is TypeScript-only:** `omp-sandbox/src/core/providers/types.ts:28-30`; `tiers.ts:41` records its detail as a skip reason. The Python engine never sees that class — the engine face of the tier gate is `SandboxUnavailable`/reason `sandbox_unavailable` (omp_driver `:335`, `:667`). sbx-3's future backend maps omp-sandbox tier failures onto the same reason; Phase 111 keys on the reason strings and MUST NOT invent a Python `TierUnavailable`.
- `omega_prime/engine/attach.py:271-300` — `delegate_via_engine` treats a not-ready probe as fallback (returns None), `:312-335` `desk_engine_dispatch` maps engine errors to a `{"status": "blocked", "reason": …}` ticket. Neither raises `waiting_on_ove` items; wiring belongs in `run()`'s except, not in probe/attach.
- `omega_prime/credentials/redact.py:13-42` — `REDACTED`, `redact_text(text, extra=())` (sk-, xai-, ghp-, Bearer, assignment patterns), `redact_value`; reused by `engine/protocol.py:94-118` and `credentials/broker.py:52-54`. Pinned by `omega_prime/tests/test_credentials.py:84-97`.
- Engine test fixtures force failures without real binaries: `test_engine.py:244-248` (`PlanNotAllowed` from `select` with a plain env dict), `:322-330` (refused run → `reason == "sandbox_unavailable"`, fake `bwrap` at `tmp_path/bin/bwrap`), `:1145-1185` (stage-gated `PlanNotAllowed` messages); `test_engine_stage.py:124-141` (`probe` reasons). A `run()`-level wiring test needs the fake-omp + fake-bwrap PATH fixture pattern these files already use (to get past `resolve_omp`/`sandbox_ready` and reach `select`).
- Push step: no executor push step in this repo — `grokbot/pin_bump.py:2813,4395` are the only `git push` call sites (release-pin flow, not the task executor). The blocked-push executor and the hourly digest routine (concurrent tasks.json writer) live in the box deployment.
</source_map>

<environment>
## Execution environment facts (verified 2026-10-21)

- This planning sandbox has NO Python deps: `python3 -m pytest` → "No module named pytest"; no pip, no venv (the box's `/workspace/omes-bot/.venv` is not mounted here). The planning stage ran no Python tests; the executor stage runs `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/<file>` in its own environment (task rules: deps on PYTHONPATH; mcp may be missing so tests needing it may skip). `python3 -m py_compile` works here.
- herdr, gh, podman, openshell are NOT available here — fakes in tests only (task rule).
- Node 24 / omp-sandbox toolchain is irrelevant to this phase: no `omp-sandbox/` file is touched.
</environment>

<decisions>
## Locked scope and operator decisions

- Planning only in this stage: no product code, no commits under `omega_prime/`, no push/PR (outer driver exports commits as a git bundle). Execution happens in plan 111-01 later.
- One plan only: `111-01-PLAN.md`. No RESEARCH.md (research disabled in `.planning/config.json`), no VALIDATION.md (Nyquist needs research artifacts; not applicable this run), no discuss phase (`discuss_mode: skip`).
- The store is created here because this tree has none; the API mirrors the box contract exactly (same call shape, same states, same item keys) so the box tree and this repo converge on one tasks.json format rather than two.
- Engine wiring is opt-in via explicit `run(..., tasks_path=None, task_id=None)` parameters — explicit configuration, not env magic (the repo's W97-03 precedent); absent context ⇒ exactly today's behaviour. Item ids are deterministic: `f"{task_id}:{reason}"` (engine) and `f"{task_id}:push"` (push hook) — stability is what makes repeats upsert.
- The engine item's `ask`/`blocks` are static per-reason texts naming the gate and the fix; the box item shape has no detail field, so exception `detail` text is NOT copied into the item (it stays in `EngineResult.detail` where it already is).
- `clear_block` flips only `blocked` → `working`; `done`/`failed` rows are never resurrected by a late clear.
- Redaction reuses `redact_text`; no second dashboard-specific redactor is written.
- Tracker identifiers (SPE-18305, SPE-18331..SPE-18335, Notion URLs) live in commit trailers and the PR body only, never in committed planning guidance files.
- No secrets or token values anywhere; receipts must pass the dashboard redaction check.
</decisions>

<pitfalls>
## Pitfalls for the execution planner

- Do NOT invent a Python `TierUnavailable` class or import anything from `omp-sandbox/`; key the engine wiring on the reason strings `plan_not_allowed` / `sandbox_unavailable`.
- Do NOT raise items from `probe()` (advisory readiness — attach falls back instead of blocking) or from `delegate_via_engine`'s not-ready path; only `run()`'s except is a block event.
- The block write must never turn an engine failure into a different failure: wrap it in `contextlib.suppress(Exception)` and keep `EngineResult` construction exactly as is.
- Upsert discipline: match `id` across the whole `waiting_on_ove` list; a naive append duplicates on every retry — that is precisely audit C10's silence problem inverted into noise.
- Hold the sidecar lock for the whole read-modify-write (`_write_locked` inside the `with` block) and write atomically (temp file + `os.replace` in the same directory): the box digest routine writes the same file concurrently.
- Never widen the tasks.json schema: only `tasks` rows' `state` and the `waiting_on_ove` list change; unknown fields on rows and items pass through untouched.
- No argv, no env values in `ask`/`blocks`/`detail`; reason codes plus short static text; `redact_text` is the backstop, not permission.
- `pytest` cannot run in the planning sandbox — record baselines in the executor environment instead of "verifying" by import alone; still run `python3 -m py_compile` on new modules there before the suite if helpful.
</pitfalls>

<out_of_scope>
## Out of scope

- sbx-3 engine backend (node n3), sbx-4 per-run tasks.json rows/Herdr panes/question forwarding (node n4 — `locked_tasks` row lifecycle per run is n4 step 3, not this phase), sbx-5 e2e (node n7).
- Editing the box deployment's executor push step or dashboard readers — different tree; this repo ships the hook they call.
- Any `waiting_on_ove` reader/UI, the hourly digest routine, the Command Center terminal (task 00025).
- Setting `OMEGA_PRIME_OMP_ALLOW_ZAI`, gh login, push.sh approval, pushing/merging anything, pip-audit red on #53/#54/#55.
- Any change under `omp-sandbox/` and any product change in this planning stage.
</out_of_scope>

<tracker>
## Tracker

Task 00030 (Notion `400bc1a0…9b582`); node n5 Linear SPE-18305; relevant subissues SPE-18331 (step 1), SPE-18332 (step 2), SPE-18333 (step 3), SPE-18334 (step 4), SPE-18335 (step 5). Kept out of committed guidance files per repo rule; cited in commit trailers and the PR body by the parent.
</tracker>

<no_implementation>
## No implementation claim

This stage wrote planning artifacts only (`.planning/ROADMAP.md` v21 Phase 111 section, this context file, `111-01-PLAN.md`). No `omega_prime/` source, tests, or docs were modified; no push, PR, or remote was touched. Read-only evidence commands run: `git log/diff/status`, ripgrep source surveys (`locked_tasks`, `waiting_on_ove`, `TierUnavailable`, `redact`, `OMEGA_PRIME_STATE_DIR`), `python3 -m pytest` (absent here — recorded as an environment fact, not a test result).
</no_implementation>
