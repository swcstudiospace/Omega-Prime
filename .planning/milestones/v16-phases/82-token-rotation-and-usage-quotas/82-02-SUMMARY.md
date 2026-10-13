---
plan: 82-02
status: complete
phase: 82
---

# 82-02 Summary: Usage quotas

## Tasks completed

- Task 1: `quotas` config node (`mode` default report / `document` / `rollup_dir` + `OMEGA_PRIME_QUOTAS_*` env overrides with loud bogus-mode fallback to report); strict versioned document loader (13 invalid shapes rejected; missing/empty document fully inert); report-mode per-caller counters with zero refusals/warnings/parity.
- Task 2: soft warnings once per (day, caller, kind) at ≥80% with warn gauge, never denying; hard mode denies pre-dispatch with `ToolDenial(code=quota)` on the existing denial pipeline (1-under succeeds, at-limit denied, zero partial effects); safe `_unknown_` bucket and unlisted callers never denied; rotated-lineage fixture proves shared label bucket across rotation; 64-label overflow folds to `_overflow_`; live mode transitions via `set_mode`/`refresh_from_config`; engine chained in `grokbot/remote.py` after ScopeInterceptor, exposed as `app.state.quota_engine`.
- Task 3: `python -m omega_prime.quotas rollup` CLI — deterministic per-UTC-day `usage-YYYY-MM-DD.json` (byte-identical reruns, reconciles with /metrics; disabled mode logs a skip and writes nothing) + heartbeat scheduling JSON in `docs/tool-host.md` (Usage quotas section: ladder, schema, series, UTC-day semantics).

## Deviations

- tokens.py / cron-heartbeat files untouched (plan ownership respected); config.py/remote.py shared edits landed in wave order (82-01 first).
- Parent lint pass converted 7 best-effort gauge/counter updates to `contextlib.suppress` and fixed import order — no behavior change (tests re-run green).

## Verification

35 quota tests (report/enforce) + rotation suite + integration/docs/metrics suites green (120 in the combined run, then 48 re-run after the lint pass); default-config assertion (`mode=report`, empty document); ruff clean; mypy clean on the quotas package; full suite in 82-VERIFICATION.md.
