# Batch 18 Plan: Purple-Team SOTA features (7)

**Go-ahead:** the operator's 2026-10-14 request ("introduce 7 new code features or improvements … Slash commands, workflows, orchestration … merge into main and repeat") is the go-ahead for the Batch 18 proposal in `BATCH-17-REPORT.md`.
**Base:** `main` after Stage 1 (`ci / lint` repaired, PR #31). **Gate:** the exact CI commands — `ruff check omega_prime/`, `ruff format --check omega_prime/`, `mypy omega_prime/`, full pytest.

## Selection

Batch 17's proposal items ① (absorb upstream v1.5) and ② (live-gateway e2e with real detection) are still externally blocked: upstream `purple-team` HEAD is v1.4 (`f6d2fc2`, already pinned) and the e2e needs a running T3MP3ST plus an operator-signed RoE. Items ③–⑦ ship as R1–R5. R6 and R7 replace ①② with the slash-command and orchestration surface the request names.

## The 7 features

| # | Feature | Surface | Default | Revert |
|---|---------|---------|---------|--------|
| R1 | Findings sync per-tracker budget | `redteam.findings_sync.sync_budget` (int or `{default, <tracker>}`) | unset = identical unless >=2 conflicting grants (advisory `grant_conflicts` key) | remove key / revert gate helper |
| R2 | Pin-bump `--report-to PATH` | pin-bump CLI + schedule config | unset = `<root>/report.md` | revert flag |
| R3 | Engagement report `--digest` | `scripts/engagement-report.py` | off | revert flag |
| R4 | Coverage `--export-jsonl PATH` | `scripts/engagement-report.py` | off | revert flag |
| R5 | RoE multi-grant conflict detection | `roe_lifecycle.grant_conflicts` + tick key `grant_conflicts` + CLI `conflicts` | single grant = no key | revert detector + tick key |
| R6 | Chat-native reporting surface | `/omega-redteam-report`, `/omega-redteam-coverage`, `/omega-redteam-sync-plan` | read-only / plan-only | revert 3 command modules |
| R7 | Purple-cycle workflow | `omega_prime/grokbot/purple_cycle.py` + `/omega-redteam-cycle` (plan by default) | plan-only | revert module + command |

## Purple-team lens (abuse → detection → mitigation)

- **R1** Abuse: a noisy or poisoned source floods trackers (alert-fatigue DoS, rate-limit bans), or floods low-severity rows to starve a real one. Detect: `budget_held` + `sync_budget` on results of a sync that ran; `sync_budget_invalid` when the key is present but unusable. Mitigate: the budget is spent medium, then low, then other severities, oldest first; RoE notices bypass the cap; held rows stay unsynced and retry on the next tick; an unusable config is off and flagged. Limit: critical/high rows are deliberately uncapped, so a source that stamps everything critical is not limited by the budget.
- **R2** Abuse: an attacker-influenced path turns the flag into an arbitrary-file-write primitive (symlink, directory, device, traversal, swapped parent, clobbering `VENDOR.md`). Detect: resolved path echoed in output and JSON (control characters escaped in text). Mitigate: usage-error validation before any subprocess; refuse empty/NUL/trailing separator/name over 200 bytes/directory/non-regular/symlink and any existing file that is neither empty nor a previous pin-bump report; write through a directory descriptor pinned at validation; atomic short-named temp file + replace; dry-run writes nothing; the scheduled replay re-validates.
- **R3** Abuse: control characters or newlines from finding data forge lines in cron mail or log pipes. Mitigate: digest lines are built only from counts and a fixed class vocabulary (schema classes or `other`, also in the gate line and all digest stderr, with control characters escaped); no free text; deterministic and at most 5 lines; no wall clock.
- **R4** Abuse: JSONL framing injection into SIEM ingestion. Mitigate: `json.dumps` escaping (class names are JSON-escaped data in the JSONL; the fixed vocabulary applies to the digest only), schema tag per row, one object per line, single trailing newline, atomic write (symlink leaf refused).
- **R5** Abuse: overlapping grants with contradictory scope silently widen or narrow what a red-team run may touch (an unrestricted grant narrowed by union semantics). Detect: `grant_conflicts` in the tick result and CLI exit code. Mitigate: warn-only — never blocks, never resolves, never signs or writes; authorization (`authorize_all`) is untouched.
- **R6** Abuse: chat arguments steer file reads/writes (`--reports-dir`, `--out`). Mitigate: no path arguments at all (reports dir comes from the host env only); strict flag allowlist via `shlex`; read-only or zero-runner-call plan mode; bounded output.
- **R7** Abuse: an orchestrator chaining git/npm/network and tracker dispatch is a chat-triggerable supply-chain or spam lever and could sidestep RoE. Mitigate: plan-only by default; apply fail-closed on sidecar state ≠ IDLE and on a missing, expired, invalid or revoked RoE, re-checked fresh on every pass and resume; pin-bump is audit-only and never applied; real tracker dispatch is a library/CLI opt-in that chat cannot reach; the cycle never signs or creates RoE and never runs a simulation; resumable validated checkpoint with per-step records; ledger rows carry no secrets.

## Quality bar

Every feature: typed and validated inputs, fail-closed or fail-open per the existing convention of its module, byte-identical output when its flag/config is unset (the one deliberate exception is the advisory `grant_conflicts` tick key), deterministic ordering, tests for the success path, boundaries, and the abuse cases above, plus the pinned-count triangle (tests, docs, plugin command files) for new commands.

## Waves

1. **A (parallel, file-disjoint):** R1 `findings_sync.py`; R2 `pin_bump.py` (+ `cron/scheduler.py` replay config only if needed); R3+R4 `scripts/engagement-report.py`; R5 `roe_lifecycle.py`; R7-lib `purple_cycle.py`.
2. **B:** R6 commands, `/omega-redteam-cycle`, command-count/doc triangle, R5 conflict step wired into the cycle.
3. **Verify:** ruff + format + mypy + full pytest, smoke runs of every flag/command, independent diff review, batch report.

## Review outcome

Two independent review rounds ran. Round 1: four reviews (correctness x3, security x1) of the wave A/B diff produced fixes, each with tests:

- `--report-to`: the write goes through a directory descriptor pinned at validation (a parent or ancestor swap cannot redirect it); an existing file that is not empty or a previous pin-bump report is never overwritten; trailing separator and name over 200 bytes are refused; short temp name; printed paths escaped.
- Engagement digest: the gate line and all digest stderr use only schema classes or `other` and escape control characters.
- `--export-jsonl`: atomic write (mode 0644), symlink leaf refused.
- Findings sync budget: severity-ranked spending (medium, low, other; oldest first; pushes in store order); `sync_budget_invalid` (`type`/`value`/`entry`); per-entry mapping resolution by the effective tracker. Urgent rows stay uncapped by design.
- Findings sync: the grant-skip result carries `grant_conflicts`; `--dispatch-dry-run` escapes non-printable characters; the `_main_<tracker>` CLI paths are documented as not applying quiet hours or the budget.
- Grant conflicts: sweep-based detector capped at 100 rows plus one `conflicts-truncated` sentinel; grant files over 1 MiB ignored.
- Purple cycle apply gate: refused without a configured RoE (`roe-missing`) and with an expired, missing, invalid or revoked RoE or a non-IDLE harness; re-checked fresh on every pass and resume; a blocked step is never checkpointed.
- Purple cycle identity: subset selections get their own id (`-s<8hex>`); checkpoints move to `<reports>/purple-cycle/workflows/` and are validated on resume; a failed pin-bump audit is retried through `purple-cycle-pending.json`.
- Purple cycle CLI: `--now` rejected in apply mode, abbreviations rejected, strict tag regexes, no bytecode written by the engagement-script loader.
- Release watch: the `gh api` call times out after 60 s (a fetch failure, exit 3).
- Chat: bounded, path-free output (sync-plan lists capped at 20 with totals, escaped and clipped hashes, grant path labels as basenames or `grant-N`; cycle returns no checkpoint or ledger path; non-structured failures return the exception type name only; ASCII-digit-only `--since`/`--threshold`).
- Not addressed (documented limits): no overall `--run` deadline in the cycle; budget keys are absent on error/skip/disabled tick results; `budget_held` is unbounded in a tick result.

Round 2 (one correctness reviewer pair, no P0) produced further fixes, each with tests:

- `--report-to`: the directory walk pins with `O_PATH`, so ancestors need only search permission; paths ending in `/.` or `/..` are usage errors.
- `--export-jsonl`: leaf policy by type (atomic replace at `0666 & ~umask`, FIFO/device/socket written in place, symlink to file/directory/dangling refused, symlink to a stream written through); digest export errors escaped; `--digest --baseline other=` rejected.
- Purple cycle audit non-starvation: a failed audit is a retried `audit-failed` warning that never halts or changes the exit code; pending file `{"attempts", "tag"}`, abandoned after 3 attempts; the pending tag is written inside `release-watch`; only pin_bump-accepted tags are audited.
- Apply gate: union of `redteam.roe_file(s)` and findings-sync grant keys, config kill / explicit `enabled: false` refusal, revocations matched by realpath and file identity across the four audit-log key names, fresh clock unless `now` injected.
- Checkpoint records are validated strictly on read-back (`checkpoint-unreadable`); ledger rows for blocked steps read `apply-refused:<cause>`, once per identical row.
- Sync-plan: `grant_conflicts_total` counts conflicts beyond the listed and detector-capped rows; `tracker` clipped and escaped; aliases never clash with real ids.
