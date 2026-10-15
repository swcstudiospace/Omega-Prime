# PLAN-F7: Scheduled engagement reports

## Problem

Purple-team posture is invisible week-over-week. Runs happen (`sidecar/simulation.py
run`), observations land in `sidecar/reports/last-run.json`, findings accumulate via
`sidecar/findings.py`, and state transitions append to
`sidecar/reports/transitions.ndjson` — but nobody can answer "are we getting better?"
There is no single deterministic artifact that rolls runs, per-technique pass rates,
detection gaps, findings deltas, and sidecar state-transition counts into one
reviewable weekly report.

## User-facing behavior

- `python scripts/engagement-report.py [--since <RFC3339>] [--out <path>] [--reports-dir <dir>]`
  renders a deterministic markdown report to stdout (or `--out` file) from
  `sidecar/reports/` history.
- Report sections (fixed order): header (window, generated-at, harness version),
  runs summary (count of runs in window), per-technique pass rate table, detection
  gaps (current `validate_suite` failures), findings deltas (new / rescinded vs
  previous report), sidecar state-transition counts.
- Weekly cadence via the heartbeat pattern (`omega_prime/cron/heartbeat.py`:
  `schedule_heartbeat(store, session, prompt, due_at, interval_seconds=604800)` with
  `HEARTBEAT_KIND = "session_heartbeat"`); ad-hoc windows via CLI `--since`.
- Byte-identical reruns on identical inputs: same history + same `--since` yields
  byte-identical markdown (fixed row ordering, no wall-clock leakage except the
  explicit `generated-at` header field, which is derived from the newest input
  record, not `now()`).
- Empty window renders a valid report with zero-row tables and an explicit
  "no runs in window" note — never an error exit.

## Interface

- **Script:** `scripts/engagement-report.py` (new; follows the
  `scripts/redteam-audit-triage.py` self-contained-stdlib convention):
  `run --suite smoke`-style argparse, `main(argv) -> int` returning 0/1.
  - `--since <RFC3339>` — window start (inclusive); default: 7 days before newest
    record (`sidecar/state.py::utc_now_rfc3339` format, `+00:00` offset).
  - `--reports-dir <dir>` — history source; default resolved exactly like
    `sidecar/state.py::reports_dir()` (env `OMEGA_PRIME_SIDECAR_REPORTS_DIR` wins,
    else `sidecar/reports/`).
  - `--out <path>` — write markdown file; default stdout.
- **Config node** (`omega_prime/config.py::_defaults()` + `OMEGA_PRIME_*` overlay):
  `redteam.reports_dir` (default `""` meaning `sidecar/reports/`; override env
  `OMEGA_PRIME_REDTEAM_REPORTS_DIR`), mirroring the existing
  `redteam.{enabled,gateway_url,kill,roe_required,roe_file,roe_key,roe_key_file}`
  family and the `sidecar/state.py::REPORTS_ENV_VAR` precedent.
- **Heartbeat:** weekly job kind stays `session_heartbeat`
  (`omega_prime/cron/heartbeat.py::HEARTBEAT_KIND`); the engagement prompt re-enters
  a named session that runs the script and attaches the markdown. No new job kind.
- **Inputs read (never written):** `sidecar/reports/last-run.json` (observations
  per `sidecar/cases/*.yaml` case ids), `sidecar/findings.py::load_findings`
  rows + `dedupe_hash`, `sidecar/state.py::transitions_path` ndjson
  (`from`/`to`/`cause`/`ts`), `sidecar/simulation.py::validate_suite` failures.
- **Output dir:** resolved from `redteam.reports_dir`, default
  `sidecar/reports/reports/` (sibling of `last-run.json` / `transitions.ndjson`,
  per `sidecar/state.py::TRANSITIONS_FILENAME` layout).
- **Output filename:** `engagement-<window-start>_<window-end>.md` (RFC3339 dates
  truncated to day, `:` replaced with `-`); rerun with same window overwrites
  byte-identically and becomes the "previous report" baseline for deltas.
- **Determinism rules:** rows sorted by case id / dedupe hash / `from->to` pair;
  counts rendered as plain integers; floats (pass rates) as `p/t (pct%)` with one
  decimal; no hostname, pid, or wall-clock timestamps outside the header's
  input-derived `generated-at`.
- **Heartbeat prompt (exact):** `"Run scripts/engagement-report.py for the past
  7 days and attach sidecar/reports/reports/<newest report>"` scheduled via
  `schedule_heartbeat(store, session, prompt, due_at, interval_seconds=604800)`
  and fired through `tick_heartbeats(store, now, session_runner)`.
- **Exit codes:** 0 report rendered (including empty window); 1 unreadable
  inputs or unwritable `--out` parent. Warnings (skipped ndjson lines) go to
  stderr and never alter stdout bytes.

## Non-goals

- External publishing (no email, Slack, PR comments, dashboards).
- LLM summaries or natural-language verdicts — tables and counts only.
- New detection logic, scoring changes, or harness changes; the script only reads
  `validate_suite` / `load_findings` / transition-log outputs.
- Backfill or mutation of history; inputs are read-only.

## Done-criteria

1. `python scripts/engagement-report.py --reports-dir <fixture> --since <t0>`
   exits 0 and emits all six sections in fixed order on a fixture history.
2. Per-technique pass-rate table lists every `sidecar/cases/*.yaml` case id
   present in the window, with passes/total computed from `last-run.json`
   observations vs case expectations.
3. Detection-gaps section equals current `validate_suite()` failures verbatim
   (each names its `case_id`).
4. Findings deltas (new/rescinded since previous report in output dir) match
   `dedupe_hash`-keyed diff of `load_findings` rows.
5. Transition counts match `transitions.ndjson` `from->to` pairs within window.
6. Byte-identical rerun: two consecutive runs on identical inputs produce
   identical SHA-256.
7. Empty-window run exits 0 with "no runs in window" note and zero-row tables.

## Test plan

- Happy (pytest, hermetic `tmp_path` fixture history mirroring
  `omega_prime/tests/test_sidecar_findings.py` style): seed `last-run.json` +
  findings rows + `transitions.ndjson`, run script, assert six sections and exact
  pass-rate/delta/count values.
- Edge: empty reports dir → valid zero report, exit 0; `--since` after newest
  record → same; malformed ndjson line → skipped with stderr warning, exit still 0;
  missing previous report → deltas section reports "no baseline" and treats all
  findings as new.
- Failure: unreadable `--reports-dir` → exit 1 with `error:` on stderr;
  unparseable `last-run.json` → exit 1 naming the path (fail-closed, same
  convention as `validate_suite` load errors).

## Docs surfaces

- `docs/red-team.md` — new "Weekly engagement report" subsection (command,
  flags, heartbeat cadence, output location).
- Script `--help` text is the CLI reference (no separate man page).

## Rollback

- Single revert: `git revert` the commit adding `scripts/engagement-report.py` +
  config node + docs; history files in `sidecar/reports/` are untouched (read-only
  consumer), so no data migration.
