import { Buffer } from "node:buffer";
import { withCreationBackoff, type CreationRetryOptions } from "./backoff.js";
import { CloudEvent, PublishRequest, type PublishReceipt } from "./envelope.js";
import { AuthenticationError, ConfigurationError, ContractError, isPlainRecord, PulsarError, redactValue, safeErrorMessage } from "./errors.js";
import { EventStreamConfig } from "./config.js";
import { createAbortError } from "./ports.js";
import type { ConsumerMode, DeliveryMetadata, EventBus, EventReader, EventSubscriber, MessageDelivery, ReadOptions, SubscribeOptions } from "./ports.js";
import type { EventType, FieldRule, ObjectShape } from "./taxonomy.js";
import { eventTypesForTopic, getEventType, payloadShapesFor } from "./taxonomy.js";

export interface PulsarMessageHandle {
  toString(): string;
}

export type PulsarMessageId = string | number | PulsarMessageHandle;

export interface PulsarMessage {
  readonly data: Uint8Array;
  readonly properties?: Readonly<Record<string, string>>;
  readonly partitionKey?: string;
  readonly messageId?: PulsarMessageId;
  readonly batchIndex?: number;
  readonly redeliveryCount?: number;
  readonly sequenceId?: number | string;
  readonly publishTimestamp?: number;
}

export interface PulsarProducer {
  send(message: {
    readonly data: Uint8Array;
    readonly properties: Readonly<Record<string, string>>;
    readonly partitionKey: string;
    readonly sequenceId?: number;
    readonly deliverAfter?: number;
  }): Promise<unknown>;
  flush?(): Promise<void>;
  close(): Promise<void>;
}
export interface PulsarProducerOptions {
  readonly producerName: string;
  readonly initialSequenceId: number;
  /** Generated native JSON schema metadata, converted to pinned-peer SchemaInfo by the native adapter. */
  readonly jsonMetadata?: NativeJsonMetadata;
}

export interface StructuredJsonCodec {
  encode(event: CloudEvent): Uint8Array;
  decode(bytes: Uint8Array): CloudEvent;
}

export const cloudEventJsonCodec: StructuredJsonCodec = Object.freeze({
  encode: (event: CloudEvent) => event.toBytes(),
  decode: (bytes: Uint8Array) => CloudEvent.fromBytes(bytes)
});

export interface PulsarConsumer {
  receive(): Promise<PulsarMessage>;
  acknowledge(message: PulsarMessageId, options?: { readonly batchIndex?: number }): Promise<void>;
  negativeAcknowledge(message: PulsarMessageId): void;
  close(): Promise<void>;
}

export interface PulsarReaderClient {
  readNext(): Promise<PulsarMessage>;
  seek(position: number | Date): Promise<void>;
  close(): Promise<void>;
}

export interface PulsarSubscribeOptions {
  readonly topic: string;
  readonly subscriptionName: string;
  readonly mode: ConsumerMode;
  readonly initialPosition: "Earliest" | "Latest";
  readonly receiverQueueSize: number;
  readonly maxInFlight: number;
}

export interface PulsarReaderOptions {
  readonly topic: string;
  readonly startAt?: number | Date;
  readonly receiverQueueSize: number;
}

export interface PulsarClient {
  createProducer(topic: string, options: PulsarProducerOptions): Promise<PulsarProducer>;
  subscribe(options: PulsarSubscribeOptions): Promise<PulsarConsumer>;
  createReader(options: PulsarReaderOptions): Promise<PulsarReaderClient>;
  close(): Promise<void>;
}

export interface PulsarClientConstructor {
  new (options: Readonly<Record<string, unknown>>): NativePulsarClient;
}

export type PulsarModuleLoader = () => Promise<unknown>;

export interface PayloadCrypto {
  encrypt(plaintext: Uint8Array, key: string): Promise<Uint8Array>;
  decrypt(ciphertext: Uint8Array, key: string): Promise<Uint8Array>;
}

export interface PulsarEventBusOptions {
  readonly client?: PulsarClient;
  readonly config?: EventStreamConfig;
  readonly crypto?: PayloadCrypto;
  readonly structuredJsonCodec?: StructuredJsonCodec;
  readonly creationRetry?: CreationRetryOptions;
  readonly onAuthenticationFailure?: (error: AuthenticationError) => void;
  readonly now?: () => number;
}

export interface NativeJsonMetadata {
  readonly schemaType: "JSON";
  readonly format: "avro-json";
  readonly topic: string;
  readonly schema: string;
  readonly constraintMapping: Readonly<Record<string, string>>;
}

interface NativePulsarMessageId {
  toString(): string;
}

interface NativePulsarMessageIdConstructor {
  earliest(): NativePulsarMessageId;
}

interface NativePulsarMessage {
  getData(): Buffer;
  getProperties(): Readonly<Record<string, string>>;
  getPartitionKey(): string;
  getMessageId(): NativePulsarMessageId;
  getPublishTimestamp(): number;
  getRedeliveryCount(): number;
}

interface NativeProducerConfig {
  readonly topic: string;
  readonly producerName: string;
  readonly initialSequenceId: number;
  readonly schema?: {
    readonly schemaType: "Json";
    readonly name: string;
    readonly schema: string;
  };
}

interface NativeProducerMessage {
  readonly data: Buffer;
  readonly properties: Record<string, string>;
  readonly partitionKey: string;
  readonly sequenceId?: number;
  readonly deliverAfter?: number;
}

interface NativePulsarProducer {
  send(message: NativeProducerMessage): Promise<NativePulsarMessageId>;
  flush(): Promise<null>;
  close(): Promise<null>;
}

interface NativeConsumerConfig {
  readonly topic: string;
  readonly subscription: string;
  readonly subscriptionType: "KeyShared" | "Shared" | "Failover" | "Exclusive";
  readonly subscriptionInitialPosition: "Earliest" | "Latest";
  readonly receiverQueueSize: number;
  readonly receiverQueueSizeAcrossPartitions: number;
  readonly batchIndexAckEnabled: true;
  readonly keySharedPolicy?: { readonly keyShareMode: "AutoSplit" };
}

interface NativePulsarConsumer {
  receive(): Promise<NativePulsarMessage>;
  acknowledge(message: NativePulsarMessage): Promise<null>;
  negativeAcknowledge(message: NativePulsarMessage): void;
  close(): Promise<null>;
}

interface NativeReaderConfig {
  readonly topic: string;
  readonly startMessageId: NativePulsarMessageId;
  readonly receiverQueueSize: number;
}

interface NativePulsarReader {
  readNext(): Promise<NativePulsarMessage>;
  seekTimestamp(timestamp: number): Promise<null>;
  close(): Promise<null>;
}

interface NativePulsarClient {
  createProducer(config: NativeProducerConfig): Promise<NativePulsarProducer>;
  subscribe(config: NativeConsumerConfig): Promise<NativePulsarConsumer>;
  createReader(config: NativeReaderConfig): Promise<NativePulsarReader>;
  close(): Promise<null>;
}

interface PulsarModule extends Record<string, unknown> {
  readonly Client: unknown;
  readonly AuthenticationToken?: unknown;
  readonly MessageId?: unknown;
}

async function loadPulsarModule(loader?: PulsarModuleLoader): Promise<PulsarModule> {
  let loaded: unknown;
  try {
    const moduleName = "pulsar-client";
    loaded = await (loader ?? (() => import(moduleName)))();
  } catch {
    throw new ConfigurationError("optional Pulsar peer is not installed");
  }
  const defaultExport = isPlainRecord(loaded) && typeof loaded.Client !== "function" ? loaded.default : undefined;
  const candidate = isPlainRecord(defaultExport) ? defaultExport : loaded;
  if (!isPlainRecord(candidate) || typeof candidate.Client !== "function") {
    throw new ConfigurationError("optional Pulsar peer has an unsupported module shape");
  }
  return candidate as PulsarModule;
}

export async function loadPulsarClient(loader?: PulsarModuleLoader): Promise<PulsarClientConstructor> {
  const module = await loadPulsarModule(loader);
  return module.Client as PulsarClientConstructor;
}

function isNativePulsarMessageIdConstructor(value: unknown): value is NativePulsarMessageIdConstructor {
  return typeof value === "function" && "earliest" in value && typeof value.earliest === "function";
}

function nativeMessageIdConstructor(value: unknown): NativePulsarMessageIdConstructor {
  if (!isNativePulsarMessageIdConstructor(value)) {
    throw new ConfigurationError("optional Pulsar peer does not provide MessageId.earliest()");
  }
  return value;
}

export async function createPulsarEventBus(options: {
  readonly config?: EventStreamConfig;
  readonly loader?: PulsarModuleLoader;
  readonly crypto?: PayloadCrypto;
  readonly structuredJsonCodec?: StructuredJsonCodec;
  readonly creationRetry?: CreationRetryOptions;
  readonly onAuthenticationFailure?: (error: AuthenticationError) => void;
  readonly now?: () => number;
} = {}): Promise<PulsarEventBus> {
  const config = options.config ?? new EventStreamConfig();
  const module = await loadPulsarModule(options.loader);
  const Client = module.Client as PulsarClientConstructor;
  const messageIds = nativeMessageIdConstructor(module.MessageId);
  const clientOptions: Record<string, unknown> = { serviceUrl: config.serviceUrl };
  if (config.tokenFile !== undefined) {
    const token = await config.readSecret("tokenFile");
    if (typeof module.AuthenticationToken !== "function") {
      throw new ConfigurationError("optional Pulsar peer does not provide token authentication");
    }
    const AuthenticationToken = module.AuthenticationToken as new (options: { readonly token: string }) => unknown;
    clientOptions.authentication = new AuthenticationToken({ token });
  }
  if (config.tlsCaFile !== undefined) clientOptions.tlsTrustCertsFilePath = config.tlsCaFile;
  const nativeClient = new Client(clientOptions);
  return new PulsarEventBus({
    client: new NativePulsarClientAdapter(nativeClient, messageIds),
    config,
    ...(options.crypto === undefined ? {} : { crypto: options.crypto }),
    ...(options.structuredJsonCodec === undefined ? {} : { structuredJsonCodec: options.structuredJsonCodec }),
    ...(options.creationRetry === undefined ? {} : { creationRetry: options.creationRetry }),
    ...(options.onAuthenticationFailure === undefined
      ? {}
      : { onAuthenticationFailure: options.onAuthenticationFailure }),
    ...(options.now === undefined ? {} : { now: options.now })
  });
}

class NativeMessageHandle implements PulsarMessageHandle {
  readonly #message: NativePulsarMessage;

  constructor(message: NativePulsarMessage) {
    this.#message = message;
  }

  getNativeMessage(): NativePulsarMessage {
    return this.#message;
  }

  toString(): string {
    return this.#message.getMessageId().toString();
  }
}

function toPulsarMessage(message: NativePulsarMessage): PulsarMessage {
  const properties = Object.freeze({ ...message.getProperties() });
  const partitionKey = message.getPartitionKey();
  const sequenceId = properties.omega_sequence_id;
  return Object.freeze({
    data: message.getData(),
    properties,
    ...(partitionKey.length === 0 ? {} : { partitionKey }),
    messageId: new NativeMessageHandle(message),
    ...(sequenceId === undefined ? {} : { sequenceId }),
    redeliveryCount: message.getRedeliveryCount(),
    publishTimestamp: message.getPublishTimestamp()
  });
}

class NativePulsarProducerAdapter implements PulsarProducer {
  readonly #producer: NativePulsarProducer;

  constructor(producer: NativePulsarProducer) {
    this.#producer = producer;
  }

  async send(message: {
    readonly data: Uint8Array;
    readonly properties: Readonly<Record<string, string>>;
    readonly partitionKey: string;
    readonly sequenceId?: number;
    readonly deliverAfter?: number;
  }): Promise<void> {
    await this.#producer.send({
      data: Buffer.isBuffer(message.data) ? message.data : Buffer.from(message.data),
      properties: { ...message.properties },
      partitionKey: message.partitionKey,
      ...(message.sequenceId === undefined ? {} : { sequenceId: message.sequenceId }),
      ...(message.deliverAfter === undefined ? {} : { deliverAfter: message.deliverAfter })
    });
  }

  async flush(): Promise<void> {
    await this.#producer.flush();
  }

  async close(): Promise<void> {
    await this.#producer.close();
  }
}

class NativePulsarConsumerAdapter implements PulsarConsumer {
  readonly #consumer: NativePulsarConsumer;

  constructor(consumer: NativePulsarConsumer) {
    this.#consumer = consumer;
  }

  async receive(): Promise<PulsarMessage> {
    return toPulsarMessage(await this.#consumer.receive());
  }

  async acknowledge(message: PulsarMessageId): Promise<void> {
    if (!(message instanceof NativeMessageHandle)) {
      throw new ContractError("native Pulsar acknowledgement handle is invalid");
    }
    await this.#consumer.acknowledge(message.getNativeMessage());
  }

  negativeAcknowledge(message: PulsarMessageId): void {
    if (!(message instanceof NativeMessageHandle)) {
      throw new ContractError("native Pulsar acknowledgement handle is invalid");
    }
    this.#consumer.negativeAcknowledge(message.getNativeMessage());
  }

  async close(): Promise<void> {
    await this.#consumer.close();
  }
}

class NativePulsarReaderAdapter implements PulsarReaderClient {
  readonly #reader: NativePulsarReader;

  constructor(reader: NativePulsarReader) {
    this.#reader = reader;
  }

  async readNext(): Promise<PulsarMessage> {
    return toPulsarMessage(await this.#reader.readNext());
  }

  async seek(position: number | Date): Promise<void> {
    const timestamp = position instanceof Date ? position.getTime() : position;
    await this.#reader.seekTimestamp(timestamp);
  }

  async close(): Promise<void> {
    await this.#reader.close();
  }
}

class NativePulsarClientAdapter implements PulsarClient {
  readonly #client: NativePulsarClient;
  readonly #messageIds: NativePulsarMessageIdConstructor;

  constructor(client: NativePulsarClient, messageIds: NativePulsarMessageIdConstructor) {
    this.#client = client;
    this.#messageIds = messageIds;
  }

  async createProducer(topic: string, options: PulsarProducerOptions): Promise<PulsarProducer> {
    const schema =
      options.jsonMetadata === undefined
        ? undefined
        : {
            schemaType: "Json" as const,
            name: safeAvroName(topic),
            schema: options.jsonMetadata.schema
          };
    const producer = await this.#client.createProducer({
      topic,
      producerName: options.producerName,
      initialSequenceId: options.initialSequenceId,
      ...(schema === undefined ? {} : { schema })
    });
    return new NativePulsarProducerAdapter(producer);
  }

  async subscribe(options: PulsarSubscribeOptions): Promise<PulsarConsumer> {
    const subscriptionType = options.mode === "Key_Shared" ? "KeyShared" : options.mode;
    const consumer = await this.#client.subscribe({
      topic: options.topic,
      subscription: options.subscriptionName,
      subscriptionType,
      subscriptionInitialPosition: options.initialPosition,
      receiverQueueSize: options.receiverQueueSize,
      receiverQueueSizeAcrossPartitions: options.maxInFlight,
      batchIndexAckEnabled: true,
      ...(options.mode === "Key_Shared" ? { keySharedPolicy: { keyShareMode: "AutoSplit" } } : {})
    });
    return new NativePulsarConsumerAdapter(consumer);
  }

  async createReader(options: PulsarReaderOptions): Promise<PulsarReaderClient> {
    const reader = await this.#client.createReader({
      topic: options.topic,
      startMessageId: this.#messageIds.earliest(),
      receiverQueueSize: options.receiverQueueSize
    });
    try {
      if (options.startAt !== undefined) {
        const timestamp = options.startAt instanceof Date ? options.startAt.getTime() : options.startAt;
        await reader.seekTimestamp(timestamp);
      }
    } catch (error) {
      try {
        await reader.close();
      } catch (closeError) {
        throw new AggregateError([error, closeError], "Pulsar Reader initialization failed");
      }
      throw error;
    }
    return new NativePulsarReaderAdapter(reader);
  }

  async close(): Promise<void> {
    await this.#client.close();
  }
}

export function toNativeSequenceId(value: string | bigint): number {
  if (typeof value === "string" && !/^(0|[1-9][0-9]*)$/.test(value)) {
    throw new ContractError("sequence id is not a canonical nonnegative decimal");
  }
  let parsed: bigint;
  try {
    parsed = typeof value === "bigint" ? value : BigInt(value);
  } catch {
    throw new ContractError("sequence id is not a nonnegative integer");
  }
  if (parsed < 0n || parsed > BigInt(Number.MAX_SAFE_INTEGER)) {
    throw new ContractError("sequence id exceeds the native numeric range");
  }
  return Number(parsed);
}

function safeAvroName(value: string): string {
  const parts = value.split(/[^A-Za-z0-9]+/).filter((part) => part.length > 0);
  const name = parts.map((part) => `${part[0]?.toUpperCase() ?? ""}${part.slice(1)}`).join("");
  return /^[A-Za-z_]/.test(name) ? name : `Omega${name}`;
}

function avroType(rule: FieldRule): unknown {
  switch (rule.kind) {
    case "string":
    case "date-time":
      return "string";
    case "integer":
      return "long";
    case "boolean":
      return "boolean";
    case "literal":
    case "enum":
      return typeof (rule.kind === "literal" ? rule.value : rule.values[0]) === "boolean" ? "boolean" :
        typeof (rule.kind === "literal" ? rule.value : rule.values[0]) === "number" ? "long" : "string";
    case "array":
      return {
        type: "array",
        items: avroRecord("NestedPayload", rule.items)
      };
    case "details":
      return { type: "map", values: ["string", "long", "boolean"] };
  }
}

function avroRecord(name: string, shape: ObjectShape): Record<string, unknown> {
  const optional = new Set(shape.optional);
  const fields = Object.entries(shape.fields).map(([fieldName, rule]) => {
    const type = avroType(rule);
    return optional.has(fieldName)
      ? { name: fieldName, type: ["null", type], default: null }
      : { name: fieldName, type };
  });
  return { type: "record", name, namespace: "omega.eventstream.payload", fields };
}

export function nativeJsonMetadataForTopic(topic: string): NativeJsonMetadata {
  const events: readonly EventType[] = eventTypesForTopic(topic);
  if (events.length === 0 && topic !== "persistent://omega/audit/log") {
    throw new ContractError("topic has no registered event payloads");
  }
  const payloadRecords: Record<string, unknown>[] = [];
  for (const eventType of events) {
    const shapes = payloadShapesFor(eventType.type);
    for (const [index, shape] of shapes.entries()) {
      const branch = shapes.length > 1 ? `Branch${index + 1}` : "";
      payloadRecords.push(avroRecord(`${safeAvroName(eventType.type)}${branch}`, shape));
    }
  }
  if (topic === "persistent://omega/audit/log") {
    payloadRecords.push(...payloadShapesFor("omega.audit.*").map((shape) => avroRecord("OmegaAuditPayload", shape)));
  }
  const dataType: unknown[] = ["null", ...payloadRecords];
  const schema = {
    type: "record",
    name: safeAvroName(topic),
    namespace: "omega.eventstream",
    fields: [
      { name: "specversion", type: "string" },
      { name: "id", type: "string" },
      { name: "source", type: "string" },
      { name: "type", type: "string" },
      { name: "subject", type: ["null", "string"], default: null },
      { name: "time", type: ["null", "string"], default: null },
      { name: "datacontenttype", type: "string" },
      { name: "dataschema", type: "string" },
      { name: "traceparent", type: ["null", "string"], default: null },
      { name: "omegakey", type: ["null", "string"], default: null },
      { name: "omegatask", type: ["null", "string"], default: null },
      { name: "omegagraph", type: ["null", "string"], default: null },
      { name: "omegaheadsha", type: ["null", "string"], default: null },
      { name: "data", type: dataType, default: null }
    ]
  };
  return Object.freeze({
    schemaType: "JSON",
    format: "avro-json",
    topic,
    schema: JSON.stringify(schema),
    constraintMapping: Object.freeze({
      "string.minLength": "Avro string; full constraint enforced by the SDK Draft 2020-12 validator",
      "integer.minimum/maximum": "Avro long; bounds enforced by the SDK Draft 2020-12 validator",
      "enum/const": "Avro string or boolean; allowed values enforced by the SDK Draft 2020-12 validator",
      "format.date-time": "Avro string; RFC3339 enforced by the SDK Draft 2020-12 validator",
      "additionalProperties": "Avro record fields; unknown-field rejection enforced by the SDK Draft 2020-12 validator"
    })
  });
}
function assertKnownTopic(topic: string): void {
  const systemTopic = /^persistent:\/\/omega\/system\/(?:retry|dlq)\.[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(topic);
  const auditTopic = /^persistent:\/\/omega\/audit\/[A-Za-z0-9._-]+$/.test(topic);
  if (!systemTopic && !auditTopic && eventTypesForTopic(topic).length === 0) {
    throw new ContractError("topic is not registered in the event taxonomy");
  }
}

function assertEventTopic(event: CloudEvent, topic: string): void {
  const registeredTopic = getEventType(event.type).topic;
  const systemTopic = /^persistent:\/\/omega\/system\/(?:retry|dlq)\.[^/]+$/.test(topic);
  if (registeredTopic !== topic && !systemTopic) {
    throw new ContractError("event type does not belong to the received topic");
  }
}

async function receiveWithAbort<T>(
  receive: () => Promise<T>,
  close: () => Promise<void>,
  signal?: AbortSignal
): Promise<T> {
  if (signal === undefined) return receive();
  if (signal.aborted) throw createAbortError();
  let abortListener: (() => void) | undefined;
  const aborted = new Promise<never>((_resolve, reject) => {
    abortListener = () => {
      void close().catch(() => undefined);
      reject(createAbortError());
    };
    signal.addEventListener("abort", abortListener, { once: true });
  });
  try {
    return await Promise.race([receive(), aborted]);
  } finally {
    if (abortListener !== undefined) signal.removeEventListener("abort", abortListener);
  }
}

function isAuthenticationFailure(error: unknown): boolean {
  return /\b(authentication|unauthori[sz]ed|permission denied|access denied|invalid credentials|invalid token|401|403)\b/i.test(
    safeErrorMessage(error)
  );
}

function redactedStringProperties(properties: Readonly<Record<string, string>> = {}): Record<string, string> {
  const redacted = redactValue({ ...properties });
  if (!isPlainRecord(redacted)) return {};
  const result: Record<string, string> = {};
  for (const [key, value] of Object.entries(redacted)) {
    if (typeof value === "string") result[key] = value;
  }
  return result;
}

function poisonHistory(properties: Readonly<Record<string, string>>, reason: string): string {
  let parsed: unknown = [];
  try {
    parsed = JSON.parse(properties.omega_retry_history ?? "[]") as unknown;
  } catch {
    parsed = [];
  }
  const rawAttempt = Number(properties.omega_attempt);
  const attempt = Number.isSafeInteger(rawAttempt) && rawAttempt >= 1 && rawAttempt <= 5 ? rawAttempt : 1;
  const history: { attempt: number; reason: string }[] = [];
  let previousAttempt = 0;
  if (Array.isArray(parsed)) {
    for (const entry of parsed) {
      if (!isPlainRecord(entry)) continue;
      if (
        typeof entry.attempt !== "number" ||
        !Number.isSafeInteger(entry.attempt) ||
        entry.attempt <= previousAttempt ||
        entry.attempt >= attempt ||
        typeof entry.reason !== "string"
      ) {
        continue;
      }
      history.push({ attempt: entry.attempt, reason: safeErrorMessage(entry.reason) });
      previousAttempt = entry.attempt;
    }
  }
  history.push({ attempt, reason: safeErrorMessage(reason) });
  return JSON.stringify(history);
}

export class PulsarEventBus implements EventBus {
  readonly #client: PulsarClient | undefined;
  readonly #config: EventStreamConfig;
  readonly #crypto: PayloadCrypto | undefined;
  readonly #structuredJsonCodec: StructuredJsonCodec;
  readonly #creationRetry: CreationRetryOptions;
  readonly #onAuthenticationFailure: ((error: AuthenticationError) => void) | undefined;
  readonly #now: () => number;
  readonly #producers = new Map<string, Promise<PulsarProducer>>();
  readonly #closers = new Set<() => Promise<void>>();
  #transportHealthy = true;
  #authenticationHealthy = true;
  #closed = false;

  constructor(options: PulsarEventBusOptions = {}) {
    this.#client = options.client;
    this.#config = options.config ?? new EventStreamConfig();
    this.#structuredJsonCodec = options.structuredJsonCodec ?? cloudEventJsonCodec;
    this.#crypto = options.crypto;
    const creationRetry = options.creationRetry ?? {};
    this.#creationRetry = {
      ...creationRetry,
      shouldRetry: (error, attempt) =>
        !(error instanceof AuthenticationError) &&
        !isAuthenticationFailure(error) &&
        (creationRetry.shouldRetry?.(error, attempt) ?? true)
    };
    this.#onAuthenticationFailure = options.onAuthenticationFailure;
    this.#now = options.now ?? Date.now;
  }

  get healthy(): boolean {
    return this.#transportHealthy && this.#authenticationHealthy;
  }

  async publish(request: PublishRequest): Promise<PublishReceipt> {
    this.#ensureOpen();
    const client = this.#requireClient();
    const data = this.#structuredJsonCodec.encode(request.event);
    const properties: Record<string, string> = {
      ...request.properties,
      ...request.event.toProperties()
    };
    if (request.sequenceId !== undefined) properties.omega_sequence_id = String(request.sequenceId);
    let payload = data;
    if (request.event.type.startsWith("omega.auth.")) {
      const key = await this.#readEncryptionKey();
      const crypto = this.#crypto;
      if (crypto === undefined) throw this.#authenticationFailure("auth-family encryption capability is unavailable");
      try {
        payload = await crypto.encrypt(data, key);
      } catch {
        throw this.#authenticationFailure("auth-family payload encryption failed");
      }
      this.#authenticationHealthy = true;
      properties.omega_encrypted = "true";
    }
    const sequenceId = request.sequenceId === undefined ? undefined : toNativeSequenceId(request.sequenceId);
    const producer = await this.#producer(client, request.topic, request.producerName);
    const deliverAfterMs =
      request.deliverAfterSeconds === undefined
        ? undefined
        : Math.max(0, Math.floor(request.deliverAfterSeconds * 1000));
    try {
      await producer.send({
        data: payload,
        properties: Object.freeze(properties),
        partitionKey: request.key,
        ...(sequenceId === undefined ? {} : { sequenceId }),
        ...(deliverAfterMs === undefined ? {} : { deliverAfter: deliverAfterMs })
      });
    } catch (error) {
      throw this.#normalizePulsarFailure(error, "publish");
    }
    this.#transportHealthy = true;
    return Object.freeze({
      topic: request.topic,
      eventId: request.event.id,
      publishedAt: this.#now(),
      ...(request.sequenceId === undefined ? {} : { sequenceId: String(request.sequenceId) })
    });
  }

  async subscribe(topic: string, options: SubscribeOptions): Promise<EventSubscriber> {
    this.#ensureOpen();
    const client = this.#requireClient();
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(options.subscriptionName)) {
      throw new ContractError("subscription name is invalid");
    }
    assertKnownTopic(topic);
    const isRetryTopic = /^persistent:\/\/omega\/system\/retry\./.test(topic);
    if (isRetryTopic && options.mode !== "Shared" && options.mode !== "Key_Shared") {
      throw new ContractError("delayed retry topics require Shared or Key_Shared consumers");
    }
    if (isRetryTopic && topic !== `persistent://omega/system/retry.${options.subscriptionName}`) {
      throw new ContractError("retry topic must match the logical subscription");
    }
    const receiverQueueSize = options.receiverQueueSize ?? this.#config.receiverQueueSize;
    const maxInFlight = options.maxInFlight ?? this.#config.maxInFlight;
    if (!Number.isSafeInteger(receiverQueueSize) || receiverQueueSize < 1) {
      throw new ContractError("receiver queue size must be a positive safe integer");
    }
    if (!Number.isSafeInteger(maxInFlight) || maxInFlight < 1) {
      throw new ContractError("max in-flight must be a positive safe integer");
    }
    const consumer = await this.#createResource(
      () =>
        client.subscribe({
          topic,
          subscriptionName: options.subscriptionName,
          mode: options.mode,
          initialPosition: options.initialPosition ?? "Earliest",
          receiverQueueSize,
          maxInFlight
        }),
      "consumer"
    );
    const subscriber = new PulsarSubscriber(
      consumer,
      this.#decodeMessage.bind(this),
      topic,
      (error, operation) => this.#normalizePulsarFailure(error, operation),
      (message, error) => this.#publishPoisonMessage(message, topic, options.subscriptionName, error)
    );
    this.#closers.add(() => subscriber.close());
    return subscriber;
  }

  async read(topic: string, options: ReadOptions = {}): Promise<EventReader> {
    this.#ensureOpen();
    assertKnownTopic(topic);
    const client = this.#requireClient();
    if (
      options.startAt instanceof Date
        ? !Number.isFinite(options.startAt.getTime())
        : options.startAt !== undefined && (!Number.isSafeInteger(options.startAt) || options.startAt < 0)
    ) {
      throw new ContractError("reader start position is invalid");
    }
    const receiverQueueSize = options.receiverQueueSize ?? this.#config.receiverQueueSize;
    if (!Number.isSafeInteger(receiverQueueSize) || receiverQueueSize < 1) {
      throw new ContractError("reader queue size must be a positive safe integer");
    }
    const reader = await this.#createResource(
      () =>
        client.createReader({
          topic,
          ...(options.startAt === undefined ? {} : { startAt: options.startAt }),
          receiverQueueSize
        }),
      "reader"
    );
    const wrapped = new PulsarEventReader(
      reader,
      this.#decodeMessage.bind(this),
      topic,
      (error, operation) => this.#normalizePulsarFailure(error, operation)
    );
    this.#closers.add(() => wrapped.close());
    return wrapped;
  }

  async close(): Promise<void> {
    if (this.#closed) return;
    this.#closed = true;
    const errors: unknown[] = [];
    const closers = [...this.#closers];
    this.#closers.clear();
    for (const close of closers) {
      try {
        await close();
      } catch (error) {
        errors.push(error);
      }
    }
    for (const producerPromise of this.#producers.values()) {
      try {
        const producer = await producerPromise;
        await producer.flush?.();
        await producer.close();
      } catch (error) {
        errors.push(error);
      }
    }
    try {
      await this.#client?.close();
    } catch (error) {
      errors.push(error);
    }
    if (errors.length > 0) throw new PulsarError(`Pulsar shutdown failed for ${errors.length} resource(s)`);
  }

  async #producer(client: PulsarClient, topic: string, producerName?: string): Promise<PulsarProducer> {
    const cacheKey = `${topic}\0${producerName ?? ""}`;
    let producerPromise = this.#producers.get(cacheKey);
    if (producerPromise === undefined) {
      producerPromise = withCreationBackoff(
        () =>
          client.createProducer(topic, {
            producerName: producerName ?? `omega-${safeAvroName(topic)}`,
            initialSequenceId: 0,
            ...(/^persistent:\/\/omega\/system\/(?:retry|dlq)\./.test(topic)
              ? {}
              : { jsonMetadata: nativeJsonMetadataForTopic(topic) })
          }),
        this.#creationRetry
      );
      this.#producers.set(cacheKey, producerPromise);
    }
    try {
      const producer = await producerPromise;
      this.#transportHealthy = true;
      return producer;
    } catch (error) {
      if (this.#producers.get(cacheKey) === producerPromise) this.#producers.delete(cacheKey);
      throw this.#normalizePulsarFailure(error, "producer creation");
    }
  }

  async #createResource<T>(operation: () => Promise<T>, kind: string): Promise<T> {
    try {
      const resource = await withCreationBackoff(() => operation(), this.#creationRetry);
      this.#transportHealthy = true;
      return resource;
    } catch (error) {
      throw this.#normalizePulsarFailure(error, `${kind} creation`);
    }
  }

  async #decodeMessage(message: PulsarMessage): Promise<CloudEvent> {
    const encrypted = message.properties?.omega_encrypted === "true";
    if (message.properties?.omega_encrypted !== undefined && !encrypted) {
      throw this.#authenticationFailure("event payload encryption marker is invalid");
    }
    let data = message.data;
    if (encrypted) {
      const key = await this.#readEncryptionKey();
      const crypto = this.#crypto;
      if (crypto === undefined) throw this.#authenticationFailure("auth-family decrypt capability is unavailable");
      try {
        data = await crypto.decrypt(data, key);
      } catch {
        throw this.#authenticationFailure("auth-family payload decryption failed");
      }
    }
    const event = this.#structuredJsonCodec.decode(data);
    const isAuthEvent = event.type.startsWith("omega.auth.");
    if (isAuthEvent !== encrypted) throw this.#authenticationFailure("auth-family payload encryption policy was violated");
    if (isAuthEvent) this.#authenticationHealthy = true;
    return event;
  }

  async #publishPoisonMessage(
    message: PulsarMessage,
    sourceTopic: string,
    subscription: string,
    error: unknown
  ): Promise<void> {
    if (message.messageId === undefined) throw new ContractError("poison message has no native message id");
    const properties = redactedStringProperties(message.properties);
    const reason = safeErrorMessage(error);
    const topic = `persistent://omega/system/dlq.${subscription}`;
    const producer = await this.#producer(
      this.#requireClient(),
      topic,
      `omega-dlq-${subscription}`
    );
    try {
      await producer.send({
        data: Uint8Array.from(message.data),
        properties: Object.freeze({
          ...properties,
          omega_origin_topic: sourceTopic,
          omega_origin_message_id: safeErrorMessage(message.messageId),
          omega_origin_ce_id: properties.omega_origin_ce_id ?? properties.ce_id ?? "unknown",
          omega_poison: "true",
          omega_last_error: reason,
          omega_retry_history: poisonHistory(properties, reason)
        }),
        partitionKey: message.partitionKey ?? properties.omegakey ?? ""
      });
    } catch (publishError) {
      throw this.#normalizePulsarFailure(publishError, "poison dead-letter publish");
    }
  }

  async #readEncryptionKey(): Promise<string> {
    try {
      return await this.#config.readSecret("encryptionKeyFile");
    } catch {
      throw this.#authenticationFailure("auth-family encryption key is unavailable");
    }
  }

  #authenticationFailure(message: string): AuthenticationError {
    this.#authenticationHealthy = false;
    const error = new AuthenticationError(message);
    try {
      this.#onAuthenticationFailure?.(error);
    } catch {
      // Health notification failure must not expose or replace the authentication failure.
    }
    return error;
  }

  #normalizePulsarFailure(error: unknown, operation: string): Error {
    if (error instanceof AuthenticationError || isAuthenticationFailure(error)) {
      return this.#authenticationFailure(`Pulsar ${operation} authentication failed`);
    }
    this.#transportHealthy = false;
    return new PulsarError(`Pulsar ${operation} failed: ${safeErrorMessage(error)}`);
  }

  #requireClient(): PulsarClient {
    if (this.#client === undefined) throw new ConfigurationError("Pulsar client is not initialized");
    return this.#client;
  }

  #ensureOpen(): void {
    if (this.#closed) throw new ContractError("Pulsar EventBus is closed");
  }
}

class PulsarSubscriber implements EventSubscriber {
  readonly #consumer: PulsarConsumer;
  readonly #decode: (message: PulsarMessage) => Promise<CloudEvent>;
  readonly #topic: string;
  readonly #normalizeFailure: (error: unknown, operation: string) => Error;
  readonly #deadLetter: (message: PulsarMessage, error: unknown) => Promise<void>;
  #closed = false;
  #inFlight = 0;

  constructor(
    consumer: PulsarConsumer,
    decode: (message: PulsarMessage) => Promise<CloudEvent>,
    topic: string,
    normalizeFailure: (error: unknown, operation: string) => Error,
    deadLetter: (message: PulsarMessage, error: unknown) => Promise<void>
  ) {
    this.#consumer = consumer;
    this.#decode = decode;
    this.#topic = topic;
    this.#normalizeFailure = normalizeFailure;
    this.#deadLetter = deadLetter;
  }

  get inFlightCount(): number {
    return this.#inFlight;
  }

  async receive(signal?: AbortSignal): Promise<MessageDelivery | undefined> {
    while (!this.#closed) {
      let message: PulsarMessage;
      try {
        message = await receiveWithAbort(() => this.#consumer.receive(), () => this.close(), signal);
      } catch (error) {
        if (signal?.aborted) throw error;
        throw this.#normalizeFailure(error, "receive");
      }
      if (message.messageId === undefined) throw new ContractError("Pulsar message has no message id");
      try {
        const event = await this.#decode(message);
        assertEventTopic(event, this.#topic);
        this.#inFlight += 1;
        return this.#delivery(event, message);
      } catch (error) {
        if (error instanceof AuthenticationError) throw error;
        try {
          await this.#deadLetter(message, error);
          await this.#consumer.acknowledge(
            message.messageId,
            message.batchIndex === undefined ? undefined : { batchIndex: message.batchIndex }
          );
        } catch (deadLetterError) {
          throw this.#normalizeFailure(deadLetterError, "poison dead-letter");
        }
      }
    }
    return undefined;
  }

  async close(): Promise<void> {
    if (this.#closed) return;
    this.#closed = true;
    await this.#consumer.close();
  }

  #delivery(event: CloudEvent, message: PulsarMessage): MessageDelivery {
    const messageId = message.messageId;
    if (messageId === undefined) throw new ContractError("Pulsar message has no message id");
    const properties = Object.freeze(redactedStringProperties(message.properties));
    const metadata: DeliveryMetadata = Object.freeze({
      topic: this.#topic,
      key: message.partitionKey ?? properties.omegakey ?? "",
      properties,
      ...(message.sequenceId === undefined ? {} : { sequenceId: String(message.sequenceId) }),
      messageId: String(messageId),
      redelivered: (message.redeliveryCount ?? 0) > 0,
      publishedAt: message.publishTimestamp ?? Date.now()
    });
    let settled = false;
    return Object.freeze({
      event,
      metadata,
      ack: async () => {
        if (settled) return;
        try {
          await this.#consumer.acknowledge(
            messageId,
            message.batchIndex === undefined ? undefined : { batchIndex: message.batchIndex }
          );
        } catch (error) {
          throw this.#normalizeFailure(error, "acknowledge");
        }
        settled = true;
        this.#inFlight -= 1;
      },
      nack: async () => {
        if (settled) return;
        try {
          this.#consumer.negativeAcknowledge(messageId);
        } catch (error) {
          throw this.#normalizeFailure(error, "negative acknowledge");
        }
        settled = true;
        this.#inFlight -= 1;
      }
    });
  }
}

class PulsarEventReader implements EventReader {
  readonly #reader: PulsarReaderClient;
  readonly #decode: (message: PulsarMessage) => Promise<CloudEvent>;
  readonly #topic: string;
  readonly #normalizeFailure: (error: unknown, operation: string) => Error;
  #closed = false;

  constructor(
    reader: PulsarReaderClient,
    decode: (message: PulsarMessage) => Promise<CloudEvent>,
    topic: string,
    normalizeFailure: (error: unknown, operation: string) => Error
  ) {
    this.#reader = reader;
    this.#decode = decode;
    this.#topic = topic;
    this.#normalizeFailure = normalizeFailure;
  }

  async receive(signal?: AbortSignal): Promise<MessageDelivery | undefined> {
    if (this.#closed) return undefined;
    let message: PulsarMessage;
    try {
      message = await receiveWithAbort(() => this.#reader.readNext(), () => this.close(), signal);
    } catch (error) {
      if (signal?.aborted) throw error;
      throw this.#normalizeFailure(error, "reader receive");
    }
    const event = await this.#decode(message);
    assertEventTopic(event, this.#topic);
    const properties = Object.freeze(redactedStringProperties(message.properties));
    const metadata: DeliveryMetadata = Object.freeze({
      topic: this.#topic,
      key: message.partitionKey ?? properties.omegakey ?? "",
      properties,
      ...(message.sequenceId === undefined ? {} : { sequenceId: String(message.sequenceId) }),
      ...(message.messageId === undefined ? {} : { messageId: String(message.messageId) }),
      redelivered: (message.redeliveryCount ?? 0) > 0,
      publishedAt: message.publishTimestamp ?? Date.now()
    });
    return Object.freeze({ event, metadata, ack: async () => undefined, nack: async () => undefined });
  }

  async seek(position: number | Date): Promise<void> {
    if (
      position instanceof Date
        ? !Number.isFinite(position.getTime())
        : !Number.isSafeInteger(position) || position < 0
    ) {
      throw new ContractError("reader seek position is invalid");
    }
    try {
      await this.#reader.seek(position);
    } catch (error) {
      throw this.#normalizeFailure(error, "reader seek");
    }
  }

  async cancel(): Promise<void> {
    await this.close();
  }

  async close(): Promise<void> {
    if (this.#closed) return;
    this.#closed = true;
    try {
      await this.#reader.close();
    } catch (error) {
      throw this.#normalizeFailure(error, "reader close");
    }
  }
}
