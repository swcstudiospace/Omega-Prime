# Phase 105 — Event Stream Core Library

## Outcome

Phase 105 is complete on `gsd/00046-event-stream`: Python + TypeScript event-stream libraries, Pulsar adapter, admin reconcile, CLI, Functions, and live IT. No Railway cutover, no VPS leaf, no ADLC wiring (106–108).

## Plans

| Plan | Summary |
|------|---------|
| 105-01 | CloudEvents 1.0 envelope, 48-type taxonomy + audit family, 12 Draft 2020-12 schema bundles, typed payloads, secure `*_FILE` config |
| 105-02 | Ports, memory adapter, SQLite outbox, idempotency ledger, SDK retry/DLQ, consumer runtime, observability, topology.yaml |
| 105-03 | TypeScript `@omega-prime/eventstream` SDK mirroring the contract (envelope, taxonomy, retry/DLQ, outbox, lazy Pulsar shape) |
| 105-04 | Native Pulsar asyncio adapter, REST admin, omega CLI, three process-runtime Functions, owned-secure CI IT, coverage ≥90% |

## Key deliverables

- `omega_prime/eventstream/` — full Python library (optional `eventstream` extra)
- `packages/eventstream-ts/` — strict ESM TypeScript SDK
- `deploy/pulsar/{standalone,functions_worker,bookkeeper}.conf` — static broker/worker templates (inactive-topic deletion off; journalSyncData=true)
- `.github/workflows/ci.yml` — separate `eventstream-it` job; verify matrix preserved
- `omega eventstream apply|status|tail` — lazy CLI registration

## Verification (105-04 final window)

| Gate | Result |
|------|--------|
| pytest eventstream + IT loopback | 306 passed, 2 skipped |
| Coverage total / outbox / idempotency | 90.12% / 91% / 91% |
| ruff / mypy / polyglot-check | green |

## Trackers

- Linear SPE-19588 (contract), SPE-19589 (library)
- Notion / graph: see `105-CONTEXT.md`

## Out of scope (next phases)

- 106 box hub + VPS leaf live feed
- 107 ADLC taxonomy wiring + timer retirement
- 108 ops/replay/DLQ + paid Railway cutover (stop before paid deploy)
