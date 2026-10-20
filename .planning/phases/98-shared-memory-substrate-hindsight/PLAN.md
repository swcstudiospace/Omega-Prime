# Phase 98 — Shared memory through substrate, Hindsight fallback

**Status:** implementation executed on the phase-98 parallel branch under the operator override; this supersedes the planning-time phase-97 prerequisite for local work. Ordered rebase/integration still follows phase 97. The phase-97 endpoint resolver and source-aware `memory_search` error contract remain upstream seams; see `SUMMARY.md` and `VALIDATION.md`.

**Requirements:** MEM-98-01 through MEM-98-10. No tracker IDs or Notion URLs were provided. G2 is an audit gap, not a tracker identifier.

## Plan checklist

### n1 — understand
1. Inspect planning state and phase-97 dependency artifacts.
2. Map existing memory entrypoints, composition, reports, and consumers.
3. Confirm read/write transport contracts and structured failures before execution.
4. Record pre-change validation baselines and their known gaps.

### n2 — decompose
1. Freeze selector semantics and source outcome states.
2. Separate recall merging from local-first retention.
3. Allocate file ownership by dependency wave without shared-file edits.
4. Map each requirement to an acceptance case and verification gate.

### n3 — design recall
1. Resolve configured sources without probing implicit loopback defaults.
2. Query local, substrate, and configured Hindsight independently.
3. Normalize/deduplicate content while preserving all source provenance.
4. Report every backend state independently of hits, deduplication, and truncation.

### n4 — design retain
1. Validate scope, repo, content, evidence, bounds, and secret policy before IO.
2. Persist locally first and fail closed if local persistence fails.
3. Bind a stable operation UUID to the canonical request and destination.
4. Report substrate/Hindsight refusals and failures without hiding local success.

### n5 — critique
1. Review repository isolation, legacy records, bank boundaries, and user-profile safety.
2. Review URL, credential, redirect, and redaction failure paths.
3. Resolve bridge removal and command approval/evidence behavior.
4. Trace every requirement and known prerequisite gap to an explicit test or blocker.

### n6 — synthesize
1. Publish the frozen API and exact wave dependencies below.
2. Publish executable plans with disjoint file ownership and test mappings.
3. Record baseline results, execution gates, smoke recipe, and operator-only checks.
4. Verify planning artifacts and write the required planning-session receipt/commit in the parent workflow.

## Frozen architecture and APIs

These are the shared design contract for all execution plans, not facts about the current checkout. Exact expected-version semantics and idempotency support remain phase-97 prerequisites; implementation MUST NOT guess them. Work is divided into four plans: `98-01` wave 1 owns backend resolver/security seams and complete shared contracts; `98-02` and `98-03` wave 2 are independent and depend only on `98-01`; `98-04` wave 3 depends on both wave-2 plans. Dependency graph: **98-01 → (98-02 + 98-03) → 98-04**. Wave-2 changes may not edit shared modules owned by 98-01 or each other's tool families. Wave 3 edits any shared integration files only after both wave-2 units land.

- `omega_prime/memory/backends.py` (98-01) exposes `resolve_memory_backends(...) -> MemoryBackends`, holding the local store, resolved substrate client, optional configured `HindsightService`, trusted default scope/repo, selected backend, and safe configuration states. The selector is `OMEGA_PRIME_MEMORY_BACKEND=local|substrate|hindsight`. Default to `substrate` only if phase 97's authoritative resolver returns an explicitly resolved URL; otherwise default to `local`. An invalid selector/configuration fails safely. An explicitly selected unavailable backend is a configuration failure, never a silent fallback. Selector is primary write preference, not recall opt-out: local always participates, configured substrate participates even when selector is `local`, configured Hindsight always participates in recall, and only selector `hindsight` enables Hindsight writes.
- `omega_prime/memory/recall.py` (98-02) exposes `recall_memory(backends, query, *, scope, repo, limit, banks=None, include_shared=True) -> dict`. Scope/repo validation occurs before IO and repo isolation applies to every backend. Result shape: `{selected_backend, scope, repo, backends:{local|substrate|hindsight:{state,attempted,error?}}, results:[{content,content_hash,sources:[{backend,bank?,id?,scope?,repo?,score?}]}]}`. States are exactly `answered`, `empty`, `not_configured`, `unreachable`, `unauthorized`. Structured response/error fields determine state; never infer outage from `[]` or authorization from exception strings. `answered` means a valid source returned at least one hit before merge/truncation; healthy zero-hit source is `empty`; absent/skipped source is `not_configured`. Backend errors are separate sanitized diagnostics. No failed or malformed response can become `empty`.

The local record codec is a wave-1 shared helper in `backends.py`: `encode_local_record(record) -> str` returns the complete persisted string with exactly one `[hindsight:<bank>]` outer wrapper, followed by `[omega-memory:v1]` and compact JSON (`ensure_ascii=False, sort_keys=True, separators=(",", ":")`); pass this complete string directly to `MemoryStore.add("memory", encoded)`, never to `Hindsight.retain` (which would add a second bank envelope). `decode_local_record(stored_text, expected_bank) -> LocalRecord|LegacyRecord` consumes that complete stored string, validates and strips exactly the expected outer bank wrapper internally, then parses the version marker and JSON and verifies inner bank equality. Other-bank records are ineligible, not malformed. A legacy expected-bank-wrapped record with no version marker remains readable as plain unscoped content. The JSON object has exactly `v=1`, `bank`, `scope`, `repo` (string or null), `kind`, `content`, `source` (string or null), `receipt_path` (string or null), `tags` (list of strings), `graph_id` (string or null), and `task_id` (string or null). Recognized malformed/unknown-version envelopes fail, never fall through as legacy. Search and hash only decoded content (canonicalized as defined below), not serialized metadata. Reject `ENTRY_DELIMITER` before encoding, and count the entire wrapper/JSON plus existing separators against the unchanged 2200-character memory budget; no budget increase. Store remote provenance separately using only established backend fields, never invented kwargs. Codec round-trip, malformed-envelope, other-bank ineligibility, legacy, delimiter, and exact budget-boundary coverage belongs to 98-01.

The shared retain signature is `retain_memory(backends, content, *, scope, repo, kind, operation_id=None, expected_version=None, source=None, receipt_path=None, tags=None, graph_id=None, task_id=None) -> dict`; preserve existing lead provenance through the final two explicit keyword arguments, not by embedding it in content or tags. 98-03 implements this API.

The operation identity ledger is shared state owned by `MemoryBackends` (types/container in 98-01; retain behavior/ledger use in 98-03). A generated UUID is created once per logical operation, bound before local mutation, and returned as `idempotency_key`; the caller retries by passing that UUID as `operation_id`. Bind the SHA-256 of canonical typed JSON containing the exact versioned local record (including content, scope/repo, kind/bank, evidence, tags, graph/task), phase-97 expected version, selected backend, and normalized effective write-destination identities (no credentials). A known UUID must match the exact binding before any local or network write; mismatch fails before I/O. Unknown caller-supplied UUIDs fail before I/O, including after restart or eviction; never adopt them or silently mint a replacement. The response explains that replay binding is unavailable. Keep at most the most recent 1024 completed bindings in an LRU; never evict in-flight bindings. No TTL, time/session cap, operation-count cap, durable journal, retry loop, queue, or background worker. Destination/config changes invalidate reuse. This in-memory ledger does not promise retry-after-restart or exactly-once behavior; substrate POST retry safety still requires phase-97 server idempotency, and Hindsight `document_id` is not proof of idempotency. The caller-facing UUID is the only safe retry identity.

Network timeouts are ordinary per-backend request timeouts, not session or turn caps. A timeout is sanitized `unreachable`; recall preserves hits from other sources and still attempts all configured sources. Retain reports `local_saved=true` and `ok=false` when a required remote write times out, and continues attempting other required destinations. Add fake-timeout coverage on the real recall/retain/doctor paths in their owning plans. No session, turn, execution-budget, or wall-clock cap may be added.

- `omega_prime/memory/retain.py` (98-03) exposes `retain_memory(backends, content, *, scope, repo, kind, operation_id=None, expected_version=None, source=None, receipt_path=None, tags=None, graph_id=None, task_id=None) -> dict`. Result shape: `{ok,local_saved,idempotency_key,selected_backend,backends,...error?}`. Validate all fields and bounds before IO. Keep local `MemoryStore` total budgets (memory 2200 chars, user 1375) and delimiter restriction; include wrapper/evidence overhead in bounds. Persist local first; local refusal means no remote calls. Then write to configured substrate regardless of selector, with exact phase-97 expected-version handling, and to Hindsight only when explicitly selected. Only substrate status `accepted` succeeds; conflict/quarantined/denied/unknown/refusal…
- Hindsight secret material comes only from environment or mode-checked secret files through the existing broker; never settings. Construct Hindsight only when URL explicitly configured. Require HTTPS; reject URL credentials, query/fragment, unsafe bank path segments; prevent unsafe redirect credential forwarding; redact every key/error path including broker/redaction failures. Do not add a hard-coded live URL. Hindsight transport failures need typed status/code sufficient to distinguish unauthorized from unreachable. Remove the tests-only service-first bridge and migrate its tests; no compatibility alias.
- Existing service entrypoints are synchronous. Async handlers use existing `asyncio.to_thread` style for blocking fanout. No async API fiction or new concurrency framework.
- Preserve `lead_memory_retain` approval gating, evidence/grant/secret checks; generic growth add keeps host write policy. `/omega-retain` must call the shared retain operation through approved lead path rather than bypass it with built-in memory. Readiness consumers are migrated to the one result shape (no duplicate legacy/new result fields). Lead brief's 300-second cache must be explicitly invalidated/refreshed after retain.

## Requirement-to-plan map

| Requirement | Acceptance coverage | Execution owner |
|---|---|---|
| MEM-98-01 selector validation, defaulting, and no hidden fallback | All valid/invalid selector and explicitly unavailable source cases; implicit substrate only with phase-97 explicit URL; local selector still recalls configured shared sources | 98-01, 98-04 |
| MEM-98-02 isolated source-aware recall | Independent source states; local + substrate + configured Hindsight fanout; per-repo filtering/isolation across all sources; legacy local banks | 98-01, 98-02 |
| MEM-98-03 local-first retention | Validate-before-IO, local-first, budgets/full-store refusal, explicit partial failure and no remote rollback | 98-01, 98-03 |
| MEM-98-04 Hindsight selected writes and recall | Always recall configured Hindsight; write only selected hindsight; failures truthful; bridge removal | 98-01, 98-03, 98-04 |
| MEM-98-05 exact backend states | answered/empty/not_configured/unreachable/unauthorized; malformed vs empty; typed auth result | 98-01, 98-02, 98-04 |
| MEM-98-06 reporting surfaces | Result provenance, both slash commands, direct lead retain, growth add, doctor source states and backend names, cache invalidation | 98-02, 98-03, 98-04 |
| MEM-98-07 security | HTTPS/URL rejection, secret broker/file only, redaction failure, safe redirects, secret and repo checks | 98-01, 98-02, 98-03 |
| MEM-98-08 runtime bridge/composition | Standalone injected handlers until wave 3; raw substrate client (not `_DeskSubstrate`) in one coordinator; synchronous calls off-loop | 98-01, 98-02, 98-03, 98-04 |
| MEM-98-09 fake-backed integration | Scripted substrate/Hindsight transports through default registry, real command and doctor paths; no live service | 98-04 |
| MEM-98-10 gates, docs, receipt | Exact gates, conditional prompt assembly, docs corrected, planning/session receipt; live operator work explicitly outside phase gate | 98-04 |

## Files and ownership by wave

- `98-01` wave 1: new `omega_prime/memory/backends.py`; `omega_prime/memory/hindsight_service.py`; remove `omega_prime/memory/hindsight_bridge.py`; migrate `omega_prime/tests/test_hindsight_service.py` bridge/service coverage. Complete resolver, types, validation/canonicalization helpers and security contracts; no composition wiring and no partial coordinator API.
- `98-02` wave 2: new `omega_prime/memory/recall.py`; `omega_prime/tools/lead.py` all memory read/write handler signatures, registration/schema and brief invalidation seam (actual doctor lines are reserved for 98-04); `omega_prime/grokbot/readiness_local.py`; `omega_prime/grokbot/readiness_workflows.py`; dedicated recall tests and updates to `omega_prime/tests/test_lead.py`. All changes consume only 98-01 contracts; lead retain wiring calls exact `retain_memory` API defined above, implemented by parallel 98-03.
- `98-03` wave 2: new `omega_prime/memory/retain.py`; intended shared-add path in `omega_prime/tools/growth.py` and `omega_prime/memory/provider.py`; dedicated retain tests and `omega_prime/tests/test_growth.py` updates. User target and replace/remove remain unchanged/local. No edits to `tools/lead.py` or shared backend modules.
- `98-04` wave 3: `omega_prime/mcp_server.py` final single-coordinator composition, constructing after raw substrate resolver and injecting same coordinator into growth and lead; `omega_prime/commands/__init__.py` route/error/output changes; serial `omega_prime/tools/lead.py` doctor reporting after 98-02; `omega_prime/substrate/probes.py` only if phase-97 resolved config must be exposed there; docs `docs/substrate.md`, `omega_prime/grokbot/SETUP.md`, `omega_prime/skills/hindsight-memory/SKILL.md`; command/doctor/integration tests including registry, lead/growth, command and probes. Existing service methods sync; handlers use `asyncio.to_thread`.

## Verification and completion policy

Verification sequencing: no implementation tests, gates, or formatters during planning. The orchestrator runs focused wave-1 tests after 98-01 and before wave 2; then focused union tests after both wave-2 plans and before wave 3. After wave 3, the orchestrator runs the exact full gates once: `ruff check omega_prime/`; `ruff format --check omega_prime/`; `mypy omega_prime/`; `pytest omega_prime/tests -q`; and `bash omega_prime/scripts/assemble-prompts.sh --check` only if prompts change. Subagents never run tests, gates, or formatters. Smoke through real `default_registry`, approved `/omega-retain` and `/omega-recall`, direct lead retain, growth memory add, and lead doctor using loopback fake substrate/Hindsight transports; never call live services. Include a fake transport which commits then drops the first response and verify stable operation identity on retry only if phase-97 server idempotency contract supports it; otherwise the prerequisite remains blocking and no exactly-once claim is permitted.

Operator-owned live URL/restart/VPS checks and phase77.1 human UAT are documented as unverified and nonblocking for implementation acceptance. Prompt assembly gate is conditional because prompt files are not planned to change. Planning artifact smoke/consistency is separate from runtime gates.

**Pre-change baseline at 44b28df (already run by the parent before planning; do not rerun as planning work):** `ruff check` passed; `ruff format --check` passed (589 files); `mypy` passed (462 source files); pytest exited 1: 27 failed, 4777 passed, 11 skipped, 15 warnings in 244.72s. These are recorded pre-change baseline results, not phase-98 implementation tests; phase-98 runtime behavior has not been tested. Failures concern uninitialized/missing `prime-agent` checkout/runtime, missing Cargo.toml/skills, or setup/preflight expectation; do not initialize submodules or repair unrelated tests. All observed failing tests are enumerated in `VALIDATION.md`; distinguish these baseline failures from new results and never claim a green baseline.
