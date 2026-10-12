---
phase: 75-integrated-local-readiness
plan: '03'
subsystem: integrated-readiness
tags:
- native-gsd
- isolated-local
- behavior-proof
provides:
- Real isolated host self-test with guarded diagnostics
affects:
- 76-clean-ci-cd
- 77-maintained-documentation-and-knowledge-base
key-files:
  modified:
  - omega_prime/grokbot/oneclick.py
  - omega_prime/tests/test_grokbot_oneclick.py
  removed: []
requirements-completed:
- LOCAL-01
- GROK-01
actuals:
  tasks: 2
  commits: 0
completed: '2026-10-10'
status: complete
---

# Phase75 Plan03: Real isolated host self-test with guarded diagnostics

Self-test exports an owned install with explicit fresh HOME/XDG/state/work/audit/native roots and no caller credentials/config/schedules. Full diagnostic redaction and unchanged strong classification both precede any clipping/window; the final scanner remains defense in depth, and rejection publishes fixed safe metadata without fragments.

## Tasks completed
- Task 1: Isolate run_self_test on the frozen shared layout contract
- Task 2: Isolation-effect regressions for self-test

## Actual verification
- Maintained local profile: exit0, ok=true,154rows/117offered/50exercised/67blocked/37disabled/0failed.
- Maintained include-prime profile: exit0, ok=true,154rows/154offered/82exercised/72blocked/0disabled/0failed.
- Current reconciled owned public+native clone:2079pytestPASS,0failures/errors/skips; real host self-test22PASS,0FAIL/SKIP, clean shutdown.
- Existing26eval cases, pip check, ruff,371-file format check, mypy332, maintained catalog, prompt assembly and setup checks passed.
- Session DB SHA matches baseline; native size/mtime/inode preserved; all five frozen production guard/security/manifest SHA values unchanged; caller-state canary absent; nested learned Python/data omission and cleanup observed.
- Exact parent receipts: `local://v15-phase75-verification.json`; final reports: `local://v15-readiness-local-verified-report.json`, `local://v15-readiness-prime-verified-report.json`.

## Files changed
- `omega_prime/grokbot/oneclick.py`
- `omega_prime/tests/test_grokbot_oneclick.py`

## Decisions and deviations
- Native GSD lane explicitly authorized; no signed Swarm row or `.swarm` mutation.
- Parent corrected observed scenario/API/fixture/export/approval/transport defects through the assigned domain owners, then exercised the corrected paths; failed iterations are retained, not suppressed.
- No live Grok/provider/external service/device/browser proof, push/PR/merge/tag/publication/deployment or upstream/package/preferences/worktree changes.

## Threat Flags
- Independent review reopened T-75-03-04 after parent reproduced scanner-only token fragments surviving clipping. The assigned owner added the full-redacted-input gate; actual owned smoke now publishes no fragment, generic/short-omk/PEM boundary regressions pass in the2079-test current union, and the maintained real self-test passes22checks. Independent security re-verification and canonical phase verification remain pending.

## Task Commits
No commits yet; parent-owned phase commit follows the complete source+documentation union and independent gates.
