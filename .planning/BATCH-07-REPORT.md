# Batch 07 Report: Purple-Team SOTA features (5 shipped)

**Shipped:** 2026-10-14 · **On main:** `1422a4a` · **Suite:** 3253 passed / 0 failed (ruff + `mypy omega_prime/` clean, 411 files)

## The 5 features

| # | Feature | Value | Revert |
|---|---------|-------|--------|
| H8 | Moderate advisory sweep | TRIAGED block documents 3 dated moderate entries (vitest dev-only, loopback-only backend) with rationale; backstop for the remediation loop | revert TRIAGED block + doc hunk |
| H9 | Teams rich cards | Structured sections with markdown finding details + optional "Open in GitHub" action button; backward compatible when no repo configured | revert `teams_message` enhancement |
| H10 | Drill-down diff | `--diff-drill-down <previous>` shows new/resolved findings between two reports by dedupe hash | revert `--diff-drill-down` flag |
| H11 | Semver-aware pin-bump | `--semver-check` classifies each fix as compatible/major/removed; WARNING block on major bumps | revert `--semver-check` flag |
| H12 | Side-by-side comparison | `--side-by-side <previous>` renders two-column metric comparison table | revert `--side-by-side` flag |

## Verification

- Full suite: **3253 passed / 0 failed** on main (`1422a4a`); ruff + `mypy omega_prime/` (411 files) clean.
- New tests: ~27 across 4 extended/new test files.

## Blocked items (documented, not silent)

- Item ② (live-gateway full-14 verification): requires a running T3MP3ST server + signed RoE artifact — both user-owned.

## Batch 08 proposal (paused — awaiting go-ahead)

1. Purple-team repo: tag v1.5 with the moderate sweep + upstream dep-bump absorbed
2. Sidecar harness: live-gateway e2e verification with real detection validation
3. Findings sync: Microsoft Graph API adapter (full Teams app, not just webhook)
4. Pin-bump: `--bump-commit` flag that also bumps the version in package.json when the fix is semver-compatible
5. Engagement report: `--export html` for browser-viewable reports
6. RoE lifecycle: automatic renewal reminders via heartbeat (T-14d/T-7d/T-3d/T-1d)
7. Coverage-matrix: risk-weighted scoring (weight classes by severity of the gaps they expose)
