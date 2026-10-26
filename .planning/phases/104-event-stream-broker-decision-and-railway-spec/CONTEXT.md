# Phase 104 — Event stream broker decision + Railway deploy spec — Context

**Gathered:** 2026-10-23 (AEDT) · **Status:** discuss complete (smart_discuss, autonomous: recommended answers auto-accepted) · **Milestone:** v21 Event Stream (phases 104–108)
**Run:** `/gsd-autonomous` with trailing keywords `ultrathink orchestrate workflowz` · **Design:** `DESIGN.md` · **Evidence:** `RESEARCH.md` · **Uplift:** `UPLIFT.md`

<domain>
## Phase Boundary
Decide the event-stream broker for the Autonomous Event-Driven ADLC with verified evidence, and write the enterprise-grade spec (topology, contract, semantics, security, FMEA, tests, Railway deploy spec) that phases 105–108 build. No code, deploys, Railway, or VPS changes in this phase.
</domain>

<decisions>
## Implementation Decisions

### D-01 Broker — Apache Pulsar 5.0 LTS standalone (auto-accepted; matches Ove's stated preference)
- Ove's condition "Pulsar if the Python + TS SDKs are production-grade and it runs sanely single-node on Railway" is **met** (RESEARCH E4, E6, E8, E9).
- Python `pulsar-client` 3.13 asyncio is the primary SDK; Node `pulsar-client` 1.18 is the TS SDK.
- Handlers depend on a broker-agnostic `EventBus` port; NATS JetStream (00043) remains a documented fallback adapter, not built.
- Pin `apachepulsar/pulsar:5.0.0` by digest; tested rollback `4.0.14` (HITL-2 default).

### D-02 Not used, with reasons recorded (auto-accepted)
- Transactions: absent from Python 3.13 and Node 1.18 (E5, E7) → effectively-once via broker dedup + idempotent consumers.
- Scalable Topics: need v5 clients (E2) → v4 persistent topics.
- Geo-replication: needs two mutually reachable clusters → documented path only.
- Railway one-click template: third-party, 2 deploys (E8) → own Dockerfile/config.

### D-03 Contract (auto-accepted)
- CloudEvents 1.0 structured JSON; `ce_*` mirrored into message properties; 00043 taxonomy re-rooted at `omega.`.
- Tenant `omega`; namespaces `sdlc, auth, job, sandbox, task, memory, control, audit, system` with policy per namespace (DESIGN §4.1).
- JSON Schema 2020-12 per type is the single source; Python/TS models generated; envelope schema registered in Pulsar with BACKWARD_TRANSITIVE (audit FULL_TRANSITIVE).
- Auto-topic creation off; `topology.yaml` + `omega eventstream apply` reconcile.

### D-04 Delivery semantics (auto-accepted)
- SQLite WAL outbox; rowid = Pulsar `sequence_id`; broker dedup on. Relay is woken by events (insert, connected), never by an interval.
- Key_Shared for scalable handlers, Failover for the merge executor, Exclusive for audit, Reader for live tail/replay.
- Portable retry topics (delayed delivery, decorrelated jitter, 5 attempts) + explicit DLQ per subscription; DLQ raises a blocked signal.
- Idempotency claim ledger keyed (subscription, ce_id); external effects head-SHA pinned.

### D-05 Timer ban (Ove's standing rule; auto-accepted design)
- Deadlines (Greptile 20-min nudge, follow-ups, heartbeat staleness) become delayed events with supersede-by-version; no polling.
- Phase 107 retires `omega_prime/cron`, routine schedules, heartbeats and collector loops; phase 108 adds the no-timer CI gate.

### D-06 Topology (auto-accepted)
- Stage 2: box hub (podman, loopback, JWT on), VPS leaf over a box-initiated `ssh -R` tunnel with its own outbox; push-only sources (journald, D-Bus, inotify, job wrappers).
- Stage 3: Railway `omega-pulsar` (own Dockerfile, volume, private network) + `omega-ingress` (GitHub HMAC) + Railway Bucket tiered storage for `omega/audit`.

### D-07 Security (auto-accepted)
- RS256 JWT per role with namespace ACLs; broker holds public key only; TLS on non-loopback hops; admin API never public; producer-side redaction; E2E encryption for `omega/auth`; secrets via 0600 `*_FILE` paths only.

### Claude's Discretion
- Partition counts (4 for `sdlc/pr`, `task/state`), JVM sizing, retention numbers, metric names — tunable in phase 108.
</decisions>

<auto_answered>
## Auto-answered (rule 5 / 5b, recorded for the final report)
| Q | Answer taken | Why |
|---|---|---|
| Pulsar vs NATS vs other | Pulsar | Ove's preference + conditions verified |
| Pulsar version | 5.0.0 LTS, rollback 4.0.14 | LTS line; .0 risk mitigated by pin + rollback test |
| Metadata store | RocksDB (standalone default) | Oxia only matters multi-node |
| Retry mechanism | SDK retry topics + explicit DLQ | Java-only retry-letter; docs warn on nack counters |
| Live tail to Command Center | Python Reader → existing `/ws/events` | Keeps one auth model; no public WebSocket |
| Railway template vs own image | Own pinned Dockerfile | Template is third-party, 2 deploys |
</auto_answered>

<hitl>
## Open for Ove (non-blocking; defaults apply)
1. Stage 3 perimeter: TLS + JWT via Railway TCP proxy (default) vs private Tailscale/WireGuard link.
2. Broker pin: 5.0.0 (default) vs 4.0.14.
3. First Node consumers of the TS SDK (default: SDK + tests only).
4. Railway spend for ~3 GB RAM + bucket at cutover (default: proceed, report cost).
</hitl>

<canonical_refs>
## Canonical References
- `.planning/phases/104-event-stream-broker-decision-and-railway-spec/DESIGN.md` — the spec
- `.planning/phases/104-event-stream-broker-decision-and-railway-spec/RESEARCH.md` — evidence E1–E14
- `/workspace/00043-sdlc/event-mesh-decision.md`, `/workspace/00043-sdlc/listener-architecture.md` — taxonomy + L1–L23 (box-local)
- PR #75 `omega_prime/sdlc_events/` — handler core (gate/blocked/review), claim ledger, MergePort
- Pulsar docs 5.0.x (messaging, security overview, functions, standalone); Railway docs (storage buckets, TCP proxy)
</canonical_refs>

<code_context>
## Existing Code Insights
- Reusable: `omega_prime.dashboard.redact.redact_value` (payload redaction), dashboard `poller.nudge(source)` + `/ws/events`, `omega_prime/telemetry/otlp_export.py`, PR #75 claim ledger + `MergePort`, dashboard Greptile `greptile_confidence_score` regexes.
- Timer code to retire: `omega_prime/cron/*`, `omega_prime/routines/{sweep,nightly,desk_lead}`, `omega_prime/prime/heartbeat.py`, `omega_prime/tools/{heartbeat,cron_admin}.py`.
- Guard rails: `omega_prime/tests/test_store_lock.py` (no Dragonfly/Greptime/Timescale clients).
</code_context>

<deferred>
## Deferred Ideas
- Multi-node Pulsar on Railway with Oxia + Pulsar proxy (DESIGN §12).
- Scalable Topics once Python/Node v5 clients ship.
- Kafka-protocol handler for third-party tools.
</deferred>
