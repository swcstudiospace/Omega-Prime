# Plan 114-01 Summary — sandbox docs + audit re-run

**Status:** complete. Four commits on `feat/sbx-5-e2e`, tree clean.
**Owned files:** `docs/omp-sandbox-engine.md` (new), `docs/SUMMARY.md` (one
added line), `.planning/phases/114-sbx5-e2e/114-AUDIT-RERUN.md` (new), plus
this summary.

## Suite outputs

- T1 verify (`test_docs.py` + `test_public_repo.py`): **31 passed**; negative
  redaction grep on the docs page: no match.
- T2 verify (C1/C2 selections: two `test_engine.py` ids + two
  `test_engine_stage.py` ids): **4 passed**; row count exactly 3; redaction
  grep clean.
- T3 verify (row count 13, redaction clean, e2e cited, C10 selections): **2
  passed**.
- T4 full verify (`test_docs.py` + `test_public_repo.py` + `test_engine.py` +
  `test_dashboard_blocks.py` + `test_engine_omp_sandbox.py` +
  `test_engine_stage.py` with the single documented mcp-import deselect):
  **127 passed, 1 deselected**.

## Individually-run audit command results

Every repo-side selection pinned in `114-AUDIT-RERUN.md` ran green before
being pinned PASS:

- C1 (fallback + model overrides, `test_engine.py`): 2 passed.
- C2 (plan/build pair, flash execute, `test_engine_stage.py`): 2 passed.
- C4 two-stage one worktree: passed.
- C6 allowlist refusal pre-spawn: passed.
- C7 export lands back and is reported: passed.
- C8 pane argv pinned + tmux fallback: 2 passed.
- C9 tracked run working→done: passed.
- C10 plan gate pre-spawn + same-call block: 2 passed.
- C11 four-suite command with deselect: green (subset of the T4 run).
- C3/C5 cite `omega_prime/tests/test_sandbox_e2e.py` — file deliberately not
  created (plan 114-02, wave 2); rows marked as end-of-phase expectations.
- C12 repo-side half is the docs/public-repo suites above; PR-check half
  named host-only. C13 stays BLOCKED (host `gh auth status`); no retry.

## Pinned decisions

- Marker name published in the docs page: `OMEGA_PRIME_SANDBOX_E2E_REAL`
  (presence-only), pinning it for plan 114-02.
- `docs/SUMMARY.md` link placed under "## Use" immediately after
  `[Agent loop](agent-loop.md)` — one added line, nothing moved.
- C3 pinned to shipped per-run semantics with the explicit note that the
  original audit wording said "by default"; ChatGPT-first default untouched.
- C1/C2 PASS scoped to the in-process opt-in (flag set only inside test env
  dicts); box env named host-only, its value never stated.
- C12 split repo-side (run here) / PR-check (host `gh`); C13 the only
  BLOCKED row.

## Drift findings (T4)

None. Every env name on the docs page was cross-checked against the wiring
(`attach.py`, `sandbox_omp.py`, `plan_router.py`, `jobs.py`,
`ultrathink_turn.py`, `run_rows.py`, `omp_driver.py` probe) — no invented
knobs, no missing wired names; the page also documents the real per-stage
model override knob `OMEGA_PRIME_OMP_MODEL_<PLAN>_<STAGE>`. Plan-cited
anchor drift (e.g. `_ANDA_FAKE` now :47-271) was avoided by citing behaviour
against the verified current source; no factual error surfaced in the docs
page during the consistency pass.

## Compliance

No env value, example value, or `NAME=` assignment for any consent/API name
in either artifact (negative grep verified on both). No tracker identifiers
in committed files. `/sandbox/home/refs/` untouched (read-only citations
only). No test created, weakened, skipped or deleted; the pre-existing
`test_engine_stage.py::test_mcp_desk_parent_reads_the_stage_from_its_env`
failure stays documented and deselected, never worked around.
