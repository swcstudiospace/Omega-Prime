# Phase 81: Telemetry export and SLO alerting - Context

**Gathered:** 2026-10-13
**Status:** Ready for planning
**Mode:** Auto-generated (discuss skipped via workflow.skip_discuss)

<domain>
## Phase Boundary

Ship opt-in telemetry export and define SLOs with alerting so regressions surface without weakening the zero-dependency default. Scope: optional OTLP export as an extra dependency (off by default), SLO definitions (serve availability, tool-call p99 latency, denial-rate budget) with alert rules, nightly scheduled readiness-profile run alerting on regression, graceful degradation when export fails. Out of scope: making OTLP a hard dependency, shipping to any hosted backend in this phase, changing the existing /metrics surface or its auth guarantees.

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
All implementation choices are at Claude's discretion — discuss phase was skipped per user setting.

### Recorded decisions binding this phase
- Zero-dependency default unchanged; OTLP ships as an optional extra (`telemetry.otlp.*` config per `.planning/updates/plan-03.md`), off by default (`.planning/decisions.md`).
- SLO alerting is defined as config + rules artifacts; hosted alert delivery awaits operator wiring (documented, not executed).

</decisions>

<code_context>
## Existing Code Insights

Verified evidence (`.planning/recon-evidence.md` R5, file:line): `omega_prime/grokbot/metrics.py` zero-dep Prometheus registry (MetricsRegistry, MetricsMiddleware, MetricsInterceptor, scope-gated metrics_endpoint :650; series omega_http_requests_total, omega_http_request_duration_seconds, omega_tool_denials_total{code}); `grokbot/telemetry.py` JsonLogFormatter + middlewares; `grokbot/verify.py:557-567` asserts unauthenticated /metrics 401s and no token leakage; MISSING: no OTLP/OTEL export, no SLOs, no alerting, no scheduled regression run. Plan: `.planning/updates/plan-03.md` (U3).

</code_context>

<specifics>
## Specifics

- The nightly readiness run should reuse the existing local readiness profile command rather than inventing a new one; alert = non-zero exit surfaced as a workflow failure.
- OTLP exporter import must be lazy/optional — the process must start with the extra absent when the flag is off, and fail loud (documented degraded mode) if on without the extra.
- SLO thresholds go in a versioned config artifact, not scattered literals.

</specifics>

<deferred>
## Deferred Items

- Hosted Grafana/backend wiring (operator-side; documented only).

</deferred>
