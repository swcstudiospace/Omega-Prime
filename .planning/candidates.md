# v16 Candidate Inventory

Issue: SPE-10592 (https://linear.app/swcstudio/issue/SPE-10592)
Evidence: v16 recon evidence log (verified 2026-10-13, HEAD df14512), rows R1–R8. `GROUNDED` tags cite rows and file:line from that log; `HYPOTHESIS` = no recon row supports the candidate.
Locked user decisions: purple-team sidecar ships BLOCKED (no live engagements until the user signs a rules-of-engagement artifact); milestone v16 executes all 7 selected updates.

## Selected

### C1 — Purple-team refresh + upgrade-resilience pipeline
- Description: Submodule refresh path plus CI/policy coverage so the `redteam` pin cannot silently rot: checkout-vs-pin check, Dependabot `gitsubmodule` ecosystem, redteam Node-dep audit, documented bump procedure, pack↔upstream equivalence check, adapter drift detection, drift→DEGRADED reporting.
- Tag: GROUNDED — R6 a–g (pin `831a6b0` ~2 releases behind upstream `06c11a5` with v1.2+v1.3 published; no pin check; only pip/actions/docker in .github/dependabot.yml; pip-only supply-chain.yml; hand-edited VENDOR.md:84-89; adapter hand-mirrors redteam/src/mcp-server.ts; no RoE/state machine/findings pipeline).
- Disposition: SELECTED
- Reason: User's explicit sidecar concern; closes all seven verified upgrade-resilience gaps (R6 a-g). Live engagements stay BLOCKED pending signed RoE (user decision); this ships refresh + resilience only.

### C2 — Protected release pipeline: staged rollout + rollback
- Description: Make image publication real and reversible: stand up the missing protected publish environment and wire staged rollout + rollback on top of the existing staging-first promotion gate.
- Tag: GROUNDED — R2 (grokbot-image.yml publish job DEAD-CLOSED: requires protected env `grokbot-image-publish` which does not exist, header lines 1-20; NO CD to any host; staging-first promotion gate with approval/digest evidence deploy.py:1570-1576).
- Disposition: SELECTED
- Reason: The delivery path is unverifiable end-to-end until publication is exercised; highest-leverage infrastructure gap.

### C3 — Telemetry export + SLO alerting
- Description: Add OTLP (and/or statsd) export plus SLO definitions and alert rules over the existing Prometheus series; keep pull-only /metrics.
- Tag: GROUNDED — R5 (zero-dep registry metrics.py; MISSING: no OTLP/OpenTelemetry/statsd export, no alerting, no SLOs, no dashboards, no shipping; verify.py:557-567 asserts unauthenticated /metrics 401s).
- Disposition: SELECTED
- Reason: Observability is produced but never leaves the host; cheapest large risk-reduction in the set.

### C4 — Automated token rotation + overlap windows
- Description: Scheduled rotation with overlap windows, key versioning, and rotation alerts on the existing FileTokenStore primitives.
- Tag: GROUNDED — R4 (rotation is MANUAL CLI; no automated rotation job, no overlap windows, no key versioning, no rotation alerts; primitives at tokens.py:256-285,345-426,495-523).
- Disposition: SELECTED
- Reason: Covers the residual secrets gap after rejecting managed injection; builds only on verified store behavior.

### C5 — OpenShell sandboxed execution + verification-bound receipts
- Description: Execute tool commands inside an OpenShell sandbox driven by a tested sandbox-policy.yaml, and bind verification receipts to the exact executed commands.
- Tag: GROUNDED — R7 (SEED-006: hand-maintained untested sandbox-policy.yaml, nothing runs sandboxed; SEED-012: receipts not bound to executed commands, tools/quality.py:148-155,240-241).
- Disposition: SELECTED
- Reason: Execution trust is the deepest unimplemented enterprise bar; largest build, gets a dedicated phase.

### C6 — Live evals + regression trend tracking
- Description: Live-model/LLM-judge eval runs plus stored results and regression trend reporting, on a nightly cadence.
- Tag: GROUNDED — R3 (deterministic only: omega_prime/evals/runner.py, golden.json + redteam.json, PyRIT battery tests/test_pyrit_target.py; NO live-model/LLM-judge evals; NO result history/trend tracking; push/PR only, no nightly).
- Disposition: SELECTED
- Reason: Current gates catch per-commit breaks, not drift across commits.

### C7 — Per-user token quotas + usage reporting, soft-then-hard
- Description: Per-user/token quota enforcement — soft warnings then hard denial — plus a usage report, riding the existing interceptor counters.
- Tag: GROUNDED — R8 (no per-user quota, token budget, or usage-reporting enforcement anywhere in omega_prime/; metrics interceptors count calls but enforce nothing).
- Disposition: SELECTED
- Reason: First cost/abuse enforcement path; low dependency-risk given existing instrumentation (R5: omega_tool_denials_total{code}, MetricsInterceptor).

## Rejected

### C8 — Managed secrets injection
- Description: Broker-injected runtime secrets for tool/credential access.
- Tag: GROUNDED — R4.
- Disposition: REJECTED
- Reason: Hashed 0600 token store, allowlisted-credentials broker, redaction, and SecretLeakError guard already exist (tokens.py:256-285; credentials/broker.py; credentials/redact.py; grokbot/secret_guard.py); the residual gap is rotation, covered by C4.

### C9 — Abuse filters
- Description: Content/behavior abuse-filtering layer ahead of tool execution.
- Tag: GROUNDED — R5, R4.
- Disposition: REJECTED
- Reason: Duplicates C7 denial accounting (omega_tool_denials_total{code}) and existing policy gates (policy-allowlisted credential broker; approval-gated tempest_mission_start, docs/tool-catalog.md:1246-1249).

### C10 — Standalone feature-flag plane
- Description: Dedicated feature-flag service/plane for rollout control.
- Tag: GROUNDED — R2.
- Disposition: REJECTED
- Reason: Rollout control already exists as the staging-first promotion gate inside the deploy pipeline (deploy.py:1570-1576); a separate plane adds surface without closing a gap.

### C11 — Session/PII management
- Description: Session tracking and PII-handling controls for conversations.
- Tag: HYPOTHESIS — no recon row.
- Disposition: REJECTED
- Reason: No verified evidence of a gap or of existing coverage; rejected for v16, revisit only after recon.

### C12 — Data-retention controls
- Description: Retention/TTL and purge controls for logs, audit NDJSON, and event mirrors.
- Tag: HYPOTHESIS — blocked on data-flow recon.
- Disposition: REJECTED
- Reason: Scoping requires a data-flow map absent from R1–R8; rejected for v16, parked pending that recon.

## Backlog (2 groups)

### Group: v17 product surface

### C13 — Native Bot palette / MCP prompts+resources
- Description: Complete the native slash-command palette and serve MCP prompts/resources.
- Tag: GROUNDED — R7 (SEED-010: slash commands half-shipped in v13 turn-path; MCP prompts/resources NOT served).
- Disposition: BACKLOG
- Reason: Product surface, not enterprise bar; v17 candidate.

### Group: dormant seeds

### C14 — Swarm bridge (SEED-003)
- Description: AgentSwarm bridge integration.
- Tag: GROUNDED — R7 (SEED-003 dormant).
- Disposition: BACKLOG
- Reason: No v16 trigger; dormant seed.

### C15 — Substrate transport fixes (SEED-002)
- Description: Agent-substrate native transport fixes.
- Tag: GROUNDED — R7 (SEED-002 dormant).
- Disposition: BACKLOG
- Reason: No v16 trigger; dormant seed.

## Verify

- 15 candidates (≥12 required).
- Exactly 7 SELECTED: C1–C7.
- 5 REJECTED: C8–C12.
- 2 backlog groups (3 BACKLOG entries: C13; C14+C15) — count as rejected-for-v16.
- Every GROUNDED tag cites R-rows with file:line from the v16 recon evidence log; C11/C12 carry no invented evidence.
