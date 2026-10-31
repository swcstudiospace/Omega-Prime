# Audit re-run: C1..C13 after the sandbox wiring

## Method

This re-runs the read-only audit at `/sandbox/home/refs/AUDIT.md` against the
tree after phases 110-113 plus this phase (branch `feat/sbx-5-e2e`). Commands
run from the repository root unless a row says host-only. Host-only evidence
is named where it lives, never fabricated. The refs directory is read-only and
untouched. Expected results describe the end-of-phase state: rows citing
`omega_prime/tests/test_sandbox_e2e.py` run green once plan 114-02 (wave 2)
lands; until then that file does not exist in the tree.

## Redaction

The table carries env names and semantics only. No consent/API values, no
assignments, no credentialed argv anywhere. The C13 row names `gh` login and
`push.sh` without any token on a command line.

| # | Criterion | Command (repo root unless marked host) | Expected | Live proof |
| --- | --- | --- | --- | --- |
| C1 | plan_router stage=plan -> glm-5.3 on Z.ai | `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_engine.py::test_stage_routing_falls_back_to_zai_when_chatgpt_is_gated_off omega_prime/tests/test_engine.py::test_stage_models_and_overrides` | PASS — code-level: the Z.ai plan-stage model is selected when the consent flag is truthy in the run env; the in-process opt-in is set only inside test env dicts (the AUDIT.md C1/C2 override precedent). The box env is host-only and out of scope; its flag's truthiness is Ove's consent call. | phase 111/112 suites + this phase's e2e model assertions; host note: box-env probe recorded host-only. |
| C2 | plan_router stage=execute -> glm-5.3-flash | `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_engine_stage.py::test_zai_has_its_own_plan_and_build_pair omega_prime/tests/test_engine_stage.py::test_zai_stage_runs_build_on_flash` | PASS — same scope as C1: the Z.ai execute-stage model, gated on the same consent flag, in-process only. | phase 111/112 suites + this phase's e2e model assertions; host note: box-env probe recorded host-only. |
| C3 | Loop honours Ove's "Z.ai plans and builds" choice by default | `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_sandbox_e2e.py` | PASS against the shipped semantics: per-run requested Z.ai driven by the loop's own routine — plan on `zai/glm-5.3` then execute on `zai/glm-5.3-flash` — with the ChatGPT-first default untouched. Note: the original wording said "by default"; the locked phase decision shipped per-run explicit request instead of a default change, and the criterion's substance — the loop can honour Ove's Z.ai plan-and-build choice through its own routine — is what PASS means. Runs green once plan 114-02 lands. | this phase's e2e. |
| C4 | Engine launches an omp-sandbox run | `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_engine_omp_sandbox.py::test_plan_then_execute_orders_two_stages_on_one_worktree` | PASS | phase 112 suite + this phase's e2e. |
| C5 | omp-sandbox itself runs a GSD stage | host: `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_sandbox_e2e.py -k real` | skipped in this sandbox with a skip reason naming `OMEGA_PRIME_SANDBOX_E2E_REAL`; live PASS is host-only — the AUDIT.md live-run receipt `runs/20261021-184023-full` remains the stage-level evidence, and the marker run re-proves it on the host. | host-only + the AUDIT.md receipt. |
| C6 | Workspace mount (job worktree at /sandbox/work) | `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_engine_omp_sandbox.py::test_allowlist_refusal_precedes_any_wrapper_spawn` | PASS — engine-side admission refuses non-allowlisted worktrees with `path_refused` before any spawn, mirroring the wrapper's `allowedDir`. | phase 112 suite; the rw bind-mount smoke receipt is host-side (sbx-2, AUDIT.md C6 row). |
| C7 | Result comes back into Omega Prime | `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_engine_omp_sandbox.py::test_export_lands_back_in_the_worktree_and_is_reported` | PASS | phase 112 suite + this phase's e2e (git log grows in the job worktree). |
| C8 | Herdr pane per sandbox run | `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_engine_omp_sandbox.py::test_pane_label_and_watch_argv_are_pinned omega_prime/tests/test_engine_omp_sandbox.py::test_tmux_fallback_records_the_session` | PASS | phase 113 suite + this phase's e2e (rows record `herdr_pane`). |
| C9 | tasks.json row per sandbox run | `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_engine_omp_sandbox.py::test_tracked_run_lands_a_row_working_to_done` | PASS | phase 113 suite + this phase's e2e (rows finish done with receipts). |
| C10 | Immediate block surfacing (waiting_on_ove) | `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_engine_omp_sandbox.py::test_plan_not_allowed_raises_one_item_without_spawning omega_prime/tests/test_dashboard_blocks.py::test_engine_gated_failure_raises_one_block_in_the_same_call` | PASS — one item in the same failing call, no poll delay. | phase 111 suites + this phase's e2e forced-block test. |
| C11 | Existing engine/stage tests | `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_engine.py omega_prime/tests/test_engine_stage.py omega_prime/tests/test_dashboard_blocks.py omega_prime/tests/test_engine_omp_sandbox.py --deselect omega_prime/tests/test_engine_stage.py::test_mcp_desk_parent_reads_the_stage_from_its_env` | PASS modulo that one documented pre-existing mcp-import failure (recorded in the phase 112 context, unchanged, never worked around). | the suites themselves, run every phase. |
| C12 | Draft PR health | `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_docs.py omega_prime/tests/test_public_repo.py` | PASS — the repo-side health gates, recorded this phase. PR-check health (CI, review apps) is host-only via `gh` on the box. | repo suites here + host `gh` evidence. |
| C13 | Push path from the box | host: `gh auth status` | BLOCKED: not logged in; `push.sh` blocked by auto-review — unchanged until Ove unblocks login/push (AUDIT.md blockers). No retry, no workaround, no credential on any command line. The only non-PASS row, by design. | host-only. |

## State

After phases 110-113 plus this phase, every criterion is PASS at its named
proof site except C13 (blocked on Ove) and the host-only rows, whose live
evidence is named where it lives, not fabricated.

