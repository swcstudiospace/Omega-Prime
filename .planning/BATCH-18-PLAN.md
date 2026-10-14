# Batch 18 Plan: Purple-Team SOTA features (7)

**Go-ahead:** the operator's 2026-10-14 request ("introduce 7 new code features or improvements … Slash commands, workflows, orchestration … merge into main and repeat") is the go-ahead for the Batch 18 proposal in `BATCH-17-REPORT.md`.
**Base:** `main` after Stage 1 (`ci / lint` repaired, PR #31). **Gate:** the exact CI commands — `ruff check omega_prime/`, `ruff format --check omega_prime/`, `mypy omega_prime/`, full pytest.

## Selection

Batch 17's proposal items ① (absorb upstream v1.5) and ② (live-gateway e2e with real detection) are still externally blocked: upstream `purple-team` HEAD is v1.4 (`f6d2fc2`, already pinned) and the e2e needs a running T3MP3ST plus an operator-signed RoE. Items ③–⑦ ship as R1–R5. R6 and R7 replace ①② with the slash-command and orchestration surface the request names.

## The 7 features

| # | Feature | Surface | Default | Revert |
|---|---------|---------|---------|--------|
| R1 | Findings sync per-tracker budget | `redteam.findings_sync.sync_budget` (int or `{default, <tracker>}`) | unset = byte-identical | remove key / revert gate helper |
| R2 | Pin-bump `--report-to PATH` | pin-bump CLI + schedule config | unset = `<root>/report.md` | revert flag |
| R3 | Engagement report `--digest` | `scripts/engagement-report.py` | off | revert flag |
| R4 | Coverage `--export-jsonl PATH` | `scripts/engagement-report.py` | off | revert flag |
| R5 | RoE multi-grant conflict detection | `roe_lifecycle.grant_conflicts` + tick key `grant_conflicts` + CLI `conflicts` | single grant = no key | revert detector + tick key |
| R6 | Chat-native reporting surface | `/omega-redteam-report`, `/omega-redteam-coverage`, `/omega-redteam-sync-plan` | read-only / plan-only | revert 3 command modules |
| R7 | Purple-cycle workflow | `omega_prime/grokbot/purple_cycle.py` + `/omega-redteam-cycle` (plan by default) | plan-only | revert module + command |

## Purple-team lens (abuse → detection → mitigation)

- **R1** Abuse: a noisy or poisoned source floods trackers (alert-fatigue DoS, rate-limit bans), or floods low-severity rows to starve a real one. Detect: `budget_held` + `sync_budget` in every tick result. Mitigate: critical/high and RoE notices bypass the cap; held rows stay unsynced and retry oldest-first; unset or invalid config is a no-op.
- **R2** Abuse: an attacker-influenced path turns the flag into an arbitrary-file-write primitive (symlink, directory, device, traversal). Detect: resolved path echoed in output and JSON. Mitigate: usage-error validation before any subprocess; refuse empty/NUL/directory/non-regular/symlink; atomic temp-file + replace; dry-run writes nothing; the scheduled replay re-validates.
- **R3** Abuse: control characters or newlines from finding data forge lines in cron mail or log pipes. Mitigate: digest lines are built only from counts and a fixed class allowlist; no free text; deterministic and at most 5 lines; no wall clock.
- **R4** Abuse: JSONL framing injection into SIEM ingestion. Mitigate: `json.dumps` escaping, fixed class vocabulary, schema tag per row, one object per line, single trailing newline.
- **R5** Abuse: overlapping grants with contradictory scope silently widen or narrow what a red-team run may touch (an unrestricted grant narrowed by union semantics). Detect: `grant_conflicts` in the tick result and CLI exit code. Mitigate: warn-only — never blocks, never resolves, never signs or writes; authorization (`authorize_all`) is untouched.
- **R6** Abuse: chat arguments steer file reads/writes (`--reports-dir`, `--out`). Mitigate: no path arguments at all (reports dir comes from the host env only); strict flag allowlist via `shlex`; read-only or zero-runner-call plan mode; bounded output.
- **R7** Abuse: an orchestrator chaining git/npm/network and tracker dispatch is a chat-triggerable supply-chain or spam lever and could sidestep RoE. Mitigate: plan-only by default; fail-closed on sidecar state ≠ IDLE and on an expired/missing/invalid RoE; pin-bump is audit-only and never applied; real tracker dispatch is a library/CLI opt-in that chat cannot reach; the cycle never signs or creates RoE and never runs a simulation; resumable checkpoint with per-step records; ledger rows carry no secrets.

## Quality bar

Every feature: typed and validated inputs, fail-closed or fail-open per the existing convention of its module, byte-identical output when its flag/config is unset, deterministic ordering, tests for the success path, boundaries, and the abuse cases above, plus the pinned-count triangle (tests, docs, plugin command files) for new commands.

## Waves

1. **A (parallel, file-disjoint):** R1 `findings_sync.py`; R2 `pin_bump.py` (+ `cron/scheduler.py` replay config only if needed); R3+R4 `scripts/engagement-report.py`; R5 `roe_lifecycle.py`; R7-lib `purple_cycle.py`.
2. **B:** R6 commands, `/omega-redteam-cycle`, command-count/doc triangle, R5 conflict step wired into the cycle.
3. **Verify:** ruff + format + mypy + full pytest, smoke runs of every flag/command, independent diff review, batch report.
