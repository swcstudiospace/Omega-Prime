# U7 — Per-User Token Quotas + Usage Reporting → Phase 82

Issue: SPE-10594 · Milestone v16 · Evidence: `local://v16-recon-findings.md` (HEAD df14512)

## TITLE_AND_SUMMARY

Add per-caller token quotas and usage reporting with a fixed soft-then-hard rollout:
report-only first, then soft warnings, then hard caps. Nothing is enforced in report-only
mode. Usage reporting runs as metrics series (plan-03 substrate) plus a scheduled rollup job.

Definitions:
- **Caller identity**: the token-store label of the authenticating bearer token (tokens.py
  store, R4); all quota accounting keys on this label, never on raw token material.
- **Quota document**: an explicit-path JSON file mapping caller label → limits
  (`requests_per_day`, `tool_calls_per_day`); absent file or absent caller ⇒ unlimited (no
  default cap).
- **Modes** (one config key, ordered): `report` — count and expose only; `soft` — additionally
  log a warning and expose a warning gauge at ≥80% of any limit; `hard` — additionally deny
  over-limit requests with the existing denial pipeline. **Report-only mode enforces
  nothing** — no denial, no warning, no behavior change (R8 baseline).
- **Usage rollup**: a scheduled job that reduces the live counters into a per-caller, per-day
  summary document.

## EVIDENCE

- R8: no per-user quota, token budget, or usage-reporting enforcement exists anywhere in
  omega_prime/ (scout sweep).
- R5: MetricsInterceptor already counts tool calls and MetricsMiddleware counts HTTP requests;
  series include `omega_tool_denials_total{code}` — the interceptors "count calls but enforce
  nothing".
- R4: token store is SHA-256-only, 0600, fail-closed live-reload; labels are the existing
  identity primitive; plan-04 adds `rotated_from`/`rotated_to` lineage so a caller's identity
  survives rotation.
- R5: /metrics is scope-gated and unauthenticated access 401s (verify.py:557-567) — per-caller
  labels never leak via an unauthenticated scrape.

## VALUE_PROPOSITION

- Multi-caller deployments see who consumes what, per day, from /metrics and the rollup —
  data first (R8 gap), enforcement second.
- The three-mode rollout lets an operator gather real usage for a cycle before any caller can
  be denied, making hard caps an informed decision instead of a guess.
- Hard mode reuses the existing denial pipeline and its metric (`omega_tool_denials_total`,
  R5), so denied requests are observable in the same series operators already read.

## ARCHITECTURE_SKETCH

- `omega_prime/quotas/__init__.py` (new): quota engine.
  - Config nodes: `quotas.mode` ∈ {`report`, `soft`, `hard`} (default `report`),
    `quotas.document` (path to the quota JSON, default empty ⇒ engine inert even in
    `report`), `quotas.rollup_dir` (default `.omega-quotas/`).
  - Engine hooks at the existing interceptor layer: MetricsInterceptor-style pass-through
    that (1) increments per-caller counters, (2) in `soft`/`hard` compares against the
    document, (3) in `hard` refuses via the same denial path that already produces
    `omega_tool_denials_total{code}` (R5) with `code="quota"`.
- Per-caller series (metrics.py registry, R5 shapes):
  `omega_caller_requests_total{caller}`, `omega_caller_tool_calls_total{caller}`,
  `omega_caller_quota_limit{caller, kind}`, `omega_caller_quota_used_ratio{caller, kind}`,
  `omega_caller_quota_warn{caller}` (soft-mode warning gauge). Cardinality is bounded by the
  token store's label count (a deployment has a handful of callers, R4).
- Usage rollup: `python -m omega_prime.quotas rollup` writes
  `rollup_dir/usage-YYYY-MM-DD.json` — one object per caller:
  `{caller, requests, tool_calls, limits, warn: bool, denied: int}`; deterministic ordering.
  Scheduling rides the same cron heartbeat family as plan-04's rotation job (one heartbeat
  job per concern; no new scheduler).
- Quota document schema (versioned):
  `{"schema_version": 1, "callers": {"<label>": {"requests_per_day": N,
  "tool_calls_per_day": M}}}` — a caller missing from the document is unlimited; the loader
  rejects unknown fields and negative/zero limits loudly.
- Denial semantics in `hard`: over-limit requests fail with the standard tool-denial result
  shape and `code="quota"`; the request is never partially executed (the check precedes
  dispatch).

## IMPLEMENTATION_STEPS

1. **Quota document loader.** Implement the schema, validation, and inert defaults.
   **Verify:** `python -m pytest tests/test_quotas_loader.py -q` — valid document loads; unknown field, negative limit, non-object callers each fail with a named error; missing file + `quotas.mode: report` ⇒ engine reports inert (no counters registered).
2. **Per-caller counters (report mode).** Hook the engine at the interceptor layer; counters
   only.
   **Verify:** `python -m pytest tests/test_quotas_report.py -q` — scripted requests from two fixture callers produce distinct `omega_caller_requests_total{caller}` series; no request is ever refused in this mode (assertion inside the test); with the feature flag off, zero new series appear (parity with R8).
3. **Soft mode.** Add the ≥80% warning path: log line + `omega_caller_quota_warn` gauge; still
   no denial.
   **Verify:** `python -m pytest tests/test_quotas_soft.py -q` — a fixture caller driven to 85% of its limit logs one warning per window and shows the gauge; the same request set in `report` mode logs nothing.
4. **Hard mode.** Add the pre-dispatch refusal with `code="quota"`.
   **Verify:** `python -m pytest tests/test_quotas_hard.py -q` — a caller at limit gets the standard denial shape with `code="quota"`, `omega_tool_denials_total{code="quota"}` increments (R5 series), and a request 1-under the limit succeeds; the over-limit request leaves no partial tool effects (fixture asserts zero writes).
5. **Usage rollup job.** Implement `rollup` writing the per-day summary.
   **Verify:** run a fixture day through the engine, then `python -m omega_prime.quotas rollup --date <day>` — `usage-<day>.json` matches the scripted counts per caller; re-running is idempotent (byte-identical output).
6. **Heartbeat scheduling.** Register the nightly rollup heartbeat job (documented setup, same
   pattern as plan-04 step 4).
   **Verify:** with the heartbeat job registered, the rollup file for yesterday exists after the job runs; with quotas disabled the job logs one skip line and writes nothing.
7. **Docs.** Document modes, the quota document, series names, and the rollup in
   docs/tool-host.md.
   **Verify:** `grep -n "quotas" docs/tool-host.md` finds the section; the documented series names match step 2/4's verified output exactly.

## DEPENDENCIES

- **plan-04 (U4, Phase 82) — required.** Caller identity must be stable across token
  rotation: plan-04's key versioning (`rotated_from`/`rotated_to`) is what lets a rotated
  caller keep its quota label instead of appearing as a new unlimited caller. plan-07 lands
  after plan-04's store versioning (its steps 1-2).
- **plan-03 (U3, Phase 81) — substrate.** The per-caller series here ride the same
  metrics.py registry and are exported by plan-03's OTLP path when enabled; rollup files give
  the same data where no collector exists. No code dependency, but plan-03 first in phase
  order keeps the substrate proven.
- plan-01/02/05/06: none.
- External: cron heartbeat enabled for the scheduled rollup (as in plan-04); no new
  dependencies (stdlib-only engine, R5 zero-dep metrics preserved).

## RISKS_AND_MITIGATIONS

- *Label cardinality explosion.* Labels are token-store labels (R4), bounded and
  operator-controlled; a cap on distinct exported labels (e.g. 64, excess rolled into
  `caller="_overflow_"`) is documented and tested if the store grows.
- *Identity break at rotation (pre-plan-04 stores).* plan-07 orders after plan-04; the hard
  mode test includes a rotated-lineage fixture proving the label persists across
  `rotated_from`.
- *Clock/window gaming (quota reset at UTC midnight).* Documented window semantics
  (UTC day); the rollup file is the source of truth for audit, the live counter only for
  enforcement within the current day.
- *Hard mode locks out an operator mid-incident.* `revoke`/new-mint (R4 CLI) and the mode
  flag are configuration-level escapes: flipping `quotas.mode` to `report` takes effect on
  live-reload (R4 fail-closed live-reload store precedent) without a restart.
- *Silent enforcement creep.* Report-only is the shipped default and the step-2 test asserts
  zero refusals in that mode — R8's "metrics count but enforce nothing" baseline is preserved
  until an operator changes the mode deliberately.

## ROLLOUT_AND_ROLLBACK

- Config flag: `quotas.mode` with values `report` (default) / `soft` / `hard`, plus
  `quotas.document` (default empty = inert) — omega_prime/config.py node-key style with env
  overrides `OMEGA_PRIME_QUOTAS_MODE` / `OMEGA_PRIME_QUOTAS_DOCUMENT` (config.py:100-115
  `OMEGA_PRIME_*` pattern).
- Rollout ladder (fixed by scope): ship with mode `report` everywhere → operator collects
  1-2 rollups → `soft` (warnings visible, no denials) → `hard` per deployment only after the
  operator sets real limits in the quota document.
- Revert step: set `OMEGA_PRIME_QUOTAS_MODE=report` (or unset the env key) — enforcement and
  warnings stop immediately on live-reload; callers are unlimited again exactly as today
  (R8). Full removal: delete the quota document + engine flag; counters disappear with the
  feature flag (step 2's parity assertion), and rollup files under `.omega-quotas/` are plain
  JSON artifacts that can be archived or deleted.

## SUCCESS_METRICS

- Report-only default: zero behavioral change vs R8 baseline — no denials, no warnings, new
  series present only when the flag is on (step-2 parity test green in CI).
- With a populated quota document, the rollup file reconciles exactly with the /metrics
  counters for the same day (step-5 verification).
- In `hard`, 100% of over-limit requests deny with `code="quota"` before dispatch, and the
  denial is visible in the existing `omega_tool_denials_total` series (R5) — no partial
  execution ever observed in tests.
- Mode transitions (`report`→`soft`→`hard`→`report`) take effect without a server restart in
  the live-reload test.

## GSD_PHASE_MAPPING

- Milestone v16, **Phase 82** — shares the phase with plan-04: plan-04 is the first half
  (rotation + identity lineage), plan-07 the second (quotas + usage reporting on that
  identity). One phase directory, two plan files.
- Phase directory under the v16 phases layout (`.planning/<v16-phases>/82-<slug>/`), v14/v15
  `NN-<slug>` convention.
- Execution order: plan-04 steps 1-2 first (store versioning + rotate), then this plan's
  steps 1-2 (loader + report mode), then soft/hard (steps 3-4), then rollup + docs
  (steps 5-7). The report-only slice is independently shippable and is the phase's minimum
  bar.
- Phase verification: SUCCESS_METRICS become VERIFICATION.md checks; "enforce nothing in
  report-only mode" is a hard acceptance criterion from the locked scope.
