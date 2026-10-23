# Phase 105 — Event stream core library (Python + TypeScript) — Context

**Gathered:** 2026-10-23 (AEDT) · **Status:** discuss complete (smart_discuss, autonomous: recommended answers auto-accepted) · **Milestone:** v21 Event Stream
**Run:** `/gsd-autonomous` trailing keywords `orchestrate workflowz ultrathink` · **Spec:** `../104-event-stream-broker-decision-and-railway-spec/DESIGN.md` (§4–§9, §13–§16)
**Trackers:** Linear SPE-19588 (n3 contract), SPE-19589 (n4 library) · graph `ut-tnbq4x-7600742c`

<domain>
## Phase Boundary
Build the broker-agnostic event-stream library in Python (`omega_prime/eventstream/`) and TypeScript (`packages/eventstream-ts/`),
the Pulsar adapter, SQLite outbox, consumer runtime, retry/DLQ, idempotency ledger, admin REST reconcile of `topology.yaml`,
CLI (`apply`, `status`, `tail` skeleton), and a Pulsar Functions scaffold, with unit, property and integration tests.
No box hub service, no VPS, no Railway, no wiring into existing routines (106/107/108).
</domain>

<decisions>
## Implementation Decisions (Ove checkpoint 1, 2026-10-23 — defaults confirmed)
- D-105-01 Broker image `apachepulsar/pulsar:5.0.0@sha256:7222f3199a0b2418abde9ce0906862598415b19b494ae185db189938cfc43fd3`; rollback `4.0.14@sha256:d404b4db9b809cd4e8b65fc2120a97dbc024c222fc2d53041d900c7d5a5f1f2d`.
- D-105-02 Python SDK `pulsar-client==3.13.0` (asyncio API, wheels cp310–cp314) as an **optional extra** `eventstream`; the in-memory adapter has no third-party deps so the core suite runs without the broker. `pydantic>=2.13` and `httpx>=0.28` join the extra.
- D-105-03 TS SDK `pulsar-client@1.18.0` as an optional peer of `@omega-prime/eventstream`; SDK + tests only, no Node consumers yet (HITL-3 default).
- D-105-04 Integration tests are marked `eventstream_integration` and skip unless `OMEGA_EVENTSTREAM_IT_URL` is set; a separate CI job runs them against the pinned broker as a service container.
- D-105-05 Retry/DLQ implemented in the SDK (delayed republish to `omega/system/retry.<sub>`, explicit `dlq.<sub>`), not Pulsar's Java-only retry letter (DESIGN §6.4).
- D-105-06 Outbox relay and consumer loops are event-woken (asyncio.Event / awaited receive). Backoff waits are bounded I/O retries in `backoff.py` only. No interval timers anywhere (standing rule).
- D-105-07 Secrets only via `*_FILE` paths (0600 enforced); config repr redacts; tokens never in argv/env values/logs.
- D-105-08 mypy `--strict` on `omega_prime/eventstream`, ruff clean, TS `strict: true`.

### Claude's Discretion
Module internals, test fixture shape, exact metric names (must follow DESIGN §14 families).
</decisions>

<auto_answered>
| Q | Answer taken | Why |
|---|---|---|
| Hard vs optional pulsar dependency | optional extra `eventstream` | core install and existing CI stay broker-free; adapter imported lazily |
| Schema model generation | hand-written frozen pydantic models + JSON Schema files + drift test | avoids a codegen toolchain dep; drift test enforces single source |
| Where integration tests run | new CI job `eventstream-it` with service container | keeps `verify` matrix untouched |
| TS test runner | vitest | DESIGN §16 |
</auto_answered>

<plan_revision>
## Orchestrator resolution of planner blockers (2026-10-23 06:55 AEDT, autonomous per rule 5b)
The first planner pass flagged two blockers; both are resolved here and supersede the `[blocked]` notes:
- R-1 **Type count.** The catalog is exactly the 48 concrete types enumerated in DESIGN §5.3 plus the open `omega.audit.*` family (one family schema, explicit registration). No extra types are invented. (R-1a: the earlier "45" was an orchestrator miscount; the planner's independent 48 count is correct and authorized.)
- R-2 **Schema layout + 25-file cap.** JSON Schema 2020-12 stays the single source, bundled one document per family:
  `omega_prime/eventstream/schemas/<family>.v1.json` with one `$defs/<type>` entry per concrete type and `$id` = `urn:omega:schema:<family>:1`.
  The per-type `dataschema` stays `urn:omega:schema:<type>:1` and resolves to `<family>.v1.json#/$defs/<type>`. Families (12 files):
  `envelope, sdlc.pr, sdlc.review, ci, auth, job, sandbox, task, mem, control, hitl, audit`. Each bundle carries `examples` (valid) and
  `x-omega-invalid-examples` (invalid) per type for contract tests in both languages.
- R-3 **Plan split** so every plan is ≤ 25 files: 105-01 contract (wave 1), 105-02 runtime (wave 2), 105-03 TypeScript (wave 2, after the
  bundles exist), 105-04 Pulsar adapter + admin + CLI + functions + integration + CI (wave 3).
- R-4 Python tests live in `omega_prime/tests/eventstream/` (existing pytest testpaths), not inside the package.
- R-5 Redaction helper is `omega_prime.credentials.redact.redact_value` (DESIGN's dashboard path is stale).
- R-6 Broker config must also set `brokerDeleteInactiveTopicsEnabled=false` and `journalSyncData=true` (observed defaults in the pinned 5.0.0 image are `true`/`false`, which would delete idle provisioned topics and skip fsync).
</plan_revision>
