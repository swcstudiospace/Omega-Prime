# PLAN-G7: Upstream release watcher digest

## Problem

New purple-team upstream releases land unnoticed. Tags `v1.2` and `v1.3`
shipped without any operator action: the F1 remediation loop
(`.planning/phases/updates/PLAN-F1.md`, `supply-chain.yml:42-72` audit
chain + `scripts/redteam-audit-triage.py` triage) only reacts once the
pinned gitlink (`831a6b0`, see `git submodule status`) audits RED, and
the G1 pin-bump path only moves after a human notices. There is no
signal of the form "upstream cut a new tag — decide whether to
re-prove the pin". The operator learns about releases by accident,
days late, with no date/commit attached.

## User-facing behavior

A daily heartbeat job watches upstream tags and digests what is new:

1. On each tick the watcher runs `gh api
   repos/swcstudiospace/purple-team/tags` (same `gh api` cross-repo
   convention as the F1 `remediate` job) and compares tag names against
   the last-seen state file `sidecar/reports/release-watch.json`
   (sibling of `sidecar/reports/last-run.json`).
2. First run (state file absent) seeds silently: writes current tags,
   fires nothing, exits 0. Seeding is not a finding.
3. Each tag present upstream but absent from state is a new release:
   the tick records it, appends it to state, and emits one
   `durable_status` line per new tag carrying tag name, tag date, and
   commit SHA. Re-ticks never re-fire already-recorded tags.
4. No new tags upstream: silent bookkeeping (state rewritten only if
   the upstream listing changed shape, e.g. commit SHAs refreshed),
   nothing fired, exit 0.
5. `gh` absent or `gh api` failing (no network, 403/rate-limit):
   degrade loudly — tick records the failure in the job history, state
   file untouched, exit 0 (signal, not red). Never deletes state on
   fetch failure.

## Interface

All names ground in existing modules; no invented paths.

- `omega_prime/cron/heartbeat.py` (pattern: `ROE_EXPIRY_KIND =
  "roe_expiry"`, `schedule_roe_expiry` / `list_roe_expiries` /
  `clear_roe_expiry` / `tick_roe_expiry` over `JobStore` from
  `omega_prime/cron/scheduler.py`, history cap
  `ROE_EXPIRY_HISTORY_LIMIT = 20`, `_roe_now` clock normalization):
  - `RELEASE_WATCH_KIND = "release_watch"` — job-kind marker stored on
    watcher jobs (`job["kind"]`), alongside `HEARTBEAT_KIND` and
    `ROE_EXPIRY_KIND`.
  - `RELEASE_WATCH_INTERVAL_SECONDS = 86400` — default daily cadence.
  - `schedule_release_watch(store, *, interval_seconds=86400, due_at)
    -> str` — mirrors `schedule_roe_expiry`: validates positive
    interval, calls `store.schedule(prompt, due_at,
    interval_seconds=...)`, stamps `kind=RELEASE_WATCH_KIND`, `_save()`,
    returns job id. Repo comes from config, not the schedule call.
  - `list_release_watches(store) -> list[dict]` /
    `clear_release_watch(store, job_id) -> dict` — mirror the roe
    list/clear pair; unknown id returns `{"error": ...}`, never raises.
  - `tick_release_watch(store, now, checker) -> list[dict]` — mirrors
    `tick_roe_expiry(store, now, checker)`: iterates only
    `RELEASE_WATCH_KIND` non-completed jobs due at `now`, claims via
    `claimed_at`/`history` (`ran_at == now` ⇒ skip), calls
    `checker(now)`, records `last_result`/`last_ran_at`, appends bounded
    history (cap 20, `del history[:-20]`), advances `due_at = now +
    interval_seconds`, `_save()`. Returns only jobs that fired (≥1 new
    tag). `JobStore.tick` callers pass `skip_kind=RELEASE_WATCH_KIND`.
- `omega_prime/grokbot/release_watch.py` (new; CLI pattern per
  `omega_prime/grokbot/doctor.py:651` `main(argv) -> int` + `argparse`,
  checker pattern per `omega_prime/grokbot/roe_lifecycle.py`):
  - `check_release_watch(state_path, fetch, now) -> dict` — pure core:
    `fetch()` returns the parsed `gh api .../tags` list (injected;
    subprocess call lives in `main`, never in the core). Diff tag names
    vs `{"last_seen": [...]}` in `state_path`. Missing state file ⇒
    seed branch (write + `{"seeded": True, "new": []}`).
  - `check --state <path> [--repo OWNER/NAME] [--json]` (default human
    lines, one per new tag: `<tag> <date> <sha>`; `--json` machine
    output `{"new": [{"tag","date","sha"}], "seeded": bool}`).
  - Exit codes: `0` new-tags/seeding/clean (digest is not a failure);
    `3` fetch failure (mirrors `roe_lifecycle` BLOCKED exit 3, but the
    heartbeat tick itself degrades to exit 0 — see behavior §5); `2`
    usage error. Never prints tokens; `gh` stderr passes through
    uncensored except secret redaction already in `gh`.
  - State file schema: `{"last_seen": [{"tag": str, "sha": str}],
    "checked_at": "<ISO-8601 UTC>"}`. Atomic write (tmp + rename).
- Surfacing (additive only):
  - `omega_prime/tools/durable_surface.py::durable_status` gains
    `releases: {new: [...], checked_at: str | null}` read from the
    state file at the registered `root`; state absent ⇒ `{"new": [],
    "checked_at": null}`. Existing keys (`journal`, `roe`,
    `workflow_checkpoints`) unchanged.
- Config (`omega_prime/config.py` `_env_overrides`, `parts[0] ==
  "redteam"` branch alongside `OMEGA_PRIME_REDTEAM_ROE_FILE` /
  `OMEGA_PRIME_REDTEAM_REPORTS_DIR`):
  - `OMEGA_PRIME_REDTEAM_RELEASE_WATCH_ENABLED` — truthy enables the
    watcher; default off (matches all-flags-default-off rule).
  - `OMEGA_PRIME_REDTEAM_RELEASE_WATCH_REPO` — default
    `swcstudiospace/purple-team`; empty means the CLI fails closed.
  - `OMEGA_PRIME_REDTEAM_RELEASE_WATCH_STATE` — state-file override;
    default `sidecar/reports/release-watch.json`. Non-secret.

## Non-goals

- Auto pin-bumping: G1 owns the bump runbook (`docs/red-team.md`
  "Bumping the redteam submodule"); the watcher never touches the
  gitlink, `redteam/` checkout, or `VENDOR.md` pins.
- Issue creation: no `gh issue create`, no omes-bot posts, no upstream
  PRs (F1 owns the PR path; G-sector review owns triage).
- Advisory auditing: `npm audit` interpretation stays with the F1 /
  `supply-chain.yml` chain; the watcher reports tags only.
- New metrics gauges, new slash commands in `omega_prime/commands/`,
  new heartbeat transports.

## Done-criteria

1. `schedule_release_watch` → `tick_release_watch` round-trips on a `JobStore`
   over a temp `jobs.json`: job carries `kind == "release_watch"`, interval 86400.
2. First tick with no state file seeds silently: state written, fired
   list empty, `seeded == True`.
3. Tick with upstream `[v1.2, v1.3]` over state `[v1.2]` fires exactly
   `[v1.3]` with tag/date/sha; re-tick same day fires nothing.
4. Tick with identical upstream and state fires nothing (silent bookkeeping);
   state `checked_at` refreshes.
5. `gh` absent / `gh api` non-zero: tick records failure in job history,
   state file byte-identical, exit path degrades (no raise).
6. `durable_status` returns the `releases` block additively;
   journal-absent and journal-present shapes otherwise byte-identical.
7. State file round-trips: write then read preserves tag/sha pairs; corrupt JSON
   ⇒ treated as seed (rewrite), never crash.

## Test plan

Repo pytest level (`omega_prime/tests/`, hermetic, `tmp_path` + injected `now`
and fake `fetch`; pattern: `test_roe_lifecycle.py`, `test_heartbeat.py`):

- Happy: `test_release_watch_new_tag` — fake fetch `[v1.2, v1.3]`, seeded state
  `[v1.2]`; tick fires `[v1.3]` with date+sha; state now lists both; history cap 20.
- Edge: `test_release_watch_seed` — no state file ⇒ seeded, no fire;
  `test_release_watch_clean` — identical lists ⇒ no fire; `JobStore.tick` with
  `skip_kind="release_watch"` leaves watcher jobs untouched.
- Failure: `test_release_watch_gh_absent` — fetch raising `FileNotFoundError` ⇒
  degrade record, state untouched; corrupt state JSON ⇒ reseed;
  `clear_release_watch` unknown id ⇒ `{"error": ...}`; CLI no `--state` ⇒ exit 2.
- Regression: `test_durable_surface.py` + `test_heartbeat.py` suites
  green unmodified; `durable_status` golden test for old keys.

## Docs surfaces

- `docs/red-team.md` — release-watcher subsection under the F1 loop: cadence,
  seed behavior, state-file path, "digest is not a bump; follow the G1 runbook".
- `docs/tool-catalog.md` — `durable_status` entry gains the `releases`
  block fields.
- No slash-command catalog or generated-prompt changes (no new
  `omega_command`).

## Rollback

Single revert: delete `omega_prime/grokbot/release_watch.py` +
`omega_prime/tests/test_release_watch.py`, revert the `heartbeat.py`
(`RELEASE_WATCH_KIND` + four functions), `durable_surface.py`
(`releases` block), and `config.py` (three `RELEASE_WATCH_*` branches)
hunks, remove the `docs/red-team.md` + `docs/tool-catalog.md`
sections, delete `sidecar/reports/release-watch.json`. No migrations —
`JobStore` jobs with `kind == "release_watch"` are inert under generic
`tick` and removable via `clear_release_watch`.

## Grounding notes

Symbols/paths cited above verified read-only on 2026-10-13: watcher
pattern `ROE_EXPIRY_KIND`/`schedule_roe_expiry`/`tick_roe_expiry` at
`omega_prime/cron/heartbeat.py:125-252`, history cap `:131`, clock
normalizer `_roe_now` at `:185-200`; checker + CLI pattern
`check_roe_lifecycle`/`main(argv)` at
`omega_prime/grokbot/roe_lifecycle.py:81-186`, CLI precedent
`doctor.py:651`; surfacing `durable_status` + `_roe_block` at
`omega_prime/tools/durable_surface.py:26-90`; config branch
`parts[0] == "redteam"` with `ROE_FILE`/`REPORTS_DIR` at
`omega_prime/config.py:260-317`; state sibling
`sidecar/reports/last-run.json`; F1 `gh api` + audit chain at
`.planning/phases/updates/PLAN-F1.md:24-65`.
