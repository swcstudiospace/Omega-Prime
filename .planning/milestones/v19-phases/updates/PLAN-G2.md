# PLAN-G2: RoE in-chat signing UX

## Problem

Creating and signing a Rules-of-Engagement artifact is a docs-only
procedure. Today the operator must copy
`sidecar/roe.yaml.template` by hand, craft the JSON document, and run the
inline `python3 -c "... sign_artifact(...)"` snippet from the template
comments. There is no in-chat path: nothing in `omega_prime/commands/`
wraps `omega_prime/redteam/roe.py`, so operators either fumble the
canonical-JSON/HMAC steps or skip the signing act that takes the harness
out of BLOCKED. `authorize()` (`omega_prime/redteam/roe.py:199`) is the
fail-closed gate; the UX around it is missing.

## User-facing behavior

Three new `omega_commands` (registered in `omega_prime/commands/__init__.py`
`COMMANDS` + `_run`/`_redteam` dispatch, mirroring `omega-redteam-triage`):

1. `/omega-redteam-roe-create --operator <name> --days <N> --key-file <path>`
   builds an unsigned artifact JSON and writes it to the reports dir
   (`sidecar/state.py:reports_dir()`, `OMEGA_PRIME_SIDECAR_REPORTS_DIR`
   override wins, same `_reports_dir(env)` pattern as
   `redteam_status.py:31`). Document fields: `schema_version` =
   `SCHEMA_VERSION` (1), `operator` from the flag, `authorized_targets`
   fixed to `[{"host": "127.0.0.1"}]` per the loopback-only scope default
   (`LOOPBACK_HOSTS`, `validate_targets`), `effective_from` = now UTC,
   `effective_until` = now + N days (RFC3339 `+00:00`, cf.
   `state.utc_now_rfc3339`), `case_classes` = full `CASE_CLASSES` tuple.
   `--key-file` is accepted for symmetric UX but NOT read at create time.
   Output: artifact path. No signature file is written.
2. `/omega-redteam-roe-sign --artifact <path> --key-file <path>` reads the
   key bytes from `--key-file`, calls `sign_artifact(artifact, key)`
   (`roe.py:91`, writes adjacent `<artifact>.sig`), and prints the exact
   install command (`export OMEGA_PRIME_REDTEAM_ROE_FILE=<artifact>` plus
   key-file placement hint). It does NOT install anything itself.
3. `/omega-redteam-roe-check` resolves artifact + key exactly like the
   runtime does (`redteam.roe_file` / `redteam.roe_key` /
   `redteam.roe_key_file` per `omega_prime/config.py:296-309`), calls
   `authorize(artifact, key=key)`, and prints `allowed`, `cause`
   (`roe-missing` / `roe-expired` / `roe-invalid` / `ok`), `detail`, and
   `days_remaining` (whole days from now until `effective_until`,
   mirroring `grokbot/roe_lifecycle.py` status output; negative/zero when
   expired).

Key hygiene: key bytes are never printed, never logged, never echoed in
`detail` or error results. Key material comes only from operator-provided
paths (`--key-file` flag or `OMEGA_PRIME_REDTEAM_ROE_KEY_FILE`); the
commands never invent, fetch, or default key locations.

## Interface

```text
/omega-redteam-roe-create --operator <name> --days <N> --key-file <path>
/omega-redteam-roe-sign --artifact <path> --key-file <path>
/omega-redteam-roe-check
```

- Modules: `omega_prime/commands/redteam_roe_create.py`,
  `redteam_roe_sign.py`, `redteam_roe_check.py`, each exposing
  `NAME` / `USAGE` / `SUMMARY` / `WHEN` / `Error` / `run(args, env)`
  per the `redteam_status.py` / `redteam_triage.py` convention.
- Arg parsing: plain flag scan (`--operator`, `--days`, `--key-file`,
  `--artifact`), `missing_<flag>` structured errors with `usage`.
- Config keys (read-only, no new keys): `OMEGA_PRIME_REDTEAM_ROE_FILE`,
  `OMEGA_PRIME_REDTEAM_ROE_KEY`, `OMEGA_PRIME_REDTEAM_ROE_KEY_FILE`,
  `OMEGA_PRIME_SIDECAR_REPORTS_DIR` (`OMEGA_PRIME_*` conventions,
  `config.py:45,296-314`).
- Heartbeat kinds: none. No `omega_prime/cron/heartbeat.py` job is added;
  `roe-check` is operator-invoked, not scheduled.
- Result dicts: `{"artifact": ..., "operator": ..., "effective_until": ...}`,
  `{"artifact": ..., "signature": ..., "install": ...}`,
  `{"allowed": bool, "cause": ..., "detail": ..., "days_remaining": int}`.

## Non-goals

- Auto-installing the artifact or key into config/env; `-sign` prints the
  install command, the operator runs it.
- Key generation or key provisioning (out-of-band, per template comments).
- Changing `authorize()` / `validate_schema()` / `validate_targets()`
  semantics in any way; commands are thin wrappers.
- Non-loopback targets or per-target tool grants (fixed loopback default;
  grants stay with `grant_tool_allowlist`).

## Done-criteria (independently verifiable)

1. `/omega-redteam-roe-create --operator alice --days 7 ...` writes valid
   JSON to the reports dir containing all six `REQUIRED_KEYS`, loopback
   host only, and a 7-day `effective_from..effective_until` window.
2. `/omega-redteam-roe-sign` on that artifact writes `<artifact>.sig` and
   `authorize()` returns `cause == "ok"` with the same key.
3. `/omega-redteam-roe-check` prints `allowed=true`, `cause=ok`, and
   `days_remaining == 6 or 7` (boundary-safe).
4. No test, log line, or result dict contains key bytes or the key file's
   content (grep the captured output for the key sentinel).
5. `authorize()` source is untouched (`git diff --stat` shows no
   `omega_prime/redteam/roe.py` changes).

## Test plan (hermetic, happy/edge/failure)

New `tests/test_redteam_roe_commands.py`, hermetic via `tmp_path` +
`monkeypatch` on `OMEGA_PRIME_SIDECAR_REPORTS_DIR`:

- Happy: create→sign→check round-trip on tmp paths (random 32-byte key
  file); assert `allowed`, `cause == "ok"`, artifact validates under
  `validate_schema` == [] and `validate_targets` == [].
- Edge: `--days 0` rejected (`invalid_days`); expired artifact
  (`effective_until` in the past) yields `cause == "roe-expired"` and
  `days_remaining <= 0`; missing artifact yields `roe-missing`.
- Failure: nonexistent `--key-file` yields `key_unreadable` without
  leaking the path contents; tampered artifact after signing yields
  `roe-invalid` (signature mismatch); non-loopback `authorized_targets`
  edit yields `roe-invalid` via `validate_targets`.
- Hygiene: `capsys` on all three commands; assert key bytes absent from
  stdout and result dicts.

## Docs surfaces

- `sidecar/roe.yaml.template` comments: replace the raw `python3 -c`
  snippet with the three in-chat commands (keep the HMAC note).
- `docs/red-team.md` RoE section: document the create→sign→check flow and
  the exact install command `-sign` prints.
- `COMMANDS` catalog entries in `omega_prime/commands/__init__.py` carry
  the `USAGE` strings (user discovery).

## Rollback (single revert)

Single revert: `git revert <merge-commit>` removes the three command
modules, their `COMMANDS`/`_run` wiring, tests, and doc edits. No schema,
config, or data migration exists; unsigned artifacts and `.sig` files in
the reports dir are inert without `OMEGA_PRIME_REDTEAM_ROE_FILE` set, and
`authorize()` semantics are unchanged so prior BLOCKED behavior resumes.
