---
phase: 76-clean-ci-cd
plan: "05"
subsystem: docs
tags: [release-boundaries, rollback, verified-guidance]
requires:
  - phase: 76-01
    provides: canonical readiness retention
  - phase: 76-02
    provides: preserved independent gates and truthful KB status
  - phase: 76-03
    provides: generated image context proof
  - phase: 76-04
    provides: actual both-transport hardened runtime, SBOM/scan and archive identity proof
provides:
  - Source-linked validation versus publication authorization guidance
  - Prior validated archive/digest rollback without rebuild
  - Existing-layout changelog entry with explicit live-operation exclusions
affects: [77-maintained-docs-and-knowledge-base, release]
actuals:
  tasks: 2
  commits: 1
tech-stack:
  added: []
  patterns: [existing-guide-layout, source-and-evidence-linked-claims]
key-files:
  created: []
  modified: [docs/deploy.md, CHANGELOG.md]
key-decisions:
  - Promotion marker findings remain warnings, never authorization
  - Keep historical kit checks separate from actual phase76 local image proof
  - Missing protected environment is an explicit future prerequisite; no observed approval
requirements-completed: [CI-01, CI-02, CI-03]
completed: 2026-10-11
status: complete
---

# Phase76 Plan05 — Evidence-aligned release and rollback

Existing deployment guidance and changelog now describe the implemented controls, exact rollback identity and actual local proof without implying publishing or deployment.

## Changes

- Generator ownership/current root-submodule exclusions and product adapter/pack preservation; removed the obsolete test-enforces-projection wording and universal secret-detection claim.
- Both transports share supported SSE server command; current verifier/admin safe-probe limits, observed hardening, SIGTERM and configured SPDX/CRITICAL scan stated accurately.
- Default validation versus real opt-in publisher; required manual/ref/environment/source/run/immutable-artifact/outcome/byte bindings, isolated permissions and concurrency, current missing environment, no false approval claim.
- Promotion marker checks explicitly warning-only. Rollback retains the prior successful validation run/artifact IDs/source/archive checksum/image ID/reports/SBOM/scan and, only if previously published, the separate registry RepoDigest. Exact prior bytes, no moving tag or rebuild substitution.
- Preserved matrix/audit/native/license/readiness identity/receipt/JUnit controls and KB missing-key skipped/not-run versus actual PR/human-reviewed retrieval. Changelog adds one entry under existing Remote tool host Changed, no invented release version/heading hierarchy/marketing metrics.
- Historical kit evidence is labelled historical; phase76 local Docker/source proof distinguished from hosted workflow/artifact/environment approval and from live Grok/provider/backend/cluster operations.

## Exercised proof

After plans01–04 current full source union2160passed/16warnings/151.24s (`artifact://402`); both image transports17pass/0fail/0skip/SIGTERM0 each, SPDX generated, configured fixed-CRITICAL scan0, deleted-tag archive rehydrate identical. Complete runtime receipt `local://v15-native-phase76-image-runtime-verification.json`; actual diagnostic tail302lines includes recognized candidate outside last200 and withholds privately (`...image-diagnostic-proof.json`).

After these documentation edits, docs/public-repo/deploy consumers158passed/1warning/8.94s, Ruff0 and401formatted. No platform deployment command or publication invoked from prose. Independent final phase review/security/Nyquist/canonical acceptance remain pending; shared CI requirements are not marked complete early.

## Carried flags / security

CI-02 spec-less edge-probe category **unclassified**, status **unresolved**, verification **null**, remains carried from `local://v15-native-phase76-edge-probe.json`; no automatic dismissal, backstop or invented classification. Source-specific success/failure/absence/secret/observed-exit criteria have real evidence, but this does not rewrite that planning row. Parent/verifier must surface the manual-review flag and descriptorless prohibitions honestly. T-76-13 remains open until independent claims/security closure; no risk accepted or fabricated wired-check descriptor. All21 milestone requirements retain their mapping; remaining phases77/78 are pending, not deferred.

## Task Commits

1. **Task 1: Correct deployment/release/rollback guidance** — `9ec6ab779ee05a4f60bbd02364ad0414f270346e`
2. **Task 2: Conventional changelog entry and exclusions** — `9ec6ab779ee05a4f60bbd02364ad0414f270346e`

Parent source-only atomic commit after exercised proof and docs checks. Planning/unrelated lock excluded, no remote publication or signedSwarm claim.
