# Omega Prime Event Stream — Enterprise Design (00046)

- **Status:** decision + spec for Ove's sanity check (phase 104). Nothing built, deployed or changed on Railway/VPS yet.
- **Date:** 2026-10-23 (AEDT) · **Branch:** `gsd/00046-event-stream` · **Evidence:** `RESEARCH.md` (E1–E14) · **Prior art:** 00043 `event-mesh-decision.md`, `listener-architecture.md` (23-listener catalog L1–L23)
- **Standing rule (Ove, 2026-10-23):** Omega Prime may not create timer/schedule routines. Every behaviour in this document is triggered by an event. Deadlines are expressed as *delayed events*, never as polling loops.

---

## 1. Decision

**Apache Pulsar 5.0 LTS, single-node standalone, behind a broker-agnostic `EventBus` port.** Python (`pulsar-client` 3.13, asyncio) is the primary SDK; TypeScript (`pulsar-client` 1.18) is the secondary SDK. NATS JetStream stays the documented fallback adapter; we do not build it now.

Ove's rule was "Pulsar if the Python + TypeScript SDKs are production-grade and it runs sanely single-node on Railway". Both conditions hold, with caveats we design around:

| Condition | Result | How the design handles the caveat |
|---|---|---|
| Python SDK production-grade | ✅ Official ASF client, full asyncio surface (producer, consumer, reader, Key_Shared, DLQ, delayed delivery, sequence ids) — E4 | Ship on 3.13.x pinned; integration tests against the pinned broker image |
| TS SDK production-grade | ✅ Official ASF client 1.18, bundled typings, prebuilt N-API binaries — E6 | Thin `@omega-prime/eventstream` package with the same semantics |
| Runs sanely single-node | ✅ with caveats: standalone is one JVM (broker + BookKeeper + RocksDB metadata); Apache calls it dev/test-grade; no HA — E3, E9 | Producer-side durable **outbox** (no event lost while broker is down), BookKeeper journal on a persistent volume, Railway volume backups, documented multi-node path (§12) |
| Railway | ✅ but the one-click template is third-party with 2 deploys — E8 | Own pinned Dockerfile + config in-repo; same volume/ports |
| Public exposure | ⚠️ Pulsar is not meant to be on the public internet — E10 | TLS + JWT + per-role authorization on every non-loopback hop; admin API never public; private-network option is a Stage 3 decision (§11, HITL-1) |
| Transactions | ❌ Not in Python 3.13 or Node 1.18 — E5, E7 | Effectively-once via broker dedup (producer name + outbox sequence id) + idempotent consumers. Recorded as an explicit non-use |
| Scalable Topics (5.0 headline) | ❌ Needs v5 client; ours are v4-API — E2 | v4 persistent topics; revisit when the Python/Node v5 API ships |

Why not the alternatives (kept honest):
- **NATS JetStream** (00043's pick) is lighter and has leaf nodes, but Ove explicitly prefers Pulsar when viable, and Pulsar gives us things NATS does not: broker-enforced schema registry with compatibility checks, native delayed delivery (our timer replacement), Key_Shared per-key ordering across many consumers, topic compaction, tiered storage to object storage, multi-tenancy with per-role namespace ACLs, and Pulsar Functions. The `EventBus` port keeps a NATS adapter possible without touching handlers.
- **Kafka / Redpanda:** no delayed delivery, no per-message ack queue semantics, heavier for this workload.
- **Redis Streams:** no schema registry, no tiered storage; Dragonfly is off-limits.

## 2. Goals and non-goals

**Goals**
1. One durable event backbone for the Autonomous Event-Driven ADLC: SDLC (PR/review/CI), auth/login, jobs, sandboxes, tasks, memory, HITL, deploys, audit.
2. No event is lost while a consumer sleeps, a broker restarts, or a network hop drops (durable subscriptions + producer outbox).
3. At-least-once delivery with **effectively-once effects** (broker dedup + idempotent handlers), replay by position or time.
4. Every routine that runs on a timer is replaced by an event or a delayed event (§7).
5. Enterprise code quality: typed contracts, ports/adapters, structured logs, OTel metrics/traces, graceful shutdown, full test pyramid, mypy-strict.

**Non-goals (this milestone)**: multi-node Pulsar cluster, geo-replication between live clusters, Pulsar transactions, Scalable Topics, Kafka/AMQP protocol handlers, exposing the admin API publicly, using Dragonfly/Greptime/Timescale.

## 3. Topology and staged rollout

```
                 ┌──────────────────────── Stage 1–2 (box hub) ─────────────────────────┐
 GitHub ──(00043 platform listeners / dashboard diff, until Stage 3)──▶ box ingress ─┐  │
                                                                                     ▼  │
 VPS (Hostinger) leaf ── SQLite outbox ──▶ 127.0.0.1:16650 ══ssh -R (box-initiated)══▶ box Pulsar standalone
   journald / D-Bus / inotify sources                                     127.0.0.1:6650 (pulsar) :8080 (admin)
                                                                           ▲  │ functions worker, dedup, DLQ
 box producers (tasks, sandboxes, herdr, sdlc handlers) ── outbox ─────────┘  ▼
                                                     consumers: gate · blocked · review · escalator · cc-bridge · audit
                 └───────────────────────────────────────────────────────────────────────┘
 Stage 3: Railway `omega-pulsar` (private net + TLS/JWT edge) becomes hub; `omega-ingress` (GitHub HMAC webhooks) on Railway;
          box + VPS become clients with the same outboxes; Railway Bucket = tiered storage for omega/audit.
```

| Stage | Ships | Infra | Phase |
|---|---|---|---|
| 0 | Decision + this spec + topology-as-code | none | 104 |
| 1 | Core library (Python + TS), in-memory adapter, Pulsar adapter, outbox, consumer runtime, admin reconcile; integration tests against pinned broker in CI | CI service container only | 105 |
| 2 | Box hub: Pulsar standalone in podman (loopback, JWT, volume `~/.omega-prime/pulsar`); VPS leaf publisher (systemd unit, outbox) over a box-initiated reverse SSH tunnel; VPS live feed (auth/login, CI, jobs, Hindsight teach) | 1 box container, 1 VPS unit, 1 tunnel | 106 |
| 3 | Taxonomy wiring: 00043 handlers + 23 listeners onto the bus; delayed-event deadlines; retire every timer routine; Command Center bridge | none new | 107 |
| 4 | Ops: OTel metrics, health/readiness, DLQ tooling, runbooks, chaos tests, no-timer CI gate; Railway deploy spec + Dockerfile ready | Railway service when cut over | 108 |

The Railway cutover (hub move) is specified here and staged in phase 108; executing it is gated on HITL-1 (perimeter choice).

## 4. Broker configuration (topology as code)

Pinned image `apachepulsar/pulsar:5.0.0` by digest (rollback target `apachepulsar/pulsar:4.0.14`, previous LTS). Config lives in-repo at `deploy/pulsar/standalone.conf` + `functions_worker.yml`, rendered per environment; no auto-topic creation.

| Setting | Value | Why |
|---|---|---|
| `allowAutoTopicCreation` | `false` | Topics are provisioned by `omega eventstream apply` (typos fail loudly) |
| `brokerDeduplicationEnabled` | `true` | Outbox sequence ids → broker drops duplicate publishes |
| `delayedDeliveryEnabled` | `true` (bucket-based tracker) | Deadlines as delayed events (§7) |
| `functionsWorkerEnabled` | `true` | Python Functions via process runtime |
| `authenticationEnabled` / `authorizationEnabled` | `true` / `true` (even on loopback) | Defense in depth; same code path in every stage |
| `authenticationProviders` | `AuthenticationProviderToken` (RS256; broker holds **public key only**) | Private signing key never leaves the operator secret store |
| `superUserRoles` | `omega-admin` | Used only by the reconcile command |
| `brokerServicePortTls` / `webServicePortTls` | `6651` / `8443` (Stage 3+) | TLS on any non-loopback hop |
| `managedLedgerDefaultEnsembleSize/WriteQuorum/AckQuorum` | `1/1/1` | Single bookie (documented limit) |
| BookKeeper `journalSyncData` | `true` | fsync before ack — no acked message lost on crash |
| `backlogQuotaDefaultRetentionPolicy` | `producer_request_hold` | Fail closed (outbox buffers) instead of silently evicting |
| JVM | `-Xms1g -Xmx1g -XX:MaxDirectMemorySize=1g` (tunable) | Fits the box (≈8 GB available) and a 3 GB Railway service |

### 4.1 Tenancy, namespaces and policies

Tenant **`omega`** (admin role `omega-admin`). Namespaces carry policy; topics are families.

| Namespace | Topics (`persistent://omega/<ns>/<topic>`) | Retention | Dedup | Schema compat | Notes |
|---|---|---|---|---|---|
| `sdlc` | `pr`, `review`, `ci`, `pr-state` (compacted) | 7 d / 5 GB | on | BACKWARD_TRANSITIVE | key = `<repo>#<pr>` |
| `auth` | `session` | 30 d | on | BACKWARD_TRANSITIVE | **E2E-encrypted payloads** (Pulsar `CryptoKeyReader`) |
| `job` | `lifecycle` | 7 d | on | BACKWARD_TRANSITIVE | key = job id |
| `sandbox` | `lifecycle` | 48 h | on | BACKWARD_TRANSITIVE | key = sandbox id |
| `task` | `state` (compacted), `heartbeat` | 48 h (state: compacted forever) | on | BACKWARD_TRANSITIVE | key = task id |
| `memory` | `hindsight` | 30 d | on | BACKWARD_TRANSITIVE | teach failures (00041) |
| `control` | `signal`, `action`, `hitl`, `deadline` | 30 d | on | BACKWARD_TRANSITIVE | `deadline` uses delayed delivery |
| `audit` | `log` | 400 d, offload > 1 GB to tiered storage (Stage 3) | on | FULL_TRANSITIVE | append-only record of every merge/refusal/deploy |
| `system` | `retry.<sub>`, `dlq.<sub>` | 30 d | on | n/a (envelope bytes) | per-subscription retry/DLQ |

Partitioning: non-partitioned topics at our volume (< 10 msg/s); `sdlc/pr` and `task/state` declared as 4-partition topics so Key_Shared can scale consumers later without re-keying. Message key is the aggregate id, so per-aggregate order is preserved on every topic.

### 4.2 Roles and permissions (JWT subjects)

| Role | Produce | Consume | Used by |
|---|---|---|---|
| `omega-admin` | — | — | `omega eventstream apply` only (superuser) |
| `omega-box` | all `omega/*` except `audit` | all | box services, handlers |
| `omega-audit` | `omega/audit` | `omega/control` | audit projector |
| `omega-vps-leaf` | `auth`, `job`, `sdlc/ci`, `memory` | none | VPS publisher |
| `omega-ingress` | `sdlc` | none | Railway GitHub ingress (Stage 3) |
| `omega-dashboard` | none | all (Reader only) | Command Center bridge |
| `omega-functions` | `sdlc`, `control` | `sdlc` | Pulsar Functions |

Tokens live in 0600 files, referenced by `OMEGA_EVENTSTREAM_TOKEN_FILE` (never argv, env value, or logs). Rotation: re-issue token file → client re-reads on `AuthenticationError` event (Pulsar supports auth refresh on reconnect).

## 5. Event contract

### 5.1 Envelope: CloudEvents 1.0 (structured JSON)

```json
{
  "specversion": "1.0",
  "id": "01JB6Z3K6H9Q4Y5M2V7T8R1C0D",          // ULID; equals GitHub X-GitHub-Delivery when sourced from a webhook
  "source": "github://swcstudiospace/Omega-Prime",
  "type": "omega.sdlc.pr.pushed",
  "subject": "pr/75",
  "time": "2026-10-23T06:40:00+11:00",
  "datacontenttype": "application/json",
  "dataschema": "urn:omega:schema:omega.sdlc.pr.pushed:1",
  "traceparent": "00-…",                          // CE distributed-tracing extension
  "omegakey": "Omega-Prime#75",                    // partition/ordering key
  "omegatask": "00046", "omegagraph": "ut-…", "omegaheadsha": "b8eb05f…",
  "data": { "repo": "Omega-Prime", "number": 75, "head_sha": "b8eb05f…", "actor": "SomeRandmGuyy" }
}
```

- `ce_*` attributes are duplicated into Pulsar **message properties** (`ce_id`, `ce_type`, `ce_source`, `ce_subject`, `ce_time`, `traceparent`) so filtering/routing never decodes the payload.
- `data` is redacted at the producer with the existing `omega_prime.dashboard.redact.redact_value` before it is persisted; secrets never reach the broker.
- Event `type` keeps 00043's dotted taxonomy, re-rooted at `omega.` (`omega.sdlc.pr.*`, `omega.ci.*`, `omega.auth.*`, `omega.job.*`, `omega.sandbox.*`, `omega.task.*`, `omega.mem.*`, `omega.hitl.*`, `omega.deploy.*`, `omega.control.*`, `omega.audit.*`).

### 5.2 Schemas (single source of truth)

- `omega_prime/eventstream/schemas/<type>/<major>.json` — JSON Schema 2020-12 per event type `data`. Python models (pydantic v2, frozen) and TS types are **generated** from these files; a CI check fails if generated code drifts.
- The **envelope** is registered in the Pulsar schema registry per topic (JSON schema type) with the namespace compatibility policy, so an incompatible producer is rejected by the broker, not discovered by a consumer.
- Payload validation happens in the SDK on publish (fail fast) and on consume (poison → DLQ, never crash the loop).
- Versioning: additive changes in place; breaking changes = new major `type` suffix (`…pushed.v2`) with dual-publish during migration.

### 5.3 Type → topic catalog (excerpt; full table generated from `taxonomy.py`)

| Type | Topic | Key | Producer | Main consumers |
|---|---|---|---|---|
| `omega.sdlc.pr.{opened,pushed,ready,converted_to_draft,merged,closed}` | `sdlc/pr` | `repo#pr` | ingress | gate, cc-bridge, deadline-scheduler |
| `omega.sdlc.review.{greptile_scored,approved,changes_requested,commented}` | `sdlc/review` | `repo#pr` | ingress / `greptile-score` Function | review, gate |
| `omega.ci.run.{started,passed,failed}` · `omega.ci.branch.{passed,failed}` | `sdlc/ci` | `repo#pr` / `repo@branch` | ingress, VPS leaf | gate, blocked |
| `omega.auth.session.{login_started,login_succeeded,login_failed,expired,logout}` | `auth/session` | `host:tool` | VPS leaf, box | escalator (HITL card on expiry), cc-bridge |
| `omega.job.{queued,started,succeeded,failed,cancelled}` | `job/lifecycle` | job id | VPS leaf, box | blocked, cc-bridge |
| `omega.sandbox.{created,started,ready,unhealthy,oom,exited,removed}` | `sandbox/lifecycle` | sandbox id | `podman events` bridge | sandbox supervisor, blocked |
| `omega.task.{state_changed,heartbeat}` | `task/state`, `task/heartbeat` | task id | locked `tasks.json` helpers | cc-bridge, escalator |
| `omega.mem.hindsight.{retained,teach_failed}` | `memory/hindsight` | bank | VPS leaf | cc-bridge, escalator |
| `omega.control.signal.{gate,blocked,review}` | `control/signal` | `repo#pr` | handlers | escalator, cc-bridge |
| `omega.control.action.{merge_requested,merged,merge_refused,fix_requested}` | `control/action` | `repo#pr` | gate / escalator | audit |
| `omega.control.deadline.{greptile_review_due,heartbeat_due,hitl_followup_due}` | `control/deadline` (delayed) | aggregate | handlers | deadline handlers |
| `omega.hitl.{asked,answered}` | `control/hitl` | task id | escalator / parent | cc-bridge (Follow Ups) |
| `omega.audit.*` | `audit/log` | aggregate | audit projector | humans, replay |

## 6. Delivery semantics

### 6.1 Producer: transactional outbox → broker dedup (effectively-once publish)
1. `publish(event)` validates + redacts, then appends to a local SQLite (WAL, `synchronous=FULL`) outbox in the **caller's** transaction where one exists (e.g. the `tasks.json` lock section writes the outbox row before releasing).
2. An outbox relay task wakes on an `asyncio.Event` (set by every insert and by the client's *connected* callback) — **no polling interval**.
3. Each row's monotonically increasing `rowid` is sent as the Pulsar `sequence_id` with a stable `producer_name` (`<site>-<component>`); `initial_sequence_id` = last acked rowid. A crash between send and mark-sent → resend → **broker dedup drops it**.
4. Row marked `sent` only after the broker ack (`send` future resolves). Broker unreachable → rows accumulate; bounded by `OMEGA_EVENTSTREAM_OUTBOX_MAX_BYTES` with a health signal at 80 %.

### 6.2 Consumer runtime
- **Durable subscriptions**, `initial_position=Earliest` for new subscriptions, individual acks, `batch_index_ack_enabled=True`.
- Subscription type per role: **Key_Shared** (auto-split hash) for handlers that may scale (`gate`, `blocked`, `review`, `escalator`) — per-key order + parallelism; **Failover** for the singleton `merge-executor` (exactly one active merger); **Exclusive** for `audit-projector`; **Reader** (non-durable, seek by time) for Command Center live tail and replay.
- Bounded concurrency: one in-flight message per key, `max_in_flight` global (default 16); back-pressure through `receiver_queue_size`.
- Handlers return `Ack | Retry(after, reason) | DeadLetter(reason)`; typed exceptions map to the same (`RetryableError`, `PermanentError`). Unknown exceptions → Retry.

### 6.3 Idempotency
- Consumer-side dedup store keyed by `(subscription, ce_id)` in SQLite (box) — claim-before-effect with owner token (reuses PR #75's claim ledger semantics). Effects that hit external systems carry the event id as their own idempotency key (e.g. the merge is head-SHA-pinned and refuses on `HeadMovedError`).

### 6.4 Retry and dead-letter (portable, deterministic)
Pulsar's built-in retry-letter (`enableRetry`/`reconsumeLater`) is Java-only and docs warn `maxRedeliverCount` can reset with negative acks (E13). So the SDK implements it explicitly, identically in Python and TS:
- `Retry(after)` → republish the original envelope to `omega/system/retry.<subscription>` with `deliver_after = backoff(attempt)` (decorrelated jitter: base 1 s, cap 10 min, attempts 1…5), properties `omega_attempt`, `omega_origin_topic`, `omega_last_error` (sanitized); then **ack** the original. The retry topic is part of the same subscription's topic list.
- After `max_attempts` → publish to `omega/system/dlq.<subscription>` (with full failure history) and ack. DLQ subscription `dlq-triage` raises an `omega.control.signal.blocked` (Command Center red badge + Follow Up).
- Transient broker-level failures (consumer disconnect) use Pulsar redelivery of unacked messages; `negative_ack_redelivery_delay_ms` = 1000 for in-process crashes.
- Replay: `omega eventstream replay --subscription gate --from 2026-10-22T09:00+11:00` (reset cursor by time via admin REST) and `--dlq` to re-drive DLQ entries after a fix.

### 6.5 Ordering
Guaranteed per key per topic. Cross-topic order is **not** assumed: every handler re-reads authoritative state (GitHub head SHA, `tasks.json`, compacted `pr-state`) before acting, so out-of-order arrival can only cause a no-op re-evaluation.

## 7. Timers are banned: how every "periodic" need becomes an event

| Old timer routine | Event-driven replacement |
|---|---|
| 5-min CI/CD sweep, per-PR Greptile loops | `omega.sdlc.pr.*`, `omega.ci.*`, `omega.sdlc.review.*` events from ingress (L1–L11) |
| "Nudge Greptile once after 20 min" | On `pr.pushed`, publish `control.deadline.greptile_review_due` with `deliver_after=20m`, key `repo#pr`, `omegaheadsha`. When delivered: if a Greptile review for that head exists (compacted `pr-state`) → ack no-op; else nudge once and record in audit. A new push supersedes (head SHA mismatch → no-op) |
| 30-min blocked follow-up | On `task.state_changed → blocked`, publish `hitl_followup_due` (delayed); answered/unblocked supersedes |
| Heartbeat staleness check | Each `task.heartbeat` publishes `heartbeat_due` (delayed = stale threshold); a later heartbeat supersedes by sequence |
| Hourly digest | Dropped; on-demand query over `audit/log` + compacted `task/state` |
| Dashboard 5 s / 15 s collectors | `cc-bridge` Reader nudges the existing `poller.nudge(source)` per event; collectors keep only a startup sync |
| `omega_prime/cron` interval jobs, routine schedules (`sweep` weekdays 16:00, `nightly`) | Each mapped in phase 107 to a trigger event (e.g. sweep runs on `omega.x.mention.received` or explicit `/omega-sweep`; nightly maintenance runs on `omega.deploy.succeeded` / `pr.merged` to main). Anything with no natural trigger is removed or made on-demand |

Delayed delivery is an event the system emits *because something happened*; nothing wakes up on a clock to check. A CI gate (`test_no_timer_routines.py`) fails the build on new `cron`/`schedule`/`APScheduler`/`while True: … sleep(...)` routine patterns outside the allowlisted bounded I/O backoff in `eventstream/backoff.py` and the deletion-pending legacy files (removed in 107).

## 8. Pulsar Functions and integrations ("use all the features")

| Feature | Use | Notes |
|---|---|---|
| BookKeeper | Durable ledgers for every topic; journal fsync | single bookie (standalone) |
| Multi-tenancy | tenant `omega`, 9 namespaces, per-role ACLs (§4) | |
| Schema registry | envelope schema per topic + compatibility policies | §5.2 |
| Key_Shared / Failover / Exclusive / Reader | §6.2 | |
| Delayed delivery | deadlines (§7), retry backoff (§6.4) | Shared/Key_Shared only |
| Dedup | outbox sequence ids (§6.1) | |
| Topic compaction | `sdlc/pr-state`, `task/state` latest-state views (TableView/compacted Reader) | |
| E2E encryption | `omega/auth` payloads (RSA keypair via `CryptoKeyReader`) | key files 0600 |
| Pulsar Functions (Python, process runtime, EFFECTIVELY_ONCE) | `greptile-score` (review comment → `review.greptile_scored`, reuses the dashboard `greptile_confidence_score` regexes), `ci-rollup` (check-suite results → `ci.run.passed/failed`), `pr-state-projector` (events → compacted `sdlc/pr-state`) | Pure transforms only; side effects (merge, GitHub writes, HITL) stay in Omega Prime consumers where secrets/audit/tests live. Deployed from `functions/` by `omega eventstream apply` on `deploy.succeeded` |
| Tiered storage | `omega/audit` offload to a **Railway Bucket** (S3-compatible) at Stage 3 | E12 |
| Admin REST | typed async client + declarative `topology.yaml` reconcile (tenants, namespaces, policies, topics, schemas, permissions, functions) with `--dry-run` drift report | runs on deploy/start events |
| WebSocket API | not exposed; the dashboard backend consumes via Python Reader and relays on its existing `/ws/events` | keeps one auth model |
| Geo-replication | not used (needs two clusters reachable both ways) | §12 path |
| Transactions / Scalable Topics | not available in our SDKs (E2, E5, E7) | recorded non-use |
| Pulsar IO connectors | none needed now (ingress is custom for HMAC) | |

## 9. Library architecture

```
omega_prime/eventstream/
  __init__.py          public API: EventBus, CloudEvent, publish/subscribe decorators
  envelope.py          CloudEvent (pydantic v2, frozen), property codec, ULID ids, traceparent
  taxonomy.py          EventType registry: type → topic, key fn, schema ref (generates docs table)
  schemas/             JSON Schema 2020-12 per type (+ generated models in _models.py)
  ports.py             EventBus / Publisher / Subscriber / AdminPort Protocols
  adapters/memory.py   in-process adapter (tests, solo mode)
  adapters/pulsar.py   pulsar.asyncio adapter (auth, TLS, crypto, reconnect hooks)
  outbox.py            SQLite WAL outbox + event-woken relay
  consumer.py          runtime: subscriptions, per-key concurrency, ack/retry/DLQ, shutdown drain
  retry.py, backoff.py decorrelated-jitter policy (pure, unit-tested)
  idempotency.py       claim ledger (subscription, ce_id)
  admin.py             async REST client (httpx) + topology reconcile
  topology.yaml        tenants/namespaces/policies/topics/roles/functions
  config.py            settings: URL, *_FILE secret paths, TLS CA, limits (validated, redacted repr)
  observability.py     OTel meters/spans, structured log fields
  cli.py               `omega eventstream {apply,status,tail,replay,dlq}`
functions/             Pulsar Functions (Python), each with unit tests
packages/eventstream-ts/   @omega-prime/eventstream (types generated from schemas/, Producer/Consumer wrappers, vitest)
deploy/pulsar/         standalone.conf, functions_worker.yml, Dockerfile (Railway), podman unit (box)
deploy/vps-leaf/       systemd unit + config for the VPS publisher
```

Patterns: hexagonal ports/adapters; dependency injection via constructors (no globals); immutable value objects; typed errors; `async with` lifecycles; structured concurrency (`asyncio.TaskGroup`); no `print`; mypy `--strict` and ruff on the package; TS `strict: true`, eslint, no `any`.

**Reconnect/backoff:** the Pulsar C++ core reconnects connections itself; the adapter adds (a) connection-state callbacks → health + outbox relay wake, (b) decorrelated-jitter backoff on producer/consumer *creation* failures, (c) a circuit state (`closed/open/half-open`) surfaced in readiness. Waiting on a backoff is a bounded I/O retry, not a routine.

**Graceful shutdown (SIGTERM/SIGINT):** stop receiving → let in-flight handlers finish (bounded drain) → ack/retry each → close consumers → flush producers → final outbox relay pass → close client. Exit code reflects unclean drain.

## 10. VPS leaf live feed (phase 106)

`omega-eventstream-leaf` (Python, systemd `Restart=on-failure`, `DynamicUser` where possible) with its own SQLite outbox. All sources are **push streams**:

| Source | Mechanism | Event types |
|---|---|---|
| Logins (sshd, sudo) | `journalctl -f -o json` follower filtered by `_SYSTEMD_UNIT`/`SYSLOG_IDENTIFIER` | `omega.auth.session.*` |
| Tool logins (Claude Code, gh, railway CLIs) | inotify on credential-file metadata (never contents) | `omega.auth.session.login_succeeded/expired` |
| Jobs / services | systemd D-Bus `PropertiesChanged` on unit `ActiveState` | `omega.job.*` |
| CI on the VPS (runners, builds) | job wrapper emits start/finish events | `omega.ci.run.*` |
| Hindsight teach (00041) | ultrathink teach wrapper exit status | `omega.mem.hindsight.*` |

Transport: the **box initiates** `ssh -N -R 127.0.0.1:16650:127.0.0.1:6650 root@187.77.130.10` (`ExitOnForwardFailure=yes`, key `/home/box/.ssh/desk_vps_ed25519`), supervised by a box user unit with restart-on-exit. TCP keepalives detect dead links (transport liveness, not a routine). The leaf publishes to `pulsar://127.0.0.1:16650` with role `omega-vps-leaf`; while the tunnel is down, events wait in the leaf outbox and drain on reconnect (relay woken by the client's connected callback). Stage 3 points the leaf at Railway instead; the tunnel is retired.

## 11. Railway deploy spec (Stage 3)

| Item | Spec |
|---|---|
| Service `omega-pulsar` | Dockerfile `deploy/pulsar/Dockerfile` FROM pinned 5.0.0 digest; `bin/pulsar standalone` with `functionsWorkerEnabled=true` from the repo config; 3 GB RAM / 2 vCPU target |
| Volume | `/pulsar/data` (BookKeeper ledgers + RocksDB metadata); Railway volume backups enabled |
| Health | `GET /admin/v2/brokers/health` as Railway healthcheck (deploy readiness gate, not a polling routine of ours) |
| Private network | `omega-pulsar.railway.internal:6650` for Railway services (ingress, functions) |
| External clients (box, VPS) | **HITL-1:** (a) Railway TCP proxy → `6651` TLS + RS256 JWT + namespace ACLs, or (b) Tailscale/WireGuard sidecar so the broker is never public (Pulsar's own guidance, E10). Admin `8443` is never exposed in either option |
| Service `omega-ingress` | Starlette app: verifies `X-Hub-Signature-256` (secret via `SDLC_GITHUB_WEBHOOK_SECRET` variable), maps GitHub payloads → CloudEvents with `id = X-GitHub-Delivery`, publishes via outbox; public HTTPS domain |
| Tiered storage | Railway Bucket → `managedLedgerOffloadDriver=aws-s3` with bucket endpoint; offload policy on `omega/audit` only |
| Variables (names only) | `PULSAR_PUBLIC_KEY_FILE`, `OMEGA_EVENTSTREAM_ADMIN_TOKEN_FILE`, `SDLC_GITHUB_WEBHOOK_SECRET`, `OMEGA_TIERED_STORAGE_*` — values never echoed |
| Cutover | stand up Railway hub → `apply` topology → switch box/VPS client URLs → box outboxes drain to Railway → keep box standalone read-only 7 d for replay → retire |
| Cost note | ~3 GB RAM service + small volume; Railway bills per GB-RAM-month (verify current rate at cutover) |

## 12. Path beyond single node (documented, not built)
3-node cluster on Railway (Oxia metadata service, 3 bookies E/W/A 3/2/2, 2 brokers, Pulsar proxy as the only exposed component) or StreamNative-style managed Pulsar; clients only change `serviceUrl`. Geo-replication becomes possible once two clusters can reach each other.

## 13. Security model
TLS on every non-loopback hop; RS256 JWT per role (§4.2); authorization on; admin API private; payload redaction before persist; E2E encryption for `omega/auth`; secrets only via `*_FILE` paths with 0600 perms (validated at startup, refuse group/world-readable); no secrets in argv, env dumps, logs, events or dashboards; webhook HMAC with constant-time compare; dependency pinning + `pip-audit`/`npm audit` in CI; SBOM for the broker image.

## 14. Observability
OTel metrics through the existing `omega_prime/telemetry` OTLP exporter: `eventstream.publish.count/latency`, `outbox.depth/bytes/oldest_age`, `consume.count/latency{outcome}`, `retry.count`, `dlq.count`, `handler.in_flight`, `connection.state`. Spans: publish → consume linked by `traceparent`. Logs: JSON with `ce_id`, `ce_type`, `subscription`, `attempt`, `trace_id`; no payload bodies at INFO. Health: `/healthz` (process) and `/readyz` (broker connected, outbox < 80 %, no open circuit). Broker stats (backlog, msg rate) are read **on demand** via admin REST by `omega eventstream status` and the Command Center panel — no scraping loop.

## 15. Failure modes (FMEA)

| Failure | Detection | Behaviour | Data loss? |
|---|---|---|---|
| Broker down / restarting | connection callback | producers buffer in outbox; consumers reconnect with backoff | No (outbox + durable cursors) |
| Disk full on broker | backlog quota, readiness | `producer_request_hold` → outbox buffers; alert event on recovery | No |
| Poison message | schema/handler `PermanentError` | DLQ with history; blocked signal | No (in DLQ) |
| Handler crash mid-effect | unacked → redelivery | claim ledger + head-pinned effects make it a no-op or safe retry | No / no double effect |
| Duplicate publish (crash after send) | broker dedup by sequence id | dropped at broker | No duplicates |
| Tunnel/VPS link down | leaf client disconnected | leaf outbox buffers; drains on reconnect | No |
| Schema-incompatible producer | broker rejects | publish fails fast in CI/dev; outbox row marked `rejected` + alert | No silent corruption |
| Token expired / revoked | `AuthenticationError` | re-read token file, reconnect; alert if still failing → HITL | No |
| Clock skew | `time` is producer time; ordering uses broker sequence | no correctness impact | — |
| Railway redeploy | volume persists; health-gated | clients reconnect | No |
| Single-node loss of volume | Railway backup restore; outboxes re-drive unacked rows | RPO = last backup for already-acked events (documented) | Possible (acknowledged limit of single node) |

## 16. Test strategy
- **Unit** (pytest, no broker): envelope round-trip, property codec, taxonomy completeness (every type has topic + schema + key), backoff math, retry/DLQ decisions, outbox state machine, idempotency claims, config secret-file validation, redaction.
- **Property-based** (hypothesis): envelope encode/decode, outbox crash-at-any-step never loses or duplicates.
- **Contract**: CloudEvents 1.0 conformance; every JSON Schema has valid + invalid fixtures; generated Python/TS models in sync (drift check).
- **Integration** (CI service container `apachepulsar/pulsar:5.0.0@digest`, standalone): topology apply idempotency, publish/consume, Key_Shared per-key ordering, Failover singleton, delayed delivery, retry→DLQ path, broker dedup on resend, schema-compat rejection, compaction view, E2E encryption, auth/ACL denial.
- **Chaos**: kill broker mid-stream → zero loss, zero duplicate effects after recovery; kill consumer mid-handler; tunnel drop for the leaf.
- **TS**: vitest unit + the same integration matrix subset.
- **Gates**: `ruff check`, `ruff format --check`, `mypy --strict omega_prime/eventstream`, `pytest`, `polyglot-check --changed`, no-timer gate, coverage ≥ 90 % on `eventstream/`.

## 17. Phases (milestone v21 "Event Stream")
104 decision + Railway spec (this doc) · 105 core library (Python + TS, adapters, outbox, consumer runtime, admin reconcile, functions scaffold, tests) · 106 box hub + VPS leaf live feed · 107 ADLC taxonomy wiring + timer retirement + Command Center bridge · 108 ops (metrics, health, DLQ tooling, chaos, no-timer gate, Railway Dockerfile/cutover runbook).

## 18. Open questions for Ove (HITL, ≤ 4)
1. **Stage 3 perimeter:** Railway TCP proxy with TLS + JWT (simple, public port) **or** a private Tailscale/WireGuard link (Pulsar's recommendation)? Default: TLS + JWT now, private link as a follow-up.
2. **Broker pin:** 5.0.0 LTS (3 weeks old) or 4.0.14 (previous LTS, mature)? Default: 5.0.0 with 4.0.14 as the tested rollback.
3. **TypeScript consumers:** which Node surfaces should use `@omega-prime/eventstream` first (omp CLI, Herdr, a future web UI)? Default: ship the SDK + tests, wire nothing Node-side until named.
4. **Railway spend** for a ~3 GB RAM Pulsar service + bucket at cutover. Default: proceed at cutover per Ove's no-hold posture, report the cost line.
