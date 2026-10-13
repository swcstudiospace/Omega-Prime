# PLAN-F2: /omega-redteam slash-command suite

## Problem

The purple-team harness is CLI-only (`python sidecar/harness.py --check`,
`sidecar/simulation.py` suite runs, raw NDJSON under `sidecar/reports/`).
The Bot operator cannot drive or inspect it from chat: there is no
`/omega-*` command that reports the fail-closed gate verdict, tails
findings, or proposes triage. The existing red-team chat surface is the
`tempest_*` tool family (`omega_prime/tools/redteam.py:REDTEAM_TOOL_NAMES`,
gateway-bound, mission start/stop gated on approval) — it covers live
gateway missions, not the sidecar simulation harness. Result: the operator
must leave chat and read files to answer "is the harness BLOCKED, and why?"

## User-facing behavior

Four new curated slash commands in `omega_prime/commands/`, dispatched
through the existing `_run` chain in `omega_prime/commands/__init__.py`
and listed by `/omega-help` via `command_catalog()`:

1. `/omega-redteam-run --suite <name>` — requests a simulation suite run
   (`sidecar/simulation.py:run_suite`, suites under `sidecar/cases/` loaded
   by `load_suite`). When the fail-closed gate verdict is BLOCKED
   (`sidecar/harness.py:evaluate`, causes `roe-missing` / `roe-expired` /
   `roe-invalid` from `omega_prime/redteam/roe.py:authorize`, or
   `OMEGA_PRIME_REDTEAM_KILL` set per `omega_prime/tools/redteam.py`), the
   command returns the harness cause line
   (`sidecar {ver}: BLOCKED ({cause}): {detail}`) and never executes.
2. `/omega-redteam-status` — current state-machine state plus recent
   transitions tailed from `sidecar/reports/transitions.ndjson`
   (`sidecar/state.py:transition`, `transitions_path`; reports dir follows
   `OMEGA_PRIME_SIDECAR_REPORTS_DIR` when set).
3. `/omega-redteam-findings [--limit N]` — read-only tail of
   `sidecar/reports/findings.ndjson` (`sidecar/findings.py:FINDINGS_FILENAME`,
   rows keyed by `dedupe_hash = sha256(...)`). Default limit 10. Never
   writes, never replays.
4. `/omega-redteam-triage <dedupe-hash> --rationale <text>` — appends one
   triage PROPOSAL row `{dedupe_hash, rationale, ts, proposer}` to the
   review queue (`sidecar/reports/review-queue.ndjson`, new file). A human
   merges accepted proposals into the workflow `TRIAGED` block
   (`scripts/redteam-audit-triage.py:TRIAGED`, dated `(date, author)`
   tuples); the command itself never edits `TRIAGED`.

## Interface

- New `CommandSpec` entries in the `COMMANDS` tuple
  (`omega_prime/commands/__init__.py:48-125`), exact existing format:
  `CommandSpec("omega-redteam-run", "/omega-redteam-run --suite <name>", ...,
  needs_args=True)` (and likewise `omega-redteam-status` without
  `needs_args`, `omega-redteam-findings` without, `omega-redteam-triage`
  with `needs_args=True`). `when` sentences follow the roster style
  ("The user asks ... red-team harness ...").
- New `_run` branches in `omega_prime/commands/__init__.py:248-296`
  (same `_ok` / `_fail` dict envelope, `missing_args` when `needs_args`
  and no args per `:217-222`). Read paths reuse `sidecar/state.py` and
  `sidecar/findings.py` helpers (stdlib-only, no gateway import); the
  `-run` branch calls `sidecar/harness.py:evaluate(env)` first and
  short-circuits on BLOCKED.
- `--suite <name>` validated against `load_suite` names; unknown suite is
  a structured `unknown_suite` failure naming available suites, never a
  raise. `--limit N` parsed as int, clamped to 1..100; non-numeric is a
  structured `bad_limit` failure. `<dedupe-hash>` must be 64 lowercase hex
  (matches `test_sidecar_findings.py:48`); `--rationale` non-empty, max
  500 chars.
- Config: no new `omega_prime/config.py` node. Gate inputs stay the
  existing `redteam.*` nodes (`config.py:233-300`: `enabled`, `kill`,
  `roe_required`, `roe_file`, `roe_key`, `roe_key_file`) with their
  `OMEGA_PRIME_REDTEAM_*` env overrides. No new heartbeat kind
  (`omega_prime/cron/heartbeat.py:HEARTBEAT_KIND` untouched).

## Non-goals

- Live gateway engagements from chat (the `tempest_mission_start` approval
  path in `omega_prime/tools/redteam.py` is unchanged).
- Bypassing, weakening, or auto-remedying the RoE gate: any BLOCKED verdict
  returns the cause line; there is no `--force`, no key provisioning, no
  artifact signing from chat.
- Auto-triage: the command writes PROPOSAL rows only; only a human edits
  the `TRIAGED` block or closes findings.

## Done-criteria

1. All four commands registered as curated `CommandSpec`s and listed by
   `/omega-help` (count +4 over the current 12).
2. Roster consistency updated together (per the roster lesson): install
   `setup_check` coverage (`omega_prime/setup_check.py`) +
   `omega_prime/tooling/catalog.py` rendering +
   `docs/tool-catalog.md` regenerated (`--out docs/tool-catalog.md`; CI
   fails on drift per its header).
3. BLOCKED path: with RoE missing (empty `roe_file`) or kill switch set,
   `/omega-redteam-run --suite <name>` returns the exact harness cause
   line and performs no run (no new rows in `findings.ndjson`,
   `transitions.ndjson` from the attempt).
4. IDLE path: with a signed RoE fixture, `-status` reports `IDLE` and
   `-findings` tails seeded rows; `-triage` appends exactly one well-formed
   PROPOSAL row and nothing else.
5. Hermetic pytest at the repo's level (`omega_prime/tests/test_*.py`,
   `sidecar` stdlib-only, fakes only, `OMEGA_PRIME_SIDECAR_REPORTS_DIR`
   pointed at `tmp_path`): BLOCKED and IDLE paths covered, no sockets.

## Test plan

- Happy: IDLE fixture — `-status` shows state + last transitions;
  `-findings --limit 2` returns the two newest rows newest-last; `-triage`
  with valid hash + rationale appends one PROPOSAL row (assert JSON keys).
- Edge: `-findings --limit 0/999/abc` clamps or fails structured;
  `-triage` with short hash, empty rationale, or 501-char rationale fails
  structured with `usage`; unknown `--suite` names available suites.
- Failure: RoE missing / expired / invalid fixtures and kill-switch env —
  `-run` returns cause line, exit-equivalent `ok: False`, zero sidecar
  writes; every new command under `execute_command` returns the dict
  envelope, never raises (mirrors `test_command_generation.py` style).

## Docs surfaces

- `docs/tool-catalog.md` (regenerated; counts the four commands).
- `docs/red-team.md` (operator runbook: the four commands, BLOCKED cause
  line meaning, PROPOSAL → `TRIAGED` merge steps).
- `docs/driving-the-bot.md` if it enumerates slash commands (keep the
  command list consistent with `/omega-help`).

## Rollback

Single revert of the one implementation commit (new `CommandSpec`s + `_run`
branches + tests + regenerated catalog). No migration, no config node, no
schema: `sidecar/reports/review-queue.ndjson`, if created, is inert
append-only output and safe to leave or delete.
