# 00046 UPLIFT — Autonomous Event-Driven ADLC Event Stream (GoTxCoT, first uplift)

- Run: `/gsd-autonomous ultrathink orchestrate workflowz …` (Ove, 2026-10-23 AEDT) · Branch `gsd/00046-event-stream` · Milestone v21 phases 104–108
- Second uplift with live tracker URLs: see the `<ISSUES>` block appended at the end of this file and `TRACKING.md`.

## 1. First uplift (XML)

```xml
<BUILD_PROMPT>
  <ORIGINAL>ultrathink orchestrate workflowz Program our codebase with code quality and programming patterns that are State-of-the-Art, Enterprise-Grade, Production-Ready and Best-in-Class. We're migrating Omega Prime into a world class Programming Bot and has identified an optimisation opportunity. Routines that run on timers waste too much usage so they have now been banned from Omega Prime altogether. Even if it seems like a timer is needed, it's not needed. So add to your memory firstly that you're forbidden from creating Routines that aren't Event Driven. We're trying to program additional code for Autonomous Event-Driven ADLC (Agentic Development Lifecycle) using Railway for hosting. We need something to actually handle Event Streams. Apparently Kafka and Pulsar etc are too heavy? I mean apparently we can only use NATS but if possible to use with Python or TypeScript SDK's Apache Pulsar hosted in Railway or if it doesn't have those then your choice of Event Stream.

If we can use Apache Pulsar we need to build out the complete spec. I mean here's some research but you might need to build something which uses all the features and potential integrations like bookkeeper as well. Here's a bit of information but yeah:</ORIGINAL>
  <ORIGINAL_ATTACHMENT kind="operator-pasted-research" treatment="verify, not ground truth">Pulsar 5.0 LTS capabilities (compute/storage separation via BookKeeper, ZooKeeper/Oxia metadata, Exclusive/Shared/Failover/Key_Shared subscriptions, multi-tenancy, geo-replication, retention/compaction/dedup/tiered storage, transactions, schema registry, delayed delivery, chunking, DLQ/retry, Pulsar Functions and IO, REST admin, Scalable Topics); Railway one-click single-node standalone template (/pulsar/data volume, 6650/9091/8080, pulsar-express GUI, /admin/v2/clusters health, single-node only); official Python and TypeScript `pulsar-client` SDKs with produce examples; fit notes for agent substrate. Verified claim-by-claim in RESEARCH.md E1–E14.</ORIGINAL_ATTACHMENT>
  <SYSTEM_ROLE>Omega Prime maintainer running a GSD autonomous milestone: principal-level distributed-systems engineer who decides the event backbone with evidence, writes an enterprise-grade spec, then builds it phase by phase with tests, and ships through PRs gated on Greptile 5/5 + green CI.</SYSTEM_ROLE>
  <APP_CONTEXT>
    <REPO>swcstudiospace/Omega-Prime (Python 3.13 package omega_prime/, Starlette Command Center dashboard, OTLP telemetry, sdlc_events handler core in PR #75, 00043 event-mesh decision + 23-listener catalog).</REPO>
    <SITES>Box = always-on hub with no public endpoint; VPS (Hostinger) = leaf publisher reachable by box-initiated SSH; Railway = later hub. Dragonfly/Greptime/Timescale are off-limits (store-lock test).</SITES>
    <TIMER_BAN>Routines that run on timers are banned. Every behaviour must be triggered by an event; deadlines become delayed events.</TIMER_BAN>
  </APP_CONTEXT>
  <SCOPE>
    <BROKER_DECISION>Choose Pulsar vs NATS vs other with verified evidence: SDK maturity (Python, TypeScript), Railway single-node viability, durability caveats.</BROKER_DECISION>
    <SPEC>Complete spec using the broker's full feature set where our SDKs support it: tenancy, namespaces/policies, topics, schema registry, subscriptions, delayed delivery, dedup, compaction, DLQ/retry, E2E encryption, Functions, tiered storage, admin REST.</SPEC>
    <LIBRARY>Python core library + TypeScript SDK: CloudEvents 1.0 envelope, typed schemas, outbox producer, durable consumer runtime, retry/DLQ, idempotency, reconnect/backoff, graceful shutdown, admin reconcile, CLI.</LIBRARY>
    <TOPOLOGY>Box hub (Pulsar standalone in podman), VPS leaf live feed (auth/login, CI, jobs, Hindsight), Railway hub spec and cutover runbook.</TOPOLOGY>
    <WIRING>Map the omega.sdlc/ci/auth/job/sandbox/task/mem/hitl/deploy taxonomy onto the bus, wire 00043 handlers and listeners, retire every timer routine, bridge to Command Center.</WIRING>
    <OPS>Metrics, traces, structured logs, health/readiness, DLQ tooling, chaos tests, no-timer CI gate.</OPS>
  </SCOPE>
  <CONSTRAINTS>
    <C>Do not invent repository facts; verify vendor claims against primary sources or by installing the SDK.</C>
    <C>No timer/schedule routines anywhere; bounded I/O retry backoff is allowed and allowlisted.</C>
    <C>Never print or commit secrets; reference *_FILE paths and env var names only.</C>
    <C>Feature branches only; merge via gsd-ship-pr + greploop when Greptile 5/5 + CI green; never force-push main.</C>
    <C>Do not touch the running dashboard on 127.0.0.1:7421 or /workspace/00044-cc.</C>
    <C>Dragonfly is off-limits.</C>
  </CONSTRAINTS>
  <SECURITY_AND_VALIDATION>TLS on non-loopback hops, RS256 JWT roles with namespace ACLs, admin API never public, producer-side redaction, E2E encryption for auth events, schema validation on publish and consume, webhook HMAC constant-time verification, secret files 0600 enforced.</SECURITY_AND_VALIDATION>
  <GRACEFUL_DEGRADATION>Broker or link down → producer outbox buffers and drains on reconnect; consumers resume from durable cursors; poison messages go to DLQ without stopping the loop; readiness reports degraded state truthfully.</GRACEFUL_DEGRADATION>
  <ACCEPTANCE_CRITERIA>
    <AC>Decision recorded in CONTEXT.md with evidence table; DESIGN.md covers topology, contract, semantics, security, FMEA, tests, Railway spec.</AC>
    <AC>Integration tests against the pinned broker prove: no loss across broker restart, broker dedup of resends, per-key ordering, delayed delivery, retry→DLQ, schema-compat rejection, ACL denial.</AC>
    <AC>VPS events (login, job, CI, Hindsight) appear on the box hub within seconds and survive a tunnel outage.</AC>
    <AC>Every former timer routine is either event-triggered, on-demand, or deleted; the no-timer CI gate passes.</AC>
    <AC>ruff, ruff format, mypy --strict (eventstream), pytest, polyglot-check, TS strict + vitest all green; Greptile 5/5 on each PR.</AC>
  </ACCEPTANCE_CRITERIA>
  <OUT_OF_SCOPE>Multi-node Pulsar, geo-replication, Pulsar transactions and Scalable Topics (unsupported by our SDKs), Kafka/AMQP handlers, Dragonfly.</OUT_OF_SCOPE>
  <ASSUMPTIONS>Throughput stays below ~10 msg/s; single-node is acceptable with outbox protection; the box has ~8 GB RAM available for a ~2 GB broker.</ASSUMPTIONS>
  <AMBIGUITIES>Stage 3 network perimeter (public TLS proxy vs private link); 5.0.0 vs 4.0.14 broker pin; first Node consumers of the TS SDK; Railway spend at cutover.</AMBIGUITIES>
</BUILD_PROMPT>
```

## 2. Graph of Thought (7 nodes, source: generated)

| Node | Kind | Depends on | Question |
|---|---|---|---|
| n1 | understand | — | What exactly must the ADLC event stream do, under the timer ban, given 00043's design and the box/VPS/Railway sites? |
| n2 | compare | n1 | Pulsar vs NATS JetStream vs others: do the Python + TS SDKs and Railway single-node meet Ove's bar? |
| n3 | decompose | n1 | What is the event contract: envelope, taxonomy, tenancy/namespaces, topics, schemas, keys? |
| n4 | generate | n2, n3 | What library architecture and delivery semantics give durable, effectively-once, replayable processing in Python and TS? |
| n5 | generate | n2 | What is the topology: box hub, VPS leaf live feed, Railway hub and cutover? |
| n6 | critique | n4, n5 | Where does this fail (single node, public exposure, missing transactions, timer-ban edge cases), and what needs Ove? |
| n7 | synthesize | n3, n4, n5, n6 | What phases, file-disjoint units, tests and gates deliver it? |

## 3. Chain of Thought

### n1 — understand
1. The ask bans timer routines outright and wants an event stream for the Autonomous Event-Driven ADLC on Railway.
2. 00043 already mapped 23 listeners (L1–L23) and a dotted subject taxonomy; those carry over to whichever broker wins.
3. Sites: box hub without public endpoint, VPS reachable only by box-initiated SSH, Railway later; Dragonfly is off-limits.
4. Repo has timer code in `omega_prime/cron`, routine schedules (`sweep`, `nightly`), heartbeats and dashboard collectors — all to retire.
5. Ove's bar is enterprise grade: durability, replay, typed contracts, observability, tests.
Conclusion: Build a broker-agnostic event backbone, wire the 00043 catalog onto it, and remove every timer.

### n2 — compare
1. Pulsar 5.0.0 LTS shipped 2026-10-05; standalone uses RocksDB metadata + embedded BookKeeper (E1, E3).
2. Python `pulsar-client` 3.13 has a full asyncio surface incl. Key_Shared, DLQ, delayed delivery, sequence ids (E4).
3. Node `pulsar-client` 1.18 is official with bundled typings and prebuilt binaries (E6).
4. Neither SDK exposes transactions; Scalable Topics need v5 clients (E2, E5, E7).
5. Railway template is third-party with 2 deploys; we ship our own pinned image/config (E8).
6. Pulsar must not be publicly exposed without TLS/auth or a private link (E10).
Conclusion: Pulsar meets Ove's conditions → chosen, behind an EventBus port with NATS as documented fallback.

### n3 — decompose
1. CloudEvents 1.0 structured JSON envelope, `ce_*` mirrored to message properties.
2. Tenant `omega`, nine namespaces carrying retention/dedup/compat policy.
3. Family topics keyed by aggregate id for per-key ordering.
4. JSON Schema 2020-12 per type → generated pydantic + TS types; envelope registered in Pulsar schema registry.
5. Type catalog generated from `taxonomy.py` (single source).
Conclusion: Contract defined in DESIGN §5; topology-as-code in `topology.yaml`.

### n4 — generate
1. Ports/adapters: EventBus Protocol, memory + Pulsar adapters.
2. SQLite WAL outbox; rowid = Pulsar sequence id → broker dedup gives effectively-once publish.
3. Consumer runtime: Key_Shared/Failover/Exclusive/Reader per role, per-key concurrency, typed outcomes.
4. Portable retry topics via delayed delivery + explicit DLQ (Java-only retry-letter avoided).
5. Idempotency claim ledger (subscription, ce_id); external effects pinned (head SHA).
6. Reconnect callbacks, decorrelated jitter, circuit state, graceful shutdown drain.
Conclusion: DESIGN §6 and §9; same semantics in the TS SDK.

### n5 — generate
1. Box hub: Pulsar standalone in podman on loopback with JWT, volume under `~/.omega-prime/pulsar`.
2. VPS leaf: Python unit with outbox; sources journald, D-Bus, inotify, job wrappers — all push.
3. Transport: box-initiated `ssh -R` tunnel; outbox covers outages.
4. Railway: own Dockerfile, volume, private network, ingress with HMAC, Bucket tiered storage.
5. Cutover runbook: apply topology, switch URLs, drain outboxes, retire box hub after 7 d.
Conclusion: DESIGN §3, §10, §11.

### n6 — critique
1. Single node is a SPOF; outbox + journal fsync + backups bound loss to acked events since last backup.
2. Public exposure risk: TLS + JWT + ACL minimum; private link preferred (HITL-1).
3. No transactions in our SDKs: effectively-once via dedup + idempotency instead.
4. Timer-ban edge cases (Greptile 20-min nudge, heartbeats, follow-ups) become delayed events with supersede-by-version.
5. 5.0.0 is three weeks old: pin digest, keep 4.0.14 rollback tested (HITL-2).
Open questions: HITL-1 perimeter, HITL-2 pin, HITL-3 first TS consumers, HITL-4 Railway spend (all have defaults; non-blocking).

### n7 — synthesize
1. Phase 104: decision, DESIGN, CONTEXT, RESEARCH, topology-as-code draft.
2. Phase 105: `omega_prime/eventstream` + `packages/eventstream-ts` + functions scaffold + integration tests.
3. Phase 106: box hub unit + VPS leaf + tunnel + live feed sources.
4. Phase 107: taxonomy wiring of L1–L23, delayed-event deadlines, timer retirement, Command Center bridge.
5. Phase 108: OTel metrics, health, DLQ CLI, chaos, no-timer gate, Railway Dockerfile + cutover runbook.
6. Each phase ships as its own PR through gsd-ship-pr + greploop.
Conclusion: WORKFLOW = 104 → 105 → (106 ∥ 107-prep) → 107 → 108; file-disjoint units per plan.


## Second uplift — materialised trackers (2026-10-23 AEDT)

```xml
<ISSUES graph_id="ut-tnbq4x-7600742c">
  <TASK linear="SPE-19585" notion="https://app.notion.com/p/401bc1a0c7ae81939dcbda1863cb49ed"/>
  <ISSUE node="n1" linear="SPE-19586" notion="https://app.notion.com/p/401bc1a0c7ae81b587e2f1fd410772e3">
    <SUB_ISSUE step="1" linear="SPE-19593" notion="https://app.notion.com/p/401bc1a0c7ae81cc806af4a47f6af6a0"/>
    <SUB_ISSUE step="2" linear="SPE-19594" notion="https://app.notion.com/p/401bc1a0c7ae8161b959ee6a4f88f3ad"/>
    <SUB_ISSUE step="3" linear="SPE-19595" notion="https://app.notion.com/p/401bc1a0c7ae811cba20e145980c05b8"/>
    <SUB_ISSUE step="4" linear="SPE-19596" notion="https://app.notion.com/p/401bc1a0c7ae812094f4eb507b863eef"/>
    <SUB_ISSUE step="5" linear="SPE-19597" notion="https://app.notion.com/p/401bc1a0c7ae8174882de5be69986b8c"/>
  </ISSUE>
  <ISSUE node="n2" linear="SPE-19587" notion="https://app.notion.com/p/401bc1a0c7ae819ea4a9d47ee730d50a">
    <SUB_ISSUE step="1" linear="SPE-19598" notion="https://app.notion.com/p/401bc1a0c7ae81e5ba5dd9639cc53c71"/>
    <SUB_ISSUE step="2" linear="SPE-19599" notion="https://app.notion.com/p/401bc1a0c7ae8190bd34fe0d7c38a33e"/>
    <SUB_ISSUE step="3" linear="SPE-19600" notion="https://app.notion.com/p/401bc1a0c7ae81a389d6d1ea3f9b82ce"/>
    <SUB_ISSUE step="4" linear="SPE-19601" notion="https://app.notion.com/p/401bc1a0c7ae81dbbe6eea3958354244"/>
    <SUB_ISSUE step="5" linear="SPE-19602" notion="https://app.notion.com/p/401bc1a0c7ae813eb3e9fecc19762a3c"/>
    <SUB_ISSUE step="6" linear="SPE-19603" notion="https://app.notion.com/p/401bc1a0c7ae81a29fa6e346f222273f"/>
  </ISSUE>
  <ISSUE node="n3" linear="SPE-19588" notion="https://app.notion.com/p/401bc1a0c7ae81d88a1afaf00ca3c36a">
    <SUB_ISSUE step="1" linear="SPE-19604" notion="https://app.notion.com/p/401bc1a0c7ae81b68f90cd6dae019921"/>
    <SUB_ISSUE step="2" linear="SPE-19605" notion="https://app.notion.com/p/401bc1a0c7ae81429228c6f843f31fa3"/>
    <SUB_ISSUE step="3" linear="SPE-19606" notion="https://app.notion.com/p/401bc1a0c7ae81bd9ac8ef040c6a91a2"/>
    <SUB_ISSUE step="4" linear="SPE-19607" notion="https://app.notion.com/p/401bc1a0c7ae8151abbfcbda2e9dfe4f"/>
    <SUB_ISSUE step="5" linear="SPE-19608" notion="https://app.notion.com/p/401bc1a0c7ae81068fbedf4ea91a38db"/>
  </ISSUE>
  <ISSUE node="n4" linear="SPE-19589" notion="https://app.notion.com/p/401bc1a0c7ae81849519ffe47309be9b">
    <SUB_ISSUE step="1" linear="SPE-19609" notion="https://app.notion.com/p/401bc1a0c7ae81d894e9dee903d2afb2"/>
    <SUB_ISSUE step="2" linear="SPE-19610" notion="https://app.notion.com/p/401bc1a0c7ae816cab68f2bb60dbf86b"/>
    <SUB_ISSUE step="3" linear="SPE-19611" notion="https://app.notion.com/p/401bc1a0c7ae81b7beffc4431f73d604"/>
    <SUB_ISSUE step="4" linear="SPE-19612" notion="https://app.notion.com/p/401bc1a0c7ae81fc86dfcf1b5b6828c7"/>
    <SUB_ISSUE step="5" linear="SPE-19613" notion="https://app.notion.com/p/401bc1a0c7ae8114b51cfc53893318b8"/>
    <SUB_ISSUE step="6" linear="SPE-19615" notion="https://app.notion.com/p/401bc1a0c7ae812e8661e93f61874f66"/>
  </ISSUE>
  <ISSUE node="n5" linear="SPE-19590" notion="https://app.notion.com/p/401bc1a0c7ae81868a1fc639ec2db0e9">
    <SUB_ISSUE step="1" linear="SPE-19614" notion="https://app.notion.com/p/401bc1a0c7ae814b9e1be011e4382836"/>
    <SUB_ISSUE step="2" linear="SPE-19616" notion="https://app.notion.com/p/401bc1a0c7ae81ea8e97f0208ab89e14"/>
    <SUB_ISSUE step="3" linear="SPE-19617" notion="https://app.notion.com/p/401bc1a0c7ae81df828ad88af2c9e77e"/>
    <SUB_ISSUE step="4" linear="SPE-19618" notion="https://app.notion.com/p/401bc1a0c7ae81749c06d6a6e6e06839"/>
    <SUB_ISSUE step="5" linear="SPE-19619" notion="https://app.notion.com/p/401bc1a0c7ae81439d3fee66105f4d60"/>
  </ISSUE>
  <ISSUE node="n6" linear="SPE-19591" notion="https://app.notion.com/p/401bc1a0c7ae817da3d3f943420d243d">
    <SUB_ISSUE step="1" linear="SPE-19620" notion="https://app.notion.com/p/401bc1a0c7ae81ccb23bdf7a6896f5f2"/>
    <SUB_ISSUE step="2" linear="SPE-19621" notion="https://app.notion.com/p/401bc1a0c7ae81d1a708e35eadc3eadc"/>
    <SUB_ISSUE step="3" linear="SPE-19622" notion="https://app.notion.com/p/401bc1a0c7ae8192b972d8ba465024b0"/>
    <SUB_ISSUE step="4" linear="SPE-19623" notion="https://app.notion.com/p/401bc1a0c7ae81f4ab07e5a7c94704b2"/>
    <SUB_ISSUE step="5" linear="SPE-19624" notion="https://app.notion.com/p/401bc1a0c7ae81d69fc8fcd2b027da18"/>
  </ISSUE>
  <ISSUE node="n7" linear="SPE-19592" notion="https://app.notion.com/p/401bc1a0c7ae81b5a554d026f120164d">
    <SUB_ISSUE step="1" linear="SPE-19625" notion="https://app.notion.com/p/401bc1a0c7ae8104a9d8c9271ccbc77e"/>
    <SUB_ISSUE step="2" linear="SPE-19626" notion="https://app.notion.com/p/401bc1a0c7ae8102914ec7921b135c95"/>
    <SUB_ISSUE step="3" linear="SPE-19627" notion="https://app.notion.com/p/401bc1a0c7ae818fa6a7d8e644693e7a"/>
    <SUB_ISSUE step="4" linear="SPE-19628" notion="https://app.notion.com/p/401bc1a0c7ae81859c83c3d7ba5512e3"/>
    <SUB_ISSUE step="5" linear="SPE-19629" notion="https://app.notion.com/p/401bc1a0c7ae81ada448e6fbd09dbfd5"/>
    <SUB_ISSUE step="6" linear="SPE-19630" notion="https://app.notion.com/p/401bc1a0c7ae81b19a94d2404a25c711"/>
  </ISSUE>
</ISSUES>
<TRACKER_GAPS>none</TRACKER_GAPS>
```
