---
phase: 76-clean-ci-cd
plan: "01"
subsystem: infra
tags: [readiness, ci-receipts, sanitized-retention]
requires:
  - phase: 75-integrated-local-readiness
    provides: canonical isolated readiness CLI and full-input publication guard
provides:
  - Maintained readiness tracer on three verify matrix legs
  - Canonical guarded report or fixed-safe failure retention with honest observed/effective exits
affects: [ci, image-evidence, release]
actuals:
  tasks: 3
  commits: 2
tech-stack:
  added: []
  patterns: [existing-canonical-validation, private-atomic-evidence, observed-vs-retention-exit]
key-files:
  created: []
  modified: [.github/workflows/ci.yml, omega_prime/scripts/ci_receipt.py, omega_prime/tests/test_grokbot_readiness_cli.py]
key-decisions:
  - Readiness runs on three verify legs; lint/type matrices preserved without duplicate integration runs
  - Upload only admitted retained output, never the raw candidate
  - Canonical validate_report/roster/fs helpers replace copied shape/writer conventions
requirements-completed: [CI-01, CI-02, CI-03]
completed: 2026-10-11
status: complete
---

# Phase76 Plan01 — Real readiness and failure-safe retention

CI uses the maintained product CLI; retained output distinguishes observed command status from publication admission without turning rejected evidence green.

## Tracer proof before expansion

Parent actionlint exited0 and real `ci_receipt.py run readiness -- ...readiness` produced exit0,165 canonical tools/failed0/problems[], private0600 JSON and actual GITHUB_OUTPUT/stage markers. Focused CLI4 tests passed. Tasks2–3 started only after this proof: `local://v15-native-phase76-tracer.json`.

## Retention behavior

- `retention --stage --observed-exit N|unobserved --interpreter --source-commit --candidate --output` admits paths under owned RUNNER_TEMP and validates with the actual canonical readiness report/checkout roster.
- Valid canonical/ok-coherent candidate retains original observed exit and verbatim private output; rejected observed0 forces1 while metadata records0 truthfully; original nonzero retained; unobserved records null/non-success. Existing identity/run/summary/JUnit and signal mappings remain.
- Full serialized failure metadata and retention stdout stage labels hit the unchanged secret gate before publication. Fixed-safe stand-ins replace recognized caller field candidates, with no fragment/echo and non-success.
- Always-run upload includes retained output plus existing JUnit, not raw candidate. Main workflow contents-read/concurrency/job bounds added; matrices3.12–3.14 preserved.

## Bugs found and corrected

Parent reproduced recognized stage/interpreter candidates leaking into failure JSON (stage also stdout). Before/after receipts `local://v15-native-phase76-retention-before.json` / `...retention-after.json` prove actual corrected CLI exit1, truthful observed0, no candidate in artifact or stdio and mode0600. Removed raw-candidate upload bypass, incomplete copied shape checker and parallel atomic writer. Accidental missing declarations surfaced by actual tests/LSP/Ruff were restored; no failure masking or rule suppression.

## Exercised proof

- Actual165-tool report retention exited0, retained bytes hash-equal to genuine tracer output and mode0600: `local://v15-native-phase76-retention-healthy.json`.
- Stdlib receipt child exit7 remains exact in return/GITHUB_OUTPUT on installed3.12/3.13/3.14: `local://v15-native-phase76-receipt-matrix.json`. No full hosted/dependency matrix claim.
- Focused wave217 consumers passed, whole-package Ruff/399-format/mypy337 and touched workflow actionlint passed: `local://v15-native-phase76-wave1-verification.json`.
- Full current package suite2134 passed,16warnings,150.50s/exit0 (`artifact://365`). Bounded source review01 PASS/no blockers or warnings; parent atomic source commit recorded in `local://v15-native-phase76-wave1-commits.json`.

## Limits / security

No hosted CI/artifact-upload run or remote publication/deployment executed. Independent ASVS13/13 now closes T-76-01..03 after the ambiguity repair below, no accepted risk. Current source/image/14-file regression evidence is retained; canonical planning-flag disposition/acceptance remains pending. Shared CI IDs are not marked complete early. Planning commits disabled; no signedSwarm claim.

## Task Commits

1. **Task 1: Readiness tracer** — `31ed79d24d5c7587fd94d2ca3118c772d87633b4`
2. **Task 2: Guarded retention and workflow hygiene** — `31ed79d24d5c7587fd94d2ca3118c772d87633b4`, corrective `c88ac8994ed1c00ef74d4c87cc1c8a67ed5debca`
3. **Task 3: Consumer regressions** — `31ed79d24d5c7587fd94d2ca3118c772d87633b4`, corrective `c88ac8994ed1c00ef74d4c87cc1c8a67ed5debca`

Parent committed the three reviewed target files together after exercised tracer/retention/217focused/full2134 proof. No planning or unrelated lock staged.

## Audit-driven corrective closure

Nyquist/ASVS found duplicate JSON members collapsed last-value-wins. Parent actual genuine165-report with `ok:false` then `ok:true` returned0 and retained ambiguous bytes. Strict recursive object-pairs admission now refuses before canonical validation; same CLI emits effective1/metadata observed0/0600 without report, and genuine unique control remains0/verbatim. Permanent actual CLI root/nested duplicates at observed0/7 preserve exact statuses/GITHUB_OUTPUT/privacy. Before/after `local://v15-native-phase76-retention-duplicate-before.json` / `...duplicate-after.json`;15CLItests passed18.39s, current full product2161/evals26/assembly0, post-doc158 and prior-phase14files254passed. Source-only correctionc88 includes code/test/docs/changelog, no planning/lock.

The copied package changed, so the previous image snapshot was not relabelled. Real committedc88 image rebuilt and both17pass/SIGTERM0, current SPDX/fixed-CRITICAL0, own-tag-deleted archive identity/hash binding verified: `local://v15-native-phase76-post-repair-image-verification.json`. CI02 unclassified and eight descriptorless flags remain honest, not auto-dismissed or waived.
