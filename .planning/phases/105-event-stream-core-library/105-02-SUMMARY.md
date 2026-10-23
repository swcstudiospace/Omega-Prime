---
phase: 105-event-stream-core-library
plan: '02'
subsystem: eventstream
tags: [python, pydantic-v2, cloudevents, sqlite, retry, consumer, topology]
requires:
  - phase: 105-01
    provides: [immutable CloudEvent and payload contracts, schema bundles, config and errors]
provides:
  - broker-neutral async ports and deterministic memory transport
  - caller-transactional WAL/FULL SQLite outbox and owner-token idempotency ledger
  - durable retry/DLQ routing, bounded consumers, injected observability and topology inventory
affects: [105-04-event-stream-pulsar-adapter]
actuals:
  tokens: null
  tasks: 3
  commits: 0
tech-stack:
  added: []
  patterns:
    - explicit post-commit notification for caller-owned transactions
    - durable destination receipt before source acknowledgement
    - event-driven wakeups and injected clocks; no timer service
key-files:
  created:
    - omega_prime/eventstream/ports.py
    - omega_prime/eventstream/backoff.py
    - omega_prime/eventstream/retry.py
    - omega_prime/eventstream/idempotency.py
    - omega_prime/eventstream/outbox.py
    - omega_prime/eventstream/consumer.py
    - omega_prime/eventstream/observability.py
    - omega_prime/eventstream/adapters/__init__.py
    - omega_prime/eventstream/adapters/memory.py
    - omega_prime/eventstream/topology.yaml
    - omega_prime/tests/eventstream/test_ports.py
    - omega_prime/tests/eventstream/test_memory.py
    - omega_prime/tests/eventstream/test_runtime_smoke.py
    - omega_prime/tests/eventstream/test_outbox.py
    - omega_prime/tests/eventstream/test_outbox_crashes.py
    - omega_prime/tests/eventstream/test_idempotency.py
    - omega_prime/tests/eventstream/test_runtime_security.py
    - omega_prime/tests/eventstream/test_retry.py
    - omega_prime/tests/eventstream/test_consumer.py
    - omega_prime/tests/eventstream/test_observability.py
    - omega_prime/tests/eventstream/test_topology.py
  modified:
    - omega_prime/eventstream/envelope.py
key-decisions:
  - "Keep native Pulsar optional; the memory transport uses standard-library async primitives and existing immutable values."
  - "Use caller-controlled SQLite transactions with explicit post-commit notification; the outbox never commits an external transaction."
  - "Preserve the exact 48 DESIGN §5.3 types plus explicit open omega.audit.* registration; per-type schema URIs resolve into family bundles."
patterns-established:
  - "Only acknowledge retry/DLQ sources after a matching durable destination receipt and successful owner-token release."
  - "Use awaited receives, Events, and injected clocks rather than periodic polling or scheduled routines."
requirements-completed: []
coverage:
  - id: D1
    description: "Async port and deterministic memory transport behavior, including read, ack, cancellation, redelivery and keyed ordering."
    verification:
      - kind: unit
        ref: "omega_prime/tests/eventstream/test_ports.py and test_memory.py"
        status: pass
      - kind: integration
        ref: "omega_prime/tests/eventstream/test_runtime_smoke.py::test_publish_read_ack"
        status: pass
    human_judgment: false
  - id: D2
    description: "Caller-transactional outbox, crash recovery, idempotency CAS, redaction and once-only local effects."
    verification:
      - kind: unit
        ref: "omega_prime/tests/eventstream/test_outbox.py, test_outbox_crashes.py, test_idempotency.py, test_runtime_security.py"
        status: pass
      - kind: integration
        ref: "omega_prime/tests/eventstream/test_runtime_smoke.py::test_transaction_to_effect_once"
        status: pass
    human_judgment: false
  - id: D3
    description: "Retry/DLQ, bounded consumer lifecycle, observability and declarative broker topology."
    verification:
      - kind: unit
        ref: "omega_prime/tests/eventstream/test_retry.py, test_consumer.py, test_observability.py, test_topology.py"
        status: pass
    human_judgment: false
duration: null
completed: 2026-10-22
status: complete
---

# Phase 105: Event Stream Core Library Summary

**Broker-free event-stream runtime with a durable SQLite outbox, token-guarded idempotency, retry/DLQ routing, bounded consumers, telemetry hooks and topology declarations.**

## Performance

- **Duration:** Not measured; resumed run.
- **Started:** Not captured for the resumed run.
- **Completed:** 2026-10-22T23:16:00Z (final verification window).
- **Tasks:** 3 plan tasks completed.
- **Files changed:** 22 code/test paths: all 21 plan paths plus the justified `envelope.py` correction.

## Accomplishments

- Added async Publisher, Subscriber, EventBus and AdminPort contracts and a deterministic memory adapter with individual ack, reader separation, redelivery, per-key ordering and injected-clock delayed delivery.
- Added caller-transactional SQLite outbox persistence with WAL/FULL, stable producer identity, durable sequence/watermark state, event-woken relay, pressure limits, terminal rejection and secret redaction; added owner-token CAS idempotency.
- Added finite decorrelated-jitter creation retry, durable retry/DLQ and singleton handoff paths, bounded TaskGroup consumers, injected metrics/tracing/export, and declarative topology with the exact 48 concrete event types plus the open audit family.
- Added recovery, security, consumer, topology and runtime tests. The full eventstream suite passed: **200 passed**. The two named real runtime smoke paths passed: **2 passed**.
- The `eventstream` optional extra was already present in `pyproject.toml`; no dependency metadata changed and the existing `otel` extra remains untouched.

## Task Commits

No commits created by instruction. Changes remain in the worktree for the orchestrator to verify and commit.

## Files Created/Modified

- Runtime: `omega_prime/eventstream/ports.py`, `backoff.py`, `retry.py`, `idempotency.py`, `outbox.py`, `consumer.py`, `observability.py`, `adapters/__init__.py`, `adapters/memory.py`, and `topology.yaml`.
- Tests: `omega_prime/tests/eventstream/test_ports.py`, `test_memory.py`, `test_runtime_smoke.py`, `test_outbox.py`, `test_outbox_crashes.py`, `test_idempotency.py`, `test_runtime_security.py`, `test_retry.py`, `test_consumer.py`, `test_observability.py`, and `test_topology.py`.
- `omega_prime/eventstream/envelope.py` — normalize Pydantic v2 payload-validation failures at the contract and byte-decoding boundaries.
- `.receipts/bot-00-omega-prime/105-02-event-stream-core-library.json` — verification evidence.
- `.planning/phases/105-event-stream-core-library/105-02-SUMMARY.md` — this summary.

## Decisions Made

- Keep broker SDK and telemetry implementations outside the broker-free runtime; no broker was provisioned or contacted.
- Use an explicit post-commit notification seam for externally owned SQLite transactions; append never commits the caller's connection.
- Keep raw delivery at-least-once. Exactly-once observable local effects rely on transaction-coupled ledger completion; external effects require event-id idempotency.
- Preserve the schema contract: per-type `dataschema` URI is `urn:omega:schema:<full-type>:1`, resolved through the family bundle definition.

## Deviations from Plan

### Auto-fixed issue

**[Rule 1 - Bug] Pydantic v2 validation errors escaped the CloudEvent contract wrapper**
- **Found during:** Task 1 runtime smoke.
- **Issue:** Pydantic `ValidationError` was not included in the payload/byte-decoding error boundaries, so malformed payloads escaped the intended `ContractError` surface.
- **Fix:** Added `ValidationError` handling in `envelope.py`; clarified direct model-validation expectations and retained `ContractError` for byte-decoding failures.
- **Files modified:** `omega_prime/eventstream/envelope.py`, `omega_prime/tests/eventstream/test_runtime_smoke.py`.
- **Verification:** Full pytest, both runtime smoke paths, Ruff, strict mypy and Pyright all passed.
- **Commit:** None; user delegated commits to the orchestrator.

### Coverage tooling unavailable

- The plan's pytest-cov invocation failed with exit code 4 because pytest did not recognize `--cov`; `/workspace/.venv-00046/bin/python -m coverage --version` failed because `coverage` is not installed. No package was installed and no package metadata was changed.
- Standard-library `trace` reran all 200 tests and reported 100% statement coverage for `backoff`, `consumer`, `idempotency`, `observability`, `outbox`, `ports` and `retry`; it did not report `adapters.memory`. Therefore the plan's canonical 90% coverage threshold for all targeted modules remains **unverified**.

**Total deviations:** 1 correctness fix in a 105-01 contract file; 1 environment-limited verification gap. **Impact:** runtime behavior and all user-required quality gates pass; complete pytest-cov coverage remains for the orchestrator to verify when the dependency is available.

## Issues Encountered

- The resumed implementation had missing outbox initialization and test-double/type errors. Root causes were corrected; no behavior was suppressed to pass tests.
- No native Pulsar broker integration was run; that remains phase 105-04 scope.

## User Setup Required

None for the broker-free library. Native broker configuration is downstream work.

## Next Phase Readiness

The ports and `topology.yaml` are ready for read-only consumption by phase 105-04. Native Pulsar adapter, broker provisioning, secure bootstrap and registry round-trip remain unverified downstream responsibilities.

---
*Phase: 105-event-stream-core-library*
*Completed: 2026-10-22*

## Orchestrator verification (2026-10-23 10:30 AEDT)

- Resumed run (first session was killed mid-plan); executor: omp `openai-codex/gpt-6-luna`, verdict omp-ok.
- Hypothesis found `test_webhook_ids_roundtrip_without_ulid_restriction` failing for secret-shaped ids (e.g. `Bearer 0`): the envelope correctly refuses ids the redactor would rewrite (fail-closed). Root cause was the test's over-broad precondition, not the runtime. The property test now `assume`s non-redactable ids, and a new explicit test pins that secret-shaped ids raise `ContractError`.
- Gates: ruff + format clean; mypy --strict clean (14 files); pyright via `agentos lsp` 0 errors (22 files); eventstream 202 passed (3 consecutive runs); full repo suite 5537 passed, 20 skipped.
- Coverage (pytest-cov now installed): `omega_prime.eventstream` total **89%** (observability/ports 100%, consumer 94%, memory adapter 90%, outbox 85%, retry 86%, idempotency 80%). The plan's 90% target is 1 point short; tracked as a follow-up for 105-04, whose real-broker tests exercise the remaining outbox/idempotency branches.
