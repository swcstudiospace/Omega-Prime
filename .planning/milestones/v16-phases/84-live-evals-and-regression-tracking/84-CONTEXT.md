# Phase 84: Live evals and regression tracking - Context

**Gathered:** 2026-10-13
**Status:** Ready for planning
**Mode:** Auto-generated (discuss skipped via workflow.skip_discuss)

<domain>
## Phase Boundary

Add scored eval cases with persistent per-case trend history and a nightly schedule, keeping the deterministic suite as the blocking push/PR gate. Scope: scored rubric cases beside the deterministic runner; persistent results history (per-case deltas across runs); nightly scheduled eval workflow surfacing regressions; optional live-model judge strictly behind an explicit flag with zero network calls when unset. Out of scope: live-model calls in the CI gate, replacing the deterministic runner, hosted dashboard backends.

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
All implementation choices are at Claude's discretion — discuss phase was skipped per user setting.

### Recorded decisions binding this phase
- Deterministic eval step remains the blocking CI gate (plan-06; EVAL-02).
- Live-model judge requires an explicit config flag AND makes zero network calls when unset — no key reads, no client construction.
- History is local NDJSON/JSON files under the repo's state conventions; no hosted backend.

</decisions>

<code_context>
## Existing Code Insights

Verified evidence (`.planning/recon-evidence.md` R3): `omega_prime/evals/runner.py` deterministic scripted-turn runner (golden.json + redteam.json via run_conversation + ScriptedModel; run_suite loads only those two files; wired into `ci.yml` verify); PyRIT battery in `tests/test_pyrit_target.py` (hermetic); MISSING: no scored rubric cases, no result history/trends, no nightly eval, no judge. The nightly-readiness pattern from phase 81 (`scripts/nightly_readiness_check.py` + `.github/workflows/nightly-readiness.yml`) is the precedent to mirror. Plan: `.planning/updates/plan-06.md` (U6; `evals.judge.enabled/model` + `regress_threshold` config).

</code_context>

<specifics>
## Specifics

- Scored cases live as data beside the existing cases (same loader conventions); scores from a rubric, not a model, unless the judge flag is on.
- History files append per run with run-id + timestamp; trend check names per-case deltas; regression fails the nightly workflow loudly (same pattern as the readiness check).
- Judge flag-off is provable: a test asserts no socket-capable import or client construction occurs when unset.

</specifics>

<deferred>
## Deferred Items

- Hosted dashboards, cross-repo eval runners, human review queues.

</deferred>
