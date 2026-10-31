# Red team module

An optional, default-off tool family that mirrors the T3MP3ST loopback
gateway. When enabled, the Omega Prime tool registry gains eleven
`tempest_*` tools that proxy the upstream gateway's HTTP API, plus a
vendored copy of T3MP3ST's declarative Grok Bot pack. Omega Prime ships
no gateway: the tools talk to a T3MP3ST sidecar you start and control.
The upstream and what was and was not vendored is in `VENDOR.md`.

This page documents the contract. It does not claim live results — see
[Status](#status).

## The gate

The family is off until `omega-prime.json` turns it on. The top-level
`redteam` key defaults to:

```json
{
  "redteam": {
    "enabled": false,
    "gateway_url": "http://127.0.0.1:3333",
    "bearer_token_file": "",
    "kill": false,
    "roe_required": true,
    "roe_file": "",
    "roe_key": "",
    "roe_key_file": ""
  }
}
```

Environment beats the file, as with the other Prime families:

```bash
OMEGA_PRIME_REDTEAM_ENABLED=1
OMEGA_PRIME_REDTEAM_GATEWAY_URL=http://127.0.0.1:3333
```

With the family off, none of the eleven tools register and the host
behaves as if the module did not exist. Restart the host after a change;
see the troubleshooting table in [Driving the bot](driving-the-bot.md).

## Signed rules of engagement

When enabled with `roe_required` (the default), the guard resolves a
signed RoE artifact before any tool registers: kill switch first, then
the file-key broker check, then `authorize()` from
`omega_prime/redteam/roe.py` — the canonical helper shipped in the
wheel and the image (`sidecar/roe.py` is only the harness-side wrapper
for checkout use, since installed installs never ship `sidecar/`).
Anything but a valid signature resolves `BLOCKED` with zero
registrations.

The verification key follows the credential-broker rule: it resolves
from `OMEGA_PRIME_REDTEAM_ROE_KEY` or from the file named by
`redteam.roe_key_file` (permission-gated like `bearer_token_file`). A
key value stored in `omega-prime.json` under `redteam.roe_key` is
rejected fail-closed (`roe-key-in-file`, warning plus `BLOCKED`) — move
it to the env var or a key file.

The parsed grant stays on the client: registration exposes only the
grant's allowed tools (a grant limited to `tempest_status` registers
only that tool), and every call re-checks the effective window plus the
per-tool allowlist after the kill switch. An expired grant answers
`BLOCKED` at call time; a tool outside the grant answers `BLOCKED`
without touching the transport.

### RoE lifecycle watcher

The RoE window (`effective_from..effective_until`) expires silently —
without a watcher the first signal is a `BLOCKED` transition
(`roe-expired`) at engagement time. A daily watcher warns before that
happens:

- **Cadence:** one check per day (`ROE_EXPIRY_KIND = "roe_expiry"` jobs in
  `omega_prime/cron/heartbeat.py`, default `interval_seconds=86400`).
- **Thresholds:** silent while healthy (`valid`); one warning per UTC day
  at T-7d (`expiring-7d`) and at T-1d (`expiring-1d`); an `expired` notice
  once `now` passes `effective_until`.
- **Scripting:** `python -m omega_prime.grokbot.roe_lifecycle check
  --artifact <path> [--now <ISO-8601 UTC>] [--json]` prints the lifecycle
  state (`valid`, `expiring-7d`, `expiring-1d`, `expired`, `missing`,
  `invalid`). Exit `0` for valid/expiring, `3` for
  expired/missing/invalid, `2` for usage errors.
- **Surfacing:** `durable_status` reports an additive `roe` block
  (`state`, `days_remaining`, `effective_until`).

Warnings never BLOCK — only actual expiry drives the pre-existing
`roe-expired` → BLOCKED transition, and the watcher never signs or
writes the artifact. To renew, re-sign with `sign_artifact` (refreshing
the adjacent `.sig`) and the watcher goes quiet.

Grant conflicts are an advisory second check over several grants (the
multi-grant `roe_files` case). `grant_conflicts(artifacts, *, now=None)` in
`omega_prime/grokbot/roe_lifecycle.py` returns rows of `kind`, `grants`
(the two grant ids, sorted) and `detail`, sorted by `(kind, grants,
detail)` with exact duplicates dropped:

- **`duplicate-id-divergent`:** two grants share one identity but carry
  different canonical content. The identity of a file input is its
  artifact path (two files that merely share an `artifact_id` are separate
  grants); the identity of an inline grant is its `artifact_id` or `id`.
  The `operator|until` fallback id is never used. No window overlap is
  needed.
- **`tool-scope-contradiction`:** on one host (case-insensitive) one grant
  allows every tool and the other restricts the tool list. Two different
  non-empty lists are not flagged.
- **`tracker-cover-contradiction`:** one grant covers a named tracker set
  while the other covers all trackers (missing or empty), or the two
  non-empty sets are disjoint.
- **`operator-mismatch`:** both grants name an operator and the names
  differ.
- **Overlap and `now`:** the window kinds fire only while the two windows
  overlap; the test is inclusive (grants that touch overlap, grants one
  second apart do not). Without `now` only overlap is checked and no clock
  is read. With `now` (datetime, epoch or ISO-8601) only grants active at
  `now` are compared; an unparseable `now` falls back to overlap-only. A
  grant without a usable window (including an inverted one) is compared
  for duplicate ids only.
- **Scripting:** `python -m omega_prime.grokbot.roe_lifecycle conflicts
  --artifact <path> --artifact <path> [--now <ISO-8601 UTC>] [--json]`
  (`--artifact` is repeatable and required). One line per conflict
  (`kind: id_a vs id_b - detail`) or `roe conflicts: none`; `--json`
  prints the list. Exit `0` none, `3` conflicts found, `2` usage error.
- **Advisory only:** the check never blocks `authorize`,
  `authorize_all` or `sign`, verifies no signature and writes nothing;
  fewer than two readable grants give an empty list and unreadable
  artifacts are skipped. `findings_sync.tick` attaches a non-empty result
  as `grant_conflicts` (see [Operator tools](#operator-tools)).
- **Scaling cap:** at most 100 rows are returned, in the same sort order,
  followed by one sentinel row `{"kind": "conflicts-truncated", "grants":
  [], "detail": "<N> more conflicts not listed"}` (`N` is the exact count
  of the remaining distinct rows); with 100 rows or fewer the output is
  unchanged. A grant file larger than 1 MiB (1,048,576 bytes) is ignored
  by the detector as unreadable; a file of exactly that size is read.
  `conflicts --json` prints the capped list with the sentinel, the text
  output prints it as `conflicts-truncated: <N> more conflicts not
  listed`, and the exit code is still `3`.
- **Not detectable:** rate limits, deny lists, per-class limits, targets
  beyond host and tools, and `case_classes` differences (treated as a
  union).

## Tools

| Tool | Effect | Gateway calls | Approval |
| --- | --- | --- | --- |
| `tempest_status` | Read mission status and gateway/LLM provider state. | `GET /api/mission/status`, `GET /api/llm/status` | no |
| `tempest_mission_start` | Start a mission. | `POST /api/mission/start` (`name?`, `targets?`, `operators?`) | **required** |
| `tempest_mission_stop` | Stop the running mission. | `POST /api/mission/stop` | **required** |
| `tempest_report` | Export the engagement report for a mission. | `GET /api/mission/report` or `GET /api/mission/{missionId}/report` | no |
| `tempest_operators_list` | List spawned operators. | `GET /api/operators/list` | no |
| `tempest_operator_task` | Task one operator. | `POST /api/operators/{operatorId}/task` (`taskName`, `taskDescription`) | **required** |
| `tempest_findings` | Read findings, optionally for one mission. | `GET /api/findings[?missionId=]` | no |
| `tempest_providers` | Read LLM provider and OAuth/subscription-session state. | `GET /api/llm/status`, `GET /api/oauth/status` | no |
| `tempest_purple_synthesize` | Merge findings with detections into a purple synthesis. | `POST /api/teams/purple/synthesize` (`findings?`, `detections?`) | no |
| `tempest_hats_list` | Read hat prompts and tool grants. | `GET /api/teams/tool-grants`, `GET /api/operators/prompts` | no |
| `tempest_hat_dispatch` | Spawn a hat operator and task it. | `POST /api/operators/spawn` (`archetype`), then `POST /api/operators/{id}/task` | **required** |

The four approval-required tools are the state-changing ones: mission
start and stop, direct operator tasking, and hat dispatch. They are
gated like any other tool in the host; see [Tool host](tool-host.md)
for the gate mechanics and the `--approve` flag.

## Slash commands

The sidecar harness is driven through fifteen curated commands (all routed
via `omega_command`, like every other `/omega-*` command):

| Command | Effect |
| --- | --- |
| `/omega-redteam-run --suite <name>` | Evaluate the fail-closed gate, then run one simulation suite. |
| `/omega-redteam-status` | Show harness state and recent transitions. Read-only. |
| `/omega-redteam-findings [--limit N] [--jsonl]` | Tail recent findings. Read-only. `--jsonl` adds `finding/1` text and writes no file. |
| `/omega-redteam-triage <dedupe-hash> --rationale <text>` | Append one triage proposal to the review queue. |
| `/omega-redteam-roe-create --operator <name> --days <N> --key-file <path>` | Write one unsigned Rules-of-Engagement artifact. |
| `/omega-redteam-roe-sign --artifact <path> --key-file <path>` | Sign one Rules-of-Engagement artifact with a key file and print the install command. |
| `/omega-redteam-roe-check` | Print the Rules-of-Engagement authorization state and days remaining. Read-only. |
| `/omega-redteam-triage-apply --hash <dedupe-hash> [--untriage] [--apply]` | Preview one triage proposal without `--apply`, apply it with `--apply`; refuses when the audit script is dirty. |
| `/omega-redteam-report [--summary] [--since <RFC3339>] [--threshold gate:<pct>\|gate-risk:<score>]` | Read-only engagement digest (3-4 plain-text lines: runs, pass, gaps and findings; coverage counts and risk score; worst classes; an optional gate line), or one summary line with `--summary`. A failing gate is reported in the result, not as an error. |
| `/omega-redteam-coverage [--jsonl] [--since <RFC3339>]` | Read-only per-class coverage rows (class, cases, runs, wins, pass_pct, gaps, verdict, tier, weight, earned, possible); `--jsonl` adds the JSONL text. |
| `/omega-redteam-sync-plan` | Plan the next findings sync with zero tracker calls and zero writes: what would sync, what quiet hours or the sync budget hold, and grant conflicts. Each list is capped at 20 entries with a `<name>_total` count. |
| `/omega-redteam-cycle [--run] [--steps <a,b>]` | Plan the purple cycle (default, pure) or run it with `--run`: refresh the release-watch state, audit a newly released upstream tag (audit-only), write a checkpoint and a ledger row. The result carries no file paths. |
| `/omega-redteam-conflicts` | List Rules-of-Engagement grant conflicts from the host env. Read-only. Basenames only, capped at 20. |
| `/omega-redteam-readiness` | Report `roe_state`, gateway reachability, and loaded suites. Read-only. Never starts an engagement. |
| `/omega-audit [--tail N]` | Verify the tamper-evident tool-call audit chain. Read-only. A missing log is empty, not success. |

BLOCKED is fail-closed: `/omega-redteam-run` checks the harness state
first and refuses with `harness_blocked` unless the state is `IDLE` —
a blocked run executes nothing. Triage is proposal-only: the command
validates the 64-hex dedupe hash and a rationale of 1–500 characters,
then appends one `PROPOSAL` row (`dedupe_hash`, `rationale`, timestamp,
proposer `operator`) to `review-queue.ndjson`. Applying the verdict is a
separate operator step; the command itself changes no finding.

`/omega-redteam-report`, `/omega-redteam-coverage`,
`/omega-redteam-sync-plan`, `/omega-redteam-conflicts`, `/omega-redteam-readiness` and
`/omega-audit` are read-only: they read the reports directory
and the configuration and write nothing. `/omega-redteam-cycle` plans by
default and plan mode is pure; with `--run` it only refreshes the
release-watch state, audits a newly released upstream tag without applying
it, and writes a checkpoint and a ledger row. Chat can never dispatch
findings or apply a pin bump: those stay with the CLI in
[Operator tools](#operator-tools).

These commands need a source checkout. Like every `/omega-redteam-*`
command they import the `sidecar/` package, and the reporting commands and
the cycle also load `scripts/engagement-report.py`; the container image
ships neither (`.dockerignore` excludes `/sidecar` and the image copies only
`omega_prime/`), so there they answer `sidecar_unavailable` or
`report_unavailable` instead of raising.

Chat output is bounded and path-free:

- `/omega-redteam-sync-plan` returns at most 20 entries per list
  (`would_sync`, `quiet_held`, `budget_held`, `grant_conflicts`) plus
  `would_sync_total`, `quiet_held_total`, `budget_held_total` and
  `grant_conflicts_total`. Hashes and row values are ASCII-escaped
  (non-printable and non-ASCII characters become `\xNN`/`\uNNNN`) and
  clipped to 64 characters; `detail` and tick-error text to 200. A grant
  label that is a file path is shown as its basename, or as `grant-N`
  when basenames collide; the same alias replaces the path inside
  `detail`. A `grant-N` alias never equals a real grant id (it takes the
  first free name). The `conflicts-truncated` sentinel row passes through
  after the capped rows; `grant_conflicts_total` counts every conflict,
  including those beyond the 20 listed rows and beyond the detector's
  100-row cap (the count comes from the sentinel text). `tracker` is
  clipped to 64 characters and escaped.
- `/omega-redteam-cycle` returns no `checkpoint` or `ledger` path, in
  results or error envelopes.
- A command failure that is not a structured error returns only the
  exception type name (`{"error": "<TypeName>"}`).
- `--since` and `--threshold` accept ASCII digits only.

## Operator tools

Four operator-run tools extend the purple-team loop. Each flag is opt-in:
with it unset, output and result shape are unchanged. The one advisory
exception is the tick key `grant_conflicts`, present only when two or more
configured grants conflict.

### Findings sync budget

`redteam.findings_sync.sync_budget` caps how many non-urgent findings one
`findings_sync.tick` dispatches per tracker. Default: unset (off).

```json
{"redteam": {"findings_sync": {"sync_budget": {"default": 5, "slack": 2}}}}
```

| Value | Meaning |
| --- | --- |
| integer `N > 0` | At most `N` non-urgent unsynced rows per tick. |
| mapping | `{"default": N, "<tracker>": M}`: the tracker's entry (case-insensitive) wins, then `default`. |
| anything else | Off: missing, `None`, bool, `0`, negative, float, string, list, `{}`, a mapping with no entry for the tracker and no `default`. |

- `critical` and `high` rows (case-insensitive, whitespace-trimmed) always
  dispatch and never consume budget. They are deliberately uncapped: a
  source that stamps every row `critical` is not limited by this budget.
- Quiet hours are decided first; quiet-held rows never consume budget.
- The budget is spent by severity: `medium` rows first, then `low`, then
  every other severity (unknown or missing), and oldest first within a
  severity. The chosen rows are still pushed in store order. The rest
  stay unsynced and retry on the next tick, so a `medium` row is not
  starved behind a backlog of `low` rows.
- The mapping is looked up by the EFFECTIVE tracker: when the graph
  tracker's credentials are complete, graph wins and a `slack` entry is
  not used (use `graph`, or `default`). Mappings resolve per entry: a
  valid entry for the tracker, or a valid `default`, still applies when a
  sibling entry is invalid.
- A valid budget adds `sync_budget: N` and `budget_held:
  [{"dedupe_hash", "reason": "sync-budget"}]` (possibly empty, store
  order) to the tick result; dry-run and plan modes choose the same rows.
- An unusable value adds `sync_budget_invalid` to the tick result (also on
  error, dry-run and plan results, not on the disabled or grant-skip
  result) and the cap is off for that tracker. Reasons: `type` (bool,
  float, string, list, ...), `value` (a non-positive integer), `entry` (a
  mapping with a non-string key or an invalid entry). An absent or `None`
  key, `{}` and a mapping with no entry for the tracker are not flagged.
- `grant_conflicts` (advisory, only when non-empty and at least two grants
  are configured) is also added to tick results, including the grant-skip
  result. With the budget unset, results are identical unless two or more
  configured grants conflict.
- The `_main_<tracker>` CLI entry points (a real run without
  `--dispatch-dry-run`) call the sync functions directly and apply neither
  quiet hours nor the budget; only `tick` and `--dispatch-dry-run` do.

```bash
python -m omega_prime.grokbot.findings_sync --tracker slack --dispatch-dry-run
```

The dry run prints `skip (sync-budget) <hash>` for each held row;
non-printable characters in printed hashes are escaped.

### Pin bump report path

`--report-to PATH` writes the delta report to `PATH` instead of
`<root>/report.md` (exclusive: `report.md` is not written).

```bash
python -m omega_prime.grokbot.pin_bump --to v1.4 --report-to /tmp/delta.md
```

- A relative `PATH` resolves against the working directory; the resolved
  absolute path is printed in the `pin-bump audit: <verdict> (report:
  <path>)` line. Printed paths have control characters escaped; the
  `--json` `report` key is the raw resolved path.
- The file is written atomically (temp file `.pin-bump-report.<hex>.tmp`,
  then rename) with mode `0644`; missing parent directories are created.
  The write goes through a directory descriptor pinned at validation, so
  swapping the parent or an ancestor afterwards cannot redirect it. The
  walk pins each directory with `O_PATH` on Linux, so ancestors need only
  search (`x`) permission, not read. If the destination is tampered with
  after validation, the run exits `1` and leaves the file unchanged.
- Usage errors exit `2` before any subprocess: empty, NUL or non-string
  path, `.` or `..` (also as the last component: a path ending in `/.` or
  `/..`), a trailing path separator, a file name over 200
  bytes (UTF-8), an existing directory, any symlink (including a dangling
  one) or other non-regular file, a parent that is not a directory, an
  existing file that is neither empty nor a previous pin-bump report
  (so `VENDOR.md` or any other file is never overwritten), and
  combination with `--audit-only`, `--rollback` or `--skip-audit`.
- `--dry-run` validates only and prints `[dry-run] report would be written
  to <path>`.
- `--json` gains a `report` key only when the flag is set.
- `--schedule` stores the absolute path; every replay re-validates it
  (including the overwrite refusal) and an invalid stored path exits `2`
  without writing.
- `--verify` failure deletes only a report this run created.
- `--rollback` still reads `<root>/report.md`, then falls back to the
  `VENDOR.md` git history, so a custom path loses the report-based
  rollback record.

### Engagement report digest and JSONL export

`scripts/engagement-report.py --digest` prints at most five stdout-only
lines and writes no report:

```text
runs=X pass=Y% gaps=Z findings=+N/-M
coverage covered=C/T gappy=G unexercised=U risk=S.S
worst=a,b,c
Gate: ...
```

The `worst=` line lists up to three classes (`worst=none` when there are
none); the `Gate:` line appears only with `--threshold` or `--baseline`.
Empty or missing history prints zeros, not an error. The `Gate:` line and
all digest stderr (exit `1`, `2` and `3`) name only schema classes or
`other` (a class that is not in the schema is shown as `other`), and
control characters appear as backslash escapes. `other` is only the display
label for on-disk classes in gate-failure text: `--digest --baseline
other=N` is rejected and `other` is not listed among the valid classes.

| Exit | Meaning |
| --- | --- |
| `0` | OK |
| `1` | Report or I/O error, `--digest` combined with `--json` or `--summary`, or a failed `--export-jsonl` write |
| `2` | Unknown `--baseline` class (with `--digest`, `other` is not selectable) |
| `3` | Gate FAIL |

`--digest` composes with `--since`, `--reports-dir`, `--threshold`,
`--baseline` and `--export-jsonl`, and ignores the other file-writing
flags like `--summary`.

`--export-jsonl OUTPUT_JSONL` also writes the coverage matrix as JSON
Lines: one compact object per class, in coverage order, with keys
`schema` (`"coverage/1"`), `class`, `cases`, `runs`, `wins`, `pass_pct`,
`gaps`, `verdict`, `tier`, `weight`, `earned`, `possible`. Stdout is
unchanged. Class names are JSON-escaped data (the fixed vocabulary above
applies to the digest only). The target is handled by type:

- Absent or regular file: written atomically (a `.engrep-*.tmp` file in
  the target directory, fsync, rename) with mode `0666 & ~umask`.
- Existing FIFO, device or socket: written in place, never replaced
  (`/dev/null` stays a device).
- Symlink to a regular file, a directory or nothing (dangling): refused,
  exit `1`.
- Symlink to a stream such as `/dev/stdout`: written through.

In `--digest` mode the export error line is printable-escaped.

```bash
python3 scripts/engagement-report.py --digest --threshold gate:80 \
  --export-jsonl coverage.jsonl
```

### Purple cycle

`python -m omega_prime.grokbot.purple_cycle [--mode plan|run|apply]
[--apply] [--steps a,b] [--reports-dir DIR] [--root DIR] [--now ISO-8601]
[--force-new] [--json]` chains the purple-team operations in order. It
never runs a simulation, signs or creates RoE, edits repo files or applies
a pin bump. `--root` is the pin-bump repository root. `--now` is rejected
(exit `2`) together with apply mode, and option abbreviations are rejected
(exit `2`).

```bash
python -m omega_prime.grokbot.purple_cycle --json
python -m omega_prime.grokbot.purple_cycle --mode run
python -m omega_prime.grokbot.purple_cycle --apply
```

| Mode | Adds |
| --- | --- |
| `plan` (default) | Pure: no checkpoint, ledger, state file, network or subprocess. |
| `run` | `release-watch` and `pin-bump-audit`. |
| `apply` | `findings-dispatch` (the real findings sync). |

The six steps, in order:

1. `preflight` - harness verdict and RoE lifecycle state (read-only).
2. `release-watch` - refresh `<reports>/release-watch.json` from the
   upstream tags (writes, uses the network).
3. `pin-bump-audit` - audit-only check of the newest new tag, 300 s per
   command; skipped when nothing is new. Only `v<digits>.<digits>...`
   tags that pin_bump accepts are audited; any other tag gives
   `unsafe-tag` and no audit. `release-watch` records the new tag in
   `<reports>/purple-cycle-pending.json` (`{"attempts": n, "tag": T}`)
   before its checkpoint, so a crash cannot lose it. A failed, timed-out or
   raising audit is a `warn` with reason `audit-failed`: it never halts the
   cycle or changes the exit code, and a later cycle retries it. After 3
   failed attempts the tag is dropped with `warn`/`audit-abandoned`. A
   successful audit clears the file.
4. `engagement` - summary and digest of the reports directory.
5. `findings-plan` - a dry findings sync: would-sync, quiet-held and
   budget-held counts and whether grants conflict.
6. `findings-dispatch` - apply only. Refused (`blocked`, `apply-refused`,
   exit `3`, cause in `detail.cause`) when the harness state is not `IDLE`
   (its cause, e.g. `kill-switch-set`), when no RoE is configured
   (`roe-missing`), when config sets `redteam.kill` (`kill-switch-set`) or
   an explicit `redteam.enabled: false` (`disabled`), or when any RoE in
   the gate is `expired`, `missing`, `invalid` or revoked (`revoked`: a
   `grant-revoked` row in an audit log, read only). The gate covers the
   union of `redteam.roe_file(s)` and the findings-sync grant keys.
   Revocations are matched by realpath and file identity, so path aliases
   do not escape them, in the audit logs named by `roe_audit_log`,
   `audit_log`, `roe_audit_log_path` or `audit_log_path` under `redteam`,
   `redteam.findings_sync` or the top level. The gate is re-checked fresh
   on every pass and every resume (with a fresh clock unless `now` was
   injected); checkpointed preflight facts are reporting only.

- **Cycle id:** `purple-cycle-YYYYMMDD-<mode>[-s<8hex>][-HHMMSS]`. A
  `--steps` subset adds `-s<8hex>` (a hash of the selected steps in
  canonical order), so each selection has its own id; `--force-new` adds
  `-HHMMSS`.
- **Checkpoint:** `run` and `apply` write
  `<reports>/purple-cycle/workflows/<cycle id>.json`, not the repository
  or the durable state root, so `durable_status.workflow_checkpoints` is
  unaffected. A checkpoint whose mode, cycle id, selection or step records
  do not match the current run, or whose stored step records fail strict
  validation (exact keys, types, bounded sizes), is `checkpoint-unreadable`
  (exit `1`).
- **Ledger:** `<reports>/purple-cycle.ndjson`; a ledger write failure never
  raises. A blocked step is recorded as `apply-refused:<cause>`, once per
  identical row.
- **Resume:** a step that errors or is blocked is never checkpointed, so a
  rerun (the same day too) re-evaluates it and reuses the completed steps;
  later steps report `halted`. A fully completed cycle returns its stored
  result without executing anything (`resumed` true); a release published
  later the same day is picked up the next UTC day or with `--force-new`.
- **Release watch:** the `gh api` call times out after 60 s; a timeout is a
  fetch failure (exit `3`).
- **Exit codes:** `0` completed, `1` a step errored, `2` usage error, `3`
  apply refused.

## Gateway contract

The tools speak the upstream loopback HTTP API, not MCP:

- Default `gateway_url` is `http://127.0.0.1:3333`. Loopback means the
  host `127.0.0.1` or `localhost` — nothing else counts.
- A non-loopback gateway URL is refused unless a bearer token is
  configured. The token comes from `T3MP3ST_BEARER_TOKEN`, from the
  file named by `T3MP3ST_BEARER_TOKEN_FILE` (the upstream environment
  contract), or from the file named by `redteam.bearer_token_file` in
  `omega-prime.json`.
- The sidecar is T3MP3ST itself, checked out as the tracked root
  `redteam/` submodule (remote `https://github.com/swcstudiospace/purple-team.git`,
  commit `d1a22097a1402976621ed6affa632981fb105ca7` per `VENDOR.md`) and
  started separately — Omega Prime does not start, supervise, install, or
  auto-launch it. See [Backend checkout and start](#backend-checkout-and-start).
## Backend checkout and start

The TypeScript backend is separate upstream source reached through the
`redteam/` gitlink. The Python package, the registry adapter
(`omega_prime/tools/redteam.py`), and the vendored 47-skill / five-seat
pack (`omega_prime/redteam/pack/`) are ordinary parent content; the
backend is not ported and never starts on its own.

An ordinary `git clone` of Omega Prime leaves the submodule path
empty. Either clone recursively or init afterwards (targeted init
suffices when only the backend is wanted):

```bash
git clone --recurse-submodules https://github.com/swcstudiospace/Omega-Prime.git omega-prime
# or, inside an existing checkout:
git submodule update --init redteam
```

The optional upstream setup follows its pinned package contract
(Node `>= 22.19.0`), authorized-use only:

```bash
npm --prefix redteam ci
npm --prefix redteam run server
```

No secrets are read from ambient examples: the gateway bearer token
reaches the tools only through the token sources named above, never
through checked-in values. Point `redteam.gateway_url` at the sidecar
you started and keep the family default-off until an authorized
engagement needs it.

## Vendored skill pack

`omega_prime/redteam/pack/` holds T3MP3ST's declarative Grok Bot pack
copied verbatim: the seat templates (COMMANDER, RED, BLACK, BLUE,
WHITE), the red-team roster, and 47 saved skills. The skills are the
upstream descriptions, one line each:

| Skill | Use |
| --- | --- |
| `tempest-bootstrap` | Use when the operator types /tempest-help or /tempest-config, or when a seat runs its first session: registers the seat id and checks gateway reachability. |
| `tempest-memory` | Use when the operator recalls past work or reviews learned entries: reads the memory capsule and accepts or rejects proposals. |
| `tempest-mission` | Use when the operator starts, parks, resumes, pauses, or stops a mission: owns mission ids and the parked/resumable lifecycle. |
| `tempest-operators` | Use when the operator types /tempest-agents or tasks a seat: lists operators and dispatches work to one operator id. |
| `tempest-report` | Use when the operator types /tempest-report: exports the engagement report for a mission id. |
| `tempest-run` | Use when the operator types /tempest-run with an objective: acknowledges with a run id, streams progress, and closes with a completion summary. |
| `tempest-status` | Use when the operator types /tempest-status or /tempest-providers: reads mission status plus provider and subscription-session state. |
| `black-anti-forensics-refusal` | Use when the operator types /black-anti-forensics-refusal with a request to wipe, clean, tamper with, or conceal logs or artifacts: refuses and reports the residue to… |
| `black-attack-mapper` | Use when the operator types /black-attack-mapper with planned actions: maps each action to vendored ATT&CK technique ids, tactic phases, and detection strategies. |
| `black-beacon-cadence` | Use when the operator types /black-beacon-cadence with callback or scheduled-transfer plans: reviews beacon timing, jitter, and fallback channels for correlation risk. |
| `black-channel-costing` | Use when the operator types /black-channel-costing with exfil paths: prices each channel with the disclosed exfil cost table and states feasibility, without moving data. |
| `black-cover-audit` | Use when the operator types /black-cover-audit with a command or artifact: audits per-action cover residue (user agent, timestamps, DNS, temp files, credentials in arg… |
| `black-evasion-review` | Use when the operator types /black-evasion-review with concealment steps: checks obfuscation, masquerading, and sandbox-awareness against the stealth checklist. |
| `black-low-noise-recon` | Use when the operator types /black-low-noise-recon with a recon scope: plans passive-first surface mapping and bounds every active probe. |
| `black-noise-ledger` | Use when the operator types /black-noise-ledger with a plan: prices each action in budget points and reports the total, tier, and doctrine violations. |
| `black-phase-gate` | Use when the operator types /black-phase-gate with a plan: issues a per-phase go or no-go from the opsec checklist before each phase starts. |
| `black-probe-cadence` | Use when the operator types /black-probe-cadence with a plan: flags repeated identical probes and burst timing that burn budget and correlate across runs. |
| `black-specter` | Use when the operator types /black-specter with a red plan: critiques the plan's covert tradecraft, cover, and noise budget from the adversary's eyes. |
| `black-stealth-router` | Use when the operator types /black-stealth-router with a plan or route: reviews declared egress, proxies, and resolvers against the command-and-control checklist. |
| `black-third-party-surface` | Use when the operator types /black-third-party-surface with a plan that touches cloud storage, web services, code repos, or cloud accounts: reviews the external logs e… |
| `black-wraith` | Use when the operator types /black-wraith with exfil paths: simulates the paths as analysis and checks them against the noise budget. |
| `blue-coverage-matrix` | Use when the operator types /blue-coverage-matrix with findings: builds a finding-by-countermeasure coverage matrix from the backend gap report. |
| `blue-deception-plan` | Use when the operator types /blue-deception-plan with findings: designs inert decoy placements for uncovered Deceive countermeasures, each with a test-touch proof. |
| `blue-evict-plan` | Use when the operator types /blue-evict-plan with findings: writes incident-response eviction recommendations for findings that show an active foothold. |
| `blue-harden-map` | Use when the operator types /blue-harden-map with findings: maps uncovered preventive countermeasures (Harden and Isolate) to each finding's controls. |
| `blue-hunt-plan` | Use when the operator types /blue-hunt-plan with findings: writes retrospective hunt cards over partially covered telemetry, labeling each outcome honestly. |
| `blue-keyless-triage` | Use when the operator types /blue-keyless-triage with findings: lists findings that carry no D3FEND countermeasure keys and names the keys each one needs. |
| `blue-logsource` | Use when the operator types /blue-logsource with findings: flags Sigma rules still on the placeholder logsource and states that the rule endpoint cannot retarget them. |
| `blue-ontology-drift` | Use when the operator types /blue-ontology-drift with findings: reports countermeasure ids the vendored D3FEND ontology does not recognize, per finding. |
| `blue-sentinel` | Use when the operator types /blue-sentinel with findings: maps each finding to telemetry and writes one detector recommendation per gap. |
| `blue-severity-check` | Use when the operator types /blue-severity-check with findings: flags findings whose severity label is unrecognized and so folds silently into the medium tier. |
| `blue-sigma-pack` | Use when the operator types /blue-sigma-pack with findings: reviews the experimental Sigma rule pack derived from uncovered gaps and checks each rule's fields. |
| `blue-telemetry-deploy` | Use when the operator types /blue-telemetry-deploy with findings: names the telemetry source that would close each uncovered countermeasure gap. |
| `blue-warden` | Use when the operator types /blue-warden with findings: audits hardening and returns a verification checklist plus misconfiguration call-outs. |
| `white-active-classifier` | Use when the operator types /white-active-classifier with a command or tool id: shows whether the keyword rule classifies it as active, and flags active-capable tools… |
| `white-approval-receipt` | Use when the operator types /white-approval-receipt with an action: checks that an approved flag is backed by a ledger receipt that names the same command and target… |
| `white-arbiter` | Use when the operator types /white-arbiter with an action: returns the ROE verdict on that action and resolves any red versus blue dispute about it. |
| `white-auditor` | Use when the operator types /white-auditor with evidence: scores each entry by provenance and aggregates the evidence-strength verdict. |
| `white-custody-trail` | Use when the operator types /white-custody-trail with an evidence or finding id: traces each entry's origin, source, observation tool, stage, input hash, run, and miss… |
| `white-orphan-evidence` | Use when the operator types /white-orphan-evidence: lists ledger entries that no finding claims, with the reason each is unclaimed, and counts them by origin and platf… |
| `white-report-coverage` | Use when the operator types /white-report-coverage with finding ids or scope: states, per finding, whether it is report-ready, names the exact gap that blocks it, and… |
| `white-retest-criteria` | Use when the operator types /white-retest-criteria with a finding id: lists the finding's recommended fix and retest acceptance criteria, matches ledger retest entries… |
| `white-roe-parse` | Use when the operator types /white-roe-parse with ROE text or a scope question: accounts for every line of the on-record ROE text and reports whether the spec is safe… |
| `white-scope-match` | Use when the operator types /white-scope-match with a target: shows which allowed ROE entry matches it, by which rule, and flags lookalike hosts that the substring rul… |
| `white-severity-replay` | Use when the operator types /white-severity-replay with finding ids: checks that each high or critical finding rests on a replayable receipt a third party can re-run… |
| `white-verdict-stamp` | Use when the operator types /white-verdict-stamp with a verdict set or action index: checks the revision, text-hash, and source-line stamps on ROE verdicts against the… |
| `dark-purple` | Use when the operator types /dark-purple with a mission scope: runs the full loop of red findings to black pressure to blue detection to white verdict to report. |


The upstream skills drive Grok Bot seats against the gateway; in Omega
Prime they are reference material for what the mirrored tools and hats
mean. The tool family itself is registered through the standard
registry — the same surface as every other rostered tool, cataloged in
[Tool catalog](tool-catalog.md).

## Status

No live T3MP3ST gateway has been exercised from this repo. The tool
family is implemented against the upstream loopback HTTP contract and
the vendored pack is a verbatim copy; actual behavior — missions,
operators, findings, reports — depends entirely on a T3MP3ST sidecar
that this repo does not ship or start. If the sidecar is down, the
tools fail with connection errors; nothing else in Omega Prime changes.

Grok's own recursive-clone behavior is not attested by these local
operator instructions: a Grok Bot seat cannot be assumed to populate
the `redteam/` (or `prime-agent/`) submodule on its own. No
deployed or live model / mission proof is established here — no
claim is made about a running sidecar, a live provider, or a real
mission outcome.

The pack and the `tempest_*` tools exist for red-team and purple-team
work against systems you own or have explicit written permission to
test. Nothing in this module authorizes testing anyone else's systems,
and the approval gates on the state-changing tools are part of that
contract, not an obstacle to it.

## Bumping the redteam submodule

The `redteam/` gitlink is the only moving part in this module. Dependabot
watches the `gitsubmodule` ecosystem (`.github/dependabot.yml`) and opens
a PR when upstream
(`https://github.com/swcstudiospace/purple-team.git`) moves; the
`redteam-provenance` CI job (`.github/workflows/ci.yml`) proves the bump
before merge. A hand bump follows the same mechanical sequence — this
section alone is the procedure, in order, with no undocumented steps:

1. **Move the pin.** Fetch upstream, check out the new commit inside the
   submodule, and stage the gitlink:

   ```bash
   git submodule update --init redteam
   cd redteam && git fetch origin && git checkout <new-sha> && cd ..
   git add redteam
   ```

2. **Run the provenance checker.** The checker was relocated from
   plan-01's `omega_prime/redteam/provenance.py` to
   `sidecar/provenance.py` (checker advisory 4) so it lives with the
   sidecar it gates instead of inside the vendored package it verifies.
   All three subcommands run offline against the local checkout:

   ```bash
   python3 sidecar/provenance.py check
   python3 sidecar/provenance.py pack-diff
   python3 sidecar/provenance.py surface
   ```

   `check` asserts the submodule gitlink, the `Commit:` line in
   `VENDOR.md`, and the `Upstream commit:` provenance docstring in
   `omega_prime/redteam/__init__.py` all agree; any disagreeing pair
   fails naming the pair. Fix by updating the lagging side, never by
   weakening the check.

3. **Update the pin records.** Set the `Commit:` line under
   `VENDOR.md` ("T3MP3ST red team pack") to the new SHA and update the
   `Upstream commit:` provenance docstring in
   `omega_prime/redteam/__init__.py` to the same SHA. The two must match
   the staged gitlink exactly — `check` re-proves this.

4. **Re-vendor the pack on divergence.** If `pack-diff` lists `DIVERGED`,
   `MISSING-UPSTREAM`, or `EXTRA-UPSTREAM` files, copy the upstream files
   from `redteam/grokbot/` over `omega_prime/redteam/pack/` byte-verbatim
   (same relative paths; the vendored `pack/LICENSE` MIT notice stays —
   it has no upstream counterpart and `pack-diff` skips it). Binary
   assets such as `pack/avatars/*.png` are outside the markdown/JSON
   comparison; copy those too when upstream changes them. Re-run
   `pack-diff` until it reports byte-identical.

5. **Refresh the tool surface on legitimate drift.** If `surface` reports
   `DRIFT`, upstream renamed or added a `tempest_*` tool in
   `redteam/src/mcp-server.ts`. When the drift is legitimate (upstream
   changelog or release notes confirm the tool), update
   `REDTEAM_TOOL_NAMES` in `omega_prime/tools/redteam.py` and
   `tool_names` in `sidecar/contract/bot-interface.json` together — the
   three sides must agree exactly, and the adapter change follows the
   consistency-triangle rule (setup check, catalog, tool-catalog docs,
   and roster move together; no new rostered names beyond what upstream
   shipped). When the drift is not legitimate, do not touch the adapter:
   leave the pin where `surface` is green.

6. **Open a PR with CI proof.** Push the bump branch and open a PR. The
   PR must show `redteam-provenance` green (`check`, `pack-diff`,
   `surface`) plus the weekly `redteam-node-audit` supply-chain result
   for the new pin; triage any untriaged high-or-above advisory into the
   dated `TRIAGED` block in `scripts/redteam-audit-triage.py` with a
   rationale, mirroring the `PYSEC-2026-4114` precedent. No bump merges
   on a red provenance job.

   6b. **Post the delta verdict.** Run the delta scanner on the old-pin
   and new-pin audits and paste the verdict plus the report link into
   the bump PR body:

   ```bash
   python3 scripts/redteam-audit-delta.py \
     --old <audit-json|lock> --new <audit-json|lock> --out report.md
   ```

   Use `--old` = old-pin audit artifact and `--new` = new-pin audit
   artifact. The script prints exactly one of `improved`, `regressed`,
   or `unchanged` — quote that line verbatim in the PR body with a link
   to `report.md`. It informs only; it never gates the bump.

Dry-run note: rehearse steps 1–3 against upstream v1.3 in a scratch
worktree before touching the working tree — fetch, checkout, `git add
redteam`, then the three checker subcommands. The rehearsal needs no
steps beyond the ones above; if it does, the missing step is a doc bug —
fix this section first.

### Moderate-advisory sweep (2026-10-14)

Post-dep-bump (pin `f6d2fc2`) the prod audit (`npm audit --omit=dev
--audit-level=high`, the `redteam-node-audit` chain) is green with 0
findings. The full audit (dev included) carries 3 moderate entries
under one advisory, GHSA-82fw-gwwq-j7x9 (vitest `@vitest/mocker` path
traversal, CVSS 5.9), triaged 2026-10-14 into the dated `TRIAGED` block
in `scripts/redteam-audit-triage.py`: the vitest chain is dev-only test
tooling the loopback sidecar never loads, the family is default-off,
and nothing Node-related enters the runtime image. Upstream ships a
semver-compatible fix (`@vitest/coverage-v8` 4.1.11) for the
remediation loop to pick up.

### Upstream remediation loop

The weekly `redteam-remediation` workflow
(`.github/workflows/redteam-remediation.yml`) keeps the supply-chain
`redteam-node-audit` RED visible AND actionable until upstream ships
fixes. It runs every Monday at 04:30 UTC (after the `supply-chain.yml`
04:17 run) and on `workflow_dispatch` (with a `dry-run` input that
builds the report but skips all `gh` writes).

1. **Audit.** The `audit` job reuses the `supply-chain.yml` audit chain
   verbatim (`git submodule update --init redteam`, Node 22,
   `npm ci --ignore-scripts`, `npm audit --omit=dev
   --audit-level=high --json` triaged by
   `scripts/redteam-audit-triage.py`). While untriaged high-or-above
   advisories remain, the job fails and the `remediate` job runs.
2. **Upstream bump PR.** With the optional `REMEDIATION_TOKEN` secret
   (a PAT with `repo` scope on `swcstudiospace/purple-team`) set, the
   workflow pushes a `redteam-remediation/npm-audit-<run-id>` fix
   branch (lockfile only, from `npm audit fix --package-lock-only`)
   and opens one bump PR per run via `gh api
   repos/swcstudiospace/purple-team/pulls`, with the advisory table in
   the body. It never pushes to upstream `main` and never
   approves or merges — an operator reviews upstream.
3. **Green-upstream comment.** The workflow also audits upstream
   `main`; when it exits 0, it comments the green commit SHA plus the
   `npm audit` exit-0 proof line on the `redteam-remediation`-labeled
   tracking issue in `swcstudiospace/Omega-Prime`, so the operator can run
   the bump runbook above. When the pin itself audits green, the
   `notify-green` job posts the pin-green proof the same way.
4. **Degrade path (first-class).** Without `REMEDIATION_TOKEN`, or when
   any cross-repo write is denied (403), the workflow degrades instead
   of failing: the lockfile delta, `redteam-audit.json`, and advisory
   table upload as the `redteam-remediation-report-<run-id>` artifact,
   and an omes-bot issue opens with the same content pasted in. The
   workflow exits 0 in degrade — it is a signal, not a red build.

Top-level permissions are `contents: read`; only `remediate` adds
`pull-requests: write` + `issues: write`, and `notify-green` adds
`issues: write`. No `contents: write`, no `actions: write`.
