# Batch 02 Report: Purple-Team SOTA features (7 shipped)

**Shipped:** 2026-10-13 · **On main:** `955309a` (PR #30, squash of `batch-02-purpleteam`) · **Suite:** 3043 passed / 0 failed (ruff + `mypy omega_prime/` clean, 406 files)

## The 7 features

| # | Feature | Value | Spec | Revert |
|---|---------|-------|------|--------|
| G1 | Pin-bump automation | One command runs the full 6-step bump runbook: submodule ref, pack re-vendor, provenance loop, npm audit, delta report, optional PR — green/regressed/degraded exits | PLAN-G1.md | revert batch-02 merge hunks in `omega_prime/grokbot/pin_bump.py` |
| G2 | RoE in-chat signing UX | roe-create → roe-sign → roe-check from the Bot; operator never touches shell for authorization | PLAN-G2.md | revert `omega_prime/commands/redteam_roe_*.py` |
| G3 | Weekly durable digest | Engagement posture line lands in `durable_status` weekly via heartbeat — visible without asking | PLAN-G3.md | revert heartbeat `engagement_digest` block |
| G4 | Triage approvals from chat | Preview exact diff, `--apply` mutates with self-check + rollback, `--untriage`, dirty-file refusal | PLAN-G4.md | revert `omega_prime/commands/redteam_triage_apply.py` |
| G5 | State timeline section | Chronological IDLE→RUNNING→… transitions with per-cause footer in every report | PLAN-G5.md | revert timeline section |
| G6 | Coverage matrix | Per-technique class × cases/runs/pass-rate/gaps/verdict table | PLAN-G6.md | revert matrix section |
| G7 | Upstream release watcher | Daily tag watch with silent seed + per-new-tag digest into `durable_status.releases` | PLAN-G7.md | revert `release_watch.py` + heartbeat block |

## Verification

- Full suite: **3043 passed / 0 failed** at the merge commit on main.
- CI: ci.yml success (verify matrix 3.12/3.13/3.14, lint, types, docs, redteam-provenance, sandbox-policy-drift), grokbot-image success.
- `redteam-node-audit` remains intentionally RED: upstream pin `831a6b0` (and v1.3) ships 1 critical + 7 high npm advisories. G1's automation is the remediation path — run `python -m omega_prime.grokbot.pin_bump --to <upstream-ref>` once purple-team lands dep bumps; do not triage the critical.

## Batch 03 proposal (paused — awaiting go-ahead)

1. Execute G1 against the first upstream dep-bump release (clears the critical for real)
2. RoE signing UX: guided prompt flow inside roe-create (interactive prompts, not flags)
3. Findings sync → Linear issues (mirror of the GitHubIssues adapter)
4. Coverage-matrix alerts: heartbeat fires when a covered class drops below pass threshold
5. Release-watcher → G1 chaining: new tag automatically opens a pin-bump PR draft
6. Engagement reports: per-finding severity timeline overlay
7. Sidecar state machine: DEGRADED-cause auto-remediation hints in durable output
