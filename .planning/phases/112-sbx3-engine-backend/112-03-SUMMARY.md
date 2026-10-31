# Phase 112 Plan 03: Fake-Anda Engine Backend Tests Summary

**Status:** complete
**Phase:** 112-sbx3-engine-backend / **Plan:** 03
**Requirement:** W112-05
**Node:** task 00030 node n3 step 6
**Branch:** feat/sbx-3-engine-backend (no push, no PR, no remote)

One-liner: `omega_prime/tests/test_engine_omp_sandbox.py` writes a protocol-faithful fake Anda
wrapper (wrapper_ready first, id-matched replies, unsolicited events, `attestation: mock`,
down-then-exit) into `tmp_path` with its behaviour in a sibling `behavior.json` — never argv —
and drives the real `omp_driver.run()` through the whole backend contract: plan→execute ordering
with the zai model pair on one worktree, real git commits + bundle landing back and reported in
`stats["omp_sandbox"]["export"]`, tier-unavailable and plan-not-allowed each raising exactly one
`waiting_on_ove` item through run()'s phase-111 wiring, interrupt-driven abort to `cancelled`,
allowlist refusal before any spawn, byte-unchanged bwrap default, secret-free artifacts, and
questions surfacing as events only.

## Per-task commits

| Task | Commit | Subject |
|------|--------|---------|
| T1 | 9386b4b | 00013 - tests: fake Anda wrapper and fixtures for the omp-sandbox backend (W112-05) |
| T2 | 00f70e3 | 00013 - tests: run contract — ordering, results back, tier and plan gates, abort, attach pass-through (W112-05) |
| T3 | 8db3346 | 00013 - tests: guards — allowlist before spawn, bwrap default, no secrets, questions as events (W112-05) |

Only `omega_prime/tests/test_engine_omp_sandbox.py` was ever staged: one new file, zero edits to
existing tests or fixtures, no conftest change, no new dependency. `git diff` over the three
commits for `test_engine.py`, `test_engine_stage.py`, `test_dashboard_blocks.py`,
`omega_prime/engine/**` and `omp-sandbox/**` is empty.

## New tests (exact names, all green)

`test_engine_omp_sandbox.py` — 11 tests:
`test_plan_then_execute_orders_two_stages_on_one_worktree`,
`test_export_lands_back_in_the_worktree_and_is_reported`,
`test_tier_unavailable_raises_exactly_one_sandbox_item`,
`test_plan_not_allowed_raises_one_item_without_spawning`,
`test_interrupt_aborts_the_stage_and_returns_cancelled`,
`test_delegate_via_engine_passes_engine_plan_to_the_wrapper`,
`test_ultrathink_engine_passes_engine_plan_to_the_wrapper`,
`test_allowlist_refusal_precedes_any_wrapper_spawn`,
`test_bwrap_default_untouched_when_backend_unset`,
`test_no_secret_shaped_value_lands_in_any_durable_artifact`,
`test_question_surfaces_as_event_only`.

## Verification (executor environment, this sandbox)

- T1 gate: module imports cleanly; the generated wrapper script compiles standalone (`py_compile`);
  a throwaway AndaClient smoke spoke the full protocol (ready → up → run_gsd_stage → stage_done →
  sandbox_status → export_commits → down; receipts `["up", "run_gsd_stage", "sandbox_status",
  "export_commits", "down"]`).
- Pytest runs UNPREFIXED from the repo root (`python3 -m pytest -q -p no:cacheprovider ...`);
  the plan's `PYTHONPATH=.` prefix breaks pytest in this sandbox ("No module named pytest"),
  per the task brief's environment facts.
- Final combined run (new module + the three existing engine suites, completely unmodified):

```
FAILED omega_prime/tests/test_engine_stage.py::test_mcp_desk_parent_reads_the_stage_from_its_env - ModuleNotFoundError: No module named 'mcp'
1 failed, 79 passed in 6.39s
```

Identical to the pre-change baseline plus this plan's 11 tests: the one pre-existing `mcp`-import
failure (optional dep absent, documented in 112-CONTEXT; not ours, not fixed) and 68 + 11 = 79
green. Zero new failures, zero skips, no existing test touched.

## Per-contract evidence

**Ordering (plan→execute on one job/worktree).** Wrapper receipts from a real run (one log, two
wrapper spawns, truncated for width):

```
{"id": "2", "tool": "run_gsd_stage", "args": {"stage": "plan",    "plan": "zai", "model": "zai/glm-5.3",       "gsd_command": "gsd-plan-phase",    "task": "plan the thing", ...
{"id": "2", "tool": "run_gsd_stage", "args": {"stage": "execute", "plan": "zai", "model": "zai/glm-5.3-flash", "gsd_command": "gsd-execute-phase", "task": "build it", ...
```

Both spawns receipted `up` with `seed_repo {path: <same worktree>, ref: "main"}` and observed the
engine-forwarded `OMP_SANDBOX_WORKSPACE_ROOTS` value (`workspace_roots` on the ready row equals
the tmp root the test set). `EngineResult` per stage: `sandbox="omp-sandbox"`, `plan="zai"`,
stages `("plan", "glm-5.3")` then `("execute", "glm-5.3-flash")`, `fallback_from=None`,
`final_text="fake final text"`, `commands == ["up", "run_gsd_stage", "sandbox_status",
"export_commits", "down"]`. Wrapper-originated events in events.jsonl all carry
`"attestation": "mock"`.

**Results land back (export head match).** The fake's `export_commits` makes a real commit and
bundle inside the job worktree; `stats["omp_sandbox"]["export"]` carries
`{head, base, ref, commits, dirty, bundle, bytes, via}` with
`export["head"] == git rev-parse HEAD` measured in the same worktree right after the run
(verified true for both stages in the evidence capture and pinned by
`test_export_lands_back_in_the_worktree_and_is_reported`: head/base equality, `ref="main"`,
`commits=1`, `dirty=False`, `bytes == bundle size > 0`, and `fake export` visible in
`git log --format=%s`).

**The two one-item block assertions (through run()'s real wiring, no test-side writer).**

- Tier unavailable (`up` → `ok:false anda_error`): `reason="sandbox_unavailable"`,
  `waiting_on_ove` ids `== ["gb:tier:sandbox_unavailable"]`,
  `blocks == "engine sandbox gate (sandbox_unavailable)"`, task row `blocked`; the run stopped at
  the refusal (`receipt tools == ["up", "down"]`); a retried run upserts — one item, same
  `seq`/`since`.
- Plan not allowed (zai off the allowlist, no other allowlisted plan — `ANTHROPIC_API_KEY`
  popped): `reason="plan_not_allowed"`, ids `== ["gb:gate:plan_not_allowed"]`,
  `blocks == "engine plan gate (plan_not_allowed)"`, row blocked — and the wrapper was **never
  started**: the canary log file does not exist; the retry upserts the same single item.

**Abort path.** With the `hang` behaviour (no `stage_done` after `run_gsd_stage`), a
`threading.Timer(0.8)` sets the real `InterruptFlag` while `run()` blocks in `_wait_stage_done`.
Result: `ok=False, reason="cancelled", started=True`; receipts prove the tool sequence
`["up", "run_gsd_stage", "abort", "down"]` with `abort args == {"stage_id": <live stageId>}`;
the `stage_done status="aborted"` event (matching stageId) is visible in events.jsonl, followed by
the `job_end ok=false reason=cancelled` line. No deadline was invented — liveness is the
wrapper's event stream plus the explicit abort.

**Attach pass-through (W112-03 keyword, proven end-to-end).**

- `delegate_via_engine("plan the thing", env=<backend env>, cwd=<worktree>, stage="plan",
  engine_plan="zai")` → JSON summary `{"engine": "omp", "stage": "plan", "model": "glm-5.3",
  "summary": "fake final text", ...}` while the wrapper log's single `run_gsd_stage` carries
  `{stage "plan", plan "zai", model "zai/glm-5.3", gsd_command "gsd-plan-phase", task "plan the
  thing"}` — the same env passed `probe()`'s backend readiness gate (the call could not have
  reached `run` otherwise).
- `ultrathink_engine({"found": True, "spec_path": <tmp spec>}, env, stage="plan",
  engine_plan="zai")` → `{"used": True, "stage": "plan", "plan": "zai", "model": "glm-5.3",
  "final_text": "fake final text"}` with the same wrapper-log shape and the spec text inside
  `task` (ultrathink runs on the process cwd, so the test chdirs into the allowlisted worktree).

**Guards.**

- Allowlist before spawn: a real git worktree created outside `OMP_SANDBOX_WORKSPACE_ROOTS` →
  `ok=False, reason="sandbox_unavailable"`, detail contains `path_refused`, and the canary fake
  left no log file — the refusal precedes any spawn.
- bwrap unchanged when the env is unset: fake bwrap installed (`test_engine.py:328-337` pattern),
  `OMEGA_PRIME_ENGINE_UNSANDBOXED="0"`, **no** `OMEGA_PRIME_OMP_BACKEND`, the Anda override set as
  a canary → `ok=True, sandbox="bwrap"`, bwrap log shows `--ro-bind`/`--unshare-pid`, Anda log
  absent. The combined rerun of the three existing suites (above) is the hard regression gate.
- No secret anywhere: a runtime-built (string-concatenated) `ZAI_API_KEY`-shaped value rides the
  env through a full happy-path run with task context; a recursive byte-scan over every file under
  the tmp root — the job state dir (events.jsonl, session.json), tasks.json, the wrapper's
  receipts, everything — finds zero hits (failure output prints paths only, never contents or the
  value). The tests never assert or print the values of `OMEGA_PRIME_OMP_ALLOW_ZAI` /
  `ZAI_API_KEY`; truthy values are set and only behaviour is asserted.
- Questions as events: the `question` behaviour's unsolicited event appears in events.jsonl
  (`attestation: mock`) with `waiting_on_ove == []`, the task row still `working`, and no
  `answer_question` receipt (that forwarding is node n4, out of scope).

## Scope and prohibitions honoured

- Fakes and `tmp_path` only: no podman, no openshell, no herdr, no gh, no network, no model
  calls, no Rust/cargo build, no live container access. Real git runs only inside tmp dirs (the
  fake's `export_commits` uses `git -C <worktree>` with a minimal env).
- The behaviour selector is never on argv: the fake reads `behavior.json` beside itself; its
  receipts log `argv == ["client"]`.
- Every behavioural assertion goes through the real `omp_driver.run()` (and its real phase-111
  block wiring); the fake replaces only the wrapper binary. No monkeypatched engine internals —
  the one process-level fixture is `monkeypatch.chdir` into the tmp worktree for the ultrathink
  entry point (which has no cwd parameter).
- `test_engine.py`'s `_env`/`_install_fake` are imported (the established cross-module pattern of
  `test_dashboard_blocks.py:26-27`); `_row`/`_seed` are local 4-line copies, since only
  `_env`/`_install_fake` are the shared fixture surface.

## Known stubs

None. The fake wrapper is test-local generated scaffolding inside `tmp_path` by design (the Rust
wrapper is deliberately never built here); every production path the tests exercise is the real
engine.
