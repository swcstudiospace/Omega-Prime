# U6 — Live Evals + Regression Tracking → Phase 84

Issue: SPE-10594 · Milestone v16 · Evidence: `local://v16-recon-findings.md` (HEAD df14512)

## TITLE_AND_SUMMARY

Extend the eval stack beyond one-shot pass/fail: scored rubric cases, persistent per-case
result history with trend reporting, a nightly scheduled eval workflow, and an optional
live-model judge behind an explicit flag. The deterministic scripted-turn suite stays the CI
gate — the judge and history never block a push.

Definitions:
- **Deterministic suite**: the existing evals runner (omega_prime/evals/runner.py) replaying
  scripted turns from golden.json + redteam.json, wired into ci.yml verify (R3).
- **Rubric case**: an eval case scored against weighted criteria (e.g. "denies the request",
  "explains why", "offers the safe alternative") instead of binary pass/fail; scoring is
  deterministic where the criterion is checkable from the transcript, judge-scored otherwise.
- **Result history**: append-only NDJSON of per-run, per-case records
  (`{run_id, commit, case_id, score, criteria, timestamp}`); **trend** = a case's score series
  across runs.
- **Live-model judge**: an LLM asked to score a criterion against the rubric — opt-in only,
  never a CI gate (R3 records none exists today).

## EVIDENCE

- R3: evals are omega_prime/evals/runner.py (golden.json + redteam.json, wired into ci.yml
  verify) plus the hermetic PyRIT adversarial battery (tests/test_pyrit_target.py, XDG
  redirect); NO live-model/LLM-judge evals; NO result history/trend tracking; evals run only
  on push/PR — no nightly.
- R2: supply-chain.yml (weekly cron) is the scheduled-workflow precedent; kb-refresh.yml shows
  the secret-gated skip pattern.
- R3: the CI gate set (pytest ~2134, ruff, mypy, deterministic evals) is what currently
  protects regressions — this plan adds signal, it does not move the gate.

## VALUE_PROPOSITION

- Binary golden-pass hides partial regressions ("still passes, but worse"); per-case score
  history makes slow degradation visible and attributable to a commit.
- Rubric cases express quality bars that binary fixtures cannot (multi-part behavioral
  expectations).
- The nightly cadence catches drift that push/PR runs miss (e.g. dependency-refresh effects),
  and the optional judge lets humans inspect *why* a case scored low without making the model
  a gatekeeper.

## ARCHITECTURE_SKETCH

- `omega_prime/evals/rubric.py` (new): rubric case schema —
  `{case_id, turns, criteria: [{id, weight, check}]}` where `check` is either a deterministic
  predicate id (transcript contains/omits, tool-call shape) or `judge` (live-model scored).
  Total case score ∈ [0, 1] = weighted criterion scores.
- Runner integration: runner.py gains `--rubric <file>` and emits per-case records; golden.json
  + redteam.json behavior is untouched (R3 parity).
- History store: `.omega-evals/history.ndjson` at the repo root (runtime data, not planning
  data), append-only, one JSON object per line, `run_id = commit + timestamp`; a corrupted
  tail is truncated on read exactly like the EventStore torn-tail repair pattern
  (tools/lead.py:359, R5).
- `omega_prime/evals/trend.py` (new): reads history, prints per-case trend
  (first/last/min/mean score, delta vs previous run for the same commit base) and a
  regression line: any case whose score dropped ≥ `evals.regress_threshold` (default 0.1)
  between the last two runs.
- Judge (optional): when `evals.judge.enabled` is true, `judge` criteria are sent to the
  configured model endpoint with the rubric and the transcript; the judge returns a score +
  one-line rationale stored in the history record. Judge unavailability ⇒ criterion scores 0
  with `judge_unavailable: true` — recorded, never fabricated, never blocks.
- `.github/workflows/nightly-evals.yml` (new, cron nightly): runs the full suite (deterministic
  + rubric), appends history, and comments on a tracking issue when trend.py reports a
  regression; secret-gated skip per kb-refresh.yml precedent (R2).

## IMPLEMENTATION_STEPS

1. **Rubric schema + loader.** Implement rubric.py with schema validation and the
   deterministic predicate set.
   **Verify:** `python -m pytest tests/test_evals_rubric.py -q` — valid case loads; unknown predicate, weight not summing to 1, and missing case_id each fail with a named error.
2. **Runner integration.** Add `--rubric` to runner.py emitting per-case records; keep
   golden/redteam output shapes unchanged.
   **Verify:** `python -m omega_prime.evals.runner --rubric omega_prime/evals/rubric_cases.json --output /tmp/run.json && python -c "import json;d=json.load(open('/tmp/run.json'));assert all('score' in c for c in d['rubric_cases'])"`; `python -m pytest tests/test_evals_runner.py -q` — existing runner tests pass unchanged.
3. **History store.** Implement the append-only NDJSON history with torn-tail repair on read.
   **Verify:** `python -m pytest tests/test_evals_history.py -q` — append → read round-trip; a hand-truncated last line is dropped with a warning and earlier lines intact.
4. **Trend + regression line.** Implement trend.py (per-case trend, delta-vs-previous,
   threshold).
   **Verify:** seed a temp history with a synthetic score drop; `python -m omega_prime.evals.trend --history <tmp>` — prints the regressed case and exits 1; a flat history exits 0.
5. **Shipped rubric cases.** Author rubric_cases.json starting from the redteam.json scenario
  set (same surface, scored instead of binary) — the six red-team classes are the first
  candidates, marked `judge: none` initially.
   **Verify:** `python -m omega_prime.evals.runner --rubric omega_prime/evals/rubric_cases.json` — every case runs and scores; any case scoring below its `min_score` in the file fails the invocation (the nightly's pass signal).
6. **Optional judge.** Implement judge scoring behind `evals.judge.enabled` with
   rationale capture and the unavailability rule above.
   **Verify:** `python -m pytest tests/test_evals_judge.py -q` — flag off: no judge criteria scored, records say `judge: skipped`; flag on with a stubbed endpoint: scores + rationale stored; endpoint down: `judge_unavailable: true`, suite still completes.
7. **Nightly workflow.** Add nightly-evals.yml (suite → history → trend → tracking-issue
   comment on regression).
   **Verify:** dispatch manually on a branch — green run appends one run_id to history and the artifact carries the report; seeding a regressing rubric case in a scratch branch makes the workflow post exactly one tracking-issue comment.
8. **Gate invariant.** Confirm ci.yml verify still gates on the deterministic suite only —
   rubric/judge/history failures cannot block a push.
   **Verify:** `git grep -n "rubric\\|judge" .github/workflows/ci.yml` — no matches (deterministic gate untouched, R3); the step-2 parity test is the CI proof.

## DEPENDENCIES

- None inside milestone v16 on plan-01..05/07. plan-01's simulation library is a *future*
  rubric-case source (its cases-as-data records could feed rubric cases), but no code is
  shared in this milestone.
- plan-03 soft: if rubric/judge outcomes should alert, plan-03's nightly pattern is the
  substrate — this plan ships its own workflow independent of plan-03's.
- External: a model endpoint + key only for the optional judge (secret-gated; absent key ⇒
  judge steps skip, R3 records no live-model evals exist today); GitHub scheduled workflow
  permissions (precedent R2).

## RISKS_AND_MITIGATIONS

- *Judge as de-facto gate (scope creep).* Hard invariant: judge criteria are excluded from
  CI gating by construction (step 8 check); the deterministic suite remains the only push
  gate (R3).
- *Judge score instability across runs.* Rationale + model id stored per record; trend treats
  judge-scored criteria separately from deterministic ones so noise is visible, not blended.
- *History growth unbounded.* One line per case per run is small; trend.py reads sequentially;
  rotation can reuse the audit-chain .1/.2 pattern (R5) if ever needed — explicitly deferred.
- *Rubric cases drift from golden coverage.* Step 5 derives the first set from redteam.json;
  a CI check later can assert rubric case_ids ⊇ golden scenario ids if the set diverges —
  out of scope for v16, noted for the phase plan.
- *Nightly flakiness spam.* One tracking issue updated per regression window (plan-03's
  pattern); green run clears the alert state.

## ROLLOUT_AND_ROLLBACK

- Config flag: `evals.judge.enabled` (node key, default `false`) with `evals.judge.model`
  (default empty ⇒ judge refuses to start even when enabled) and
  `evals.regress_threshold` (default 0.1), in the omega_prime/config.py node-key style with
  env overrides `OMEGA_PRIME_EVALS_JUDGE_ENABLED` / `OMEGA_PRIME_EVALS_JUDGE_MODEL` /
  `OMEGA_PRIME_EVALS_REGRESS_THRESHOLD` (config.py:100-115 `OMEGA_PRIME_*` pattern).
  The judge additionally requires its endpoint credential from the environment/secret store —
  never from the config file (R4 secrets conventions).
- Rollout: history + rubric land dark (write-only, nothing reads them for gating) → nightly
  workflow enabled → judge last, only for triage use.
- Revert step: `OMEGA_PRIME_EVALS_JUDGE_ENABLED=0` — judge criteria report
  `judge: skipped` and the suite behaves as pre-judge; the nightly workflow reverts by file
  deletion; history files are additive data — deleting `.omega-evals/` returns the repo to the
  exact pre-plan state (the deterministic gate never depended on any of it, R3).

## SUCCESS_METRICS

- Deterministic CI gate unchanged and green on every push (R3 parity verified by the step-8
  check).
- History accrues one record per case per run with zero schema breaks across 7 nightly runs;
  trend.py reports stable cases as stable (delta < threshold).
- The shipped rubric set covers every redteam.json scenario as a scored case (step 5
  verification output).
- Judge (when enabled for triage) returns rationale for 100% of scored criteria; 0 judge
  failures leak into CI results.

## GSD_PHASE_MAPPING

- Milestone v16, **Phase 84** — "Live evals + regression tracking".
- Phase directory under the v16 phases layout (`.planning/<v16-phases>/84-<slug>/`), v14/v15
  `NN-<slug>` convention.
- Execution order: steps 1-4 (schema, runner, history, trend) are one sequential task;
  steps 5-7 (cases, judge, nightly) a second wave; step 8 is the phase gate check.
- Phase verification: SUCCESS_METRICS become VERIFICATION.md checks; "deterministic suite
  stays the CI gate" (R3) is a hard acceptance criterion — any change to ci.yml gating in
  this phase is a plan deviation.
