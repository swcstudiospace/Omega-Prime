# Phase 80: Protected release pipeline - Context

**Gathered:** 2026-10-13
**Status:** Ready for planning
**Mode:** Auto-generated (discuss skipped via workflow.skip_discuss)

<domain>
## Phase Boundary

Activate the dead-closed image publication path behind a protected GitHub environment and add a staged rollout with a one-step rollback. Scope: the `grokbot-image-publish` protected environment (required reviewers), the manual `workflow_dispatch publish:true` path with checksum/image-id binding, staged-rollout evidence via `deploy.py check()` staging-first promotion, and an exercised rollback path against a non-production target. Out of scope: automatic publication/deployment (stays manual and default-off), Railway deploy automation (excluded by planning decisions), live production deployment.

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
All implementation choices are at Claude's discretion — discuss phase was skipped per user setting. Use ROADMAP phase goal, success criteria, and codebase conventions to guide decisions.

### Recorded decisions binding this phase
- Publication stays manual, default-off, environment-gated (v15 CI-03 boundary carries forward; `.planning/decisions.md`).
- Rollback is exercised against a non-production target only.

</decisions>

<code_context>
## Existing Code Insights

Verified evidence (`.planning/recon-evidence.md` R2, file:line): `grokbot-image.yml` publish job is DEAD-CLOSED pending the `grokbot-image-publish` protected environment (workflow header lines 1-20; publish job ~line 590+); the `image` job already builds, live-verifies per transport, SBOMs, trivy-scans and uploads validated bytes with throwaway in-run tokens; `omega_prime/grokbot/deploy.py` renders four hardened targets and enforces staging-first promotion with approval/digest evidence (`deploy.py:1570-1576`, `check()` at `:1069-1108`); no CD to any host exists today; image bytes stop at CI artifacts. Full update plan: `.planning/updates/plan-02.md` (U2; rollout mode flag `OMEGA_PRIME_IMAGE_ROLLOUT` staged|direct, staged default).

</code_context>

<specifics>
## Specifics

- Creating the GitHub environment may require repo-admin API rights: attempt via `gh api`; if refused, deliver the exact environment configuration as a documented operator step and keep the workflow gate unchanged (fail-closed).
- Rollback exercise must not touch production: use a local/sandboxed target (e.g., Docker load + run + redeploy previous digest locally).
- Workflow YAML changes follow the strengthened 79-03 verify pattern: parse YAML, grep permissions, no silent skips.

</specifics>

<deferred>
## Deferred Items

- Any production deployment or hosted publish execution beyond the first gated publish attempt (operator-gated).

</deferred>
