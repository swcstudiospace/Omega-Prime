# Batch 15 Report: Purple-Team SOTA features (5 shipped)

**Shipped:** 2026-10-14 · **On main:** `59c6f84` · **Suite:** 3657 passed / 0 failed / 3 skipped (ruff + `mypy omega_prime/` clean, 411 files)

## The 5 features

| # | Feature | Value | Revert |
|---|---------|-------|--------|
| O8 | Retry-After rate-limit awareness | Shared retry seam parses Retry-After on 429s (int seconds, 60s cap; HTTP-date falls back to default backoff); all adapters benefit via the seam | revert _retry_after_delay + _sleep seam |
| O9 | Pin-bump `--verify` | Post-bump re-audit gate: improved verdict + zero untriaged highs + semver holds, else local rollback + exit 4, no PR; JSON verify object | revert `--verify` flag |
| O10 | Engagement report `--compare-multi` | N-way trend across a directory of reports ordered by snapshot timestamp; per-metric →-joined columns with net arrows; unreadable files noted | revert `--compare-multi` flag |
| O11 | RoE expiry notices via adapters | Expiring/expired grants deliver a deterministic notice through the node's configured tracker (same transports), fire-once per (artifact, state), digest-only when unconfigured | revert expiry_notice wiring |
| O12 | Coverage trend glyphs | `▁▂▃▄▅▆▇█` sparkline Trend column in the markdown matrix (win-rate ramp, em dash when no runs); HTML/CSV/JSON untouched | revert Trend column |

## Verification

- Full suite: **3657 passed / 0 failed** on main (`59c6f84`); ruff + `mypy omega_prime/` (411 files) clean.
- New tests: ~50 across 4 extended test files (302 findings-sync+roe-lifecycle combined, 172 pin-bump, 158 engagement-report).

## Blocked items (documented, not silent)

- Item ① (absorb upstream v1.5): requires the upstream purple-team release to ship first.
- Item ② (live-gateway e2e with real detection): requires running T3MP3ST + signed RoE — user-owned.

## Batch 16 proposal (paused — awaiting go-ahead)

1. Purple-team repo: absorb upstream v1.5 when it ships (release_watch + pin-bump chain)
2. Live-gateway e2e with real detection validation (requires T3MP3ST + RoE)
3. Findings sync: per-tracker quiet hours (suppress non-critical dispatches outside a configurable window; criticals always fire)
4. Pin-bump: `--from-audit <file>` — run the full bump flow from a previously exported audit JSON without re-auditing
5. Engagement report: `--sidebar` — one-glance KPI strip (runs/pass/coverage/risk/gate) at the very top of every output mode
6. RoE lifecycle: grant renewal one-click link generation (pre-signed renewal command echo, embedded in notices)
7. Coverage-matrix: `--baseline <class>=<pct>` — per-class floor gates wired into the existing threshold mechanism
