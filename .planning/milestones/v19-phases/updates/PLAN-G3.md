# PLAN-G3: Weekly digest into durable_status

## Problem

Engagement posture is invisible in the Bot's durable surface. Operators who ask
the Bot "how are we doing?" get the cron turn journal (`cron/turns.sqlite`),
workflow checkpoint counts, and the RoE lifecycle block — but nothing about
purple-team engagement health: how many harness runs happened, what the
per-technique pass posture looks like, how many detection gaps are open, or
whether findings grew or shrank since the last window. That posture only exists
inside markdown files under the reports output dir
(`output_dir()`: `reports_dir / "reports"` in `scripts/engagement-report.py`),
which nothing in the durable path reads.

Batch 01 (F1–F7) shipped the sidecar harness (`sidecar/`), the
`scripts/engagement-report.py` weekly report, and RoE lifecycle surfacing
(`omega_prime/grokbot/roe_lifecycle.py` + `_roe_block()` in
`omega_prime/tools/durable_surface.py`). G3 closes the loop: a weekly
heartbeat carries the newest engagement posture into `durable_status` as one
more additive block, so the durable surface answers posture questions without
opening report files.

## User-facing behavior

- A new heartbeat kind `engagement_digest` lives in
  `omega_prime/cron/heartbeat.py`, patterned exactly on `roe_expiry`
  (`ROE_EXPIRY_KIND`, `schedule_roe_expiry()`, `tick_roe_expiry()`).
- Default cadence is weekly (`604800` seconds), matching the report epilog's
  existing guidance (`interval_seconds=604800`). The interval is configurable
  per job via `schedule_engagement_digest(..., interval_seconds=...)`; no new
  global config keys.
- On each fire, the checker runs the engagement-report summary (see Interface)
  in summary-line form and writes it into `durable_status` via an additive
  `engagement` block in `omega_prime/tools/durable_surface.py`, mirroring how
  `_roe_block()` adds the `roe` key without touching the journal/checkpoint
  keys.
- When no reports exist yet, the tick completes cleanly and the block reports
  an explicit absent state (e.g. `state: "no-reports"`), never an error.
- The durable line format is stable: `runs=X pass=Y% gaps=Z findings=+N/-M`.

## Interface

### Heartbeat kind (`omega_prime/cron/heartbeat.py`)

```python
ENGAGEMENT_DIGEST_KIND = "engagement_digest"
ENGAGEMENT_DIGEST_INTERVAL_SECONDS = 604800
ENGAGEMENT_DIGEST_HISTORY_LIMIT = 20

def schedule_engagement_digest(store, *, interval_seconds=..., due_at) -> str
def list_engagement_digests(store) -> list[dict]
def clear_engagement_digest(store, job_id) -> dict
def tick_engagement_digest(store, now, checker) -> list[dict]
```

Semantics mirror `tick_roe_expiry()`: iterate only `ENGAGEMENT_DIGEST_KIND`
non-completed jobs due at `now`; accept datetime-or-timestamp `now` via the
existing `_roe_now()` normalizer; claim via `claimed_at`/`history`
(`ran_at == now` ⇒ skip); call `checker(reports_dir, now)`; record
`last_result`/`last_ran_at`, bounded history (cap 20), advance
`due_at = stamp + interval`, `_save()`. Every fire returns the job (the
digest always "fires" when it runs — there is no silent-valid state, unlike
RoE's `valid` ticks). Pass `skip_kind=ENGAGEMENT_DIGEST_KIND` to the generic
`JobStore.tick` driver. Never calls `transition()`.

### Report summary flag (`scripts/engagement-report.py`)

```
python scripts/engagement-report.py --summary [--reports-dir DIR] [--since RFC3339]
```

- `--summary` emits exactly one line on stdout and writes no files:
  `runs=X pass=Y% gaps=Z findings=+N/-M`, where `X` = runs in window,
  `Y` = pass percentage over `pass_rows` (integer, `0` when no rows),
  `Z` = `len(gaps)`, `+N`/`-M` = new/rescinded counts diffed against the
  newest baseline via the existing `newest_baseline()` + snapshot logic.
- Reads the newest report's inputs (same `reports_dir` resolution:
  `--reports-dir` > `$OMEGA_PRIME_SIDECAR_REPORTS_DIR` > `sidecar/reports/`).
  Exit 0 with `state: no-reports` content when no reports exist; exit 1 with
  `error:` on stderr only for genuine I/O or parse failures (`ReportError`).
- Naming follows existing flags (`--since`, `--reports-dir`, `--out`).

### Durable block (`omega_prime/tools/durable_surface.py`)

```python
def _engagement_block(root: Path) -> dict[str, Any]: ...
# durable_status() gains one additive key:
{..., "roe": roe, "engagement": {"state": ..., "line": "runs=X pass=Y% gaps=Z findings=+N/-M",
  "report": "<newest report name or None>", "generated_at": "<RFC3339 or None>"}}
```

- Follows `_roe_block()` conventions: never raises; config/artifact problems
  report `invalid`/`missing`/`no-reports` states, never exceptions.
- `state: "ok"` when a line was produced; `state: "no-reports"` when the
  reports dir is empty or missing. No new `OMEGA_PRIME_*` keys (per
  `omega_prime/config.py` `OMEGA_PRIME_` conventions): only the existing
  `OMEGA_PRIME_SIDECAR_REPORTS_DIR` / `OMEGA_PRIME_REDTEAM_REPORTS_DIR`
  resolution is reused.

## Non-goals

- External publishing (posting the digest anywhere, notifications, webhooks).
- New global config keys beyond the per-job interval: no `OMEGA_PRIME_*`
  additions, no scheduler-config schema changes.
- Changing the full markdown report format, the snapshot marker, or
  `build_report()` semantics — `--summary` is a read-only projection.
- Backfill, trend history, or charting across windows.

## Done-criteria

1. `schedule_engagement_digest()` validates empty interval/positive-interval
   exactly like `schedule_roe_expiry()` (`ValueError` on non-positive).
2. Hermetic injected-clock test: two jobs due at `T`, `tick(T)` fires both
   once; `tick(T)` again fires zero (claimed/history dedupe).
3. `tick_engagement_digest()` with an empty/missing reports dir completes
   cleanly, records `no-reports`, and returns the job without raising.
4. `--summary` output matches `^runs=\d+ pass=\d+% gaps=\d+ findings=\+\d+/-\d+$`
   against a fixture reports dir, and equals the values parsed from the newest
   full report's sections.
5. `durable_status` includes the `engagement` key alongside `roe` with no
   change to journal/checkpoint keys; missing journal still returns `absent`
   plus the engagement block.
6. Rollback is a single revert (one commit touching only the three files).

## Test plan (hermetic, no network/clock)

- Happy: fixture `reports_dir` with two canned reports (known snapshot
  markers) → `--summary` emits the expected line; `tick` with injected
  datetime `now` fires once and advances `due_at` by the interval.
- Edge: empty reports dir → `--summary` exit 0, `runs=0 pass=0% gaps=0
  findings=+0/-0`, block state `no-reports`; naive datetime `now` treated as
  UTC (via `_roe_now`); custom `interval_seconds` honored on advance.
- Failure: corrupt findings row → fail-closed `ReportError`, exit 1,
  `error:` on stderr (existing `load_findings()` contract); `tick` checker
  exception propagates to the job's `last_result`, never corrupts the store
  (history still appended, `_save()` still called).
- All tests use tmp dirs + injected clocks; no wall-clock, no repo-root
  writes, no network.

## Docs surfaces

- `scripts/engagement-report.py --help` documents `--summary` (one-line
  format, exit codes, no-file-writes guarantee).
- `omega_prime/cron/heartbeat.py` docstrings for the four new functions,
  mirroring the `roe_expiry` docstrings.
- `durable_status` tool description string updated to mention the engagement
  block. No new microsite or runbook pages.

## Rollback

Single revert of the one implementing commit (touches only
`omega_prime/cron/heartbeat.py`, `scripts/engagement-report.py`,
`omega_prime/tools/durable_surface.py` plus tests). After revert,
`durable_status` returns its current keys, the heartbeat kind is unknown to
the scheduler (existing `engagement_digest` jobs are inert non-completed rows
clearable via `clear_engagement_digest` before revert, or ignored after), and
`--summary` is an unrecognized flag again. No migrations, no stored state to
clean.
