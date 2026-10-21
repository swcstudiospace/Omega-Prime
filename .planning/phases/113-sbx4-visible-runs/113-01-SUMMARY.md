# Phase 113 Plan 01: Dashboard visibility modules Summary

One never-raising Herdr pane launcher with a pinned stdout parse contract and tmux fallback, plus the locked tasks.json row lifecycle (start/touch/finish) — the dashboard half of sbx-4 visibility, engine-free by construction.

**Phase:** 113-sbx4-visible-runs
**Plan:** 01
**Requirements:** W113-01, W113-02
**Status:** complete
**Date:** 2026-10-21
**Base:** d7400ea (`feat/sbx-4-routines-visible`)

## Commits

| Task | Commit | Subject |
| ---- | ------ | ------- |
| 113-01-T1 | af12b22 | 00014 - dashboard: herdr_launch pane launcher, tmux fallback, minimal child env (W113-01) |
| 113-01-T2 | 2cab136 | 00014 - dashboard: run_rows locked row lifecycle start/touch/finish (W113-02) |
| 113-01-T3 | fa0d4f8 | 00014 - dashboard: export herdr_launch and run_rows from the package (W113-01, W113-02) |
| summary | (this commit) | 00014 - phase 113: plan 113-01 summary (W113-01, W113-02) |

Diff scope: the three implementation commits touch ONLY `omega_prime/dashboard/**` (herdr_launch.py +163, run_rows.py +165, `__init__.py` +3). `status_file.py` and `blocks.py` are byte-identical (`git diff HEAD~3..HEAD` over both: empty). No engine, test, or omp-sandbox edits.

## Pinned interface signatures (cross-plan contract for 113-02/113-03)

```python
# herdr_launch.py
open_pane(env: Mapping[str, str], *, cwd: str | Path, label: str,
          command: Sequence[str], session: str | None = None) -> str | None
# returns the herdr pane id | "tmux:<session>" | None; NEVER raises.

# run_rows.py
start(path, task_id, *, job_id: str, stage: str | None, pane: str | None) -> None
touch(path, task_id, event: Mapping[str, Any] | str) -> None
finish(path, task_id, ok: bool, receipt: Mapping[str, Any]) -> None
```

- **open_pane (W113-01):** resolves herdr via `shutil.which` on `env["PATH"]`; `herdr workspace create --cwd <cwd> --label <label> --no-focus` (list-exec, captured, `timeout=10.0`, two-key child env `{PATH, HOME}`); pane id = **last whitespace-separated token of the last non-empty stdout line** (pinned parse contract — the 113-03 fake runner follows it exactly); then `herdr pane run <pane> *command` → pane id on rc 0. Every failure (missing binary, non-zero rc, timeout, empty/unparseable stdout, OSError) falls through to the tmux fallback: skip on falsy `OMEGA_PRIME_ENGINE_TMUX` (local `_FALSY` check, jobs.py semantics, no engine import), missing tmux, or `session=None`; `has-session` rc 0 → `"tmux:<session>"`; else `new-session -d -s <session> -- <command...>` → `"tmux:<session>"` on rc 0, else `None`.
- **run_rows (W113-02):** every mutation is one `locked_tasks(path, write=True)` + `_write_locked` pair — no second writer. `start` creates `{id, state "working", job_id, stage, started_at, last_activity_at}` + `herdr_pane` only when pane is not None; re-start on a non-terminal row moves back to "working" with `started_at` pinned at first creation; terminal (done/failed) rows never resurrect (no write). `touch` stamps `last_activity_at` + `last_event` (`"question"`; `"stage_done <status>"` collapsed to `"stage_done"` when empty; plain str verbatim; other shapes leave the field), no-op without writing for missing/terminal rows. `finish` upserts done/failed with the receipt redacted recursively (`redact_text`) under the single `"receipt"` key. Store corruption raises naturally (`ValueError` from the loader) — the suppress discipline belongs to the 113-02 engine call sites (omp_driver.py:385 precedent).
- **Row additive fields:** `herdr_pane`, `started_at`, `last_activity_at`, `last_event`, `receipt` — legal via the store's unknown-field passthrough (status_file.py:22-26). Items are never written here; `blocks.raise_block/clear_block` stay the only item writers.

## Deviations from Plan

**[Documented keyword deviation — W113-01] `session: str | None = None` keyword on `open_pane`.** Planned and locked in `<assumption_delta_decision>` before implementation: the tmux session name is an engine convention (`omega-<job>`) the dashboard layer must not derive, so the fallback needs it passed in; `session=None` disables the fallback (returns None). The engine caller (113-02) passes `session=f"omega-{job_id}"`; tests pin the None path. No other deviation — the plan executed as written otherwise.

## Verification (all run in this environment, no PYTHONPATH prefix)

| Gate | Command | Result |
| ---- | ------- | ------ |
| py_compile | `python3 -m py_compile omega_prime/dashboard/__init__.py omega_prime/dashboard/herdr_launch.py omega_prime/dashboard/run_rows.py` | pass |
| Fake-herdr pane parse smoke | plan T1 one-liner (`echo workspace created pane-7` fake on tmp PATH, `open_pane({'PATH': str(d)}, cwd=str(d), label='00042 plan', command=['true'])`) | pass — returns `pane-7` |
| Store lifecycle smoke | plan T2 one-liner (start→touch question→finish on tmp tasks.json) | pass — row `done` with `herdr_pane='pane-7'`, `last_event='question'`, `receipt['stage_id']=='stage-1'` |
| Layer gate | `! grep -rn "omega_prime\.engine" omega_prime/dashboard/` | pass — clean (every file) |
| Import/__all__ | `from omega_prime.dashboard import herdr_launch, run_rows` + both names in `__all__` | pass, zero side effects |
| Regression suite | `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_dashboard_blocks.py` | **16 passed** (baseline before edits: 16 passed) |

Additional spot-checks (beyond the plan's gates, evidence for 113-03): missing-herdr/garbage-stdout/nonzero-exit → `None`, never raises; empty stdout with a failing create → fallback; `started_at` pinned across re-start; terminal row immune to start/touch; `finish` upserts a missing row terminal; receipt strings (`ghp_…`, `api_key = …`) → `[REDACTED]`; `herdr_pane` absent when `pane=None`; unknown fields + sibling rows byte-preserved after full lifecycle.

## Notes for 113-02 / 113-03

- Consume exactly as pinned in the plan's key_links: `from omega_prime.dashboard import herdr_launch, run_rows`; `herdr_launch.open_pane(env, cwd=work, label=f"{task_id} {stage}", command=[sys.executable, "-m", "omega_prime.engine.watch", job_id, "--follow", "--state-dir", str(state_dir(env))], session=f"omega-{job_id}")`.
- Wrap every run_rows call in `contextlib.suppress(Exception)` engine-side — the modules themselves raise on a corrupt/unwritable store by design.
- The 113-03 fake herdr runner must print the pane id as the last whitespace token of its last non-empty stdout line; the parse contract is enforced in `_parse_pane_id`.
- Behavioral proof against the engine remains wave-3 work (113-03), per plan.

## Self-Check: PASSED

- Files exist: `omega_prime/dashboard/herdr_launch.py`, `omega_prime/dashboard/run_rows.py`, `omega_prime/dashboard/__init__.py` (all committed).
- Commits af12b22 / 2cab136 / fa0d4f8 are HEAD ancestors (`git log --oneline -4`); working tree clean after the three task commits.
- No stubs, no deferred items, no auth gates, no new dependencies (stdlib + `omega_prime.dashboard.status_file` + `omega_prime.credentials.redact` only).
