# Batch 16 Report: Purple-Team SOTA features (5 shipped)

**Shipped:** 2026-10-14 · **On main:** `4be3a56` · **Suite:** 3703 passed / 0 failed / 3 skipped (ruff + `mypy omega_prime/` clean, 411 files)

## The 5 features

| # | Feature | Value | Revert |
|---|---------|-------|--------|
| P8 | Per-tracker quiet hours | `quiet_hours {start,end}` window (wrap-safe) holds non-critical/high findings with structured `quiet-hours` reasons; criticals, notices, and expiry notices always fire; unconfigured = byte-identical behavior | revert in_quiet_hours + hold logic |
| P9 | Pin-bump `--from-audit` | Full bump flow from a previously exported audit JSON — no node/npm re-audit; composes with dry-run/semver/json/bump-commit/verify | revert `--from-audit` flag |
| P10 | Engagement report `--sidebar` | One-glance KPI strip (Runs/Pass/Coverage/Risk/Gate) after the title, with Δ arrows when a baseline exists; flows into HTML; JSON sidebar object when flagged | revert `--sidebar` flag |
| P11 | Renewal links in notices | `renewal_command(artifact)` builds the deterministic re-sign command; expiry notices embed a final `Renew:` line when the artifact has a path | revert renewal_command + notice line |
| P12 | Per-class `--baseline` gates | Per-class win-rate floors wired into the threshold mechanism; class failures listed in gate line/JSON; unknown class = exit 2 with valid classes | revert `--baseline` flag |

## Verification

- Full suite: **3703 passed / 0 failed** on main (`4be3a56`); ruff + `mypy omega_prime/` (411 files) clean.
- New tests: ~57 across 4 extended test files (320 findings-sync+roe-lifecycle combined, 183 pin-bump, 175 engagement-report).

## Blocked items (documented, not silent)

- Item ① (absorb upstream v1.5): requires the upstream purple-team release to ship first.
- Item ② (live-gateway e2e with real detection): requires running T3MP3ST + signed RoE — user-owned.

## Batch 17 proposal (paused — awaiting go-ahead)

1. Purple-team repo: absorb upstream v1.5 when it ships (release_watch + pin-bump chain)
2. Live-gateway e2e with real detection validation (requires T3MP3ST + RoE)
3. Findings sync: dispatch dry-run ledger (`--dispatch-dry-run` logs what WOULD sync per tracker without touching stores)
4. Pin-bump: `--pin-note <advisory>=<text>` — operator annotations persisted into the delta report
5. Engagement report: `--spark-history <dir>` — inline sparkline history table across all reports in a dir
6. RoE lifecycle: grant coverage matrix in the report (which grants cover which trackers, one table)
7. Coverage-matrix: `--worst N` — top-N weighted-gap classes surfaced in the executive summary automatically
