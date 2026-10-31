# 00051 — Command Center task labels: SUMMARY

## Root cause
`omega_prime/dashboard/model.py::normalize_tasks` created a task for every live
`omp` pid whose cwd was not claimed by a session, Herdr pane or wiring row,
titled `omp process <pid>` with detail `unmatched process`. agentOS runs
(`omp -p --mode json --no-title --session-dir /home/box/agentos/state/omp/<run>/sessions`)
are invisible to the `~/.omp/agent/sessions` collector and record no title, so
every run — and each `__omp_worker_*` helper in the same cwd — became a raw
pid row. Untitled sessions were titled with their JSONL file name.

## Changes
- `dashboard/labels.py` (new): bounded, redacted argv parsing (prompt header
  task/title/role/phase/plan, run id, model, helper worker) and title/description builders.
- `collectors/procs.py`: allowlisted `label`; `role=helper` for workers;
  `omp auth-broker|daemon|mcp|serve|server` are services (with auth-broker,
  openshell-gateway, herdr server). Dead/zombie/reused pids are still dropped.
- `poller.py`: services published as `sources.procs.services` (source health, not tasks).
- `model.py`: unclaimed procs grouped by cwd, helpers folded, services skipped;
  `000NN - Title · activity` titles, descriptive `detail`; session title fallback.
- Writer contract unchanged (all writes still via `status_file.write_tasks`).

## Verification
- ruff check / format --check, mypy (dashboard), polyglot-check --changed: clean.
- `pytest -k dashboard`: all pass (12 new behaviour tests).
- Full `omega_prime/tests`: 5707 passed; 17 failures pre-exist identically on
  origin/main on this box (missing `rlm` module / strict preflight).
