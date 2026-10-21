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
| C11 | Existing engine/stage tests | `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_engine.py omega_prime/tests/test_engine_stage.py omega_prime/tests/test_dashboard_blocks.py omega_prime/tests/test_engine_omp_sandbox.py --deselect omega_prime/tests/test_engine_stage.py::test_mcp_desk_parent_reads_the_stage_from_its_env` | PASS modulo that one documented pre-existing mcp-import failure (recorded in the phase 112 context, unchanged, never worked around). | the suites themselves, run every phase. |
