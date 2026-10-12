# Project Retrospective

*A living document updated after each milestone. Lessons feed forward into future planning.*

## Milestone: v15 — Integrated Readiness and Repository Delivery

**Shipped:** 2026-10-12
**Phases:** 5 (75, 76, 77, inserted 77.1, 78) | **Plans:** 33 | **Sessions:** at least 2 recorded (`01a125ac`, `01a12fb2`)

### What Was Built
- One isolated readiness command that accounts for all 165 roster tools through production dispatch, with real local effects and no Grok credentials.
- CI that keeps the supported verify/lint/types matrices, retains sanitized readiness evidence and validates the image behind a manual publish boundary.
- Source-linked canonical docs beside one complete, refreshable historical mirror of all 21 Greptile documents.
- Grok Bot import packaging: root `mcp.json`, Cursor plugin manifests, 46 generated `/omega-*` commands, a stamp-gated bootstrap launcher with exit classes 10–13, and a Bot-driven setup prompt. Live import is the user's deferred UAT.
- Repository marketing that states only verified capabilities, an accessible banner and a social card with its inspected raster.

### What Worked
- Digest-fingerprinted verification refused to pass a report whose covered files had changed, so every final verdict describes the merged tree.
- The milestone integration check found two defects that phase-level verification missed: stale include-prime counts in `docs/local-validation.md` and Bot template first-run paths that did not exist at the clone root.
- Evidence levels (local proof, optional capability, unproven external) kept marketing honest about the unproven live import.
- Fresh-clone demo runs with recorded exit codes gave the marketing page receipts instead of claims.

### What Was Inefficient
- Phase reports covered shared files (REQUIREMENTS, ROADMAP, CHANGELOG, README, `test_docs.py`), so every later phase and transition made earlier reports stale; phases 75–77 were re-verified three times.
- Acknowledging open artifacts at close wrote a marker into `77.1-UAT.md`, which the 77.1 report covers, so 77.1 needed one more re-verification.
- Nyquist tests were written after the phase review; their own follow-up review found seven robustness gaps.
- A `command | jq || retry` pipeline re-invoked the irreversible `milestone.complete` after it had already succeeded. The CLI's unstarted-phase guard refused the second call, but the pattern was unsafe.

### Patterns Established
- Tag every public capability claim with its evidence level and link unproven rows to the user UAT list.
- Record a user-deferred human check as `verification_deferred_human`, acknowledge it at close, and resume it with `/gsd:verify-work`.
- Guards that must fail closed carry an in-test negative control (synthesized inputs), not a dependency on the shipped asset.

### Key Lessons
1. Keep a phase report's covered set to phase-owned files where possible; shared planning docs in a covered set force re-verification at every transition.
2. Never wrap an irreversible CLI in `|| retry`; write its output to a file and read it.
3. Review tests written by validation agents before the regression gate, not after the phase review.
4. Run the milestone integration check before the final transition so its fixes land in one re-verification pass.

### Cost Observations
- Model mix: not recorded
- Sessions: at least 2 recorded
- Notable: re-verification after cross-phase edits was the largest repeated cost

---

## Cross-Milestone Trends

### Process Evolution

| Milestone | Sessions | Phases | Key Change |
|-----------|----------|--------|------------|
| v15 | 2+ | 5 | Native GSD with per-phase review, security and Nyquist gates, digest-checked verification and a milestone integration check |

### Cumulative Quality

| Milestone | Tests | Coverage | Zero-Dep Additions |
|-----------|-------|----------|-------------------|
| v13 | 1957 | not measured | — |
| v14 | 1992 | not measured | — |
| v15 | 2544 | not measured | 0 new packages (PyYAML declared at its locked version) |

### Top Lessons (Verified Across Milestones)

1. A completion claim needs the command and its exit code (v13, v14, v15 closeouts).
2. Live Grok, provider and account behavior stays unproven until a person runs it; local proof never substitutes (v11–v15).
