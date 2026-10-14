# Batch 17 Report: Purple-Team SOTA features (5 shipped)

**Shipped:** 2026-10-14 · **On main:** `5c0670a` · **Suite:** 3754 passed / 0 failed / 3 skipped (ruff + `mypy omega_prime/` clean, 411 files)

## The 5 features

| # | Feature | Value | Revert |
|---|---------|-------|--------|
| Q8 | Dispatch dry-run ledger | `--dispatch-dry-run` plans would-sync per tracker through the full gate order (grants, quiet hours, idempotency) with zero runner calls/store writes; JSONL ledger + `dispatch_plan` in the tick result | revert `--dispatch-dry-run` flag |
| Q9 | Pin-bump `--pin-note` | Repeatable operator annotations rendered as an Operator notes table (matched by advisory substring, unmatched flagged); JSON notes object; scheduled replays carry notes | revert `--pin-note` flag |
| Q10 | Engagement report `--spark-history` | Inline Run history table across a directory of reports with trend glyphs per row; current report included last | revert `--spark-history` flag |
| Q11 | Engagement report `--grant-matrix` | Grants×trackers coverage table from a RoE artifact's covers key; fail-soft on unreadable artifacts | revert `--grant-matrix` flag |
| Q12 | `--worst N` | Top-N weighted-gap classes auto-appended to the executive summary; JSON worst_classes when rendered | revert `--worst` flag |

## Verification

- Full suite: **3754 passed / 0 failed** on main (`5c0670a`); ruff + `mypy omega_prime/` (411 files) clean.
- New tests: ~51 across 3 extended test files (240 findings-sync, 197 pin-bump, 197 engagement-report).

## Blocked items (documented, not silent)

- Item ① (absorb upstream v1.5): requires the upstream purple-team release to ship first.
- Item ② (live-gateway e2e with real detection): requires running T3MP3ST + signed RoE — user-owned.

## Batch 18 proposal (paused — awaiting go-ahead)

1. Purple-team repo: absorb upstream v1.5 when it ships (release_watch + pin-bump chain)
2. Live-gateway e2e with real detection validation (requires T3MP3ST + RoE)
3. Findings sync: per-tracker sync budget (max dispatches per tick; overflow held with structured reasons)
4. Pin-bump: `--report-to <path>` — write the delta report to an arbitrary path (decouples report.md location)
5. Engagement report: `--digest` — 5-line plain-text summary to stdout for cron/pipe consumption
6. RoE lifecycle: multi-grant conflict detection (overlapping covers with contradictory rules warn in tick)
7. Coverage-matrix: `--export-jsonl` — one line per class for stream consumers
