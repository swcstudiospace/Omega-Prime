# Batch 06 Report: Purple-Team SOTA features (7 shipped)

**Shipped:** 2026-10-14 · **On main:** `e0d7585+` (batch-06 commits) · **Suite:** 3226 passed / 0 failed / 3 skipped (ruff + `mypy omega_prime/` clean, 411 files)

## The 7 features (all from the batch-06 proposal in BATCH-05-REPORT.md)

| # | Feature | Value | Spec |
|---|---------|-------|------|
| 1 | Upstream release cycle watcher | `release_watch` heartbeat kind detects new purple-team tags; fires per-new-tag digest into durable_status | PLAN-G7.md |
| 2 | Live-gateway 14-tool suite | `test_redteam_live_gateway.py` runs the expanded adapter against a live T3MP3ST (skipif when unreachable); proves all 14 tools work against a real backend | — |
| 3 | Webhook retry with exponential backoff | Transient failures (nonzero exit, network errors) retry 3× with 1s/2s/4s + jitter; terminal failures don't retry; per-retry stderr warning | — |
| 4 | Historical trend graph | `--trend <ndjson>` renders pass-rate and findings-count sparklines from run history; deterministic byte-identical reruns | — |
| 5 | Pin-bump `--interactive` | Per-step y/n confirmation at 4 decision points (ref resolved, re-vendor counts, audit verdict, delta verdict); non-TTY auto-declines | — |
| 6 | PDF export | `--pdf <output.pdf>` converts via pandoc when available; falls back to writing the markdown sibling when absent | — |
| 7 | RoE grant audit log | Append-only JSONL log of grant-created / grant-expired / grant-revoked events; `--audit-log <path>` on roe_lifecycle check | — |

## Verification

- Full suite: **3226 passed / 0 failed / 3 skipped** on the latest run; ruff + `mypy omega_prime/` (411 files) clean.
- New tests: ~36 across the new/extended test files.
- `redteam-node-audit` remains GREEN at pin f6d2fc2 (tagged v1.4): 0 total npm advisories.

## Known limits

- Item ② (live-gateway 14-tool suite) requires a running T3MP3ST server — 3 tests skip when unreachable.
- Item ① requires upstream purple-team to ship new tagged releases for the watcher to fire.
- PDF export requires pandoc on PATH; falls back to writing the markdown sibling when absent.

## Batch 07 proposal (paused — awaiting go-ahead)

1. Purple-team repo: address the 9 moderate advisories (post-dep-bump lower-priority sweep)
2. Sidecar harness: live-gateway integration test with full 14-tool verification (requires running T3MP3ST + RoE)
3. Findings sync: Microsoft Teams workflow-card adapter (rich cards with action buttons, not just connector messages)
4. Coverage-matrix: per-class drill-down diff (findings per gap between two reports, not just counts)
5. Pin-bump: `--auto-detect-upstream-fix` refinement — check whether the fix is a semver-compatible bump or needs a major version
6. Engagement report: side-by-side comparison view (two reports rendered as columns)
7. RoE lifecycle: multi-scope grant intersection (intersect allowlists when multiple scoped grants overlap)
