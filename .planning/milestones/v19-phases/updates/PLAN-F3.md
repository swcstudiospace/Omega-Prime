# PLAN-F3: Findings → tracker sync (GitHub issues push)

## Problem

`export_work_items()` in `sidecar/findings.py` writes one tracked-work-item
row per deduped finding to `sidecar/reports/work-items.json` — a local JSON
file nobody consumes. Red-team findings (missed detections, no-detections)
accumulate in `sidecar/reports/findings.ndjson` with no path to a human
workflow. The module docstring even admits it: "convertible to issues later;
no network, no external tracker API calls". This spec closes that gap: a
one-way push from local findings to GitHub issues.

## User-facing behavior

- Operator runs `python -m omega_prime.grokbot.findings_sync --repo OWNER/NAME`
  (repo also resolvable from config `redteam.findings_sync.repo`).
- Each NEW (not-yet-synced) work item becomes one GitHub issue on omes-bot
  (`swcstudiospace/omes-bot`, the repo named in
  `omega_prime/grokbot/SETUP.md` and `omega_prime/grokbot/deploy.py`
  `SOURCE_URL`) via `gh api repos/{repo}/issues`.
- Severity maps to labels: `critical` → `severity:critical` + `redteam`,
  `high` → `severity:high` + `redteam`, `medium` → `severity:medium`,
  `low` → `severity:low`; operator-overridable via
  `redteam.findings_sync.labels` config map.
- After a successful push, the finding record gains the pushed issue ref
  (`issue_url` + `synced_at` RFC3339, cf. `sidecar/state.py::utc_now_rfc3339`)
  so a second run pushes nothing (idempotent by `dedupe_hash`, the
  `sha256(canonical(case_id + normalized response))` key from
  `sidecar/findings.py::dedupe_hash`).
- Config-off (`redteam.findings_sync.enabled: false`, the default) the CLI
  and the heartbeat entrypoint are inert: exit 0, push nothing, print
  `findings_sync disabled`.
- Heartbeat-schedulable: a tick function reuses the same sync entrypoint so
  `omega_prime/cron/heartbeat.py` (`HEARTBEAT_KIND = "session_heartbeat"`)
  / `scheduler.py` (`tick`, `schedule`) precedent can schedule it; the sync
  itself stays a plain function, not a new cron kind.

## Interface

New module `omega_prime/grokbot/findings_sync.py` (stdlib + `gh` CLI only):

```bash
python -m omega_prime.grokbot.findings_sync --repo OWNER/NAME [--reports-dir DIR] [--dry-run]
```

- `--repo`: overrides `redteam.findings_sync.repo`. Empty repo + no flag →
  exit 2 with `no target repo configured` (fail-closed, never guess).
- `--reports-dir`: overrides `sidecar/state.py::reports_dir()` (tests + CI).
- `--dry-run`: print would-push items, write nothing, call no `gh`.
- `GhRunner` seam: `Callable[[list[str], dict], CompletedProcess-like]` —
  default `subprocess.run` (the `local_validation.py` / `oneclick.py`
  precedent); tests inject a fake. Zero network in tests.
- `sync_findings(reports_dir, repo, *, runner, labels, dry_run=False) ->
  {"pushed": [...], "skipped": [...], "dry_run": bool}` — the heartbeat
  entrypoint calls this, never `gh` directly.

Config node in `omega_prime/config.py::_defaults()` (follows the
`telemetry.otlp` / `rotation` off-by-default precedent):

```python
"redteam": {
    ...,
    "findings_sync": {
        "enabled": False,
        "repo": "",
        "labels": {},   # e.g. {"critical": ["severity:critical", "redteam"]}
    },
}
```

Env overrides in `_env_overrides()` (the `OMEGA_PRIME_*` / `_ENV_PREFIX`
precedent, cf. `OMEGA_PRIME_REDTEAM_ROE_KEY` broker rule — no secrets here,
labels/repo are non-secret):

- `OMEGA_PRIME_REDTEAM_FINDINGS_SYNC_ENABLED` → `redteam.findings_sync.enabled`
- `OMEGA_PRIME_REDTEAM_FINDINGS_SYNC_REPO` → `redteam.findings_sync.repo`

Issue body template (fixed fields, no raw payload — threat T-79-11 keeps raw
payload text in the versioned case file): title from `export_work_items()`
(`[{severity}] {case_id}: {observed_detection} ({verdict})`), body with
`case_id`, `severity`, `verdict`, `dedupe_hash`, `occurrence_count`, footer
`synced by findings_sync`.

## Non-goals

- Two-way sync (never pull issue state, comments, or edits back).
- Auto-close (closing an issue when a finding stops reproducing is out).
- Non-GitHub trackers (no Linear/Jira/generic webhook sink).
- Changing `export_work_items()` semantics (it stays file-based, no network).
- New cron kind or scheduler changes (reuse `tick`/`schedule` precedent only).

## Done-criteria

1. `enabled:false` (default) → CLI exits 0, pushes nothing, prints disabled.
2. Empty repo (no flag, no config) → exit 2, pushes nothing.
3. Happy path (fake runner): N unsynced findings → N `gh api` calls with
   correct `repos/{repo}/issues` path, title, and severity labels.
4. Idempotency: second run with the same store pushes 0 (rows carry
   `issue_url`); replay of identical content bumps `occurrence_count`
   (existing `record_finding` dedupe) without a second issue.
5. `--dry-run` writes nothing (findings NDJSON byte-identical) and calls no runner.
6. `gh` failure (nonzero exit) → nonzero CLI exit, row left unsynced, retry
   on next run pushes it.

## Test plan

New `omega_prime/tests/test_findings_sync.py` (pytest, hermetic, zero
network — fake `GhRunner` recording argv, mirroring
`test_sidecar_findings.py` + `test_grokbot_sync.py` fixtures):

- Happy: seed 2 findings via `record_finding` in `tmp_path` reports dir,
  run `sync_findings` with fake runner → 2 calls, issue refs recorded.
- Edge: pre-synced row (has `issue_url`) skipped; unknown severity falls
  back to `redteam` label only; empty findings → `{"pushed": [], ...}`.
- Failure: runner raises / returns nonzero → row unsynced, retried next run;
  missing `gh` binary → structured error, exit nonzero, no partial write
  (atomic write via the `_write_rows_atomic` precedent).
- Config-off inertness: `enabled:false` → no runner calls.
- Idempotency: run twice → second run 0 calls.
- Env override: `OMEGA_PRIME_REDTEAM_FINDINGS_SYNC_ENABLED=1` /
  `_REPO=OWNER/NAME` reflected in `load_config()` (the `test_prime_config.py`
  precedent).

## Docs surfaces

- `omega_prime/grokbot/SETUP.md` — findings-sync section (enable, repo,
  heartbeat scheduling pointer).
- `sidecar/findings.py` module docstring — point at `findings_sync` as the
  consumer of `work-items.json`.
- New user page under `docs/` if the repo convention requires
  (`omega_prime/tests/test_docs.py` asserts SUMMARY ↔ docs agreement —
  update whichever page the implementer wires).

## Rollback

Single revert of the implementing commit: `findings_sync.py` +
`config.py` node + tests removed; `export_work_items()` output
(`work-items.json`) untouched and still file-consumable. Config-off default
means a partial rollout is already inert.
