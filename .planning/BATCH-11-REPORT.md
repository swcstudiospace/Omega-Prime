# Batch 11 Report: Purple-Team SOTA features (5 shipped)

**Shipped:** 2026-10-14 · **On main:** `7761406` · **Suite:** 3442 passed / 0 failed / 3 skipped (ruff + `mypy omega_prime/` clean, 411 files)

## The 5 features

| # | Feature | Value | Revert |
|---|---------|-------|--------|
| K8 | Opsgenie Alert v2 adapter | Alias = dedupe hash, priority mapped P1–P5 with override table, `og:` idempotency, fail-closed blank API key | revert opsgenie branch in findings_sync |
| K9 | Pin-bump `--pin <advisory>` | Targeted audit/fix scoped to named advisories (repeatable, substring match); out-of-scope advisories listed but non-blocking; exit 2 with advisory list when nothing matches | revert `--pin` flag |
| K10 | Engagement report `--exec-summary` | 5-bullet deterministic executive summary at report top; `--recipient` adds a <2000-char email block; JSON gains sections only when flagged | revert `--exec-summary` flag |
| K11 | RoE expiry watchdog | `watchdog_check` reusing the STATES vocabulary + `roe_watchdog` cron kind persisting state transitions to the job digest — expired grants flagged even when heartbeat is down | revert watchdog_check + cron kind |
| K12 | Coverage CSV export | `--export-csv` writes the matrix (fixed column order) + risk-weighted-score row for spreadsheet import | revert `--export-csv` flag |

## Verification

- Full suite: **3442 passed / 0 failed** on main (`7761406`); ruff + `mypy omega_prime/` (411 files) clean.
- New tests: ~61 across 4 extended test files (140 findings-sync, 106 pin-bump, 112 engagement-report, 59 roe-lifecycle).

## Blocked items (documented, not silent)

- Item ① (absorb upstream v1.5): requires the upstream purple-team release to ship first.
- Item ② (live-gateway e2e with real detection): requires running T3MP3ST + signed RoE — user-owned.

## Batch 12 proposal (paused — awaiting go-ahead)

1. Purple-team repo: absorb upstream v1.5 when it ships (release_watch + pin-bump chain)
2. Live-gateway e2e with real detection validation (requires T3MP3ST + RoE)
3. Findings sync: Jira Service Management adapter (create/update issues keyed by dedupe hash)
4. Pin-bump: `--schedule <cron>` mode — run the audit chain on a cron and open PRs only when verdict improves
5. Engagement report: `--threshold gate:<pct>` — exit nonzero when pass rate or risk score drops below a gate
6. RoE lifecycle: multi-grant support (several concurrent artifacts per operator, per-artifact watchdog state)
7. Coverage-matrix: HTML export drill-down gains per-gap evidence links (case id → finding detail anchor)
