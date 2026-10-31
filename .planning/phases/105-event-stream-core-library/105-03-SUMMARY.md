---
phase: 105-event-stream-core-library
plan: "03"
subsystem: event-stream
tags: [typescript, cloudevents, pulsar, json-schema, vitest]
requires:
  - phase: 105-event-stream-core-library
    provides: CloudEvent contract, taxonomy, and 12 Draft 2020-12 schema bundles from plan 105-01
provides:
  - Strict ESM TypeScript event-stream SDK with memory delivery, taxonomy, retry/DLQ, outbox, and lazy Pulsar adapter
  - Mandatory Draft 2020-12 bundle-fixture contract gate
  - Broker-free tests for the pinned Pulsar 1.18.0 native API mapping
affects: [phase-105-04]
actuals:
  tokens: null
  tasks: 3
  commits: 0
tech-stack:
  added: [strict TypeScript ESM SDK, Ajv 2020 schema contract validation]
  patterns: [lazy optional-peer normalization, receipt-before-ack retry, event-woken process-local outbox]
key-files:
  created: []
  modified:
    - packages/eventstream-ts/eslint.config.js
    - packages/eventstream-ts/src/envelope.ts
    - packages/eventstream-ts/src/index.ts
    - packages/eventstream-ts/src/memory.ts
    - packages/eventstream-ts/src/outbox.ts
    - packages/eventstream-ts/src/ports.ts
    - packages/eventstream-ts/src/pulsar.ts
    - packages/eventstream-ts/test/envelope.test.ts
    - packages/eventstream-ts/test/outbox.test.ts
    - packages/eventstream-ts/test/pulsar.test.ts
    - packages/eventstream-ts/test/retry.test.ts
    - packages/eventstream-ts/test/schema-contract.test.ts
    - packages/eventstream-ts/README.md
key-decisions:
  - Keep the R-1a catalog at exactly 48 concrete types, with omega.audit.* registered separately as an open family.
  - Normalize both named exports and the CommonJS default-export shape used by pulsar-client 1.18.0 at the lazy adapter boundary.
  - Resolve each concrete schema URN through its bundle definition; add an Ajv alias only when that definition has no nested $id.
  - Preserve the process-local outbox and raw at-least-once guarantees; no Node consumers or durable ledger were added.
patterns-established:
  - Typed lint rules use TypeScript project service instead of disabling unsafe checks.
  - Native Pulsar behavior is exercised through typed broker-free fakes; no static peer import is present.
requirements-completed: []
coverage:
  - id: D1
    description: Immutable CloudEvent and event-woken memory publish/receive/ack path.
    verification:
      - kind: unit
        ref: packages/eventstream-ts/test/envelope.test.ts and test/memory.test.ts; npm run test
        status: pass
    human_judgment: false
  - id: D2
    description: Exact 48-type taxonomy and mandatory schema bundle/fixture drift gate.
    verification:
      - kind: unit
        ref: packages/eventstream-ts/test/schema-contract.test.ts; npm run test
        status: pass
    human_judgment: false
  - id: D3
    description: Retry/DLQ receipt ordering and process-local outbox boundaries.
    verification:
      - kind: unit
        ref: packages/eventstream-ts/test/retry.test.ts and test/outbox.test.ts; npm run test
        status: pass
    human_judgment: false
  - id: D4
    description: Lazy pinned-peer adapter configuration, native producer/consumer/reader mapping, and package gates.
    verification:
      - kind: unit
        ref: packages/eventstream-ts/test/pulsar.test.ts; npm run test
        status: pass
      - kind: other
        ref: npm run lint, npm run typecheck, npm run build, polyglot-check --no-daemon
        status: pass
    human_judgment: false
duration: not recorded (resumed worktree)
completed: 2026-10-23
status: complete
---

# Phase 105-03: Event Stream Core Library Summary

**Strict TypeScript Event Stream SDK with 48-type contract validation, event-woken memory delivery, retry/DLQ, outbox, and lazy Pulsar support.**

## Performance

- **Duration:** Not recorded; execution resumed after a killed session.
- **Started:** Exact start time not captured.
- **Completed:** 2026-10-23.
- **Tasks:** 3 plan tasks completed.
- **Files modified:** 13 package paths in this resumed repair; the plan's 25 package paths were already present on resume.

## Accomplishments

- Kept the package strict ESM with typed ESLint rules enabled through `projectService` and `tsconfigRootDir`; all lint findings were fixed without disabling typed rules.
- Strengthened CloudEvent traceparent validation, exported typed Pulsar acknowledgement ID/handle types, and retained event-woken in-memory delivery semantics.
- Made the contract gate require all 12 family bundles and exact `$defs` membership, resolve all 48 concrete schema URNs, and run embedded valid/invalid examples through Ajv 2020 and handwritten validators.
- Added outbox byte accounting for serialized event and publish metadata, atomic capacity/sequence coverage, and regression coverage that retry and DLQ publish failures leave source deliveries unacked.
- Adapted the native boundary to pulsar-client 1.18.0 producer, consumer, Reader, schema metadata, message getter, and acknowledgement APIs; accepted the CommonJS default-export shape while preserving lazy optional loading.
- Updated package documentation without implying the source catalog is authoritatively closed.

## Task Commits

No commits created. The user directed that the orchestrator commit the working-tree changes.

## Files Created/Modified

The 13 modified package paths are listed in the frontmatter. The other 12 paths in the plan manifest were already present and were left unchanged in this resumed repair. No Node consumers were added.

## Decisions Made

- R-1a supersedes stale catalog notes: the test oracle is the exact 48 names in DESIGN §5.3 plus separate open-family handling for `omega.audit.*`.
- Native `pulsar-client@1.18.0` exports through a CommonJS `module.exports` object; the loader now accepts the dynamic-import `default` object as well as named module exports.
- The bundle contract gate reuses nested `$id` values where present and registers a URN-to-JSON-Pointer alias only for definitions without an `$id`.
- The existing `eventstream` optional extra in `pyproject.toml` already existed, and the existing `otel` extra remains intact; neither was edited.

## Deviations from Plan

- The Python directory currently contains `envelope.py`, `taxonomy.py`, `config.py`, and `errors.py`, but no Python retry/backoff/outbox modules. Taxonomy membership and mappings were manually compared with Python `taxonomy.py`; runtime parity for retry/backoff/outbox could not be executed. No Python source changed, so Python Ruff, mypy, and pytest gates were not run.
- TypeScript rejects W3C traceparent version `ff` and all-zero trace/span IDs. Python 105-01 currently validates only the traceparent syntax regex and accepts those values; TypeScript follows the stricter W3C validity rule, so this is a deliberate cross-language validation difference.
- No actual native peer or Pulsar broker was installed or started. The pinned native API mapping is covered by broker-free native-shaped fakes; live registry compatibility and Reader roundtrip remain for phase 105-04.
- `.planning/STATE.md` was not changed; it records a separate stale v20 position and is outside this plan's package manifest/output scope.

## Verification

- `npm run lint` — pass.
- `npm run typecheck` (`tsc --noEmit`) — pass.
- `npm run test` — pass: 7 test files, 48 tests.
- `npm run build` — pass; TypeScript output and declarations generated.
- `polyglot-check --no-daemon` on the 13 modified package paths — 0 errors, 0 warnings in 12 checked files; README was skipped because no LSP is configured for Markdown.
- Timer/polling scan of package source — only the finite bounded `setTimeout` in `src/backoff.ts` was found; no scheduler, interval, polling loop, or sleep loop.
- Python checks were not applicable because no Python files changed.

## Issues Encountered

The first final lint/typecheck/build run exposed an incomplete schema-test type declaration, and the contract test exposed duplicate Ajv registration for definitions that already had nested `$id`s. The follow-up test then showed that not every definition has a nested `$id`. The gate now types bundles explicitly, verifies any existing `$id`, and creates an alias only when needed. Final package gates pass.

## User Setup Required

None for broker-free use. Native integration requires an explicit opt-in installation of the pinned `pulsar-client@1.18.0` peer in an isolated supported environment.

## Next Phase Readiness

Ready for phase 105-04 native Pulsar integration. Exercise a real pinned peer and broker for schema registry compatibility, Reader roundtrip, and auth-family encryption/decryption. The TS contract gate is green; no live broker behavior is claimed.

---
*Phase: 105-event-stream-core-library*
*Plan: 03*
*Completed: 2026-10-23*
