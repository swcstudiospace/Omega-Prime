# Batch 18 Report: Purple-Team SOTA features (7 shipped)

**Shipped:** 2026-10-14 · **On main:** the squash merge of this PR (`git log --grep 'batch-18'`) · **Suite:** 4555 passed / 0 failed / 3 skipped (was 3754; +801), `ruff check` + `ruff format --check` + `mypy omega_prime/` clean (421 files), run exactly as CI does

Stage 1 shipped first as PR #31: `ci / lint` had been red on main since batch 05 (21 ruff errors that earlier reports called clean); it is green again and every batch gate now runs CI's exact commands.

## The 7 features

| # | Feature | Value | Revert |
|---|---------|-------|--------|
| R1 | Findings sync per-tracker budget | `redteam.findings_sync.sync_budget` (int or per-tracker map) caps non-urgent dispatches per tick, spent medium → low → rest then oldest first; critical/high and notices bypass; invalid config is flagged (`sync_budget_invalid`); one shared `_RowGate` replaces nine copy-pasted loops. 10,935-scenario differential vs main: 0 diffs with the budget unset | remove the key / revert `_RowGate` |
| R2 | Pin-bump `--report-to PATH` | Exclusive atomic report write; destination parent pinned by `O_PATH` fd (a swap after validation cannot redirect it); refuses non-report files, symlinks, directories, special files, dot/slash endings, names over 200 bytes; scheduled replays re-validate | revert `--report-to` |
| R3 | Engagement `--digest` | ≤5 deterministic lines (summary, coverage + risk, worst classes, optional gate) for cron/pipes; schema-class vocabulary only, control characters escaped; exit 0/1/2/3 | revert `--digest` |
| R4 | Coverage `--export-jsonl` | One `coverage/1` object per class for stream consumers; atomic for regular files, in-place for FIFOs/devices and `/dev/stdout`, symlinked regular files refused | revert `--export-jsonl` |
| R5 | RoE multi-grant conflict detection | `grant_conflicts()` finds duplicate-id-divergent, tool-scope, tracker-cover and operator conflicts between overlapping grants; tick key `grant_conflicts` + `roe_lifecycle conflicts` CLI (exit 0/3/2); capped at 100 rows, scales to 2,000 grants in 1.4 s; advisory only | revert detector + tick key |
| R6 | Chat-native reporting | `/omega-redteam-report`, `-coverage`, `-sync-plan`: read-only / zero-write, no path arguments, bounded and path-free output | revert 3 command modules |
| R7 | Purple-cycle workflow | `python -m omega_prime.grokbot.purple_cycle` + `/omega-redteam-cycle`: resumable six-step cycle (preflight, release-watch, pin-bump audit, engagement, findings plan, findings dispatch) with durable checkpoint + ledger; plan mode is pure; apply refused unless the harness is IDLE and every RoE grant is valid and unrevoked; chat can never dispatch | revert module + command |

Curated slash commands 20 → 24, total 54 → 58. Specs: `.planning/BATCH-18-PLAN.md`.

## Verification

- Full suite **4555 passed / 0 failed / 3 skipped** at the branch head; ~800 new tests (findings sync 462 in file, pin-bump 271, engagement 214 + 34, grant conflicts 56, purple cycle 188, chat commands 216, integration 5).
- CI-equivalent gates at head: ruff check, ruff format --check, `mypy omega_prime/` (421 files), evals 26/26, readiness `ok: true`, `assemble-prompts --check`, `tooling.catalog --check`, `tooling.plugin --check` (62 files), docs tests 31, provenance check/pack-diff/surface.
- Smoke runs of the real CLIs: digest (clean, gappy, gate PASS/FAIL, `--json` conflict), JSONL export, `roe_lifecycle conflicts` (3 kinds, exit 3; single grant exit 0), pin-bump `--report-to` refusals (directory, symlink), `purple_cycle` plan (completed, zero files written).

## Independent review (two rounds, seven reviewers)

Round 1 (correctness ×3, security ×1) found one TOCTOU write redirect in `--report-to` (parent swapped for a symlink after validation), a digest gate line echoing raw class names, a stale-preflight bypass of the apply gate on checkpoint resume, a same-day checkpoint that silently no-ops later runs, unbounded/path-leaking chat output, an O(n²) conflict detector (25 s, 213 MB for 2,000 mixed grants), a FIFO-ordered budget that starved mediums behind lows, and silent fail-open on a mistyped budget. Round 2 re-ran every reproduction and found one regression (the descriptor walk needed read permission on ancestors), JSONL replacing `/dev/null`-style leaves, permanently failing audit tags starving apply, a crash window that lost a release tag, alias-dodgeable revocation, config-level kill ignored, and a forged-checkpoint exception. All of it is fixed with fail-before/pass-after regression tests; the reviewers' repro scripts were re-run at head as closure checks.

## Known limits (documented, not silent)

- Urgent (critical/high) rows are deliberately uncapped by `sync_budget`; the per-tracker `_main_<tracker>` CLIs apply neither quiet hours nor the budget (only `tick` does).
- One cycle per UTC day per mode and step selection: a finding recorded later the same day dispatches next day or with `--force-new` (CLI/library only). No cross-process lock between concurrent cycles. An unreadable or garbage RoE audit log fails open. Library `run_cycle(now=...)` accepts an injected clock; the CLI rejects `--now` in apply mode and chat cannot reach apply.
- `grant_conflicts` lists at most 100 rows and ignores grant files over 1 MiB. Chat `--run` uses the network (`gh api`) and writes the release-watch state, checkpoint and ledger under the reports dir.
- Item ① (absorb upstream v1.5): upstream `purple-team` HEAD is v1.4 (already pinned). Item ② (live-gateway e2e): needs a running T3MP3ST and an operator-signed RoE, so it stays user-owned; an agent must not self-authorize it.
- Phase 77.1 live import UAT stays user-owned (`/gsd:verify-work 77.1`). Ten dependabot PRs remain open and untouched.

## Batch 19 proposal (paused — awaiting go-ahead)

1. Purple-team repo: absorb upstream v1.5 when it ships (release_watch + pin-bump chain)
2. Live-gateway e2e with real detection validation (requires T3MP3ST + RoE)
3. Cron/heartbeat kind `purple_cycle` (schedule/list/clear/tick + `run_due_jobs` skip) so plan/run cycles run unattended
4. `durable_status` additive `cycle` block (last cycle id/state/age, pending audit tag) and sync backlog depth/oldest-held age per tracker
5. `/omega-redteam-conflicts`: read-only chat view of RoE grant conflicts (basenames, capped) reusing the detector
6. Findings JSONL export (`finding/1`, same atomic/special-file policy) for SIEM ingestion, plus `/omega-redteam-findings --jsonl`
7. Cycle hardening: per-run file lock against concurrent cycles and a total wall-clock deadline
