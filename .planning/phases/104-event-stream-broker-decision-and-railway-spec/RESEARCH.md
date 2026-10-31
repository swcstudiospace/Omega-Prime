# Phase 104 research — broker evidence (verified 2026-10-23 AEDT)

Ove's pasted Pulsar research was treated as input to verify, not as ground truth. Every row below was checked against a primary source or by installing the SDK on the box.

| # | Claim (Ove's research) | Verdict | Evidence |
|---|---|---|---|
| E1 | Pulsar 5.0 LTS exists (Oct 2026) with Scalable Topics, Oxia recommended | **True** | pulsar.apache.org/release-notes: v5.0.0 released 2026-10-05 (LTS); 4.0.14 (previous LTS) and 4.2.5 same day. Blog "Apache Pulsar 5.0 LTS" 2026-10-05 |
| E2 | Scalable Topics usable from Python/TS | **False for us** | docs/5.0.x/concepts-scalable-topics: "require a v5-capable client SDK … The Java v5 client supports scalable topics". Python 3.13 / Node 1.18 are v4-API clients. We use v4 persistent topics |
| E3 | Oxia is the metadata store | **Not for standalone** | docs/next/getting-started-standalone: standalone uses **RocksDB metadata** by default (ZooKeeper opt-in via `PULSAR_STANDALONE_USE_ZOOKEEPER=1`). Oxia matters only for a future multi-node cluster |
| E4 | Python SDK `pulsar-client` is production-grade | **True** | Installed `pulsar-client==3.13.0` (C++ 4.2.0) on the box. `pulsar.asyncio` exposes Client/Producer/Consumer/Reader; async `subscribe` has `consumer_type`, `key_shared_policy`, `dead_letter_policy`, `negative_ack_redelivery_delay_ms`, `crypto_key_reader`, `regex_subscription_mode`, `is_pattern_topic`; async `send` has `partition_key`, `ordering_key`, `sequence_id`, `deliver_after`, `deliver_at`, `event_timestamp`. Releases 3.9 (Dec 2025, async consumer) → 3.13 (2026, async Reader) |
| E5 | Python SDK supports transactions | **False** | `pulsar.Client` has no `new_transaction` in 3.13.0 (checked by introspection). Transactions are Java-client only for our purposes |
| E6 | TS/Node SDK `pulsar-client` is production-grade | **True** | npm `pulsar-client@1.18.0` (2026-06-23, Apache-2.0, ~20k weekly downloads), bundled `index.d.ts`, prebuilt N-API binaries for linux glibc/musl x64/arm64. Typings include `deadLetterPolicy`, `negativeAcknowledge`, `deliverAfter`/`deliverAt`, `keySharedPolicy`, `CryptoKeyReader`, `AuthenticationToken`/`Tls`/`Oauth2` |
| E7 | Node SDK supports transactions | **False** | No `Transaction` symbol in `pulsar-client@1.18.0/index.d.ts` |
| E8 | Railway one-click template, volume `/pulsar/data`, ports 6650/9091/8080, pulsar-express GUI, health `/admin/v2/clusters` | **True, but weak** | railway.com/deploy/pulsar-1: third-party (INAPP), **2 total deploys**, ZooKeeper metadata, auto-topic-creation on, broker advertises the public domain. We will NOT depend on it; we ship our own pinned Dockerfile/config with the same volume/ports |
| E9 | Standalone is single-node only (no HA/geo) | **True** | Standalone runs broker + bookie + metadata in one JVM; Apache docs label standalone "for local development and testing" |
| E10 | Safe to expose on the public internet | **False** | getting-started-standalone + security-overview: "A Pulsar cluster is not intended to be exposed on the public internet … expects network perimeter security"; "By default, Pulsar configures no encryption, authentication, or authorization" |
| E11 | Pulsar Functions (Python) | **True** | docs/5.0.x/functions-quickstart: `functionsWorkerEnabled=true` in standalone.conf; Python via process runtime (`pythonInstanceLocation`) |
| E12 | Tiered storage to S3/GCS | **True + Railway target exists** | docs.railway.com/storage-buckets: Railway Buckets are private S3-compatible storage (all plans, $0.015/GB-month, free S3 ops) |
| E13 | Delayed delivery, DLQ, Key_Shared, schema registry, dedup, compaction | **True** | concepts-messaging docs; DLQ only on Shared/Key_Shared; docs warn `maxRedeliverCount` may not be honored with negative-ack unless retry is enabled (Java `enableRetry`) — Python/Node have no retry-letter API, so we implement retry topics ourselves (see DESIGN §6.4) |
| E14 | Box can host it | **True** | Box: 8 cores, 15 GB RAM (~8 GB available), 46 GB free disk, podman present, no Java (container brings JVM). Standalone container ~2–3 GB RSS budget |

## Box topology facts (from prior 00043 work)
- Box has no public endpoint (dashboard binds 127.0.0.1:7421; no Tailscale on the box). Box can SSH to the VPS (`/home/box/.ssh/desk_vps_ed25519`), so box-initiated reverse tunnels are the only inbound path from the VPS today.
- Dragonfly / Greptime / Timescale are off-limits (store-lock test `omega_prime/tests/test_store_lock.py`).

## Timer inventory to retire (phase 107 input)
`omega_prime/cron/{scheduler.py,apscheduler_backend.py,heartbeat.py,heartbeat_runtime.py,desk_driver.py}` (interval jobs, `interval_seconds`, APScheduler), `omega_prime/routines/{sweep,nightly,desk_lead}` (cron schedules e.g. `0 16 * * 1-5`), `omega_prime/prime/heartbeat.py`, `omega_prime/tools/{heartbeat.py,cron_admin.py}`, `omega_prime/grokbot/{findings_sync.py,pin_bump.py}` (to be confirmed per file in phase 107 discuss), plus the dashboard collectors' 5 s/15 s loops (00043 listener-architecture §1 path C).
