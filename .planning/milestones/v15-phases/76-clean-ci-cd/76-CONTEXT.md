# Phase 76: Clean CI/CD — Context

Configured skip-discuss; all three CI requirements and dependency on passed phase75 are user-approved. Detailed planning/execution waits for phase75 proof.

## Locked boundary
Preserve Python3.12–3.14 verification/lint/type matrices, interpreter/distribution/command/JUnit receipts, eval/assembly/catalog/docs checks, lockfile audit with only the existing documented PYSEC-2026-4114 exception, and pinned native/license/parity gates. Add least explicit permissions, concurrency and bounded jobs; remove real redundancy only. CI must run the maintained isolated readiness CLI and retain sanitized success/failure evidence. Image validation keeps read-only uid10001 container checks, both transports, SIGTERM, SBOM and blocking critical scan. Publication remains default-off, manual, protected-ref and same-source-commit gated, using validated bytes rather than an unverified rebuild. No image publication/deployment/remote operations are executed. KB refresh stays PR-only, with truthful missing-secret skip and no automatic merge. Correct deployment/rollback documentation.

## Execution
Explicit native GSD authorization replaces signed Swarm-only routing. Parallel disjoint targetlists, one shared doc/root owner, no worktrees/upstream/.swarm writes. Children skip all tests/build/lint/format/gates/commits; parent runs actual union checks and container smoke before independent verification/review/security and transition. Incidental source/workflow-text tests are deleted, never repinned. Core inventory: agent://DocsDeliveryInventory/report and /files; full scope: local://v15-readiness-brief.md.

## Deferred scope
No approved requirement deferred. Live publication/deployment is excluded, not claimed tested.
