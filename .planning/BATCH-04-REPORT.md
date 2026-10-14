# Batch 04 Report: Purple-Team SOTA features (7 shipped)

**Shipped:** 2026-10-14 · **On main:** `da3c92f` · **Suite:** 3143 passed / 0 failed (ruff + `mypy omega_prime/` clean, 407 files)

## The 7 features

| # | Feature | Value | Revert |
|---|---------|-------|--------|
| H1 | Purple-team tagged release v1.4 | Upstream tagged at the clean-audit SHA; VENDOR.md documents the tag; audit at 0 total advisories | revert VENDOR.md tag line |
| H2 | Live-gateway readiness check | `roe_lifecycle readiness --artifact <path>` returns JSON {roe_state, gateway_reachable, suites_loaded, suites_total, ready_for_live} — operator knows if live engagements can start without running one | revert `readiness` subcommand |
| H3 | Findings sync → Slack webhook | `--tracker slack` posts severity-colored attachments via webhook; same dedupe/idempotency as GitHub/Linear modes | revert Slack mode in findings_sync.py |
| H4 | Coverage-matrix drill-down | `--drill-down <class>` renders per-finding detail table for any technique class | revert `--drill-down` flag |
| H5 | Pin-bump auto-detect upstream fix | `--auto-detect-upstream-fix` probes upstream audit before bumping — skips wasted attempts when upstream hasn't fixed advisories yet | revert `--auto-detect-upstream-fix` flag |
| H6 | Engagement report comparison | `--compare <previous>` renders week-over-week metric deltas with directional arrows | revert `--compare` flag |
| H7 | RoE multi-artifact support | `roe_files` config key accepts multiple concurrent grants; union of tool allowlists; one expiring doesn't invalidate others | revert `authorize_all` + `roe_files` |

Specs: `.planning/phases/updates/PLAN-G2.md` (H2), `.planning/phases/updates/PLAN-F3.md` ext (H3), `.planning/phases/updates/PLAN-F7.md` ext (H4), `.planning/phases/updates/PLAN-G1.md` ext (H5).

## Verification

- Full suite: **3143 passed / 0 failed** on main (`da3c92f`); ruff + `mypy omega_prime/` (407 files) clean.
- CI: ci.yml success on the PR branch (verify matrix, lint, types, docs, redteam-provenance, sandbox-policy-drift all green).
- New tests: ~80 across 7 extended/new test files.
- `redteam-node-audit`: **GREEN** on main (0 total advisories at pin f6d2fc2 / tagged v1.4).

## Known limits

- Live-gateway readiness check (H2) requires the gateway to be running; TCP connect timeout is 2s.
- Slack sync (H3) requires `gh api` webhook URL; no OAuth flow.
- Multi-artifact RoE (H7) is backward-compatible: single `roe_file` still works; `roe_files` replaces it when non-empty.
- Release-watcher chaining (F/batch-03) and pin-bump auto-detect (H5) are separate opt-in flags.

## Batch 05 proposal (paused — awaiting go-ahead)

1. Purple-team repo: address the 9 moderate advisories (lower priority but clean sweep)
2. Sidecar harness: live-gateway integration test (requires running T3MP3ST server)
3. Findings sync: Microsoft Teams webhook adapter (mirror of Slack)
4. Coverage-matrix: per-class drill-down with diff-vs-previous-week
5. Pin-bump: `--dry-run` mode that shows the delta without any file mutation
6. Engagement report: JSON output mode for programmatic consumption
7. RoE lifecycle: multi-scope grant composition (intersect allowlists across scoped grants)
