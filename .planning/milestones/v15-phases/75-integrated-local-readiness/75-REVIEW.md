---
phase: 75-integrated-local-readiness
reviewed: 2026-10-12T12:00:00Z
depth: standard
files_reviewed: 22
files_reviewed_list:
  - CHANGELOG.md
  - docs/driving-the-bot.md
  - docs/grok-bot-native.md
  - omega_prime/commands/__init__.py
  - omega_prime/grokbot/oneclick.py
  - omega_prime/grokbot/readiness.py
  - omega_prime/grokbot/readiness_prime.py
  - omega_prime/tests/test_grokbot_diag_bundle.py
  - omega_prime/tests/test_grokbot_oneclick.py
  - omega_prime/tests/test_grokbot_readiness.py
  - omega_prime/tests/test_grokbot_readiness_cli.py
  - omega_prime/tests/test_grokbot_readiness_prime.py
  - omega_prime/tests/test_grokbot_secret_guard.py
  - omega_prime/tests/test_lead.py
  - omega_prime/tests/test_mcp_session.py
  - omega_prime/tests/test_mobile.py
  - omega_prime/tests/test_omega_commands.py
  - omega_prime/tests/test_omega_surface.py
  - omega_prime/tools/lead.py
  - omega_prime/tools/mcp_session.py
  - omega_prime/tools/mobile.py
  - omega_prime/tools/omega_command.py
findings:
  critical: 0
  warning: 2
  info: 1
  total: 3
status: issues_found
---

# Phase 75: Code Review Report (delta since 3208b9b)

**Reviewed:** 2026-10-12T12:00:00Z
**Depth:** standard
**Files Reviewed:** 22
**Status:** issues_found

## Summary

Reviewed the `3208b9b..HEAD` delta across all 22 listed files (Phase 77.1
implementation plus PR #18 review fixes). The delta is in good shape: the
prior fixed findings WR-03 (git ref validation), WR-04 (atomic roster
save), WR-05 (torn-tail event repair), WR-06 (metadata secret gate) and
WR-07 (summary/checks shape validation) are verified still in force in the
current code, and the four previously deferred items are now resolved in
this delta and are not re-reported — WR-01 (`--work-root` forwarded to the
stdio child, `oneclick.py`), WR-02 (`_exported_token` deleted, token by
argument only), WR-08 (`--json` on stderr under served stdio documented in
`docs/grok-bot-native.md`), IN-01 (`run_preflight_doctor` deleted; no stale
references remain outside planning docs). Test-only credential shapes stay
fragment-assembled (scanner-clean); the only matching literals in product
code are the detector strings themselves. No Critical findings. Two new
Warnings below: an unguarded `summary.problems` iteration that survives the
WR-07 validator (same crash class), and cross-process duplicate event
`seq` allocation in the reworked `EventStore`. One Info on the
process-lifetime generated-command cache.

## Warnings

### WR-09: `validate_report` still leaves `summary.problems` shape unchecked

**File:** `omega_prime/grokbot/readiness.py:178-207`
**Issue:** The WR-07 fix validates `summary` is a dict and `checks` is a
list of dicts, but `_human_lines` iterates `summary.get("problems", [])`
with no shape check (line 206). A child-derived report such as
`{"summary": {"problems": 5}, ...}` passes `validate_report` and then
raises `TypeError: 'int' object is not iterable` out of `_human_lines`,
which `main` calls outside its `except ValueError` guard — an uncaught
traceback from child-controlled data, the exact contract breach WR-07 was
about. (A string `problems` degrades to per-character lines; a dict
iterates keys; only non-iterables crash, but none are rejected.)
**Fix:**
```python
problems = report["summary"].get("problems", [])
if not isinstance(problems, list):
    raise ValueError("report summary problems is not a list")
```
placed in `validate_report` next to the checks loop, plus a regression
test mirroring `test_validate_report_rejects_malformed_summary_and_checks`
with `problems` in (5, None, "x", {}).

### WR-10: `EventStore` allocates `seq` from in-memory count across processes

**File:** `omega_prime/tools/lead.py:365-373,433-440`
**Issue:** `__init__` sets `self._seq` by counting rows once, and
`append` does `self._seq += 1` inside the sidecar `flock`. The lock
serializes file bytes (repair + append) but not `seq` allocation: two live
stores on the same path (host plus cron/admin, the exact concurrent-writer
pair the new docstring and
`test_event_store_torn_tail_does_not_eat_a_live_stores_append` contemplate)
that load the same count both mint the same next `seq`. Duplicate seqs
break the mirror's per-event identity (the suite itself asserts
`[1, 2, 3, 4]` contiguity). The in-lock repair makes bytes safe while the
counter stays racy.
**Fix:** Derive the next seq inside the exclusive section from the repaired
file instead of instance memory, e.g. in `append` after `_repair_tail()`:
```python
self._seq = sum(1 for _ in self.records()) + 1
```
(or track `max(seq) + 1`), so concurrent writers serialize allocation
under the same `flock` that serializes the bytes.

## Info

### IN-07: Generated command catalog is cached for process lifetime

**File:** `omega_prime/commands/__init__.py:141-142`
**Issue:** `_generated_for` is `functools.cache`d, so a long-running host
never picks up newly added skills/routines in `/omega-help` or dispatch
until restart (updated skill *content* is read fresh per call in
`_run_generated`, only the name catalog is stale). The restart-after-change
convention in `docs/driving-the-bot.md` covers this operationally, so this
is minor.
**Fix:** Document that adding a skill/routine needs a host restart (one
sentence beside the generated-commands docs), or key the cache off the
skills/routines mtimes.

---

_Reviewed: 2026-10-12T12:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
