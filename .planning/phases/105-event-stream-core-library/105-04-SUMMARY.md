---
phase: 105-event-stream-core-library
plan: '04'
subsystem: eventstream
tags: [python, pulsar, cloudevents, admin-rest, functions, ci]
requires:
  - phase: 105-02
    provides: [ports, outbox, idempotency, retry/DLQ, consumer runtime, topology.yaml]
provides:
  - lazy optional Pulsar asyncio adapter implementing runtime ports
  - typed private REST admin reconcile with dry-run/apply/status
  - omega eventstream apply/status/tail CLI (lazy import)
  - three deterministic process-runtime Functions
  - pinned owned-secure eventstream-it CI job + local loopback IT
affects: [106-event-stream-deploy, 108-replay-dlq]
actuals:
  tokens: null
  tasks: 4
  commits: null
tech-stack:
  added: [apachepulsar/pulsar:5.0.0 pinned IT image, pulsar-client optional]
  patterns:
    - cancel-safe shielded receive; JIT KeyShared ConsumerKeySharedPolicy
    - empty retention / UNDEFINED schemaCompatibility treated as unset
    - credentials via env / *_FILE only; CryptoKeyReader for auth topics
key-files:
  created:
    - omega_prime/eventstream/adapters/pulsar.py
    - omega_prime/eventstream/admin.py
    - omega_prime/eventstream/cli.py
    - omega_prime/eventstream/functions/__init__.py
    - omega_prime/eventstream/functions/greptile_score.py
    - omega_prime/eventstream/functions/ci_rollup.py
    - omega_prime/eventstream/functions/pr_state_projector.py
    - deploy/pulsar/standalone.conf
    - deploy/pulsar/functions_worker.yml
    - deploy/pulsar/bookkeeper.conf
    - omega_prime/scripts/eventstream_it.py
    - omega_prime/tests/eventstream/test_pulsar_adapter.py
    - omega_prime/tests/eventstream/test_admin.py
    - omega_prime/tests/eventstream/test_cli.py
    - omega_prime/tests/eventstream/test_function_helpers.py
    - omega_prime/tests/eventstream/functions/test_greptile_score.py
    - omega_prime/tests/eventstream/functions/test_ci_rollup.py
    - omega_prime/tests/eventstream/functions/test_pr_state_projector.py
    - omega_prime/tests/eventstream/integration/test_pulsar_integration.py
  modified:
    - omega_prime/engine/cli.py
    - omega_prime/eventstream/retry.py
    - omega_prime/tests/eventstream/test_outbox.py
    - omega_prime/tests/eventstream/test_idempotency.py
    - omega_prime/tests/eventstream/test_envelope.py
    - omega_prime/tests/eventstream/test_retry.py
    - .github/workflows/ci.yml
key-decisions:
  - "Key_Shared uses ConsumerKeySharedPolicy; seek wraps MessageId; partition suffix stripped for logical topic."
  - "Admin treats empty retention body and schemaCompatibility UNDEFINED as unset; schema GET compares `data` to POST `schema`."
  - "Failover active consumer discovered by race, not creation order; Exclusive ConsumerBusy caught as reject."
  - "Auth encryption unconditional on original/retry/DLQ auth destinations; secrets never logged."
patterns-established:
  - "IT: external-loopback for local noauth; owned-secure only via helper-owned container + ownership file."
  - "Coverage gate: outbox + idempotency ≥90% and total eventstream ≥90% with IT env."
requirements-completed: []
coverage:
  - id: D1
    description: Native Pulsar adapter ports — publish, subscribe modes, reader seek, cancel-safe receive, encryption.
    verification:
      - kind: unit
        ref: omega_prime/tests/eventstream/test_pulsar_adapter.py
        status: pass
      - kind: integration
        ref: omega_prime/tests/eventstream/integration/test_pulsar_integration.py (external-loopback)
        status: pass
    human_judgment: false
  - id: D2
    description: Admin reconcile dry-run/apply/status; CLI apply/status/tail; deploy configs.
    verification:
      - kind: unit
        ref: omega_prime/tests/eventstream/test_admin.py, test_cli.py
        status: pass
    human_judgment: false
  - id: D3
    description: Pure process-runtime Functions (greptile-score, ci-rollup, pr-state-projector).
    verification:
      - kind: unit
        ref: omega_prime/tests/eventstream/functions/*, test_function_helpers.py
        status: pass
    human_judgment: false
  - id: D4
    description: Owned-secure CI job + local loopback IT matrix; coverage ≥90%.
    verification:
      - kind: other
        ref: ".github/workflows/ci.yml eventstream-it; pytest --cov-fail-under=90 → 90.12% total; outbox 91%; idempotency 91%"
        status: pass
      - kind: other
        ref: "ruff check/format; mypy (9 source files); polyglot-check 0 errors"
        status: pass
    human_judgment: false
duration: null
completed: 2026-10-23
status: complete
---

# Phase 105-04: Pulsar Adapter, Admin, Functions, CLI, Live IT

**Production broker/operator path for the event stream: lazy Pulsar adapter, REST admin reconcile, omega CLI, three pure Functions, pinned CI IT, ≥90% coverage.**

## Performance

- **Duration:** Resumed across interruptions; final verification window 2026-10-23 (Australia/Sydney).
- **Tasks:** 4 plan tasks completed.
- **Suite:** 306 passed, 2 skipped (owned-secure without ownership file), with live loopback broker.

## Accomplishments

- Implemented `PulsarEventBus` against runtime ports: Key_Shared / Failover / Exclusive / Shared, cancel-safe shielded receive, MessageId seek, logical topic strip, CryptoKeyReader encryption for auth topics, Avro JSON native schema metadata, secret redaction in adapter errors.
- Implemented `PulsarAdminPort` private REST reconcile: tenant/namespaces/topics/schemas/permissions/functions, dry-run zero writes, second apply no-op, empty retention and UNDEFINED compatibility as unset, schema GET/POST equivalence.
- Wired `omega eventstream apply|status|tail` via lazy dispatch from `omega_prime/engine/cli.py` (no Pulsar import on unrelated paths).
- Added three deterministic process-runtime Functions with explicit parsers and keyed publish via `publish_keyed`.
- Staged `deploy/pulsar/{standalone,functions_worker,bookkeeper}.conf` (inactive-topic deletion off; journalSyncData=true).
- Added `eventstream_it.py` helper and separate `eventstream-it` CI job pinning `apachepulsar/pulsar:5.0.0@sha256:7222f3199a0b2418abde9ce0906862598415b19b494ae185db189938cfc43fd3`.
- Live loopback IT covers Shared/Key_Shared/Failover/Exclusive, retry/DLQ, schema roundtrip, and encryption paths.
- Coverage: **outbox 91%**, **idempotency 91%**, **total eventstream 90.12%** (`--cov-fail-under=90`).
- Gates: ruff clean, mypy clean on production modules, polyglot-check 0/0 on changed surface.

## Task Commits

See git log on `gsd/00046-event-stream` for feat(105-04) commits per plan task.

## Coverage table (final IT run)

| Module | Stmts | Miss | Cover |
|--------|------:|-----:|------:|
| adapters/memory.py | 247 | 25 | 90% |
| adapters/pulsar.py | 529 | 80 | 85% |
| admin.py | 336 | 28 | 92% |
| backoff.py | 33 | 2 | 94% |
| cli.py | 77 | 5 | 94% |
| config.py | 139 | 12 | 91% |
| consumer.py | 218 | 12 | 94% |
| envelope.py | 346 | 39 | 89% |
| functions/ci_rollup.py | 157 | 23 | 85% |
| functions/greptile_score.py | 81 | 9 | 89% |
| functions/pr_state_projector.py | 196 | 29 | 85% |
| idempotency.py | 127 | 12 | 91% |
| outbox.py | 489 | 45 | 91% |
| retry.py | 164 | 23 | 86% |
| taxonomy.py | 325 | 15 | 95% |
| **TOTAL** | **3634** | **359** | **90.12%** |

## Verification commands

```bash
/workspace/.venv-00046/bin/ruff check …   # All checks passed
/workspace/.venv-00046/bin/python -m mypy omega_prime/eventstream/adapters/pulsar.py …  # Success
polyglot-check …  # 0 error(s), 0 warning(s)
OMEGA_EVENTSTREAM_IT_MODE=external-loopback \
OMEGA_EVENTSTREAM_IT_URL=pulsar://127.0.0.1:26650 \
OMEGA_EVENTSTREAM_IT_ADMIN_URL=http://127.0.0.1:28080 \
/workspace/.venv-00046/bin/python -m pytest omega_prime/tests/eventstream \
  --cov=omega_prime.eventstream --cov-fail-under=90
# 306 passed, 2 skipped; Total coverage: 90.12%
```

## Not in this plan

- Phase 106 Railway/deploy wiring and paid cutover.
- Phase 108 replay/DLQ operator commands.
