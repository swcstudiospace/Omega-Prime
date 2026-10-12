---
phase: 75-integrated-local-readiness
plan: '04'
subsystem: integrated-readiness
tags:
- native-gsd
- isolated-local
- behavior-proof
provides:
- One fresh-child readiness controller with truthful full-roster reports
affects:
- 76-clean-ci-cd
- 77-maintained-documentation-and-knowledge-base
key-files:
  modified:
  - omega_prime/grokbot/local_validation.py
  - omega_prime/grokbot/readiness.py
  - omega_prime/tests/test_grokbot_readiness.py
  removed: []
requirements-completed:
- LOCAL-01
- LOCAL-02
- GROK-01
- GROK-02
actuals:
  tasks: 3
  commits: 0
completed: '2026-10-10'
status: complete
---

# Phase75 Plan04: One fresh-child readiness controller with truthful full-roster reports

Maintained default and include-prime commands execute actual production handlers in one fresh child, validate all154 canonical rows separately from117/154offered names, and require nonzero on eligible failures/gaps. Public export omits learned/runtime/private data, maintains provenance across nested export, and executes genuine OFF absence/refusal before ON. Both real MCP transports prove file effects, sys_cache denial/grant/revoke/unchanged-value, sanitized audit and clean shutdown.

## Tasks completed
- Task 1: Shared isolation helper and probe
- Task 2: Readiness CLI, JSON v1 report, loopback transport proof
- Task 3: Meaningful CLI and isolation tests

## Actual verification
- Maintained local profile: exit0, ok=true,154rows/117offered/50exercised/67blocked/37disabled/0failed.
- Maintained include-prime profile: exit0, ok=true,154rows/154offered/82exercised/72blocked/0disabled/0failed.
- Current reconciled owned public+native clone:2085pytestPASS,0failures/errors/skips; real host self-test22PASS,0FAIL/SKIP, clean shutdown.
- Existing26eval cases, pip check, ruff,372-file format check, mypy333, maintained catalog, prompt assembly and setup checks passed.
- Session DB SHA matches baseline; native size/mtime/inode preserved; all five frozen production guard/security/manifest SHA values unchanged; caller-state canary absent; nested learned Python/data omission and cleanup observed.
- Exact parent receipts: `local://v15-phase75-verification.json`; final reports: `local://v15-readiness-local-verified-report.json`, `local://v15-readiness-prime-verified-report.json`.

## Files changed
- `omega_prime/grokbot/local_validation.py`
- `omega_prime/grokbot/readiness.py`
- `omega_prime/tests/test_grokbot_readiness.py`

## Decisions and deviations
- Native GSD lane explicitly authorized; no signed Swarm row or `.swarm` mutation.
- Parent corrected observed scenario/API/fixture/export/approval/transport defects through the assigned domain owners, then exercised the corrected paths; failed iterations are retained, not suppressed.
- No live Grok/provider/external service/device/browser proof, push/PR/merge/tag/publication/deployment or upstream/package/preferences/worktree changes.

## Threat Flags
- All independently reproduced exporter roots now have actual after-proof and current regressions, including regular credential-named Python/excluded ancestors, both alias ends, private Prime entries and missing/malformed provenance. One predicate guards copy and inventory admission; private-named public product Python requires tracked/inherited membership, fresh safe product Python stays ungated. Prime producer windows now pass complete raw input to the shared guard, with actual no-fragment and prior-output preservation proof. Current2085-test union/profiles/project gates and original source/frozen baselines pass; independent all50-threat closure and canonical verification pending.

## Task Commits
No commits yet; parent-owned phase commit follows the complete source+documentation union and independent gates.
