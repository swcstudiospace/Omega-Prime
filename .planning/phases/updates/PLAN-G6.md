# PLAN-G6: Case-coverage matrix

## Problem

`scripts/engagement-report.py` renders per-case pass rows (`## Per-technique
pass rates`, built by `build_report()` from `last-run.json` observations) but
has no view of **technique-class coverage vs detection rate**. With one case
file per class today (`sidecar/cases/*.yaml`: `abuse-01`, `auth-bypass-01`,
`cost-abuse-01`, `jailbreak-01`, `malformed-input-01`,
`prompt-injection-01`), an operator cannot answer: which of the six classes in
`sidecar/case.schema.json` (`prompt-injection`, `jailbreak`, `abuse`,
`malformed-input`, `auth-bypass`, `cost-abuse`) are actually exercised, which
have gaps, and which are unexercised. A class can silently rot — zero cases,
or cases never passing — while the per-case table still looks fine.

## User-facing behavior

`scripts/engagement-report.py` output gains a seventh section,
`## Coverage matrix`, rendered after `## Per-technique pass rates` and before
`## Detection gaps` (appended in `render_report()`'s fixed section order):

```markdown
## Coverage matrix

| class | cases | runs | pass rate | gaps | verdict |
| --- | --- | --- | --- | --- | --- |
| abuse | 1 | 1 | 1/1 (100.0%) | 0 | covered |
| auth-bypass | 1 | 0 | 0/0 (n/a) | 1 | gappy |
| cost-abuse | 0 | 0 | 0/0 (n/a) | 0 | unexercised |
```

Semantics per class row:

- `cases`: count of loaded case files in `sidecar/cases/*.yaml` whose
  top-level `class:` equals the row (from the extended `read_cases()` rows).
- `runs`: cases of that class present in `last-run.json` observations with an
  `observed_detection` key (same pass/fail rule as existing `pass_rows`:
  `got == expected` or non-empty `residual_rationale` counts as pass).
- `pass rate`: `wins/runs (pct%)`, `0/0 (n/a)` when `runs == 0`.
- `gaps`: count of `detection_gaps()` failure strings attributable to that
  class (case-load errors and observation mismatches mentioning a `case_id`
  of the class; drift/global errors count toward no class).
- `verdict`: `covered` (runs > 0 and all pass, gaps == 0), `gappy` (cases > 0
  and (some fail or gaps > 0 or runs == 0)), `unexercised` (cases == 0).
- Row order is fixed: the six schema-enum classes sorted byte-wise, then any
  unknown `class:` values seen on disk sorted after them (never dropped).
- `runs == 0` report wide still renders the matrix (all rows `0/0 (n/a)`),
  mirroring the existing `- no runs in window` behavior of `## Runs summary`.

## Interface

- CLI unchanged: `python3 scripts/engagement-report.py [--since RFC3339]
  [--reports-dir DIR] [--out PATH]`. Same inputs (`--reports-dir` default
  `$OMEGA_PRIME_SIDECAR_REPORTS_DIR` else `sidecar/reports/`; reads
  `transitions.ndjson`, `findings.ndjson`, `last-run.json`, plus
  `sidecar/cases/*.yaml` via `REPO_ROOT / "sidecar" / "cases"`); no new flags.
- Code change (all in `scripts/engagement-report.py`):
  - `parse_case_text()` also extracts top-level `class:` (same line-reader
    style; missing/empty yields `""`, never raises beyond existing
    `ValueError` contract).
  - `read_cases()` rows widen to `(case_id, detection, rationale, class)`;
    existing sort-by-`case_id` preserved.
  - New `coverage_rows(cases, observations, gaps) -> list[tuple]` pure helper;
    `render_report()` takes `coverage_rows` param and emits the section;
    `build_report()` wires it. `detection_gaps()` signature unchanged.
- Config: no new `omega_prime/config.py` node and no new `OMEGA_PRIME_*` env
  var — display-only section over already-read inputs (existing
  `OMEGA_PRIME_SIDECAR_REPORTS_DIR` / `OMEGA_PRIME_REDTEAM_REPORTS_DIR`
  overrides keep working untouched).
- Heartbeat: no new `omega_prime/cron/heartbeat.py` kind (still
  `HEARTBEAT_KIND = "session_heartbeat"`); the existing weekly
  `schedule_heartbeat(..., interval_seconds=604800)` prompt (see `main()`
  epilog) picks the new section up with zero prompt change.

## Non-goals

- Auto-generating cases for `unexercised`/`gappy` classes (batch-01 F4
  `case_from_finding.py` owns case creation; this plan only surfaces gaps).
- Enforcement: no CI red/green gate, no threshold, no alerting on verdicts;
  verdicts are informational text in the markdown report.
- No schema change: `sidecar/case.schema.json` enum and the six `class:`
  values are read as-is; unknown classes are displayed, never validated here.
- No history-format change: `transitions.ndjson` / `last-run.json` /
  `findings.ndjson` shapes untouched; no backfill of old reports.

## Done-criteria

1. Running `python3 scripts/engagement-report.py` (default `sidecar/reports/`
   fixture: 6 cases, 6 observations, all passing) prints a `## Coverage matrix`
   section with 6 rows, each `1 | 1 | 1/1 (100.0%) | 0 | covered`.
2. Removing all `cost-abuse` case files from a temp copy and re-running with
   `--reports-dir <copy>` yields a `cost-abuse | 0 | 0 | 0/0 (n/a) | 0 |
   unexercised` row; all other rows unchanged.
3. A failing observation (`observed_detection` mismatch, empty rationale) in a
   temp `last-run.json` flips that class row to `gappy` with `0/1 (0.0%)`
   while every other class stays `covered`.
4. Byte-identical output on two consecutive runs against the same inputs
   (determinism: fixed row order, no timestamps beyond existing header).
5. `--since` far-future (zero runs in window) still renders all six rows as
   `0/0 (n/a)` with verdicts `gappy` (cases exist) — never an empty section.
6. Existing six sections render byte-identical to pre-change output on the
   same inputs (matrix is purely additive; snapshot comment format unchanged).

## Test plan

Hermetic tests (stdlib `unittest`, temp dirs only, no network/repo writes;
new `scripts/test_engagement_coverage.py`, mirroring any existing
`scripts/test_engagement*.py` layout):

- Happy: fixture with 2 classes × 2 cases, all passing observations →
  asserts exact matrix markdown lines, ordering, and `covered` verdicts.
- Happy: mixed fixture (one pass, one fail, one `residual_rationale` pass) →
  asserts `1/2 (50.0%)`-style rates and `gappy` verdict.
- Edge: empty class (no case files for a schema class) → `unexercised` row
  present with `0/0 (n/a)`; unknown `class:` on disk → extra row sorted last.
- Edge: zero runs (`observations == {}`, missing `last-run.json`) → all rows
  `0/0 (n/a)`; section still rendered with six schema rows.
- Edge: case-load error (unreadable file) attributed to its class `gaps`
  count; global drift error attributed to no class (matrix totals unaffected).
- Failure: corrupt `last-run.json` (not JSON) → same fail-closed behavior as
  today (`mapping_invalid` path), matrix rows fall back to `0/0 (n/a)` +
  `gappy`, exit code still 0 with report rendered.
- Determinism: render twice over fixture, `assertEqual(bytes, bytes)`.

## Docs surfaces

- `scripts/engagement-report.py` `main()` epilog + `--help`: one line noting
  the seventh section (no new flags to document).
- If `docs/red-team.md` (batch-01 F-series docs) describes report sections by
  name, append the `## Coverage matrix` column/verdict table there; otherwise
  no docs change (module + test docstrings carry the contract).

## Rollback

Single revert: `git revert <commit>` of the plan's one commit (confined to
`scripts/engagement-report.py` + `scripts/test_engagement_coverage.py`);
prior reports on disk keep rendering — old binaries simply ignore the
already-written section, and no history or schema files are migrated.
