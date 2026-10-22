---
phase: 112-sbx3-engine-backend
subsystem: omp-sandbox engine backend (sbx-3)
tags: [engine, omp-sandbox, sandbox_omp, plan_router, tests, node-n3]
requires: [110 (sbx-2 merge finish), 111 (waiting_on_ove blocks)]
provides:
  - omega_prime/engine/sandbox_omp.py — stdlib-only Anda stdio client + backend run loop
  - OMEGA_PRIME_OMP_BACKEND=omp-sandbox selection (bwrap stays the byte-identical default)
  - engine_plan keyword on delegate_via_engine / ultrathink_engine
  - omp-sandbox folded-ops suite (tests/ops.test.ts) + the test-only attachTierForTests seed seam
  - omega_prime/tests/test_engine_omp_sandbox.py — 11 fake-Anda tests through the real run()
affects: []
tech-stack:
  added: []
  patterns:
    - id-matched stdio reply matching with unsolicited-event buffering (thread + queue)
    - allowlist admission before any spawn, re-validated by the TS core (defense in depth)
key-files:
  created:
    - omega_prime/engine/sandbox_omp.py
    - omega_prime/tests/test_engine_omp_sandbox.py
    - omp-sandbox/tests/ops.test.ts
  modified:
    - omega_prime/engine/omp_driver.py (_run + probe backend branches)
    - omega_prime/engine/attach.py (engine_plan keyword)
    - omp-sandbox/src/core/core.ts (test-only seed seam ONLY)
    - omp-sandbox/tests/fake-omp.mjs (prompt-log content ONLY)
    - omp-sandbox/package.json (test script registration ONLY)
decisions:
  - backend selected only by OMEGA_PRIME_OMP_BACKEND=omp-sandbox; any other value or unset keeps bwrap byte-identical (uplift SELECTION)
  - up() seeds with seed_repo from the allowlisted worktree so export_commits has a repo (results come back, audit C7)
  - keyword named engine_plan (ultrathink_engine's plan positional is the spec mapping; attach.py:174 precedent)
status: complete
---

# Phase 112: omp-sandbox engine backend (sbx-3) — Summary

Task 00030 node n3. The engine can now run omp stages inside omp-sandbox (audit C4 FAIL → PASS)
and get the results back (C7 FAIL → PASS), with Ove's per-run Z.ai choice reachable from the
loop's entry points (C3 FAIL → reachable-by-request; the ChatGPT-first stage default stays
unchanged per clarification c3).

## Plans executed (wave 1 parallel, wave 2 after 112-02)

| Plan | Scope | Tasks | Commits | Result |
| ---- | ----- | ----- | ------- | ------ |
| 112-01 | omp-sandbox/** folded-ops coverage (W112-01) | 3/3 | 77cf274, f434b76, da27928 (+ summary 25af193) | complete |
| 112-02 | omega_prime/engine/** backend (W112-02..04) | 4/4 | 478eaea, f25565f, 4932c07, a21b853 (+ summary 67208a0) | complete |
| 112-03 | omega_prime/tests/test_engine_omp_sandbox.py (W112-05) | 3/3 | 9386b4b, 00f70e3, 8db3346 (+ summary 2523971) | complete |
| parent | retention harness root-cause fix | 1 | 97c2877 | complete |

Plus this summary/ROADMAP/receipt commit. 18 commits total, all local, no push/PR/remote.

## New tests → AUDIT capability map

Every new test, with the read-only-audit capability (AUDIT.md C1..C13) it proves:

### omp-sandbox/tests/ops.test.ts (8 tests, run by `npm test`)

| Test | Proves |
| ---- | ------ |
| omp-sandbox/tests/ops.test.ts::seed_repo ships a real git bundle through the seam and checks the ref out in the mapped workdir | C4 — a sandbox run's worktree seeding really lands (bundle in, ref checked out) |
| omp-sandbox/tests/ops.test.ts::seed_repo through the seam refuses paths outside the workspace roots before any git runs | C4 — workspace-allowlist guard on the sandbox side |
| omp-sandbox/tests/ops.test.ts::export_commits returns a size-verified bundle for the seeded repo and refuses unseeded cores | C7 — the result path (bundle out, byte-verified) exists and refuses honestly without a seed |
| omp-sandbox/tests/ops.test.ts::run_check gates argv and cwd, runs confined in the workdir, and receipts every run at 0600 | C4 — in-sandbox run_check the backend exposes; receipts stay redaction-safe (0600, fixed fields) |
| omp-sandbox/tests/ops.test.ts::py_deps stages importable stubs and wires PYTHONPATH through the sandbox env file | C4 — python stages run inside the sandbox (deps staged, import check passes) |
| omp-sandbox/tests/ops.test.ts::runStage routes plan=zai to the zai model with no fallback and sends the exact GSD prompt | C3 + C4 — Z.ai plans: `zai/glm-5.3`, stageRoute zai primary (no fallback), byte-exact `/{gsdCommand} orchestrate workflowz ultrathink\n\n{task}` prompt |
| omp-sandbox/tests/ops.test.ts::runStage routes execute=zai to glm-5.3-flash with no fallback | C3 + C4 — Z.ai builds: `zai/glm-5.3-flash` |
| omp-sandbox/tests/ops.test.ts::runStage keeps the chatgpt default carrying the zai fallback; an explicit model wins | C3 — the ChatGPT-first default is unchanged; Z.ai is an explicit per-run choice (clarification c3) |

### omega_prime/tests/test_engine_omp_sandbox.py (11 tests, fake Anda wrapper, real run())

| Test | Proves |
| ---- | ------ |
| omega_prime/tests/test_engine_omp_sandbox.py::test_plan_then_execute_orders_two_stages_on_one_worktree | C4 + C3 — one engine `run` per stage drives plan (`zai/glm-5.3`) then execute (`zai/glm-5.3-flash`) inside omp-sandbox on one job (node n3 acceptance) |
| omega_prime/tests/test_engine_omp_sandbox.py::test_export_lands_back_in_the_worktree_and_is_reported | C7 — exported commits land in the job worktree and are reported under stats["omp_sandbox"]["export"] |
| omega_prime/tests/test_engine_omp_sandbox.py::test_tier_unavailable_raises_exactly_one_sandbox_item | C4 — graceful degradation: tier failure → `sandbox_unavailable`, exactly one waiting_on_ove item, task blocked (no silent fallback) |
| omega_prime/tests/test_engine_omp_sandbox.py::test_plan_not_allowed_raises_one_item_without_spawning | C3 — the Z.ai consent gate is enforced (PlanNotAllowed → `plan_not_allowed`, one item, zero wrapper spawns) |
| omega_prime/tests/test_engine_omp_sandbox.py::test_interrupt_aborts_the_stage_and_returns_cancelled | C4 — a launched run aborts cleanly (abort tool → stage_done aborted → reason `cancelled`) |
| omega_prime/tests/test_engine_omp_sandbox.py::test_delegate_via_engine_passes_engine_plan_to_the_wrapper | C3 + C4 — the loop's delegate entry point can request Z.ai; probe readiness is proven end-to-end |
| omega_prime/tests/test_engine_omp_sandbox.py::test_ultrathink_engine_passes_engine_plan_to_the_wrapper | C3 + C4 — the ultrathink entry point can request Z.ai (Ove's "Z.ai plans and builds" choice reachable per-run) |
| omega_prime/tests/test_engine_omp_sandbox.py::test_allowlist_refusal_precedes_any_wrapper_spawn | C4 — a worktree outside OMP_SANDBOX_WORKSPACE_ROOTS is refused (`path_refused`) before any process starts |
| omega_prime/tests/test_engine_omp_sandbox.py::test_bwrap_default_untouched_when_backend_unset | C4 — backend selection is purely additive: env unset ⇒ bwrap wrap path, Anda canary never executed |
| omega_prime/tests/test_engine_omp_sandbox.py::test_no_secret_shaped_value_lands_in_any_durable_artifact | C7 — results come back redaction-clean (ZAI_API_KEY-shaped value absent from events.jsonl, session.json, tasks.json, wrapper receipts) |
| omega_prime/tests/test_engine_omp_sandbox.py::test_question_surfaces_as_event_only | C4 — wrapper questions stream as events with no premature block item (answer_question forwarding is node n4, out of scope) |

## Verification (all run at final state, parent-executed)

- `cd omp-sandbox && npx tsc --noEmit` → **exit 0**; `npm test` → **27 pass / 0 fail / 0 skipped**
  (19 pre-existing unmodified + 8 new ops tests).
- `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/ -k "engine or stage or sandbox or
  plan_router" --continue-on-collection-errors` → **1 failed, 109 passed, 1 skipped**.
  The single failure is the pre-existing
  `test_engine_stage.py::test_mcp_desk_parent_reads_the_stage_from_its_env`
  (ModuleNotFoundError: No module named 'mcp' — optional dep absent in this sandbox; identical at
  baseline before any change). Baseline comparison: 96 passed / 3 failed → 109 passed / 1 failed
  (+2 retention tests fixed at root cause, +11 new tests).
- Existing tests unchanged: `git diff 97c2877..HEAD --stat` over core.test.ts, paths.test.ts,
  guards.test.ts, test_engine.py, test_engine_stage.py, test_dashboard_blocks.py is **empty**;
  `omega_prime/engine/sandbox.py` diff is **empty** (bwrap byte-identical); EngineResult fields
  unchanged; no new dependencies (stdlib only on the Python side).
- `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_engine_omp_sandbox.py` →
  **11 passed**.
- git status clean; no remotes touched; no podman/openshell/bwrap/herdr/gh/network/model calls
  anywhere (fakes and tmp dirs only; real git/tar confined to tmp).

## Parent-level root-cause fix outside the three plans (97c2877)

The acceptance filter (`-k "engine or stage or sandbox or plan_router"`) selected two
pre-existing failures in `test_grokbot_readiness_cli.py` (names contain "stage"). Root cause:
the retention producer is invoked as `python3 omega_prime/scripts/ci_receipt.py`, so the
subprocess's sys.path holds only the script dir; where omega-prime is not pip-installed the
`omega_prime.tooling.fs` import fails and the write fails closed. Fix: the test harness hands
the repo root to the subprocess via PYTHONPATH. Assertions untouched; the real producer runs
end to end (proven: `2 passed` for the previously failing pair).

## Blocked / pre-existing (not caused by this phase, unchanged by it)

- `test_engine_stage.py::test_mcp_desk_parent_reads_the_stage_from_its_env` — fails on
  `ModuleNotFoundError: No module named 'mcp'` (optional dependency not installed; no network to
  install it). Identical at baseline; the arguments anticipated "mcp may be missing".
- 49 collection errors across `omega_prime/tests/` (test_tools, test_x_tweepy, test_telegram,
  …) from other optional dependencies absent in this sandbox; irrelevant to the phase filter and
  identical at baseline. Acceptance runs therefore use `--continue-on-collection-errors`.
- Live interoperability with the real omp-sandbox-anda binary and real Z.ai models is node n7
  (sbx-5 e2e), out of scope here by the plan; all proofs use the protocol-faithful fake wrapper.
