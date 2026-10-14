# Batch 08 Report: Purple-Team SOTA features (5 shipped)

**Shipped:** 2026-10-14 · **On main:** `d9baf48` · **Suite:** 3306 passed / 0 failed / 3 skipped (ruff + `mypy omega_prime/` clean, 411 files)

## The 5 features

| # | Feature | Value | Revert |
|---|---------|-------|--------|
| G8 | Microsoft Graph adapter | Full Graph-mode sender: OAuth2 client-credentials token + chatMessage POSTs per finding; rich card reused from the Teams webhook path; fail-closed on blank/missing config; `--mode graph` CLI branch | revert graph branch in findings_sync |
| G9 | Pin-bump `--bump-commit` | Bumps redteam/package.json version (minor or patch) when verdict improved AND every fix semver-compatible; fail-closed on major/unknown; composes with --dry-run and --open-pr | revert `--bump-commit` flag |
| G10 | Engagement report `--export html` | Self-contained HTML export via stdlib-only deterministic markdown converter; inline severity/verdict colors; canonical .md and JSON output untouched | revert `--export` flag |
| G11 | RoE renewal reminders | T-14d/T-7d/T-3d/T-1d reminders, once per (artifact, threshold) via heartbeat dedupe pattern; `roe_renewal` heartbeat kind; fail-closed on malformed artifacts | revert renewal_reminders + heartbeat wiring |
| G12 | Risk-weighted coverage score | Deterministic weights (critical=10, high=5, moderate=2, low=1) × class risk tiers; `## Risk-weighted score` section with per-class breakdown | revert risk_weighted_score |

## Verification

- Full suite: **3306 passed / 0 failed** on main (`d9baf48`); ruff + `mypy omega_prime/` (411 files) clean.
- New tests: ~53 across 4 extended test files (95 findings-sync, 71 pin-bump, 78 engagement-report, 37 roe-lifecycle).

## Blocked items (documented, not silent)

- Item ① (tag v1.5): requires upstream purple-team release cycle to move first.
- Item ② (live-gateway full-14 e2e with real detection validation): requires running T3MP3ST + signed RoE — both user-owned.

## Batch 09 proposal (paused — awaiting go-ahead)

1. Purple-team repo: absorb upstream v1.5 when it ships (release_watch fires; pin-bump chain runs end-to-end)
2. Sidecar harness: live-gateway e2e with real detection validation (requires running T3MP3ST + RoE)
3. Findings sync: Slack Block Kit adapter (rich layout blocks, not just plain text)
4. Pin-bump: `--audit-only` mode (run the audit + semver classification without touching the lockfile)
5. Engagement report: `--compare` combining side-by-side + drill-down diff + trend in one run
6. RoE lifecycle: renewal reminders via Microsoft Graph (reuse the G8 adapter for reminder delivery)
7. Coverage-matrix: per-class finding drill-down in the HTML export (clickable class rows)
