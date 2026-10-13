---
plan: 81-01
status: complete
phase: 81
---

# 81-01 Summary: Optional OTLP export

## Tasks completed

- Task 1: `telemetry.otlp` config node (enabled/endpoint/headers/interval defaults off/empty/60s) in `omega_prime/config.py`; `otel` extra EXTENDED into the existing `[project.optional-dependencies]` table (opentelemetry-proto + opentelemetry-exporter-otlp-proto-http); `omega_prime/telemetry/otlp_export.py` lazy exporter (`maybe_start` → None degraded mode; ImportError seam warns exactly once naming the extra; never imported at module load).
- Task 2: `create_sse_app` wiring in `omega_prime/grokbot/remote.py` — startup block after MetricsRegistry, lazy imports, `stop()` registered in the existing lifespan finally, exporter exposed as `app.state.otlp_exporter`; `/metrics` route and middlewares untouched.
- Task 3: `test_telemetry_otlp.py` (default-None; stub-sender pushes all three R5 series; seam warns once + registry still renders) + `test_zero_dep_metrics.py` (base-deps absence from pyproject `[project] dependencies` and requirements-lock; delta sys.modules assertions; flag-off render works).

## Deviations and correction

- Initial guards asserted absolute `opentelemetry` absence from sys.modules — false in this environment: `opentelemetry-api` is installed transitively (PyRIT dep chain; verified via importlib.metadata). Guards were revised (parent-dispatched fix) to environment-honest invariants: base-dependency absence + no NEW otel modules on the flag-off path. `slo.py` also gained the missing duplicate-objective-id rejection (real loader bug caught by its own test).
- Warning capture scoped to the omega_prime logger name so third-party opentelemetry.util records do not count.

## Verification

16 otlp/zero-dep/slo tests pass — including with opentelemetry pre-imported in-process (polluted full-suite condition); `slo check` CLI exit 0 (3 objectives); included in the phase full run (see 81-VERIFICATION.md).
