# U3 — Telemetry Export & SLO Alerting → Phase 81

Issue: SPE-10594 · Milestone v16 · Evidence: `local://v16-recon-findings.md` (HEAD df14512)

## TITLE_AND_SUMMARY

Add optional OpenTelemetry OTLP export beside the existing zero-dependency /metrics endpoint,
define SLOs (serve availability, tool-call latency p99, denial-rate budget), generate alert
rules from those definitions, and add a nightly scheduled readiness-profile run that alerts on
regression.

Definitions:
- **OTLP export**: pushing metrics to a collector over the OpenTelemetry Line Protocol via an
  extra `otel` dependency group — off by default; without it nothing changes.
- **SLO (service level objective)**: a threshold on a metric over a window, e.g. "5xx share of
  served requests < 0.5% over 30 days"; the **error budget** is 1 − SLO target.
- **Readiness profile**: the isolated local readiness run produced by
  `python -m omega_prime.grokbot.readiness --root SOURCE --json --output REPORT`
  (verified in-tree, readiness.py usage line); its report carries `ok`, a scope/profile label,
  and a tool summary (`ok=… tools=… exercised=…`).
- **Regression**: a nightly readiness run with `ok: false`, or an exercised-tool count lower
  than the stored baseline for the same profile.

## EVIDENCE

- R5: metrics.py is a zero-dep Prometheus registry (counters/gauges/histograms, text 0.0.4)
  with series `omega_http_requests_total`, `omega_http_request_duration_seconds`,
  `omega_tool_denials_total{code}`; MetricsMiddleware, MetricsInterceptor, scope-gated
  /metrics.
- R5 MISSING list: no OTLP/OpenTelemetry/statsd export, no alerting, no SLOs, no dashboards,
  no shipping — observability is pull-only /metrics + local files.
- R2: supply-chain.yml (weekly schedule) and kb-refresh.yml (schedule + secret-gated skip) are
  the existing scheduled-workflow precedents; ci.yml evals run on push/PR only.
- R3: evals run only on push/PR — no nightly cadence exists for anything yet.

## VALUE_PROPOSITION

- Operators with an existing collector get push-based metrics without forking the runtime; the
  zero-dependency default (R5) is preserved for everyone else.
- SLOs turn raw counters into pass/fail burn signals with generated alert rules — the "no
  alerting, no SLOs" gap in R5 closes with configuration, not a new subsystem.
- Nightly readiness alerting catches environment/profile regressions within 24h instead of at
  the next human run.

## ARCHITECTURE_SKETCH

- `omega_prime/telemetry/otlp_export.py` (new): OTLP exporter thread. Constructed only when
  `telemetry.otlp.enabled` is true AND the `otel` extra is importable; otherwise the flag logs
  one warning and the exporter stays off (fail-open for telemetry, never for serving).
  Reads from the existing metrics.py registry (same counters/gauges/histograms — no second
  source of truth) and pushes at a fixed interval.
- pyproject: `[project.optional-dependencies] otel = ["opentelemetry-proto", ...]` — the only
  new dependency path in this plan; the base install stays dependency-free.
- `omega_prime/telemetry/slo.json` (explicit-path JSON document, per the repo convention of
  per-subsystem JSON loaded from explicit paths — omega_prime/config.py module docstring):
  ```json
  {
    "schema_version": 1,
    "objectives": [
      {"id": "serve-availability", "series": "omega_http_requests_total",
       "kind": "ratio", "good": "code!~5..", "target": 0.995, "window_days": 30},
      {"id": "tool-call-latency-p99", "series": "omega_http_request_duration_seconds",
       "kind": "quantile", "quantile": 0.99, "threshold_seconds": 5, "window_days": 30},
      {"id": "denial-rate-budget", "series": "omega_tool_denials_total",
       "kind": "budget", "target": 0.98, "window_days": 30}
    ]
  }
  ```
- `scripts/slo_rules.py` (new): reads slo.json, emits a Prometheus rules YAML (recording rules
  for burn rate + alerting rules on budget exhaustion) to a path given with `--output`.
  Deterministic: same input → byte-identical output. Alerting requires an operator-run
  Prometheus scraping /metrics (pull-only today, R5); the generated rules file is the contract,
  and docs/tool-host.md gains a short "Scraping & alerting" section.
- `.github/workflows/nightly-readiness.yml` (new, cron nightly): runs the readiness CLI from
  source, stores the JSON report as an artifact, and on regression (definition above) opens or
  updates a tracking issue; secret-gated skip pattern mirrors kb-refresh.yml (R2).
- Baseline storage: `.planning/` is wrong for runtime data — baseline lives as a repo variable
  or a committed `omega_prime/telemetry/readiness_baseline.json` updated only by an explicit
  `--bless` step, so regressions cannot self-heal silently.

## IMPLEMENTATION_STEPS

1. **Optional OTLP exporter.** Add `otel` extra + otlp_export.py guarded by
   `telemetry.otlp.enabled`; wire construction at mcp_server startup alongside the existing
   metrics middleware (R5 series are the source).
   **Verify:** `python -m pytest tests/test_telemetry_otlp.py -q` — asserts (a) default config constructs no exporter, (b) flag+extra enabled pushes records to a stub OTLP endpoint in-process, (c) flag on without the extra logs one warning and serves normally.
2. **Zero-dep regression guard.** Add a test that a default install imports mcp_server and
   serves /metrics with no `opentelemetry` modules imported.
   **Verify:** `python -m pytest tests/test_zero_dep_metrics.py -q` — passes on a venv without the `otel` extra (`pip list` shows no opentelemetry packages).
3. **SLO document + loader.** Add slo.json with the three objectives above and a stdlib loader
   validating schema/series names against the R5 registry.
   **Verify:** `python -m omega_prime.telemetry.slo check` — exits 0; corrupting a series name in a copy makes it exit non-zero naming the objective.
4. **Alert-rule generator.** Add scripts/slo_rules.py emitting Prometheus rules YAML from
   slo.json.
   **Verify:** `python scripts/slo_rules.py --output /tmp/rules.yaml && grep -c "alert:" /tmp/rules.yaml` — one alert per objective (3); re-running yields a byte-identical file (`diff` empty).
5. **Nightly readiness workflow.** Add nightly-readiness.yml per the architecture above.
   **Verify:** `python -m omega_prime.grokbot.readiness --root . --json --output /tmp/report.json && python -c "import json;r=json.load(open('/tmp/report.json'));print(r['ok'],r['profile'])"` locally; on CI, dispatch the workflow manually — green run, artifact attached; forcing `ok:false` via a broken fixture root opens the tracking issue.
6. **Regression alert wiring.** Implement the baseline comparison + issue open/update logic in
   the workflow (script under scripts/, stdlib-only).
   **Verify:** run the comparison script against the committed baseline and a synthetic report with a lower exercised count — exits non-zero printing the regressed profile and delta; identical report exits 0.
7. **Docs.** Add the "Scraping & alerting" section to docs/tool-host.md: scrape /metrics,
   load the generated rules file, enable OTLP only with a collector.
   **Verify:** `grep -n "Scraping & alerting" docs/tool-host.md` finds the section; the documented example rules path matches the generator's `--output` usage.

## DEPENDENCIES

- plan-07 depends on this plan's metrics substrate (per-caller usage counters land in the same
  registry); this plan has no hard dependency on plan-07 and ships first in Phase 81.
- plan-04's new counters (rotation/expiry) automatically flow into /metrics and any OTLP
  export once defined in metrics.py — no coupling beyond registry convention (R5).
- plan-02: independent — nightly readiness runs from source, not from the published image.
- External: an operator collector/Prometheus for real alerting; GitHub scheduled workflow
  permissions for issue creation (precedent: kb-refresh.yml, R2).

## RISKS_AND_MITIGATIONS

- *Dependency creep into the zero-dep core.* OTLP lives behind an optional extra with an
  import guard and the step-2 regression test; default install stays dependency-free (R5).
- *Cardinality explosion from per-request labels.* Export only the existing R5 series shapes;
  no new per-user labels in this plan (per-caller labels arrive with plan-07 as its own
  decision).
- *SLO thresholds guessed wrong.* slo.json is data with a schema_version; thresholds tune
  without code changes, and the loader rejects unknown series so drift from metrics.py fails
  loudly.
- *Nightly readiness flakiness alerting spam.* The tracking issue is opened/updated (not one
  issue per failure), and a green run closes the alert state.
- *Self-healing baseline.* Baseline changes only via explicit `--bless` in a reviewed PR.

## ROLLOUT_AND_ROLLBACK

- Config flag: `telemetry.otlp.enabled` (node key, default `false`) with
  `telemetry.otlp.endpoint` (default empty; export refuses to start without it) and
  `telemetry.otlp.interval_seconds` (default 60), following the omega_prime/config.py node-key
  style; env overrides `OMEGA_PRIME_TELEMETRY_OTLP_ENABLED` /
  `OMEGA_PRIME_TELEMETRY_OTLP_ENDPOINT` per the config.py:100-115 `OMEGA_PRIME_*` pattern.
- Alerting is side-effect-free until an operator loads the generated rules file into their
  Prometheus; nothing in the runtime pushes alerts.
- Revert step: unset `OMEGA_PRIME_TELEMETRY_OTLP_ENABLED` (or set the node key `false`) — the
  exporter is never constructed and /metrics serving is byte-identical to today (R5). The
  nightly workflow reverts by deleting nightly-readiness.yml; the tracking issue is closed
  manually.
- The /metrics endpoint, its scope gating, and its 401-on-unauthenticated behavior
  (verify.py:557-567, R5) are untouched by this plan.

## SUCCESS_METRICS

- Default deployment: zero new dependencies, zero new network egress, /metrics unchanged
  (verified by the step-2 test in CI on every push).
- With the flag on: OTLP push observed at the configured interval with all R5 series present at
  the collector.
- Three objectives defined, generator output validates against a Prometheus instance in the
  maintainer's environment at least once (recorded in the phase VERIFICATION).
- Nightly readiness runs for 7 consecutive nights with either all-green or exactly one tracked
  issue per real regression — no duplicate issues, no missed regression in the drill.

## GSD_PHASE_MAPPING

- Milestone v16, **Phase 81** — "Telemetry export & SLO alerting".
- Phase directory under the v16 phases layout (`.planning/<v16-phases>/81-<slug>/`), v14/v15
  `NN-<slug>` convention.
- Execution order: steps 1-2 (exporter + zero-dep guard) are one task; steps 3-4 (SLO +
  rules) a second; steps 5-6 (nightly workflow) a third; step 7 rides with the last.
  All three tasks are independent after the slo.json schema (step 3) exists.
- Phase verification: SUCCESS_METRICS become VERIFICATION.md checks; the zero-dep regression
  guard is a hard acceptance criterion.
