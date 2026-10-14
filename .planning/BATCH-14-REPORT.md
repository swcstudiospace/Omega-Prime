# Batch 14 Report: Purple-Team SOTA features (5 shipped)

**Shipped:** 2026-10-14 · **On main:** `448a18e` · **Suite:** 3611 passed / 0 failed / 3 skipped (ruff + `mypy omega_prime/` clean, 411 files)

## The 5 features

| # | Feature | Value | Revert |
|---|---------|-------|--------|
| N8 | Webhook payload templates | Deterministic `{{field}}` substitution over a whitelisted field set (no eval); rendered body HMAC-signed; blank template = legacy payload byte-identical | revert render_template + template branch |
| N9 | Pin-bump `--explain <advisory>` | Human-readable one-pager: WHAT/WHY/FIX/ROLLBACK per advisory; JSON explain object; exit 2 with advisory list when not found | revert `--explain` flag |
| N10 | Engagement report `--recipient-file` | Standalone email file (Subject + <2000-char body); mirrors --recipient block or derives exec-summary; written alongside all outputs | revert `--recipient-file` flag |
| N11 | RoE grant usage ledger | Append-only JSONL record of which grants gated which dispatches (`record_grant_usage`/`read_grant_usage`); tick writes allow/skip verdicts when ledger path configured | revert ledger helpers + tick hook |
| N12 | Coverage diffable scorecard | `--export-scorecard` emits `CLASS→verdict→PASS/FAIL` + `OVERALL→risk-score` lines with documented mapping — greppable by CI annotation tooling | revert `--export-scorecard` flag |

## Verification

- Full suite: **3611 passed / 0 failed** on main (`448a18e`); ruff + `mypy omega_prime/` (411 files) clean.
- New tests: ~53 across 4 extended test files (282 findings-sync+roe-lifecycle combined, 157 pin-bump, 147 engagement-report).

## Blocked items (documented, not silent)

- Item ① (absorb upstream v1.5): requires the upstream purple-team release to ship first.
- Item ② (live-gateway e2e with real detection): requires running T3MP3ST + signed RoE — user-owned.

## Batch 15 proposal (paused — awaiting go-ahead)

1. Purple-team repo: absorb upstream v1.5 when it ships (release_watch + pin-bump chain)
2. Live-gateway e2e with real detection validation (requires T3MP3ST + RoE)
3. Findings sync: rate-limit awareness (honor Retry-After on 429s across all adapters via the shared retry seam)
4. Pin-bump: `--verify` post-bump smoke (re-run the audit against the new pin and assert clean before opening the PR)
5. Engagement report: `--compare-multi <dir>` — trend across ALL reports in a directory (N-way, not just three-way)
6. RoE lifecycle: grant expiry auto-notice via the findings-sync trackers (deliver expiry notices through configured adapters)
7. Coverage-matrix: per-class sparkline glyphs in the markdown matrix (unicode trend markers, deterministic)
