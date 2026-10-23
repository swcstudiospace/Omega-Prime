# Omega Prime Event Stream SDK (TypeScript)

`@omega-prime/eventstream` is a strict-ESM SDK for validated CloudEvents 1.0, the concrete Omega event types registered by the SDK and the separately registered open `omega.audit.*` family. It does not claim that the source catalog is authoritatively closed. The package contains broker-agnostic ports, an event-woken in-memory adapter, retry/DLQ policy, a process-local outbox and a lazy Apache Pulsar adapter. It does not include a Node consumer or claim restart-durable TypeScript storage.

## Install and build

The native Pulsar client is an optional peer. For a broker-free install, omit peers:

```bash
npm install @omega-prime/eventstream --omit=peer
```

Opt in to the pinned native adapter explicitly when needed:

```bash
npm install @omega-prime/eventstream pulsar-client@1.18.0
```

The Pulsar peer is loaded only by `createPulsarEventBus()` or `loadPulsarClient()`; importing the package entry does not import or initialize native code. Follow the native client’s platform-specific installation instructions in the opted-in environment. No consumer is implemented in this package.

Package scripts:

| Script | Purpose |
| --- | --- |
| `npm run test` | All Vitest tests, including the mandatory Draft 2020-12 schema contract gate. |
| `npm run test:unit` | Broker-free unit and adapter tests, excluding only the schema contract test. |
| `npm run test:contract` | The schema-bundle and cross-language contract gate. |
| `npm run typecheck` | Strict TypeScript checking with `tsc --noEmit`. |
| `npm run lint` | ESLint. |
| `npm run build` | TypeScript declarations and JavaScript output in `dist/`. |

The contract test reads the 12 canonical bundles from `../../omega_prime/eventstream/schemas/`; it fails closed when bundles, definitions, fixture arrays, references or validator agreement are missing.

## Broker-free use

```ts
import { CloudEvent, MemoryEventBus, PublishRequest } from "@omega-prime/eventstream";

const event = new CloudEvent({
  specversion: "1.0",
  id: "pr-42-pushed",
  source: "https://github.com/example/project",
  type: "omega.sdlc.pr.pushed",
  datacontenttype: "application/json",
  dataschema: "urn:omega:schema:omega.sdlc.pr.pushed:1",
  data: { repo: "example/project", number: 42, head_sha: "abc123", actor: "ci", before_sha: "def456" },
  omegakey: "example/project#42"
});

const bus = new MemoryEventBus();
const subscriber = await bus.subscribe("persistent://omega/sdlc/pr", {
  subscriptionName: "example-worker",
  mode: "Shared"
});
await bus.publish(new PublishRequest({
  event,
  topic: "persistent://omega/sdlc/pr",
  key: "example/project#42"
}));
const delivery = await subscriber.receive();
if (delivery !== undefined) await delivery.ack();
await bus.close();
```

CloudEvent construction checks envelope fields, registered type/schema, payload shape, extensions, traceparent and routing identity. Values and nested JSON are immutable. `toBytes()` and `toProperties()` redact payload/property text before persistence or transport. Redaction is rejected if it changes envelope identity or the event’s routing key.

The memory adapter is process-local, wakes receivers on publish or injected-clock advancement, and has no background poller. It supports delayed delivery, cancellation, seek, bounded in-flight delivery, per-key ordering and redelivery after `nack()` or an unacknowledged close. In-flight holds are owned per receiver: closing one receiver redelivers only the messages it held, and a settlement from a receiver that no longer holds the message (for example after close) is rejected with `ContractError` instead of acknowledging another receiver's work. Delivery properties put caller-supplied properties first and the CloudEvent's own properties last, so envelope identity is authoritative. It is a test adapter, not a broker substitute.

## Pulsar adapter and configuration

```ts
import { createPulsarEventBus, EventStreamConfig } from "@omega-prime/eventstream";

const config = EventStreamConfig.fromEnv();
const bus = await createPulsarEventBus({ config });
```

Auth-family payloads use **native Pulsar end-to-end encryption**, the same mechanism and key material as the Python SDK, so either SDK can decrypt what the other published. Producers for `omega.auth.*` events — and for any system retry/DLQ publish whose `omega_origin_topic` is a `persistent://omega/auth/` topic — are created with `publicKeyPath` = `OMEGA_EVENTSTREAM_ENCRYPTION_KEY_FILE`, `encryptionKey` = `AUTH_ENCRYPTION_KEY_NAME` (`"omega-auth"`) and `cryptoFailureAction: "FAIL"`. Consumers and Readers on `persistent://omega/auth/*` and on every `system/retry.*` / `system/dlq.*` topic are created with `privateKeyPath` = `OMEGA_EVENTSTREAM_PRIVATE_KEY_FILE` and `cryptoFailureAction: "FAIL"`. Poison messages from an auth topic stay encrypted on the DLQ. There is no application-level cipher, plaintext fallback or `omega_encrypted` marker. An auth-family delivery that did not arrive natively encrypted, or whose native decryption failed, is refused as an `AuthenticationError` and left unacknowledged. Missing, rotated-invalid or unusable key files raise `AuthenticationError`, mark the adapter unhealthy and invoke the optional authentication-failure callback; the next encrypted publish (or auth-family delivery) that succeeds after the key files are repaired marks it healthy again, so a publisher-only bus recovers readiness. Encrypting producers register on the broker as `<producerName>-encrypted` (`nativeProducerName()`, same rule as the Python adapter's `native_producer_name`), because retry/DLQ topics carry both auth-origin and plain traffic and Pulsar rejects a second live producer with the same name on one topic. An authentication failure does not acknowledge or business-retry the source delivery; refresh key material and reconnect through the owning service.

Configuration accepts secret **paths**, never secret values in environment variables or argv:

- `OMEGA_EVENTSTREAM_URL` and `OMEGA_EVENTSTREAM_ADMIN_URL` configure endpoints.
- `OMEGA_EVENTSTREAM_TOKEN_FILE`, `OMEGA_EVENTSTREAM_TLS_CA_FILE`, `OMEGA_EVENTSTREAM_ENCRYPTION_KEY_FILE` (PEM public key) and `OMEGA_EVENTSTREAM_PRIVATE_KEY_FILE` (PEM private key) configure file paths. These are the same names the Python `EventStreamConfig` reads.
- `OMEGA_EVENTSTREAM_OUTBOX_MAX_BYTES`, `OMEGA_EVENTSTREAM_MAX_IN_FLIGHT` and `OMEGA_EVENTSTREAM_RECEIVER_QUEUE_SIZE` configure positive integer bounds.

Token and encryption-key paths are never printed. `readSecret()` opens the file without following symlinks and checks that it is a regular file with mode `0600` on every read; every encrypted publish and every auth/redelivery subscription revalidates both key files. `createPulsarEventBus()` reads the token when creating the native client, so token rotation requires the owning service to refresh/recreate that client. The CA path is passed to the native client. Non-loopback endpoints require `pulsar+ssl://`, an `https://` admin endpoint, token file and CA file. The loopback no-auth exception is limited to transport; auth-family payload encryption remains mandatory.

Native producer sends resolve only after the broker receipt. Sequence IDs remain decimal strings/BigInts until checked against JavaScript’s safe integer range for the native API. Producer names are stable per topic. Subscription setup uses `Earliest`, bounded receiver queues and batch-index acknowledgements. `Key_Shared`, `Shared`, `Failover` and `Exclusive` modes are supported; a non-durable `Reader` supports seek, cancellation and close. Ordering is per key/topic according to the selected Pulsar mode; there is no cross-topic global ordering guarantee.

Pulsar’s registry boundary uses native `SchemaType.JSON` with Avro-format metadata. These native schemas are not Draft 2020-12 documents. The TypeScript adapter does not derive its own native schemas: `src/native-schemas.generated.ts` is rendered from the Python SDK's `native_json_schema` (topic records, the redelivery template and sample retry/DLQ records), so a Python and a TypeScript producer always register the same record name, namespace and fields on a shared topic. Regenerate it with `python -m omega_prime.eventstream.native_schema_contract --write` after changing the Python schema; `--check` (run by the Python test suite) fails on drift. SDK payload validators enforce the full event contract; the mandatory contract tests validate the canonical Draft 2020-12 bundles and every embedded valid/invalid fixture with Ajv, then compare their behavior to the typed SDK validators.

## Retry, DLQ and ordering

The retry policy samples first, then caps: `min(600, uniform(1, 3 × previousDelay))` seconds. The effective retry delay is `min(600, max(requestedAfter, policyDelay))`. Unknown/retryable failures retry on business attempts 1–4; attempt 5 goes to DLQ. Permanent failures, explicit dead-letter outcomes and schema poison go directly to DLQ. Failure text/history is redacted before publication and is never empty: `safeFailureReason()` trims the redacted message and falls back to `"unspecified failure"` (bounded to 256 characters), matching Python's `_safe_reason()`, so `new Error()` or `PermanentError("")` still yields a publishable dead letter. A dead letter is published as an `omega.control.signal.blocked` CloudEvent, built exactly like the Python runtime builds it (`buildDeadLetterRequest()`): id `dlq:<subscription>:<source id>`, a `kind: "pr"` payload keyed `repo#number` for pull-request events, otherwise a `kind: "dlq"` payload (origin topic, aggregate key, source CloudEvent id, reason, failure history) keyed `dlq:["<origin>","<aggregate>"]`. Its properties carry `omega_attempt`, `omega_origin_topic`, `omega_source_ce_id`, `omega_last_error` and `omega_failure_history`; `omega_retry_history` is kept as a legacy alias. Retry-only routing metadata (`omega_retry_target`, `omega_retry_previous`) is dropped from dead letters in both SDKs, and Python `DlqTriage` ignores it, so a rejected retry delay cannot strand a blocked signal in triage. The retry/DLQ publish receipt is awaited before the original delivery is acknowledged; failed publication leaves that delivery unacknowledged.

`retryTopicsFor()` returns a direct `retry.<subscription>` receiver for `Shared` and `Key_Shared` logical consumers. `Failover` and `Exclusive` keep their singleton source consumer and use a separate `Shared` receiver for the delayed retry topic. `runSharedRetryHandoff()` republishes an eligible retry to the original topic without another delay and with `omega_retry_target=<logical subscription>`; only the matching handler processes it, while other subscriptions acknowledge and ignore it. A crash between receipt and source acknowledgement may duplicate raw deliveries.

Raw message delivery is at-least-once. Exactly-once **effects** require idempotent handlers backed by the exported owner-token `IdempotencyStore` contract or an external ledger; this package does not implement a durable TypeScript ledger. `omega.control.signal.blocked` validates its PR and DLQ payload branches without adding event types.

## Outbox and lifecycle limits

`Outbox` stages transaction appends until explicit commit; rollback publishes nothing. A successful commit wakes the relay, as does `connected()` after reconnection. Empty queues await an event signal rather than polling. The relay marks a row sent only after a publish receipt; `close(publisher)` wakes the relay and performs a final drain. Queue health is signaled at 80% of the configured byte limit. Stable per-topic producer names and monotonic sequence high-watermarks survive row cleanup only for that adapter’s lifetime.

The TypeScript outbox is in-memory only: it is not restart-durable, is not the Python SQLite WAL/synchronous=`FULL` implementation and does not use a Pulsar transaction. Raw outbox/broker delivery remains at-least-once. Shutdown is event-driven through abort signals, explicit `close()` and wake signals. There are no schedulers, intervals or polling loops; only finite bounded creation-I/O retries may wait between attempts.
