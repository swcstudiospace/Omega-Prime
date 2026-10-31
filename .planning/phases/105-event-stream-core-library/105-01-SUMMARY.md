---
phase: 105-event-stream-core-library
plan: '01'
subsystem: eventstream
tags: [python, pydantic-v2, cloudevents, json-schema, pulsar]
requires:
  - phase: 104-event-stream-broker-decision-and-railway-spec
    provides: Authoritative CloudEvents, taxonomy, routing, configuration, and retry/DLQ design
provides:
  - Immutable CloudEvents 1.0 envelope and broker-neutral publish, receipt, and delivery contracts
  - Exact 48-type DESIGN §5.3 catalog, open omega.audit.* family, and twelve Draft 2020-12 schema bundles
  - Typed payload models, package-local schema resolution, redaction, and secure event-stream configuration
  - Optional eventstream dependency extra and strict eventstream type-check metadata
affects: [105-02, 105-03, 105-04]
# Approximate chars/4 across the implementation and execution artifacts; not a harness token count.
actuals:
  tokens: 45000
  tasks: 3
  commits: 0
tech-stack:
  added:
    - "pydantic>=2.13,<3"
    - "pulsar-client==3.13.0"
    - "httpx>=0.28,<1"
    - jsonschema
    - hypothesis
  patterns:
    - frozen-pydantic-contracts
    - package-resource-json-schema
    - "*_FILE-credential-loading"
    - redaction-before-serialization
key-files:
  created:
    - omega_prime/eventstream/envelope.py
    - omega_prime/eventstream/taxonomy.py
    - omega_prime/eventstream/config.py
    - omega_prime/eventstream/errors.py
    - omega_prime/eventstream/schemas/envelope.v1.json
    - omega_prime/eventstream/schemas/control.v1.json
    - omega_prime/tests/eventstream/test_envelope.py
    - omega_prime/tests/eventstream/test_taxonomy.py
    - omega_prime/tests/eventstream/test_schemas.py
    - omega_prime/tests/eventstream/test_config.py
  modified:
    - pyproject.toml
key-decisions:
  - "Followed CONTEXT R-1a: exactly the 48 DESIGN §5.3 concrete types plus the open omega.audit.* family; stale unresolved-inventory text in the plan was superseded."
  - "Payload and envelope boundaries remain broker-free; Pulsar is declared only in the optional eventstream extra."
  - "JSON Schema format assertions use local deterministic RFC3339 and URI-reference checkers in tests; no optional format package is required."
patterns-established:
  - "CloudEvent and transport values freeze nested mappings and sequences; field serializers expose JSON-compatible copies."
  - "Schema URNs resolve only to packaged Draft 2020-12 family bundles; payload keys derive from validated identity."
  - "Secret files are reopened and checked by descriptor for regular-file type and exact 0600 mode; errors and reprs avoid credential contents."
requirements-completed: []
coverage:
  - id: D1
    description: CloudEvents 1.0 codec, immutable transport values, redaction, and acknowledgement contract
    verification:
      - kind: unit
        ref: "/workspace/.venv-00046/bin/python -m pytest omega_prime/tests/eventstream -q"
        status: pass
    human_judgment: false
  - id: D2
    description: Forty-eight concrete types, open audit family, and twelve schema/model bundles
    verification:
      - kind: unit
        ref: "/workspace/.venv-00046/bin/python -m pytest omega_prime/tests/eventstream -q"
        status: pass
    human_judgment: false
  - id: D3
    description: Safe configuration, secure credential-file lifecycle, and redaction
    verification:
      - kind: unit
        ref: "/workspace/.venv-00046/bin/python -m pytest omega_prime/tests/eventstream -q"
        status: pass
    human_judgment: false
  - id: D4
    description: Optional dependency and strict type-check metadata
    verification:
      - kind: other
        ref: "/workspace/.venv-00046/bin/python -m mypy --strict omega_prime/eventstream"
        status: pass
    human_judgment: true
    rationale: "The optional Pulsar dependency and distributable wheel were not installed or built in this contract-only wave; review metadata before release."
duration: not tracked
completed: 2026-10-23
status: complete
---
# Phase 105: Event Stream Core Library Summary

**Broker-free CloudEvents and transport contracts with the 48-type taxonomy, twelve bundled schemas, and secure configuration.**

## Performance

- **Duration:** Not tracked.
- **Started:** Not recorded in the available session context.
- **Completed:** 2026-10-22T21:15:05Z.
- **Tasks:** 3.
- **Files modified:** 26 — 24 plan paths, this summary, and the required receipt.

## Execution plan

### n1 understand
1. Read the phase decisions, plan revisions, and authoritative DESIGN in the requested order.
2. Resolve the 48-type catalog and broker-free contract scope.
3. Check the exact planned file manifest and ownership.
4. Identify shared envelope, schema, configuration, and security boundaries.

### n2 implement
1. Add immutable CloudEvents and transport contracts with safe codecs.
2. Add the 48-type catalog, typed payload models, and audit-family handling.
3. Package twelve Draft 2020-12 schema bundles with shared fixtures.
4. Add secure configuration, tests, and the optional dependency metadata.

### n3 verify
1. Exercise envelope and transport boundaries through the contract suite.
2. Compare schema fixtures with Pydantic acceptance and rejection.
3. Run Ruff, strict mypy, and the full eventstream test suite.
4. Record exact gate outcomes and remaining integration limits.

### n4 critique
1. Apply R-1a over superseded catalog notes in the plan.
2. Check immutable data, key derivation, redaction, and credential-file reopening.
3. Confirm no broker runtime, timer, or out-of-scope service interaction was added.
4. Resolve lint, typing, schema-fixture, and test failures before final gates.

### n5 synthesize
1. Record implementation files, tests, and deviations.
2. Write the execution summary and verification receipt.
3. Leave all changes uncommitted for the orchestrator.
4. Report passing gates and unverified runtime coverage.

## Accomplishments

- Added frozen Pydantic CloudEvents 1.0 and transport models, strict string/integer/boolean boundaries, RFC3339 timestamps, URI-reference source checks, broker properties, and injected individual acknowledgement.
- Implemented the exact 48 concrete event types, the separate open `omega.audit.*` family, typed payload validation, immutable registry, package-local schema resolution, and key derivation.
- Added twelve Draft 2020-12 bundles with per-definition valid/invalid fixtures and schema/model parity tests.
- Added broker-independent endpoint settings, safe repr/errors, mode-0600 credential reads with descriptor revalidation, and `redact_value`-backed serialization.
- Added the `eventstream` optional extra, development test tools, integration marker, package data retention, and eventstream mypy override.
- Added no broker runtime, timer/schedule, outbox, consumer, or topology implementation; those remain outside wave 1.

## Task commits

No commits created. The orchestrator owns commits; changes remain in the working tree. No pull request was opened or pushed.

## Files created/modified

- `omega_prime/eventstream/__init__.py` — public contract exports.
- `omega_prime/eventstream/errors.py` — typed contract, configuration, schema, retry, and permanent errors.
- `omega_prime/eventstream/envelope.py` — CloudEvent codecs and broker-neutral transport values.
- `omega_prime/eventstream/taxonomy.py` — typed 48-type catalog, audit family, schema resolver, and key derivation.
- `omega_prime/eventstream/config.py` — validated endpoints, safe credential files, and redaction.
- `omega_prime/eventstream/schemas/envelope.v1.json` — CloudEvents envelope schema and fixtures.
- `omega_prime/eventstream/schemas/sdlc.pr.v1.json` — PR lifecycle schemas.
- `omega_prime/eventstream/schemas/sdlc.review.v1.json` — review schemas.
- `omega_prime/eventstream/schemas/ci.v1.json` — CI schemas.
- `omega_prime/eventstream/schemas/auth.v1.json` — authentication-session schemas.
- `omega_prime/eventstream/schemas/job.v1.json` — job lifecycle schemas.
- `omega_prime/eventstream/schemas/sandbox.v1.json` — sandbox lifecycle schemas.
- `omega_prime/eventstream/schemas/task.v1.json` — task state and heartbeat schemas.
- `omega_prime/eventstream/schemas/mem.v1.json` — memory schemas.
- `omega_prime/eventstream/schemas/control.v1.json` — control signal, action, and deadline schemas.
- `omega_prime/eventstream/schemas/hitl.v1.json` — HITL schemas.
- `omega_prime/eventstream/schemas/audit.v1.json` — open audit-family schema.
- `omega_prime/tests/eventstream/__init__.py` — test package marker.
- `omega_prime/tests/eventstream/conftest.py` — packaged schema fixtures.
- `omega_prime/tests/eventstream/test_envelope.py` — codec, immutability, redaction, and transport tests.
- `omega_prime/tests/eventstream/test_taxonomy.py` — registry, payload, identity, and key tests.
- `omega_prime/tests/eventstream/test_schemas.py` — Draft 2020-12 fixtures, parity, and Hypothesis boundaries.
- `omega_prime/tests/eventstream/test_config.py` — endpoint and credential lifecycle tests.
- `pyproject.toml` — optional extra, test tools, marker, package-data retention, and mypy configuration.
- `.planning/phases/105-event-stream-core-library/105-01-SUMMARY.md` — this execution summary.
- `.receipts/bot-00-omega-prime/00046-105-01-eventstream-contract.json` — verification receipt.

## Decisions made

- R-1a authorizes the exact DESIGN §5.3 expansion: 48 concrete names plus the open audit family; no exclusions were invented.
- Integral JSON numeric floats normalize to integer fields before strict Pydantic validation, matching JSON Schema integer semantics.
- Event identity and routing keys fail closed if redaction changes them; non-identity payload fields use the shared credentials redactor.
- Immutable mapping inputs are materialized to plain JSON objects only at strict validation/serialization boundaries.

## Deviations from plan

### Auto-fixed issues

1. **Pydantic strict mappings and serialization:** strict payload models rejected the custom immutable mapping, and Pydantic could not serialize it directly. Taxonomy validation now recursively materializes mappings; model field serializers emit defensive JSON-compatible copies. Envelope/config/taxonomy tests cover the behavior.
2. **Schema fixture correctness:** the supplied `omega.sdlc.pr.pushed` envelope example omitted required `before_sha`; added it to align the fixture with the registered model and payload schema.
3. **Mypy per-module option compatibility:** mypy 2.4 rejects `strict_bytes` inside per-module override sections. Removed that unsupported setting rather than applying it globally; the required `mypy --strict omega_prime/eventstream` gate passes.
4. **Format assertion availability:** `jsonschema`'s optional date-time and URI-reference validators were absent in the read-only venv. Added deterministic test-local format checkers rather than adding optional runtime dependencies. An attempted `pip install 'jsonschema[format]'` failed because site-packages is read-only.

**Total deviations:** 4 auto-fixed, all correctness or tool-compatibility fixes. No runtime or scope expansion.

## Issues encountered

- The initial Ruff, mypy, and test runs exposed import/type errors, strict mapping conversion, serializer behavior, and a missing test argument; all were corrected before the final gates.
- Installing optional JSON Schema format extras failed with a read-only filesystem error; the test suite uses local format checkers and no extra dependency was added.
- No unresolved implementation blockers.

## User setup required

None for this contract-only phase. Production broker credentials and broker services are owned by later runtime/integration phases.

## Next phase readiness

- 105-02 and 105-03 can consume the same catalog, envelope contracts, schemas, and blocked-event fixtures.
- 105-04 can use the config contract and optional Pulsar dependency for owned-broker integration.
- Live Pulsar/Railway/Dragonfly behavior, package installation, dependency provenance, and broker authorization remain unverified by this wave.

---
*Phase: 105-event-stream-core-library*
*Completed: 2026-10-23*
