# PLAN-G4: Triage approvals from chat

## Problem

`review-queue.ndjson` PROPOSAL rows are write-only. `/omega-redteam-triage`
(`omega_prime/commands/redteam_triage.py:54-85`) validates the 64-hex
`dedupe_hash` plus a 1–500 char rationale and appends one row
`{dedupe_hash, rationale, ts, proposer}` to
`sidecar/reports/review-queue.ndjson` (dir follows
`OMEGA_PRIME_SIDECAR_REPORTS_DIR`, see `_queue_path` at
`redteam_triage.py:37-51`), but applying the verdict is still a manual
operator step: hand-edit `scripts/redteam-audit-triage.py:27-30` to add a
dated `TRIAGED` entry `"<key>": ("<date>", "<rationale>")`, mirroring the
`PYSEC-2026-4114` precedent. There is no second confirmation, no
preview-before-mutate, and no guard against editing a dirty script file —
so a typo or a concurrent edit can silently corrupt the fail-closed audit
gate (`triage()` at `redteam-audit-triage.py:33-84`, `main()` untriaged
check at `:106-111`).

## User-facing behavior

A new command `/omega-redteam-triage-apply` promotes one PROPOSAL row into
the `TRIAGED` block (or removes it again):

1. `/omega-redteam-triage-apply --hash <64-hex> --apply` finds the newest
   PROPOSAL row for the hash in the review queue, inserts
   `"<key>": ("<today-UTC-date>", "<proposal rationale>")` into the
   `TRIAGED` dict in `scripts/redteam-audit-triage.py` (alphabetical key
   order, existing entries untouched), then runs the script's hermetic
   self-check (import the module + parse `TRIAGED`) and reports
   `{applied, key, date, self_check}`.
2. Without `--apply` the command mutates nothing: it renders a preview of
   the exact patch (unified diff of the `TRIAGED` block, plus the resolved
   queue path and the matched row's `ts`/`proposer`) and returns
   `{preview_only: true, diff}`.
3. `--untriage` reverses an approval: with `--apply` it removes the
   `TRIAGED` entry for the hash (preview without `--apply` shows the
   removal diff); refuses with `not_triaged` when no entry exists.
4. Refusals are structured errors, never partial edits: `no_proposal`
   when the hash has no PROPOSAL row, `dirty_script` when
   `git status --porcelain -- scripts/redteam-audit-triage.py` is non-empty,
   `already_triaged` on double-apply (without `--untriage`).

## Interface

- New module `omega_prime/commands/redteam_triage_apply.py`:
  `NAME = "omega-redteam-triage-apply"`,
  `USAGE = "/omega-redteam-triage-apply --hash <dedupe-hash> [--untriage] [--apply]"`,
  `SUMMARY = "Apply (or preview) one triage PROPOSAL into the TRIAGED block."`,
  `WHEN = "The user asks to approve or apply a red-team triage proposal."`
  `run(args, env)` parses `--hash` (64-hex, same `_HASH_RE` as
  `redteam_triage.py:25`), `--untriage` and `--apply` flags in any order;
  bad flags / bad hash raise `Error("bad_args", {"usage": USAGE})`.
- Queue lookup reuses the reports-dir resolution
  (`OMEGA_PRIME_SIDECAR_REPORTS_DIR` override, else
  `sidecar.state.reports_dir()`, same as `redteam_triage.py:37-51`);
  reads `review-queue.ndjson` newest-last, takes the newest row whose
  `dedupe_hash` equals `--hash`. No row ⇒ `Error("no_proposal", ...)`.
- Mutation is a line-scoped edit of the `TRIAGED` dict only
  (`scripts/redteam-audit-triage.py:27-30`): insert/remove exactly one
  `"<key>": ("YYYY-MM-DD", "<rationale>")` line, date = today UTC
  (`sidecar.state.utc_now_rfc3339()[:10]`), rationale = the proposal's
  verbatim text (no regeneration). Dirty check first via
  `git status --porcelain -- <script>`; non-empty ⇒
  `Error("dirty_script", ...)` before touching the file.
- Self-check after every applied mutation: `importlib` import of the
  edited script (compile check) + `isinstance(TRIAGED, dict)` and every
  value is a `(date, rationale)` 2-tuple with non-empty strings; report
  `self_check: "pass"` or roll the edit back and report
  `self_check: "fail"` with the error. Result dicts:
  `{preview_only, diff}` vs `{applied: bool, key, date, self_check}`.
- Registration: one `CommandSpec("omega-redteam-triage-apply", USAGE, ...,
  needs_args=True)` in `omega_prime/commands/__init__.py:49-150` plus one
  `_redteam(name, args, env, "triage_apply")` branch at `:323-330`;
  one plugin doc `omega_prime/grokbot/plugin/commands/omega-redteam-triage-apply.md`
  following `omega-redteam-findings.md` (call the `omega_command` tool,
  never reimplement).
- Config: no new `omega_prime/config.py` node and no new
  `OMEGA_PRIME_*` key (chat-time operator action, not runtime policy);
  no new `omega_prime/cron/heartbeat.py` kind (`HEARTBEAT_KIND` stays
  `"session_heartbeat"` — nothing scheduled).

## Non-goals

- Bypassing or weakening the `redteam-node-audit` gate
  (`.github/workflows/supply-chain.yml:42-72`): the command only edits
  the dated `TRIAGED` block; pass/fail threshold and `--audit-level=high`
  are untouched.
- Auto-applying proposals: without the explicit `--apply` flag the
  command is strictly read-only (preview only). No batch-apply-all, no
  auto-merge on schedule, no rationale generation.
- Changing the PROPOSAL write path (`redteam_triage.py`), the
  `triage()` fail-closed semantics, or the `PYSEC-2026-4114` precedent.
- Network access of any kind; the command and its self-check are
  hermetic (stdlib, `tmp_path` queue fixtures, `git status` stubbed).

## Done-criteria

1. Preview: `/omega-redteam-triage-apply --hash <known>` with a seeded
   PROPOSAL row returns `preview_only: true` with a unified diff that
   applies cleanly, and the script file is byte-identical afterwards.
2. Apply: same call + `--apply` inserts exactly one dated entry with the
   proposal's verbatim rationale, `self_check: "pass"`, and a second
   apply without `--untriage` refuses `already_triaged` with no dup line.
3. Unapply: `--untriage --apply` removes the entry (`self_check: pass`);
   `--untriage` on a never-triaged hash refuses `not_triaged`.
4. Refusals: unknown hash ⇒ `no_proposal`; dirty script per
   `git status --porcelain` ⇒ `dirty_script`, file untouched in both.
5. Hermetic pytest covers apply, preview-no-mutate, missing proposal,
   dirty-file refusal, and untriage (happy + edge + failure), stdlib-only
   with `OMEGA_PRIME_SIDECAR_REPORTS_DIR` pointed at `tmp_path`.

## Test plan

- Happy: seed `review-queue.ndjson` with one PROPOSAL row in `tmp_path`;
  preview returns the exact diff and leaves the fixture script untouched
  (assert bytes); apply inserts the line, parses `TRIAGED`, self-check
  passes; untriage-apply removes it again.
- Edge: newest-wins when two PROPOSAL rows share a hash (uses later
  `ts`); 64-hex uppercase rejected as `bad_args`; rationale with quotes
  round-trips escaped; already-triaged re-apply refuses without dup.
- Failure: hash with no row ⇒ `no_proposal`; stubbed
  `git status --porcelain` non-empty ⇒ `dirty_script` with zero writes;
  corrupted post-edit import ⇒ edit rolled back, `self_check: fail`;
  empty queue file / missing queue file ⇒ `no_proposal`, not a crash.
- Hermeticity: no sockets, no real repo writes — script fixture copied
  to `tmp_path`, `git status` and date stubbed, env override
  `OMEGA_PRIME_SIDECAR_REPORTS_DIR=<tmp>`; assert `--apply`-less runs
  never open the script for writing.

## Docs surfaces

- `docs/red-team.md` command table: new row for
  `/omega-redteam-triage-apply` plus a PROPOSAL → preview → `--apply`
  approval recipe (replacing the hand-edit paragraph, keeping the dated
  + rationale rule).
- `docs/driving-the-bot.md` if it enumerates slash commands (keep the
  list consistent with `/omega-help` and the `COMMANDS` tuple).
- Plugin doc `omega-redteam-triage-apply.md` (MCP `omega_command`
  delegation, same shape as `omega-redteam-findings.md`).

## Rollback

Single revert: `git revert` the one commit (new
`redteam_triage_apply.py` + `__init__.py` registration + plugin doc +
tests + docs edits). No migration, no config node, no schema:
`review-queue.ndjson` rows written meanwhile stay valid PROPOSAL input
for the manual hand-edit path, and the `TRIAGED` block keeps whatever
entries were applied before the revert.
