# Batch 01 Report: Purple-Team SOTA features (7 shipped)

**Shipped:** 2026-10-13 · **On main:** `0df9cf3` · **Suite:** 2966 passed / 0 failed (ruff + `mypy omega_prime/` clean, 395 files)

## The 7 features

| # | Feature | Value | Commit | Revert |
|---|---------|-------|--------|--------|
| F1 | Upstream dependency remediation loop | Weekly cross-repo audit + bump PRs on purple-team; honest artifact+issue degrade — the path to clearing the CRITICAL properly | `feat(F1)` ee369a9.. | `git revert ee369a9` |
| F2 | `/omega-redteam` slash-command suite (run/status/findings/triage) | Operator drives the harness from chat; BLOCKED cause line verbatim; triage proposals queued for human merge | `feat(F2)` in 1cba11b | revert 1cba11b hunk |
| F3 | Findings → tracker sync | Severity-routed GitHub issues from the findings store, idempotent by dedupe hash, config-off inert | `feat(F3)` in 1cba11b | revert 1cba11b hunk |
| F4 | Adversarial case self-provisioning | Confirmed findings become schema-valid draft cases; drafts never execute until a human activates them | `feat(F4)` in 1cba11b | revert 1cba11b hunk |
| F5 | Delta advisory scanner | Old-vs-new pin advisory diff (new/fixed/rescinded + severity drift) as a reviewable report | `feat(F5)` in e392085+ | revert scripts + doc hunk |
| F6 | RoE lifecycle automation | Heartbeat-driven T-7d/T-1d/expired warnings, durable_status surfacing — authorization never lapses silently | `feat(F6)` in e392085 | revert roe_lifecycle hunk |
| F7 | Scheduled engagement reports | Deterministic weekly posture trend (runs, pass rates, gaps, deltas) | `feat(F7)` in 1cba11b | revert script + config hunk |

Specs: `.planning/phases/updates/PLAN-F1..F7.md`. Merge note: batch 01 landed on main as focused commits on `v16-milestone-close`→`main` (PR #20 merge + follow-on commits pushed to main per your merge-when-green default); each feature is one focused commit or one revertable hunk.

## Verification

- Full suite: **2966 passed / 0 failed** on main (`0df9cf3`); ruff + `mypy omega_prime/` (395 files) clean.
- New tests: 103 (F1 13, F2 17, F3 23, F4 10, F5 14, F6 16, F7 10).
- Docs updated: docs/red-team.md (remediation loop, delta step, slash commands, RoE watcher), docs/tool-catalog.md, docs/driving-the-bot.md, SETUP.md, grok-bot-native.md, marketing counts (46→50 commands), CHANGELOG.

## Known limits (documented, not silent)

- `redteam-node-audit` remains RED on main by design: the upstream pin ships 1 critical + 7 high; upstream v1.3 also carries them; F1's loop is the remediation path. Do not triage the critical.
- RoE remains BLOCKED (no signed artifact) — every /omega-redteam-run returns the harness cause line instead of executing.
- Findings sync defaults off; nightly rollup consumes host-dumped counters (restore-on-boot + atomic dump verified).

## Proposed batch 02 (pause point — awaiting your go-ahead)

1. Purple-team repo dependency-bump execution (paired with F1's loop; clears the critical for real)
2. RoE signing UX inside the Bot (guided artifact creation + validation before handoff)
3. Engagement report → durable_status weekly digest line
4. Findings triage approval from chat (promote PROPOSAL rows into the TRIAGED block via a second confirmation)
5. Sidecar state history visualization (transitions → markdown timeline in engagement reports)
6. Case-library coverage report (technique classes × detection rate matrix)
7. Submodule pin freshness digest (upstream release watcher posting release notes on every new tag)
