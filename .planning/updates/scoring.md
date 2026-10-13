# v16 Update Scoring

Issue: SPE-10597 (https://linear.app/swcstudio/issue/SPE-10597)
Evidence: v16 recon evidence log (verified 2026-10-13, HEAD df14512), rows R1–R8. Candidates: .planning/candidates.md (SPE-10592); U1–U7 map 1:1 to C1–C7.

Scale: impact and risk-reduction — 5 highest; effort — 5 largest build; dependency-risk — 5 most external coupling. Rank order U1–U7 is the user's explicit priority (U1 first — the sidecar concern); scores size effort and phase placement, they do not reorder rank.

## Score table

| Rank | Update | Candidate | Impact | Risk-reduction | Effort | Dependency-risk |
|------|--------|-----------|--------|----------------|--------|-----------------|
| U1 | Purple-team refresh + upgrade-resilience pipeline | C1 | 5 | 5 | 3 | 3 |
| U2 | Protected release pipeline: staged rollout + rollback | C2 | 5 | 4 | 3 | 3 |
| U3 | Telemetry export + SLO alerting | C3 | 4 | 4 | 2 | 2 |
| U4 | Automated token rotation + overlap windows | C4 | 4 | 5 | 3 | 1 |
| U5 | OpenShell sandboxed execution + verification-bound receipts | C5 | 4 | 5 | 5 | 4 |
| U6 | Live evals + regression trend tracking | C6 | 3 | 3 | 2 | 4 |
| U7 | Per-user token quotas + usage reporting, soft-then-hard | C7 | 4 | 4 | 2 | 2 |

## Justifications

### U1 — Purple-team refresh + upgrade-resilience pipeline (C1)
- Impact 5: closes all seven verified upgrade-resilience gaps (R6 a-g) on the only purple-team surface (submodule `redteam`, 11 tempest_* tools via mcp_server.py:747-776).
- Risk-reduction 5: pin `831a6b0` is ~2 releases behind upstream `06c11a5` (v1.2+v1.3 published) and adapter drift breaks silently (R6 a,f); the consistency triangle (setup_check.py:125-138) stays intact across bumps.
- Effort 3: mechanical parts copy existing patterns (.github/dependabot.yml ecosystems, pip-only supply-chain.yml, VENDOR.md:84-89 procedure); pack equivalence, drift detection, and drift→DEGRADED are new build (R6 e,f,g).
- Dependency-risk 3: depends on upstream purple-team.git release cadence (observed v1.2→v1.3); everything else in-repo (R6).

### U2 — Protected release pipeline: staged rollout + rollback (C2)
- Impact 5: image publication is DEAD-CLOSED (grokbot-image.yml publish job requires nonexistent protected env `grokbot-image-publish`, header lines 1-20) and NO CD to any host exists (R2) — this is the whole delivery path.
- Risk-reduction 4: staging-first promotion gate with approval/digest evidence already exists (deploy.py:1570-1576); staged rollout + rollback completes the never-exercised publish leg (R2).
- Effort 3: workflows, protected environment, and rollback wiring on the existing deterministic renderer (R2).
- Dependency-risk 3: requires GitHub protected-environment setup and human approval gates outside repo code (R2).

### U3 — Telemetry export + SLO alerting (C3)
- Impact 4: /metrics is pull-only with no export, alerting, SLOs, dashboards, or shipping (R5 MISSING); export plus SLO alerting closes the gap without changing the data model (zero-dep registry metrics.py).
- Risk-reduction 4: failures are invisible off-host today; the surface is already auth-guarded (verify.py:557-567), so export extends a secured path.
- Effort 2: standard series already emitted (omega_http_requests_total, omega_http_request_duration_seconds, omega_tool_denials_total{code}; MetricsMiddleware/MetricsInterceptor, R5) — export is additive.
- Dependency-risk 2: OTLP/collector endpoint is an external config choice; no code coupling (R5).

### U4 — Automated token rotation + overlap windows (C4)
- Impact 4: rotation is MANUAL CLI today (R4); automation removes standing operational security toil on the only credential path.
- Risk-reduction 5: builds directly on verified primitives — plaintext-once mint, SHA-256-only 0600 store, atomic writes, fail-closed live reload, revoke/TTL (tokens.py:256-285,345-426,495-523) — and overlap windows close the residual secrets gap left by rejected managed injection (C8).
- Effort 3: scheduler, overlap/verification semantics, and rotation alerts are new; store primitives are not (R4).
- Dependency-risk 1: fully local FileTokenStore, no external service in the loop (R4).

### U5 — OpenShell sandboxed execution + verification-bound receipts (C5)
- Impact 4: nothing runs sandboxed (hand-maintained untested sandbox-policy.yaml, SEED-006) and receipts do not bind to executed commands (tools/quality.py:148-155,240-241, SEED-012) (R7).
- Risk-reduction 5: sandboxing execution and binding receipts to commands is the core execution-trust gap against an enterprise bar (R7).
- Effort 5: two dormant, unimplemented seeds; the policy file is untested and no harness exists (R7) — largest build in the milestone.
- Dependency-risk 4: depends on OpenShell surface behavior and policy correctness with no existing test path (R7).

### U6 — Live evals + regression trend tracking (C6)
- Impact 3: deterministic runner (omega_prime/evals/runner.py; golden.json + redteam.json) and hermetic PyRIT battery (tests/test_pyrit_target.py) already gate ci.yml verify (R3); live evals extend rather than create coverage.
- Risk-reduction 3: no result history or trend tracking and no nightly run (R3); trends catch slow regressions per-run gates miss.
- Effort 2: runner exists; add live-model path, history store, trend report (R3).
- Dependency-risk 4: live-model evals need external LLM access, cost, and stability the repo does not control (R3: NO live-model/LLM-judge evals today).

### U7 — Per-user token quotas + usage reporting, soft-then-hard (C7)
- Impact 4: zero per-user quota, token budget, or usage-reporting enforcement anywhere in omega_prime/ (R8 scout sweep) — first enforcement path for multi-user exposure.
- Risk-reduction 4: interceptors already count calls and denials (omega_tool_denials_total{code}, R5) but enforce nothing (R8); soft-then-hard rides existing instrumentation.
- Effort 2: enforcement point exists (MetricsInterceptor/ASGI middleware, R5); quota ledger, soft/hard transition, and usage report are additive (R8).
- Dependency-risk 2: quota identity keys off the existing token store (R4); no external dependency; per-token vs per-user mapping is an internal design decision.

## Staging map

| Phase | Updates | Load | Rationale |
|-------|---------|------|-----------|
| 79 | U1 | 1 | User's top concern leads the milestone; self-contained (R6), no dependency on later updates. |
| 80 | U2 | 1 | Release path second; unblocks the DEAD-CLOSED publish leg (R2) before anything depends on deployable images. |
| 81 | U3 | 1 | Telemetry export/alerting live before execution-trust work, so later phases run with signal (R5). |
| 82 | U4 + U7 | 2 | Same domain: both key off the token store (R4) and interceptor counts (R5, R8); both low dependency-risk (D=1/2), pair cleanly. |
| 83 | U5 | 1 | Largest build (E=5) gets a dedicated phase; benefits from U3 alerting already shipping (R7). |
| 84 | U6 | 1 | Live evals + trends last: external LLM access (D=4) and trend baselines validate the accumulated changes (R3). |

Coverage check: 7 updates across 6 phases; max load 2 (Phase 82); ≤2 updates per phase holds; every U1–U7 mapped exactly once.
