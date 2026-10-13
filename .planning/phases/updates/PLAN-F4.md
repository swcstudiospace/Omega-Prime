# PLAN-F4: Adversarial case self-provisioning from confirmed findings

## Problem

The purple-team simulation library is six hand-written files under
`sidecar/cases/*.yaml` (`abuse-01`, `auth-bypass-01`, `cost-abuse-01`,
`jailbreak-01`, `malformed-input-01`, `prompt-injection-01`). It only grows
by hand, while the suite already records every detection gap as a structured
row in `sidecar/reports/findings.ndjson` via `sidecar/findings.py::
record_finding` (keyed by `dedupe_hash = sha256(case_id + NUL +
normalized_response)`). Detection gaps that the machinery paid to discover
are never converted back into regression cases, so the same miss can recur
in a later run with no new coverage to show for it.

## User-facing behavior

An operator converts one confirmed finding into a schema-valid draft case:

```
python -m omega_prime.evals.case_from_finding --dedupe-hash <hash> --class <class>
```

The tool looks up the row in `findings.ndjson`, writes one YAML file under
`sidecar/cases/drafts/` with `status: draft`, and prints its path. The draft
is inert data (same DATA-ONLY convention as the checked-in cases: header
comment, no anchors/tags/multiline blocks) and is EXCLUDED from suite
loading until a human moves it to `sidecar/cases/`, flips `status` to
`active`, sets a real `expected.detection`, and bumps `version` to `1.0.0`.
Nothing auto-activates, ever.

## Interface

**New module** `omega_prime/evals/case_from_finding.py` (stdlib-only, like
`sidecar/simulation.py`; `main(argv: list[str] | None = None) -> int` plus a
`__main__` guard in the style of `omega_prime/evals/runner.py`):

```
python -m omega_prime.evals.case_from_finding --dedupe-hash <64-hex> --class <class>
    [--reports-dir <dir>] [--cases-dir <dir>]
```

- `--dedupe-hash` (required): 64-char hex row identity produced by
  `sidecar/findings.py::dedupe_hash`. Lookup via `load_findings(directory)`;
  default directory is `sidecar/state.py::reports_dir()` resolved through
  `findings_path`. Unknown hash exits nonzero naming the hash.
- `--class` (required): new case's technique class. Must be one of
  `CASE_CLASSES` in `sidecar/simulation.py` (`prompt-injection`,
  `jailbreak`, `abuse`, `malformed-input`, `auth-bypass`, `cost-abuse`).
  The CLI value wins over the row's recorded class (the operator is
  re-classifying the variant); a mismatch is reported on stderr as a
  warning, exit still 0. Anything outside the six exits nonzero.
- `--reports-dir` / `--cases-dir` (optional): hermetic overrides threading
  through the existing `directory` parameters of `load_findings` and
  `load_suite`/`cases_dir`. No other flags.
- **Confirmed** means the row's `verdict` is `missed-detection` or
  `no-observation` — the two failure verdicts `_record_suite_finding`
  (`sidecar/simulation.py`) writes. A `pass` row exits nonzero ("already
  detected; nothing to provision").
- **Payload echo, no invention:** findings rows deliberately do NOT persist
  the raw gateway response (threat T-79-11 — `record_finding` stores only
  `dedupe_hash`). The only recorded observation is the row's
  `observed_detection` string, so the draft `payload` is a verbatim copy of
  that field. `expected.detection` is the literal placeholder
  `REVIEW-REQUIRED`, which `validate_case` accepts only on drafts.
- **Draft filename:** `sidecar/cases/drafts/<case_id>.yaml` with
  `case_id = "<class>-<hash[:12]>"`, `version: "0.1.0"`, `severity` copied
  from the row, `transport: loopback-only`, `scope: llm-own-endpoints`,
  `status: draft`. Re-running the same hash is idempotent (prints the
  existing path, exit 0). A `case_id` collision with a different hash exits
  nonzero naming both files. The file carries the source `dedupe_hash` and
  source `case_id` in a comment header for traceability.
- **Loader exclusion (two layers):** (1) `load_suite` globs `*.yaml`
  non-recursively, so `drafts/` is never entered; (2) `load_suite` skips any
  parsed doc with `status: draft` even if it lands directly in `cases/`
  (skipped, not an error — defense in depth against a stray copy).
- **Schema:** `sidecar/case.schema.json` gains optional `status: enum
  [draft, active]`; `validate_case` allows the optional key with those two
  values and rejects `expected.detection == "REVIEW-REQUIRED"` on any
  non-draft doc. Files without `status` validate exactly as today.
- **No new config keys** (`OMEGA_PRIME_*` in `omega_prime/config.py`
  untouched — paths follow the `sidecar/state.py` repo-root convention),
  **no new slash command** (`omega_prime/commands/` untouched),
  **no new heartbeat kind** (`omega_prime/cron/heartbeat.py` untouched).

## Non-goals

- Auto-activation or auto-triage of drafts; any path that lets a draft
  execute in `run_suite` without a human edit.
- Payload invention beyond the row's recorded `observed_detection`
  (no LLM paraphrase, no synthesized variants, no fetching the original
  probe from anywhere else).
- Bulk/backfill conversion, verdict-schema changes, work-item export
  changes (`export_work_items` untouched), drift-check changes.
- Touching `docs/red-team.md` (documents the unrelated T3MP3ST sidecar).

## Done-criteria

1. `case_from_finding --dedupe-hash <known-miss-hash> --class abuse`
   writes `sidecar/cases/drafts/abuse-<12hex>.yaml` with `status: draft`
   and exit 0; re-run prints the same path, exit 0, no duplicate file.
2. The produced draft passes `validate_case` with zero errors (checked
   programmatically, not by eye).
3. `load_suite` on a directory containing an active case plus a
   `status: draft` file returns only the active case and no errors.
4. Unknown hash, `pass`-verdict row, and non-six-class `--class` each exit
   nonzero with the offending value named on stderr.
5. Draft `payload` is byte-identical to the source row's
   `observed_detection`; `expected.detection` is `REVIEW-REQUIRED`.
6. Activating a draft unchanged (move to `cases/`, flip to `active`)
   fails validation until `expected.detection` is set to a real value.

## Test plan

Hermetic pytest at the repo's level (`tmp_path`, no repo writes), e.g. in
`omega_prime/tests/test_case_from_finding.py` following
`test_sidecar_findings.py` fixture style (`record_finding` + `tmp_path`):

- **Happy:** seed a `missed-detection` row, run `main()` with
  `--reports-dir/--cases-dir` pointed at `tmp_path`; assert draft YAML
  exists, `validate_case(load_case_file(...)) == []`, payload equals the
  row's `observed_detection`, `status == "draft"`, exit 0; re-run asserts
  idempotence (one file, exit 0).
- **Loader skip (the key test):** write one active + one `status: draft`
  file into a tmp dir; assert `load_suite` returns only the active case
  with `errors == []`; assert a `drafts/` subdir file is likewise ignored.
- **Edge:** `no-observation` verdict provisions; `--class` differing from
  the row's class warns but uses the CLI value; hash-prefix collision with
  a different full hash errors naming both files.
- **Failure:** unknown hash, `pass` verdict, bad `--class`, corrupt
  `findings.ndjson` row (`ValueError` from `load_findings` surfaces, exit
  nonzero), activating a draft without setting `expected.detection` fails
  `validate_case`.

## Docs surfaces

- `sidecar/case.schema.json`: `status` property description (the
  user-facing contract for draft vs active).
- New module docstring of `omega_prime/evals/case_from_finding.py`
  (operator workflow: provision → review → move → activate → bump).
- `--help` output of the new CLI. No wiki/guide page exists for the
  simulation loop today (workflow lives in module docstrings), so none is
  created; `docs/red-team.md` is out of scope (different subsystem).

## Rollback

Single revert: `git revert` the feature commit. Safe because the `status`
key is optional — pre-existing case files validate identically with or
without the change. Any provisioned drafts are inert data; remove with
`rm -r sidecar/cases/drafts/` (never loaded by any suite path).
