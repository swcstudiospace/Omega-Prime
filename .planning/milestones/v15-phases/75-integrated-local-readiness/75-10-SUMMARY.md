---
phase: 75-integrated-local-readiness
plan: '10'
subsystem: integrated-readiness
tags:
- native-gsd
- isolated-local
- behavior-proof
provides:
- Canonical initialized public MCP client with bounded cleanup
affects:
- 76-clean-ci-cd
- 77-maintained-documentation-and-knowledge-base
key-files:
  modified:
  - omega_prime/tools/mcp_session.py
  - omega_prime/tools/platform.py
  - omega_prime/tests/test_platform.py
  - omega_prime/tests/test_mcp_session.py
  removed:
  - omega_prime/tools/mcp_client.py
requirements-completed:
- LOCAL-02
- LOCAL-03
actuals:
  tasks: 3
  commits: 0
completed: '2026-10-10'
status: complete
---

# Phase75 Plan10: Canonical initialized public MCP client with bounded cleanup

Public mcp_call uses the existing official initialized SDK session implementation outside or within an active event loop, preserving structured errors, deadlines, private stderr/protocol diagnostics and joined/cancellation-shielded cleanup. Obsolete uninitialized module is removed without alias/re-export.

## Tasks completed
- Task 1: Active-loop-safe synchronous bridge in the canonical session client
- Task 2: Canonical platform binding and obsolete client removal
- Task 3: Consumer-boundary session tests over an official SDK server

## Actual verification
- Maintained local profile: exit0, ok=true,154rows/117offered/50exercised/67blocked/37disabled/0failed.
- Maintained include-prime profile: exit0, ok=true,154rows/154offered/82exercised/72blocked/0disabled/0failed.
- Owned public+native clone:2069pytestPASS,0failures/errors/skips; real host self-test22PASS,0FAIL/SKIP, clean shutdown.
- Existing26eval cases, pip check, ruff,371-file format check, mypy332, maintained catalog, prompt assembly and setup checks passed.
- Session DB SHA matches baseline; native size/mtime/inode preserved; all five frozen production guard/security/manifest SHA values unchanged; caller-state canary absent; nested learned Python/data omission and cleanup observed.
- Exact parent receipts: `local://v15-phase75-verification.json`; final reports: `local://v15-readiness-local-verified-report.json`, `local://v15-readiness-prime-verified-report.json`.

## Files changed
- `omega_prime/tools/mcp_session.py`
- `omega_prime/tools/platform.py`
- `omega_prime/tools/mcp_client.py` — removed obsolete implementation
- `omega_prime/tests/test_platform.py`
- `omega_prime/tests/test_mcp_session.py`

## Decisions and deviations
- Native GSD lane explicitly authorized; no signed Swarm row or `.swarm` mutation.
- Parent corrected observed scenario/API/fixture/export/approval/transport defects through the assigned domain owners, then exercised the corrected paths; failed iterations are retained, not suppressed.
- No live Grok/provider/external service/device/browser proof, push/PR/merge/tag/publication/deployment or upstream/package/preferences/worktree changes.

## Threat Flags
- Planned isolation, persistence, authorization, output and lifecycle controls have actual source/runtime receipts above. Independent security review and canonical phase verification remain pending; this source-plan summary is not a phase security/pass verdict.

## Task Commits
No commits yet; parent-owned phase commit follows the complete source+documentation union and independent gates.
