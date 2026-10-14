# Batch 10 Report: Purple-Team SOTA features (5 shipped)

**Shipped:** 2026-10-14 · **On main:** `8b4bb5c` · **Suite:** 3381 passed / 0 failed / 3 skipped (ruff + `mypy omega_prime/` clean, 411 files)

## The 5 features

| # | Feature | Value | Revert |
|---|---------|-------|--------|
| J8 | PagerDuty Events v2 adapter | Trigger events with dedup_key = dedupe hash (idempotent), severity mapped from class/verdict with per-severity override table, `pd:` idempotency marking, fail-closed blank routing key | revert pagerduty branch in findings_sync |
| J9 | Pin-bump `--json` | Machine-readable delta report (deterministic key order); stdout purity — human progress to stderr; emits on every exit path incl. usage errors; composes with --audit-only | revert `--json` flag |
| J10 | Engagement report `--annotate` | Repeatable operator notes rendered as a table, persisted into trend history (kind:note lines, deduped), unknown case-ids flagged | revert `--annotate` flag |
| J11 | RoE `--revoke` | CLI revocation via revoke_artifact seam + audit row; renewal_reminders suppresses revoked artifacts (audit-log scan, fail-open on missing log) with one `roe_revoked` marker per artifact | revert revoke subcommand + suppression |
| J12 | HTML risk-score badge | `Risk score: N / 100` span after h1 in `--export html`, tier-colored via sev-* classes; markdown untouched | revert risk_score_badge |

## Verification

- Full suite: **3381 passed / 0 failed** on main (`8b4bb5c`); ruff + `mypy omega_prime/` (411 files) clean.
- New tests: ~43 across 4 extended test files (122 findings-sync, 89 pin-bump, 98 engagement-report, 47 roe-lifecycle).

## Blocked items (documented, not silent)

- Item ① (absorb upstream v1.5): requires the upstream purple-team release to ship first.
- Item ② (live-gateway e2e with real detection): requires running T3MP3ST + signed RoE — user-owned.

## Batch 11 proposal (paused — awaiting go-ahead)

1. Purple-team repo: absorb upstream v1.5 when it ships (release_watch + pin-bump chain)
2. Live-gateway e2e with real detection validation (requires T3MP3ST + RoE)
3. Findings sync: Opsgenie alert adapter (dedup via alias = dedupe hash, priority from severity)
4. Pin-bump: `--pin <advisory>` targeted mode (bump one advisory's package without the full tree scan)
5. Engagement report: `-- recipient <email>` — render a one-page executive summary section for email
6. RoE lifecycle: expiry watchdog cron (independent ticker that flags expired grants even when heartbeat is down)
7. Coverage-matrix: CSV export of the matrix + risk score for spreadsheet import
