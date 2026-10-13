---
status: passed
phase: 84
verified_at: 2026-10-13
covered_files:
  - omega_prime/evals/rubric.py
  - omega_prime/evals/history.py
  - omega_prime/evals/trend.py
  - omega_prime/evals/judge.py
  - omega_prime/evals/runner.py
  - omega_prime/evals/cases/rubric_cases.json
  - omega_prime/evals/history-baseline.json
  - omega_prime/config.py
  - .github/workflows/nightly-evals.yml
covered_digest: v3:sha256:98871596d61fad8464a4790484071ddb83767cc66f6aac98b94c3e2fbe18d784
---

# Phase 84 Verification

## Requirement outcomes

| Req | Outcome | Evidence |
|-----|---------|----------|
| EVAL-01 | ✓ VERIFIED | 14 scored rubric cases (weighted [0,1] scores, rubric schema/loader with named rejections) as data beside the deterministic cases; additive `--rubric/--output` runner flags leave the CI evals step byte-identical (`git grep rubric\|judge -- .github/workflows/ci.yml` exit 1); append-only `.omega-evals/history.ndjson` (gitignored) with run-id + timestamp and torn-tail repair; `trend` reports per-case deltas — seeded 0.9→0.5 drop exits 1 naming `rt_injection (-0.400)` against `evals.regress_threshold`; coverage superset proven: all 14 redteam.json ids present in rubric_cases.json. |
| EVAL-02 | ✓ VERIFIED | `nightly-evals.yml` mirrors nightly-readiness (cron + dispatch, issues:write, artifact upload, open-on-red/close-on-green) with TWO failing gates: deterministic suite + `trend check --baseline omega_prime/evals/history-baseline.json` (committed, seeded with the 14 cases; `--bless` is the reviewed-PR update path — C84 BLOCKER closed); live-model judge is strictly behind `evals.judge.enabled` — stdlib-only top level, socket-capable imports only inside the enabled path, off ⇒ skipped records and a test proving no network import/client construction (import-hook assertion); endpoint-down records `judge_unavailable` instead of failing. |

## Automated runs (parent, 2026-10-13)

- Targeted: rubric/history/judge + existing evals + docs suites → 43 passed (31 after the lint pass re-run).
- CI-gate invariance: `git grep -n "rubric\|judge" -- .github/workflows/ci.yml` → no matches (exit 1).
- Regression drill: two-run seeded history → `trend` exit 1 naming the regressed case and delta.
- Coverage: 14/14 redteam ids present in the rubric set (no missing).
- ruff/format clean (SIM108 ternary, trend None-guard added); mypy clean on rubric/history/trend; full suite close-of-phase: **2790 passed, 0 failed, 16 warnings** in 243.16s.

## Known limits

- Hosted nightly run and judge-with-key behavior are authored but unproven until the next hosted run; the judge never ran against a live model in-phase (by design).
- History lives in gitignored `.omega-evals/` on the runner; cross-run continuity comes from the committed baseline (planner choice (b), documented in the plan).
