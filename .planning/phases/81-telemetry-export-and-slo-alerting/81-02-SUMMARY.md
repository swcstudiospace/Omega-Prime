---
plan: 81-02
status: complete
phase: 81
---

# 81-02 Summary: SLOs, alert rules, nightly readiness

## Tasks completed

- Task 1: `omega_prime/telemetry/slo.json` (schema_version 1; three objectives: serve-availability ratio 0.995, tool-call p99 5s, denial-budget 0.98; 30-day windows) + `omega_prime/telemetry/slo.py` (load/validate; KNOWN_SERIES mirrors the R5 registry; rejects unknown series/kind, bad numbers, duplicate ids naming the objective; `check` CLI) + 9 hermetic tests incl. a metrics-drift guard.
- Task 2: `scripts/slo_rules.py` — deterministic Prometheus generator: 3 groups from the loaded doc, recording rule + alert per objective (3 alerts), budgets = 1−target with dust-safe formatting, generic `good` matcher parsing (incl. negation), fixed header, sort_keys, no timestamps; PyYAML (base dep) only.
- Task 3: `scripts/nightly_readiness_check.py` (`check`/`bless`; report-nested baseline shape per checker note, seeded from a REAL local readiness run: exercised 50/165, ok true; check exits 1 naming profile+delta on regression, fail-closed on malformed docs; bless is human-only atomic write) + `omega_prime/telemetry/readiness_baseline.json` + `.github/workflows/nightly-readiness.yml` (cron 03:17 + dispatch; reuses `python -m omega_prime.grokbot.readiness --root . --json --output`; always uploads artifact; failure opens-or-updates one stable-title issue via GITHUB_TOKEN only; success closes it) + `docs/tool-host.md` "Scraping & alerting".

## Deviations

- Alert denominators: budget alerts use `omega_tool_calls_total` (denials ⊂ calls) and quantile alerts use the Prometheus histogram `_bucket`/`le` convention — both commented in-code; no threshold literal outside the SLO doc.
- Baseline `blessed_at` 2026-10-13T02:50:34Z (session clock).

## Verification (parent-run)

- `slo check` exit 0; generator ran twice → byte-identical output, 3 alerts; nightly `check` against the blessed baseline with a fresh real readiness report → `ok: profile=local-only exercised=50 baseline=50`, exit 0; docs/zero-dep/otlp/slo tests 28 passed; ruff/format clean after parent pass; mypy clean; full suite in 81-VERIFICATION.md.
