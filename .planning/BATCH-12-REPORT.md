# Batch 12 Report: Purple-Team SOTA features (5 shipped)

**Shipped:** 2026-10-14 · **On main:** `b733ace` · **Suite:** 3506 passed / 0 failed / 3 skipped (ruff + `mypy omega_prime/` clean, 411 files)

## The 5 features

| # | Feature | Value | Revert |
|---|---------|-------|--------|
| L8 | Jira Service Management adapter | Create/update issues keyed by dedupe hash via JQL label search; Basic auth rides argv only; `jira:` idempotency; fail-closed incomplete config | revert jira branch in findings_sync |
| L9 | Pin-bump `--schedule <cron>` | Registers a recurring audit job in the cron scheduler; PR opens only on improved + semver-compatible verdict; invalid cron = exit 2 before registering; duplicate name = replace | revert `--schedule` flag + PIN_BUMP_SCHEDULE kind |
| L10 | Engagement report `--threshold` | `gate:<pct>` / `gate-risk:<score>` exit-3 gates with Gate: PASS/FAIL line and JSON gate object; boundary passes; malformed specs exit 1 pre-write | revert `--threshold` flag |
| L11 | RoE multi-grant support | `watchdog_check_all` + list-form renewal_reminders/heartbeat keys with per-(artifact, kind) dedupe across the set; single-artifact behavior byte-identical | revert watchdog_check_all + list plumbing |
| L12 | HTML evidence links | Gap case ids in the drill-down link to collapsible per-gap details blocks with associated finding titles; markdown untouched | revert html gap details |

## Verification

- Full suite: **3506 passed / 0 failed** on main (`b733ace`); ruff + `mypy omega_prime/` (411 files) clean.
- New tests: ~74 across 4 extended test files (157 findings-sync, 128 pin-bump, 126 engagement-report, 70 roe-lifecycle).

## Blocked items (documented, not silent)

- Item ① (absorb upstream v1.5): requires the upstream purple-team release to ship first.
- Item ② (live-gateway e2e with real detection): requires running T3MP3ST + signed RoE — user-owned.

## Batch 13 proposal (paused — awaiting go-ahead)

1. Purple-team repo: absorb upstream v1.5 when it ships (release_watch + pin-bump chain)
2. Live-gateway e2e with real detection validation (requires T3MP3ST + RoE)
3. Findings sync: webhook signature (HMAC) support for outbound webhooks (signing + secret rotation)
4. Pin-bump: `--rollback` to the previous pin (re-pin old lockfile from the audit trail, gated on RoE)
5. Engagement report: `--delta <older-report>` three-way trend (older → previous → current arrows)
6. RoE lifecycle: per-grant scoped tool allowlists enforced at findings-sync dispatch (grant names which trackers it covers)
7. Coverage-matrix: `--export-md` fragment (embeddable markdown table for PR comments)
