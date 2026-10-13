---
plan: 84-01
status: complete
phase: 84
---

# 84-01 Summary: Live evals and regression tracking

## Tasks completed

- Task 1: `omega_prime/evals/rubric.py` (weighted [0,1] rubric schema/loader/validator, named rejections; accepts bare-list or cases-wrapped JSON) + `omega_prime/evals/cases/rubric_cases.json` (14 scored cases derived from the redteam surface, case_id == redteam id so the coverage superset check passes for real) + additive `--rubric/--output` runner flags in `omega_prime/evals/runner.py` (CI evals step untouched — grep-verified).
- Task 2: `omega_prime/evals/history.py` (append-only `.omega-evals/history.ndjson` with run-id + timestamp, torn-tail repair per the EventStore pattern; directory gitignored) + `omega_prime/evals/trend.py` (per-case deltas; `check --baseline … --report …` exits 1 naming regressed cases and deltas; `--bless` rewrites the committed baseline as the reviewed-PR path) + `evals.regress_threshold` config node (default 0.1).
- Task 3: `omega_prime/evals/judge.py` (strictly behind `evals.judge.enabled/model`; stdlib-only top level; socket-capable imports only inside the enabled path; off ⇒ skipped records with zero network; endpoint-down ⇒ `judge_unavailable` record) + `.github/workflows/nightly-evals.yml` (cron + dispatch; deterministic suite, rubric run, trend vs history, baseline compare as failing steps; artifact upload; open-on-red/close-on-green tracking issue via GITHUB_TOKEN only) + committed `omega_prime/evals/history-baseline.json` seeded with the 14 cases.

## Revision history

- First check (C84) FAILED: the nightly had no cross-run comparison (empty ephemeral history ⇒ regressions could never fire). Revised (P84r): committed history-baseline + `check --baseline` failing step + `--bless` update path (option (b), readiness_baseline precedent; artifact-restore rejected with rationale); Task 2 trend verify made non-vacuous (shell-allocated temp dir); rubric-coverage superset assertion added. Recheck (C84r) PASS.

## Deviations

- Two author-side edit misfires (baseline placeholder; indented defs) self-repaired, re-verified by compile + tests.
- Parent lint pass: SIM108 ternary in rubric loader; `trend.py` None-run-id guard (mypy) — records without a string run_id are skipped in ordering.

## Verification

28 new tests (rubric/history/judge) + existing evals = 43 in the combined run, 31 in the post-lint re-run; seeded regression drill exits 1 naming `rt_injection (-0.400)`; coverage 14/14 redteam ids; CI-gate grep empty; full suite 2790 passed / 0 failed (84-VERIFICATION.md).
