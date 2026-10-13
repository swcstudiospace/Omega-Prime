# Requirements: v16 Enterprise Hardening and Continuous Verification

Approved 2026-10-13. Execution: autonomous GSD phase loop under `/gsd-autonomous` (user instruction: continue without validation; grey areas resolved by documented defaults in `.planning/decisions.md`). Evidence base: `.planning/recon-evidence.md` (verified 2026-10-13, HEAD df14512).

## Purple-team sidecar

- [x] **SIDE-01**: Maintainer receives automated pin freshness for the T3MP3ST submodule: Dependabot `gitsubmodule` PRs, CI provenance/equivalence/surface-drift checks, and a Node-dependency supply-chain audit, so constant upstream upgrades cannot silently rot the sidecar. (U1, plan-01)
- [x] **SIDE-02**: The sidecar fails closed: no start without a present, unexpired rules-of-engagement artifact (BLOCKED, nonzero exit), a working kill switch, and logged IDLE/RUNNING/DEGRADED/BLOCKED/ERROR transitions. Ships BLOCKED per user decision 2026-10-13 — no live engagement until the user signs the artifact. (U1, plan-01)
- [x] **SIDE-03**: Adversarial coverage is versioned data with detection validation and a findings pipeline: cases tagged technique/severity/expected behavior, passes require observed detection matching expectation, findings dedupe into tracked work items, and version drift reports DEGRADED. (U1, plan-01)

## Release

- [x] **REL-01**: Maintainer can publish the hardened image through the `grokbot-image-publish` protected environment via manual `workflow_dispatch`, with checksum/image-id binding and retained evidence; publication stays manual and default-off. (U2, plan-02)
- [x] **REL-02**: A staged rollout path with `deploy.py check()` staging-first promotion evidence and a documented, exercised one-step rollback (previous digest) exists. (U2, plan-02)

## Observability

- [x] **OBS-01**: Optional OTLP telemetry export ships as an extra dependency, off by default, leaving the zero-dependency `/metrics` surface and its auth guarantees unchanged; export failure never degrades local metrics/logging/audit. (U3, plan-03)
- [x] **OBS-02**: SLOs for serve availability, tool-call p99 latency and denial-rate budget are defined with alert rules, and a nightly scheduled readiness-profile run alerts on regression. (U3, plan-03)

## Token lifecycle and cost

- [x] **TOK-01**: Host-token rotation is automated: replacement minting with a configurable overlap window, cron-heartbeat scheduling, audit-chain rotation events, live-reload honored, old token dead at window end. (U4, plan-04)
- [x] **TOK-02**: Per-caller usage quotas are enforced soft-then-hard (report-only → warnings → hard caps) with usage reporting visible through `/metrics`; report-only mode enforces nothing. (U7, plan-07)

## Sandboxed execution

- [x] **SAND-01**: The OpenShell sandbox policy is generated from the seat policy (generated-and-tested replaces hand-maintained), with writable paths matching Omega home/root defaults. (U5, plan-05)
- [x] **SAND-02**: Verification and execute paths run inside OpenShell sandboxes where available; quality receipts bind to the recorded executed command and exit status; sandbox-unavailable outcomes are explicitly degraded, never silent passes. (U5, plan-05)

## Evaluation

- [x] **EVAL-01**: Scored rubric eval cases run beside the deterministic suite with persistent per-case history showing trends. (U6, plan-06)
- [x] **EVAL-02**: A nightly scheduled eval workflow surfaces regressions; the deterministic step remains the blocking push/PR gate; any live-model judge requires an explicit flag and makes no network calls without it. (U6, plan-06)

## Out of scope

- Live purple-team engagements without the user-signed rules-of-engagement artifact (ships BLOCKED).
- Testing, scanning or probing anything other than Omega Prime's own assets; no third-party targets.
- Replacing the bot's framework, model provider or hosting platform; no Railway resource changes.
- Native Bot palette / MCP prompts+resources (SEED-010), Agent Swarm bridge (SEED-003), Substrate transport fixes (SEED-002) — v17 candidates (decision log 2026-10-13).
- The six live 77.1 Grok Bot import UAT rows — user-side, deferred until after the next Grok Bot upload (STATE.md Deferred Verification).
- Automatic publication or deployment: publication remains manual behind the protected environment.

## Traceability

| Requirement | Phase | Update | Status |
|-------------|-------|--------|--------|
| SIDE-01 | 79 | U1 | Complete |
| SIDE-02 | 79 | U1 | Complete |
| SIDE-03 | 79 | U1 | Complete |
| REL-01 | 80 | U2 | Complete |
| REL-02 | 80 | U2 | Complete |
| OBS-01 | 81 | U3 | Complete |
| OBS-02 | 81 | U3 | Complete |
| TOK-01 | 82 | U4 | Complete |
| TOK-02 | 82 | U7 | Complete |
| SAND-01 | 83 | U5 | Complete |
| SAND-02 | 83 | U5 | Complete |
| EVAL-01 | 84 | U6 | Complete |
| EVAL-02 | 84 | U6 | Complete |

Coverage: 13 requirements, each mapped once to phases 79-84 and update plans plan-01..07.
