# PLAN-G5: Transitions timeline in engagement reports

## Problem

`scripts/engagement-report.py::build_report` aggregates in-window
`transitions.ndjson` rows into `transition_counts` (`(from, to, count)` pairs,
rendered in `render_report` as the `## State transitions` table) but discards
chronology. A reviewer cannot answer "what happened, in what order, and why?"
— e.g. whether `IDLE -> RUNNING (run-started)` preceded
`RUNNING -> BLOCKED (kill-switch-set)`, or when each hop fired. The raw
`sidecar/state.py::transition` rows (`{from, to, cause, ts}`, RFC3339 UTC via
`utc_now_rfc3339`) already carry `cause` and `ts`, but `load_transitions`
parses them only to compute the window and the counts; `cause` never reaches
the report. Counts without a timeline lose the incident narrative.

## User-facing behavior

- The engagement report gains a seventh section, `## State timeline`,
  rendered immediately after `## State transitions` (before the
  `<!-- findings-snapshot: ... -->` marker) in `render_report` fixed order.
- The section lists every in-window transition (same
  `window_start <= _dt <= window_end` filter as the counts) as one bullet
  per row, chronological ascending:
  `- <ts> <from> -> <to> (<cause>)` where `<ts>` is
  `fmt_rfc3339(row["_dt"])` (`+00:00`, seconds precision).
- Sort key is `(ts, from, to, cause)` so equal-timestamp rows order
  deterministically; byte-identical reruns hold.
- A footer line follows the bullets: `- causes: <cause>=<n>, ...` with
  causes sorted alphabetically, counting only in-window rows. Rows with a
  missing/empty `cause` render as `(unknown)` and count under `unknown`
  (never dropped, never an error).
- Empty window renders `- no transitions in window` instead of bullets, plus
  `- causes: none`. Exit stays 0.
- No CLI changes: same `python scripts/engagement-report.py [--since
  <RFC3339>] [--out <path>] [--reports-dir <dir>]`; same filename scheme
  `engagement-<start>_<end>.md`; canonical per-window copy still kept in
  `output_dir` (`sidecar/reports/reports/` by default).

## Interface

- **Script:** `scripts/engagement-report.py` only (stdlib-only convention
  preserved; no new imports beyond `collections.Counter`-free inline dict).
  - `load_transitions(path)` (existing, `sidecar/state.py::transitions_path`
    layout, `TRANSITIONS_FILENAME = "transitions.ndjson"`): keep fail-open
    malformed-line warnings on stderr; additionally normalize
    `row["cause"]` to `str(row.get("cause") or "unknown")` so the timeline
    and footer never KeyError on legacy rows lacking `cause`.
  - `render_report(...)`: new keyword-only param `timeline: list[tuple[str,
    str, str, str]]` (`(ts_rfc3339, from, to, cause)` pre-sorted by caller)
    plus derived `cause_counts: list[tuple[str, int]]`; renders the new
    section. Callers pass empty list for the no-transition case.
  - `build_report(reports_dir, since_text)`: after computing `in_window`,
    build `timeline = sorted(...)` with the `(ts, from, to, cause)` key and
    `cause_counts = sorted(Counter(...).items())`; window endpoints unchanged
    (`window_end` = newest of `transitions_newest` / `findings_newest`, else
    `EPOCH_RFC3339`; `window_start` = `--since` or `window_end - 7d`).
- **Config keys:** none new. History source still
  `OMEGA_PRIME_SIDECAR_REPORTS_DIR` (`sidecar/state.py::REPORTS_ENV_VAR`,
  `SIDECAR_REPORTS_ENV_VAR` mirror); output dir still
  `OMEGA_PRIME_REDTEAM_REPORTS_DIR` (`omega_prime/config.py`
  `redteam.reports_dir`, `REDTEAM_REPORTS_ENV_VAR` mirror).
- **Heartbeat kinds:** none new. Weekly cadence stays
  `omega_prime/cron/heartbeat.py::HEARTBEAT_KIND = "session_heartbeat"` with
  `interval_seconds=604800`; same prompt as PLAN-F7
  (`"Run scripts/engagement-report.py for the past 7 days ..."`).
- **Row schema consumed:** `{from, to, cause, ts}` per
  `sidecar/state.py::transition` (`STATES`, `KNOWN_CAUSES =
  BLOCKED_CAUSES + OPERATIONAL_CAUSES`); `ts` parsed by existing
  `parse_rfc3339`, rendered by existing `fmt_rfc3339`.
- **Exit codes:** unchanged (0 incl. empty window; 1 unreadable inputs /
  unwritable out). Timeline warnings reuse the transitions-line stderr
  convention and never alter stdout bytes.

## Non-goals

- Visualization: no sparklines, Mermaid/graph output, HTML, or color — plain
  markdown bullets only.
- External formats: no JSON/CSV sidecar export of the timeline, no email /
  Slack / PR-comment publishing.
- No history mutation: inputs stay read-only; no backfill of missing `cause`
  values into `transitions.ndjson`.
- No new detection, scoring, or state-machine changes; `sidecar/state.py`
  (`LEGAL_TRANSITIONS`, `BLOCKED_CAUSES`) untouched.

## Done-criteria

1. Report on a fixture history emits seven sections in fixed order with
   `## State timeline` after `## State transitions` and before the snapshot
   marker.
2. Timeline lists exactly the in-window rows, one bullet each in
   `(ts, from, to, cause)` ascending order, in
   `- <ts> <from> -> <to> (<cause>)` format.
3. Footer `- causes: ...` counts match in-window rows per cause, alphabetical;
   empty window shows `- no transitions in window` + `- causes: none`.
4. Out-of-window rows appear in neither bullets nor footer counts.
5. Byte-identical rerun: two consecutive runs on identical inputs produce
   identical SHA-256 (timeline determinism incl. equal-timestamp ties).
6. Missing-`cause` legacy row renders `(unknown)` and counts under `unknown`.

## Test plan

- Happy (pytest, hermetic `tmp_path`, mirroring
  `omega_prime/tests/test_engagement_report.py` `seed_transitions` /
  `section_lines` style): seed `transitions.ndjson` fixture with 4-5 rows
  (mixed causes incl. `run-started`, `kill-switch-set`, out-of-order file
  order, one duplicate timestamp), `last-run.json` + findings rows for a
  non-empty window; run script with `--since`; assert bullet order, exact
  strings, and footer counts.
- Edge: `--since` after newest record → `- no transitions in window` +
  `- causes: none`, exit 0; malformed ndjson line → skipped with stderr
  warning, timeline still correct, exit 0; legacy row without `cause` →
  `(unknown)` bullet + count; equal timestamps → `(from, to, cause)`
  tiebreak order stable across reruns.
- Failure: unreadable `--reports-dir` → exit 1 with `error:` on stderr;
  unparseable `last-run.json` → exit 1 naming the path (existing fail-closed
  paths untouched).
- Determinism: run twice on identical fixture, assert `sha256(stdout)` equal
  and canonical file bytes equal stdout.
- Stdlib-only guard: existing `test_script_is_stdlib_only` keeps passing
  (no new third-party imports).

## Docs surfaces

- `docs/red-team.md` — extend "Weekly engagement report" subsection: document
  the `## State timeline` bullet format, footer, and empty-window text.
- Script `--help` text unchanged (no new flags); section format documented in
  `render_report` docstring ("seven report sections in fixed order").

## Rollback

- Single revert: `git revert` the commit extending
  `scripts/engagement-report.py` + tests + docs; report consumers fall back to
  the six-section layout and `newest_baseline` deltas keep working (snapshot
  marker position unchanged). History files in `sidecar/reports/` untouched
  (read-only consumer), so no data migration.
