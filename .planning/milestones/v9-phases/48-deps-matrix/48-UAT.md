---
status: testing
phase: 48-deps-matrix
source: [48-VERIFICATION.md]
started: 2026-10-07T16:03:45Z
updated: 2026-10-07T16:03:45Z
audit_acknowledged:
  milestone: v15
  at: 2026-10-12
  gap_snapshot: "testing::scenarios=4"
---

## Current Test

number: 1
name: Inspect the hosted 9-combination + docs run for the pushed matching head.
expected: |
  All 9 verify/lint/types jobs plus docs complete; named guard testcase passed; exits recorded.
awaiting: user response

## Tests

### 1. Inspect the hosted 9-combination + docs run for the pushed matching head.

expected: All 9 verify/lint/types jobs plus docs complete; named guard testcase passed; exits recorded.
result: [pending]

### 2. Run the stopped-writer real 3-lane union on actual 3.12/3.13/3.14 interpreters.

expected: Full suite (with named guard), evals, assembly, Ruff, mypy, pip check green per lane.
result: [pending]

### 3. Confirm adjacent-lane isolation on the hosted run (one failing combination does not cancel siblings, yet still fails the workflow).

expected: fail-fast:false retains sibling receipts; workflow conclusion still fails.
result: [pending]

### 4. Confirm empty-install-input impossibility on the hosted run (every matrix job installs before gating; failed install records dependents as not_run, never passed).

expected: Receipt summaries show not_run for dependents of any failed install; no pass without install.
result: [pending]

## Summary

total: 4
passed: 0
issues: 0
pending: 4
skipped: 0
blocked: 0

## Gaps
