# Batch 03 Report: Purple-Team SOTA features (6 shipped + upstream remediation)

**Shipped:** 2026-10-14 · **On main:** `e01cfc5` · **Suite:** 3100 passed / 0 failed (ruff + `mypy omega_prime/` clean, 407 files)

## Upstream remediation (item ①)

- **purple-team dep-bump PR #2 merged** — `npm audit fix` cleared 1 critical (proxy-addr) + 4 high + 5 moderate; lockfile-only change, tsc + test:pr verified green at the fixed lock.
- **Red Team adapter expanded 11 → 14 tools** — `tempest_blue_gaps`, `tempest_black_cover`, `tempest_white_roe` (upstream additions at f6d2fc2); consistency triangle updated (roster yaml, policy json, bot-interface.json, catalog regenerated).
- **Pin advanced `831a6b0` → `f6d2fc2`** — provenance check + pack-diff + surface check all pass; **`redteam-node-audit` goes GREEN** at the new pin (0 total advisories).

## The 6 code features

| # | Feature | Value | Spec |
|---|---------|-------|------|
| B3-A | Guided RoE signing UX | Interactive prompt flow (non-TTY unchanged); auto-chains into signing; `--print-install` prints export command | PLAN-G2.md |
| B3-B | DEGRADED-cause remediation hints | `durable_status.sidecar.hint` with per-cause remediation commands | — |
| B3-C | Findings sync → Linear issues | `--tracker linear` + `linear_team_id`; severity→priority; GraphQL issueCreate; idempotent | PLAN-F3.md ext |
| B3-D | Severity timeline overlay | Chronological findings grouped critical-first in every engagement report | — |
| B3-E | Coverage-matrix alerts | Heartbeat fires per covered class below pass threshold (configurable, default 80%) | — |
| B3-F | Release-watcher auto-chaining | New upstream tag + `auto_pin_bump=true` ⇒ automatic pin-bump execution | — |

## Verification

- Full suite: **3100 passed / 0 failed** (suite grew 3043 → 3100, +57 tests).
- New tests: 103 (RoE commands 10→14, durable_surface +hints, findings_sync 23→38, engagement_report 18→21, coverage_alert 20, release_watch 11→22, redteam_commands 17, case_from_finding 10).
- Docs: docs/red-team.md (remediation loop, delta step, slash commands ×8, RoE watcher), docs/tool-catalog.md (14 tools), docs/driving-the-bot.md, SETUP.md, grok-bot-native.md, marketing.md (54 commands), CHANGELOG.
- `redteam-node-audit` goes **GREEN** at the new pin: 0 total advisories.

## Batch 04 proposal (paused — awaiting go-ahead)

1. Purple-team repo: absorb the dep-bump PR #2 into a tagged release (v1.4)
2. Sidecar harness: run the 6-case suite against the live gateway post-RoE-signing (requires RoE artifact)
3. Findings sync → Slack webhook (mirror of the GitHub/Linear adapters)
4. Coverage-matrix: per-class drill-down report (findings per gap, not just counts)
5. Pin-bump: `--auto-detect-upstream-fix` mode that checks upstream HEAD audit before running the full bump
6. Engagement report: comparison view (this week vs last week side-by-side)
7. RoE lifecycle: multi-artifact support (multiple concurrent RoE grants with different scopes)
