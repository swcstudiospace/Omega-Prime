# Phase 114: sandbox steering end-to-end (sbx-5) — Summary

Task 00030 node n7 / U5. Two plans, two sequential waves, both file-disjoint; no
product code touched — the phase proves the chain phases 110-113 shipped and
publishes its operator docs. Branch `feat/sbx-5-e2e`; every commit local; no
push, no PR, no remote.

## Plans executed

| Wave | Plan | Files (the only diff of the phase) | Result |
| --- | --- | --- | --- |
| 1 | 114-01 | docs/omp-sandbox-engine.md (new), docs/SUMMARY.md (+1 link line), 114-AUDIT-RERUN.md (new) | complete |
| 2 | 114-02 | omega_prime/tests/test_sandbox_e2e.py (new, 510 lines) | complete |

- 114-01 documents the backend exactly as shipped (selection, env names with
  semantics only, stage→model routing, workspace allowlist, result/export
  contract, visibility contract) and re-opens the read-only audit as a
  runnable table (`114-AUDIT-RERUN.md`, C1..C13, one command per row).
- 114-02 proves the whole chain from the loop's own entrypoint:
  `execute_ultrathink` drives plan on `zai/glm-5.3` then execute on
  `zai/glm-5.3-flash` through the sbx-3 backend against a self-contained
  two-stage fake Anda wrapper whose stage work really commits GSD-shaped files;
  exports land back in the job worktree; both task rows finish `done` with
  receipts and their own herdr pane ids; the per-job `session.json` reports
  sandbox `omp-sandbox` with the per-stage models.

## Audit re-run: C1..C13 (result at final state)

Commands per row are pinned in `114-AUDIT-RERUN.md`; every repo-side command
was run green at the final tree. Criteria wording verbatim from
`/sandbox/home/refs/AUDIT.md` (read-only, untouched).

| # | Criterion | Result | Where the proof ran |
| --- | --- | --- | --- |
| C1 | plan_router stage=plan -> glm-5.3 on Z.ai | PASS | in-process test env dicts: `test_engine.py::test_stage_routing_falls_back_to_zai_when_chatgpt_is_gated_off`, `::test_stage_models_and_overrides` + this phase's e2e model assertions; box-env probe host-only |
| C2 | plan_router stage=execute -> glm-5.3-flash | PASS | `test_engine_stage.py::test_zai_has_its_own_plan_and_build_pair`, `::test_zai_stage_runs_build_on_flash` + e2e; box-env probe host-only |
| C3 | Loop honours Ove's "Z.ai plans and builds" choice by default | PASS (per-run requested Z.ai via the routine; original "by default" wording re-run against the shipped semantics, ChatGPT-first default untouched) | this phase's e2e: `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_sandbox_e2e.py` → 2 passed, 1 skipped |
| C4 | Engine launches an omp-sandbox run | PASS | `test_engine_omp_sandbox.py::test_plan_then_execute_orders_two_stages_on_one_worktree` + e2e |
| C5 | omp-sandbox itself runs a GSD stage | PASS host-only / skipped here naming `OMEGA_PRIME_SANDBOX_E2E_REAL` | `-k real` → exactly the named skip; live evidence stays the AUDIT.md receipt `runs/20261021-184023-full`, marker run re-proves on host |
| C6 | Workspace mount (job worktree at /sandbox/work) | PASS (engine-side `path_refused` pre-spawn; rw bind-mount smoke is host-side) | `test_engine_omp_sandbox.py::test_allowlist_refusal_precedes_any_wrapper_spawn` |
| C7 | Result comes back into Omega Prime | PASS | `test_engine_omp_sandbox.py::test_export_lands_back_in_the_worktree_and_is_reported` + e2e (git log grows, bundles exist) |
| C8 | Herdr pane per sandbox run | PASS | `::test_pane_label_and_watch_argv_are_pinned`, `::test_tmux_fallback_records_the_session` + e2e (rows record `herdr_pane`) |
| C9 | tasks.json row per sandbox run | PASS | `::test_tracked_run_lands_a_row_working_to_done` + e2e (rows finish done with receipts) |
| C10 | Immediate block surfacing (waiting_on_ove) | PASS | `::test_plan_not_allowed_raises_one_item_without_spawning`, `test_dashboard_blocks.py::test_engine_gated_failure_raises_one_block_in_the_same_call` + e2e forced-gate test (one item in the same returning call) |
| C11 | Existing engine/stage tests | PASS | 98 passed, 1 skipped (the marker gate), 1 deselected (documented pre-existing mcp import), 11.9s |
| C12 | Draft PR health | PASS repo-side (docs + public-repo suites 31 passed); PR checks host-only via gh | `test_docs.py`, `test_public_repo.py` |
| C13 | Push path from the box | BLOCKED (the only non-PASS row, by design — gh not logged in, push.sh held by auto-review; Ove's call, unchanged) | host-only |

## Verification (parent-executed at final state)

| Command | Result |
| --- | --- |
| `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_sandbox_e2e.py` | 2 passed, 1 skipped in 0.52s |
| e2e + `test_engine.py test_engine_omp_sandbox.py test_dashboard_blocks.py test_engine_stage.py` with the documented deselect | 98 passed, 1 skipped, 1 deselected in 11.93s |
| `... test_sandbox_e2e.py -k real` | 1 skipped, reason names `OMEGA_PRIME_SANDBOX_E2E_REAL` |
| `test_docs.py test_public_repo.py` | 31 passed in 0.69s |
| `cd omp-sandbox && npx tsc --noEmit` | 0 errors |
| `cd omp-sandbox && npm test` | 27 pass / 0 fail in 1.7s |
| redaction grep on docs page, audit table, e2e file | clean (no consent/API `NAME=` text anywhere) |
| `git status --porcelain` | clean at closeout |

No existing test was weakened, skipped or deleted: the phase diff is exactly
the six files listed above; `omp-sandbox/**`, `omega_prime/engine/**`,
`omega_prime/dashboard/**`, `omega_prime/routines/**` are untouched.

## Findings (recorded, not patched)

- **Routine-layer gate gap.** `execute_ultrathink` never reaches `run()`'s
  block wiring on a plan gate: the probe short-circuit
  (`omega_prime/engine/attach.py:416-418`) returns
  `{"used": False, "reason": "plan_not_allowed"}` with zero writes — no
  `waiting_on_ove` item at the routine layer. The item surfaces only through
  the engine call the routine wraps (`omp_driver.py:378-396`), which the e2e
  proves in the same returning call. Whether the routine should raise the item
  itself is a product decision; recorded as a finding in 114-02-SUMMARY.md.
- **Host-only rows** (C1/C2 box env, C5 live stage, C6 mount smoke, C12 PR
  checks, C13 push) name where their evidence lives; nothing was fabricated
  and no host command was run from here.

## Blocked

- **C13 push path** stays blocked until Ove unblocks `gh auth login` (or
  approves push.sh): drafts #53/#54/#55 cannot receive these local commits.
  No retry, no workaround, no credential on any command line.

## Commits (all `00015 - ...`)

da165f8 planning · ae82c58 docs page · 3a01dc7 audit table C1/C2/C11 ·
80e62f5 audit rows C3..C13 · ebe284a plan 114-01 summary · e146209 e2e
harness · 63edc58 happy-path e2e · 48353d5 forced-gate e2e · 9c4c6d9 redaction
kwarg fix · 91e21c0 plan 114-02 summary · (closeout) phase summary, roadmap
tick, receipt.
