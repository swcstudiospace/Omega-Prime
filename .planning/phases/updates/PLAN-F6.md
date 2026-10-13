# PLAN-F6: RoE lifecycle automation (expiry warnings, no auto-renewal)

## Problem

RoE artifacts (`redteam.roe_file`, default empty ⇒ BLOCKED when enabled;
`omega_prime/config.py` + `OMEGA_PRIME_REDTEAM_ROE_FILE`) carry a bounded
window (`effective_from..effective_until`, enforced fail-closed by
`omega_prime/redteam/roe.py::authorize`, cause `roe-expired` =
`CAUSE_EXPIRED`). The window expires silently: the first signal the operator
gets is a `BLOCKED` transition (`sidecar/state.py` legal-cause set includes
`roe-expired`; gateway path `omega_prime/tools/redteam.py::_sidecar_note`)
at engagement time. The operator must remember to re-sign (`sign_artifact`
writes the adjacent `.sig`); nothing warns at T-7d / T-1d.

## User-facing behavior

- A daily heartbeat job watches the configured RoE artifact and warns before
  expiry: silent while healthy, one warning per day at T-7d, one per day at
  T-1d, and an expired notice once `now > effective_until`.
- `python -m omega_prime.grokbot.roe_lifecycle check --artifact <path>`
  prints the current lifecycle state as JSON for scripting/cron.
- Expiry state is visible in the existing surfaces: `durable_status` gains an
  additive `roe` block; `/metrics` `omega_sidecar_state` still reflects only
  the state machine (BLOCKED on expiry via the existing path — no new gauge,
  no RoE contents per `omega_prime/metrics.py` T-79-06 secrecy rule).
- Warnings never change the state machine: only actual expiry drives the
  pre-existing `roe-expired` → BLOCKED transition. No auto-renewal, no
  signing, no new causes.

## Interface

All names ground in existing modules; no invented paths.

- `omega_prime/cron/heartbeat.py` (pattern: `HEARTBEAT_KIND =
  "session_heartbeat"`, `schedule_heartbeat` / `list_heartbeats` /
  `clear_heartbeat` / `tick_heartbeats` over `JobStore` from
  `omega_prime/cron/scheduler.py`):
  - `ROE_EXPIRY_KIND = "roe_expiry"` — job-kind marker stored on RoE
    watcher jobs (`job["kind"]`), alongside `HEARTBEAT_KIND`.
  - `schedule_roe_expiry(store, artifact_path, *, interval_seconds=86400,
    due_at) -> str` — mirrors `schedule_heartbeat`: validates non-empty
    artifact path and positive interval, calls
    `store.schedule(prompt, due_at, interval_seconds=...)`, stamps
    `kind=ROE_EXPIRY_KIND` + `artifact_path`, `_save()`, returns job id.
    Default cadence is daily (86400s).
  - `list_roe_expiries(store) -> list[dict]` / `clear_roe_expiry(store,
    job_id) -> dict` — mirror the heartbeat list/clear pair; unknown id
    returns `{"error": ...}`, never raises.
  - `tick_roe_expiry(store, now, checker) -> list[dict]` — mirrors
    `tick_heartbeats(store, now, session_runner)`: iterates only
    `ROE_EXPIRY_KIND` non-completed jobs due at `now`, claims via
    `claimed_at`/`history` (`ran_at == now` ⇒ skip), calls
    `checker(artifact_path, now)`, records `last_result`/`last_ran_at`,
    appends bounded history (cap 20, `del history[:-20]`), advances
    `due_at = now + interval_seconds`, `_save()`. `JobStore.tick` callers
    pass `skip_kind=ROE_EXPIRY_KIND` so the generic driver leaves these
    jobs to this tick (existing `skip_kind` parameter).
  - Deduplication: each threshold (7d / 1d / expired) fires at most once
    per UTC calendar day per job — tracked as
    `job["last_fired"] = {threshold: "YYYY-MM-DD"}`; a re-tick the same
    day is silent.
- `omega_prime/redteam/roe.py` loaders (reuse, no changes): the checker
  `json.load`s the artifact, runs `validate_schema` (structural only) and
  `_parse_time(document["effective_until"])`. `days_remaining =
  (end - now).total_seconds() / 86400`. Signature verification
  (`verify_signature` / `authorize(key=...)`) is NOT part of the watcher:
  expiry warnings must fire even for an unsigned artifact; tamper verdicts
  stay with `authorize`. Unparseable/missing `effective_until` ⇒ state
  `invalid` (never `expired`); missing file ⇒ `missing`.
- `omega_prime/grokbot/roe_lifecycle.py` (new; CLI pattern per
  `omega_prime/grokbot/doctor.py:651` `main(argv) -> int` + `argparse`):
  - `check --artifact <path> [--now <ISO-8601 UTC>] [--json]` (default
    human line + `--json` machine output). `--now` is test/scripting
    override; default `datetime.now(UTC)`.
  - Stdout JSON: `{"state": "valid" | "expiring-7d" | "expiring-1d" |
    "expired" | "missing" | "invalid", "days_remaining": float | null,
    "cause": str, "effective_until": str | null}`. `cause` reuses the
    `roe.py` vocabulary (`"ok"`, `CAUSE_MISSING="roe-missing"`,
    `CAUSE_EXPIRED="roe-expired"`, `CAUSE_INVALID="roe-invalid"`).
  - Exit codes: `0` valid/expiring (warnings are not failures); `3`
    expired/missing/invalid (mirrors `sidecar/harness.py --check` BLOCKED
    exit 3); `2` usage error. Never prints key material or file contents.
- Surfacing (additive only):
  - `omega_prime/tools/durable_surface.py::durable_status` gains `roe:
    {state, days_remaining, effective_until}` read from
    `redteam.roe_file` at the registered `root`; artifact absent ⇒
    `{"state": "missing", ...}`. Existing keys (`journal`, `path`,
    `open_runs`, `entry_count`, `last_run_id`, `workflow_checkpoints`)
    unchanged.
  - `omega_prime/metrics.py`: no new gauge. Expiry reaches
    `omega_sidecar_state` only through the existing
    `register_sidecar_state` / `_sidecar_note("BLOCKED", "roe-expired")`
    path in `omega_prime/tools/redteam.py`.
- Config: no new keys. Artifact path resolves from existing
  `redteam.roe_file` / `OMEGA_PRIME_REDTEAM_ROE_FILE`; CLI `--artifact`
  overrides per-invocation. No `OMEGA_PRIME_*` additions.

## Non-goals

- Auto-renewal or re-signing: `sign_artifact` untouched; watcher never
  writes the artifact or its `.sig`.
- Signing, key management, broker rule: `roe_key` / `roe_key_file` paths
  unchanged.
- New state-machine causes: only the existing `roe-expired` cause may drive
  BLOCKED; warnings emit notices, never transitions.
- New metrics gauges, new slash commands in `omega_prime/commands/`, new
  heartbeat transports.

## Done-criteria

1. `schedule_roe_expiry` → `tick_roe_expiry` round-trips on a `JobStore`
   over a temp `jobs.json`: job carries `kind == "roe_expiry"`.
2. Hermetic clock (inject `now: datetime`, UTC; no wall-clock reads in the
   checker): T-8d tick is silent (`last_result.state == "valid"`, nothing
   fired); T-7d tick fires `expiring-7d` exactly once; second tick same UTC
   day is silent; T-1d tick fires `expiring-1d` exactly once; post-window
   tick fires `expired` exactly once per day.
3. `check --artifact` JSON matches the schema above for all six states;
   exit codes 0/3/2 as specified; `--now` reproduces the tick verdicts.
4. Missing file ⇒ `missing` (not `expired`); garbage JSON / schema mismatch
   / unparseable `effective_until` ⇒ `invalid` (never BLOCKED as expired).
5. BLOCKED transitions unchanged: `omega_prime/tests/test_sidecar_state.py`
   passes unmodified; warnings never call `transition()`.
6. `durable_status` returns the `roe` block additively; journal-absent and
   journal-present shapes otherwise byte-identical to today.
7. `/metrics` output unchanged except the existing gauge value on real
   expiry; no operator name, date, or artifact content in gauge labels.

## Test plan

Repo pytest level (`omega_prime/tests/`, hermetic, `tmp_path` + injected
`now`; pattern: `test_sidecar_state.py`, `test_sidecar_roe.py`):

- Happy: `test_roe_lifecycle_thresholds` — signed fixture artifact with
  fixed `effective_until`; ticks at T-8d/T-7d(+repeat same day)/T-1d/T+0s
  assert states `valid`/`expiring-7d`(once)/`expiring-1d`/`expired` and
  `days_remaining` within 1e-6; history capped at 20.
- Edge: midnight boundary (23:59 vs 00:01 UTC fire independently);
  `effective_until` with timezone offset parses via `_parse_time`;
  `JobStore.tick(skip_kind="roe_expiry")` leaves watcher jobs untouched
  while `tick_roe_expiry` runs them.
- Failure: missing file ⇒ `missing`/exit 3; non-JSON ⇒ `invalid`/exit 3;
  schema mismatch (e.g. no `operator`) ⇒ `invalid`; unparseable date ⇒
  `invalid`; `clear_roe_expiry` unknown id ⇒ `{"error": ...}`; CLI no
  `--artifact` ⇒ exit 2.
- Regression: full `test_sidecar_state.py` + `test_sidecar_roe.py` suites
  green unmodified; `durable_status` golden test for old keys.

## Docs surfaces

- `docs/red-team.md` — RoE lifecycle section: watcher cadence, thresholds,
  `check` usage, "warnings never BLOCK; only expiry does; re-sign to
  renew".
- `docs/tool-catalog.md` — `durable_status` entry gains the `roe` block
  fields.
- No slash-command catalog or generated-prompt changes (no new
  `omega_command`).

## Rollback

Single revert: delete `omega_prime/grokbot/roe_lifecycle.py` +
`omega_prime/tests/test_roe_lifecycle.py`, revert the `heartbeat.py`
(`ROE_EXPIRY_KIND` + four functions) and `durable_surface.py` (`roe`
block) hunks, remove the `docs/red-team.md` + `docs/tool-catalog.md`
sections. No migrations, no config keys, no state-file format change —
`JobStore` jobs with `kind == "roe_expiry"` are inert under generic
`tick` and removable via `clear_roe_expiry`.
