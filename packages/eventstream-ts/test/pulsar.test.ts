import { randomBytes } from "node:crypto";
import { mkdtemp, chmod, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { CloudEvent, PublishRequest } from "../src/envelope.js";
import { AuthenticationError, ConfigurationError, ContractError } from "../src/errors.js";
import { NATIVE_REDELIVERY_SCHEMA_SAMPLES, NATIVE_TOPIC_SCHEMAS } from "../src/native-schemas.generated.js";
import { EventStreamConfig } from "../src/config.js";
import {
  AUTH_ENCRYPTION_KEY_NAME,
  createPulsarEventBus,
  PulsarEventBus,
  requiresAuthDecryption,
  requiresAuthEncryption,
  loadPulsarClient,
  nativeJsonMetadataForTopic,
  nativeProducerName,
  toNativeSequenceId
} from "../src/pulsar.js";
import type { PulsarProducerOptions, PulsarReaderOptions, PulsarSubscribeOptions, StructuredJsonCodec } from "../src/pulsar.js";

const makeEvent = (type = "omega.sdlc.pr.pushed") =>
  new CloudEvent({
    specversion: "1.0",
    id: "ce-pulsar",
    source: "test://pulsar",
    type,
    datacontenttype: "application/json",
    dataschema: `urn:omega:schema:${type}:1`,
    data:
      type === "omega.auth.session.login_started"
        ? { host: "box", tool: "gh", session_id: "s-1", actor: "test", action: "login" }
        : { repo: "repo", number: 3, head_sha: "head", actor: "test", before_sha: "base" },
    ...(type === "omega.sdlc.pr.pushed" ? { omegakey: "repo#3" } : {})
  });
async function writeKeyPair(): Promise<{ directory: string; publicKeyPath: string; privateKeyPath: string }> {
  const directory = await mkdtemp(join(tmpdir(), "eventstream-auth-"));
  const publicKeyPath = join(directory, "omega-auth.pub.pem");
  const privateKeyPath = join(directory, "omega-auth.key.pem");
  for (const path of [publicKeyPath, privateKeyPath]) {
    await writeFile(path, `-----BEGIN TEST KEY-----\n${randomBytes(16).toString("hex")}\n-----END TEST KEY-----\n`, {
      mode: 0o600
    });
    await chmod(path, 0o600);
  }
  return { directory, publicKeyPath, privateKeyPath };
}

interface CapturedNativeSend {
  readonly data: Buffer;
  readonly properties: Record<string, string>;
  readonly partitionKey: string;
  readonly sequenceId?: number;
}

describe("lazy Pulsar adapter", () => {
  it("imports the SDK without loading the optional Pulsar peer", async () => {
    // This dynamic import intentionally exercises the package boundary: a static import cannot prove the peer stays lazy.
    const sdk = await import("../src/index.js");
    expect(sdk.CloudEvent).toBe(CloudEvent);
    await expect(loadPulsarClient(async () => {
      throw new Error("Cannot find package 'pulsar-client'");
    })).rejects.toThrow(ConfigurationError);
  });

  it("loads the optional peer only when explicitly requested", async () => {
    let loaded = false;
    await expect(
      loadPulsarClient(async () => {
        loaded = true;
        throw new Error("module missing");
      })
    ).rejects.toThrow(ConfigurationError);
    expect(loaded).toBe(true);
    class FakeClient {}
    await expect(loadPulsarClient(async () => ({ Client: FakeClient }))).resolves.toBe(FakeClient);
    await expect(loadPulsarClient(async () => ({ default: { Client: FakeClient } }))).resolves.toBe(FakeClient);
  });

  it("preserves exact numeric bounds and builds deterministic JSON metadata", () => {
    expect(toNativeSequenceId("9007199254740991")).toBe(Number.MAX_SAFE_INTEGER);
    expect(() => toNativeSequenceId("9007199254740992")).toThrow();
    expect(() => toNativeSequenceId("-1")).toThrow();
    const metadata = nativeJsonMetadataForTopic("persistent://omega/sdlc/pr");
    expect(metadata.schemaType).toBe("JSON");
    expect(metadata.format).toBe("avro-json");
    // Same named record and schema name the Python SDK registers for sdlc/pr.
    expect(metadata.name).toBe("OmegaCloudEvent_pr");
    const parsed = JSON.parse(metadata.schema) as { name: string; namespace: string; fields: { name: string; type: unknown }[] };
    expect(parsed.namespace).toBe("omega.eventstream");
    const dataField = parsed.fields.find((field) => field.name === "data");
    expect(Array.isArray(dataField?.type) && (dataField?.type as unknown[]).includes("null")).toBe(false);
    expect(metadata.schema).toContain("head_sha");
    expect(nativeJsonMetadataForTopic("persistent://omega/control/signal").name).toBe("OmegaCloudEvent_signal");
    expect(nativeJsonMetadataForTopic("persistent://omega/sdlc/pr-state").name).toBe("OmegaPrState");
    expect(() => nativeJsonMetadataForTopic("persistent://omega/unknown/topic")).toThrow(ContractError);
  });

  it("derives every native schema from the shared Python-rendered contract", () => {
    for (const [topic, schema] of Object.entries(NATIVE_TOPIC_SCHEMAS)) {
      const metadata = nativeJsonMetadataForTopic(topic);
      expect(metadata.schema).toBe(schema);
      expect(metadata.name).toBe((JSON.parse(schema) as { name: string }).name);
    }
    for (const [topic, schema] of Object.entries(NATIVE_REDELIVERY_SCHEMA_SAMPLES)) {
      expect(nativeJsonMetadataForTopic(topic).schema).toBe(schema);
    }
  });
  it("refuses auth-family publication without file-backed encryption keys", async () => {
    const bus = new PulsarEventBus({
      client: {
        createProducer: async () => ({ send: async () => ({}), close: async () => undefined }),
        subscribe: async () => ({
          receive: async () => ({ data: new Uint8Array(), messageId: 1 }),
          acknowledge: async () => undefined,
          negativeAcknowledge: () => undefined,
          close: async () => undefined
        }),
        createReader: async () => ({
          readNext: async () => ({ data: new Uint8Array(), messageId: 1 }),
          seek: async () => undefined,
          close: async () => undefined
        }),
        close: async () => undefined
      },
      config: new EventStreamConfig({ serviceUrl: "pulsar://127.0.0.1:26650" })
    });
    await expect(
      bus.publish(
        new PublishRequest({
          event: makeEvent("omega.auth.session.login_started"),
          topic: "persistent://omega/auth/session",
          key: "box:gh"
        })
      )
    ).rejects.toThrow(AuthenticationError);
  });


  it("recovers producer creation and supports native modes, acknowledgements, and Reader lifecycle", async () => {
    const sourceTopic = "persistent://omega/sdlc/pr";
    const event = makeEvent();
    const message = {
      data: event.toBytes(),
      properties: event.toProperties(),
      partitionKey: "repo#3",
      messageId: "message-1",
      batchIndex: 7,
      sequenceId: "23"
    };
    let producerAttempts = 0;
    let sentCount = 0;
    const producerOptions: { topic: string; options: PulsarProducerOptions }[] = [];
    let sentData: Uint8Array | undefined;
    const decodedData: Uint8Array[] = [];
    const structuredJsonCodec: StructuredJsonCodec = {
      encode: (value) => value.toBytes(),
      decode: (bytes) => {
        decodedData.push(Uint8Array.from(bytes));
        return CloudEvent.fromBytes(bytes);
      }
    };
    const producerNames: unknown[] = [];
    const subscriptionOptions: PulsarSubscribeOptions[] = [];
    const readerOptions: PulsarReaderOptions[] = [];
    const acknowledgements: { message: unknown; options?: { readonly batchIndex?: number } }[] = [];
    const waits: number[] = [];
    const bus = new PulsarEventBus({
      client: {
        createProducer: async (_topic, options) => {
          producerAttempts += 1;
          producerOptions.push({ topic: _topic, options });
          producerNames.push(options.producerName);
          if (producerAttempts === 1) throw new Error("temporary connection failure");
          return {
            send: async (outgoing) => {
              sentCount += 1;
              sentData = Uint8Array.from(outgoing.data);
              return { messageId: 1 };
            },
            flush: async () => undefined,
            close: async () => undefined
          };
        },
        subscribe: async (options) => {
          subscriptionOptions.push(options);
          return {
            receive: async () => message,
            acknowledge: async (messageId, ackOptions) => {
              acknowledgements.push(
                ackOptions === undefined ? { message: messageId } : { message: messageId, options: ackOptions }
              );
            },
            negativeAcknowledge: () => undefined,
            close: async () => undefined
          };
        },
        createReader: async (options) => {
          readerOptions.push(options);
          return {
            readNext: async () => message,
            seek: async () => undefined,
            close: async () => undefined
          };
        },
        close: async () => undefined
      },
      creationRetry: {
        attempts: 2,
        random: () => 0.5,
        wait: async (seconds) => {
          waits.push(seconds);
        }
      },
      structuredJsonCodec
    });

    const request = new PublishRequest({
      event,
      topic: sourceTopic,
      key: "repo#3",
      sequenceId: "7",
      producerName: "stable-pr-producer"
    });
    await bus.publish(request);
    expect(producerOptions[0]?.topic).toBe(sourceTopic);
    expect(producerOptions[0]?.options.jsonMetadata).toEqual(nativeJsonMetadataForTopic(sourceTopic));
    expect(producerOptions[0]?.options.jsonMetadata?.schemaType).toBe("JSON");
    expect(sentData).toEqual(event.toBytes());
    await bus.publish(request);
    expect(producerAttempts).toBe(2);
    expect(producerNames).toEqual(["stable-pr-producer", "stable-pr-producer"]);
    expect(waits).toEqual([2]);
    expect(sentCount).toBe(2);

    const modes = ["Key_Shared", "Shared", "Failover", "Exclusive"] as const;
    for (const [index, mode] of modes.entries()) {
      await bus.subscribe(sourceTopic, { subscriptionName: `worker-${index}`, mode });
    }
    expect(subscriptionOptions.map((options) => options.mode)).toEqual(modes);
    expect(subscriptionOptions.every((options) => options.initialPosition === "Earliest")).toBe(true);

    const subscriber = await bus.subscribe(sourceTopic, { subscriptionName: "batch-worker", mode: "Shared" });
    const delivery = await subscriber.receive();
    if (delivery === undefined) throw new Error("expected a Pulsar delivery");
    expect(delivery.metadata.sequenceId).toBe("23");
    expect(delivery.metadata.key).toBe("repo#3");
    await delivery.ack();
    await delivery.ack();
    expect(acknowledgements).toEqual([{ message: "message-1", options: { batchIndex: 7 } }]);

    const reader = await bus.read(sourceTopic, { startAt: new Date(1234), receiverQueueSize: 5 });
    expect(readerOptions[0]?.startAt).toEqual(new Date(1234));
    const readDelivery = await reader.receive();
    expect(readDelivery?.event.id).toBe(event.id);
    expect(decodedData).toEqual([event.toBytes(), event.toBytes()]);
    await reader.seek(8);
    await reader.seek(new Date(2345));
    await reader.cancel();
    await reader.close();
    await bus.close();
  });

  it("maps the pinned native client API for publishing, subscriptions, and Readers", async () => {
    const topic = "persistent://omega/sdlc/pr";
    const event = makeEvent();
    const nativeMessageId = { toString: () => "ledger:entry" };
    const nativeMessage = {
      getData: () => Buffer.from(event.toBytes()),
      getProperties: () => ({ ...event.toProperties(), omega_sequence_id: "7" }),
      getPartitionKey: () => "repo#3",
      getMessageId: () => nativeMessageId,
      getPublishTimestamp: () => 1234,
      getRedeliveryCount: () => 0
    };
    let clientOptions: Readonly<Record<string, unknown>> | undefined;
    let producerOptions: Readonly<Record<string, unknown>> | undefined;
    const consumerOptions: Readonly<Record<string, unknown>>[] = [];
    let readerOptions: Readonly<Record<string, unknown>> | undefined;
    let sentMessage: CapturedNativeSend | undefined;
    let acknowledgedMessage: typeof nativeMessage | undefined;
    let negativelyAcknowledgedMessage: typeof nativeMessage | undefined;
    const seekTimestamps: number[] = [];

    class NativeClientFake {
      constructor(options: Readonly<Record<string, unknown>>) {
        clientOptions = options;
      }

      async createProducer(options: Readonly<Record<string, unknown>>) {
        producerOptions = options;
        return {
          send: async (message: CapturedNativeSend) => {
            sentMessage = message;
            return { toString: () => "sent-id" };
          },
          flush: async () => null,
          close: async () => null
        };
      }

      async subscribe(options: Readonly<Record<string, unknown>>) {
        consumerOptions.push(options);
        return {
          receive: async () => nativeMessage,
          acknowledge: async (message: typeof nativeMessage) => {
            acknowledgedMessage = message;
            return null;
          },
          negativeAcknowledge: (message: typeof nativeMessage) => {
            negativelyAcknowledgedMessage = message;
          },
          close: async () => null
        };
      }

      async createReader(options: Readonly<Record<string, unknown>>) {
        readerOptions = options;
        return {
          readNext: async () => nativeMessage,
          seekTimestamp: async (timestamp: number) => {
            seekTimestamps.push(timestamp);
            return null;
          },
          close: async () => null
        };
      }

      async close() {
        return null;
      }
    }

    class MessageIdFake {
      static earliest() {
        return { toString: () => "earliest" };
      }
    }

    const bus = await createPulsarEventBus({
      config: new EventStreamConfig({ serviceUrl: "pulsar://127.0.0.1:26650" }),
      loader: async () => ({ default: { Client: NativeClientFake, MessageId: MessageIdFake } })
    });
    await bus.publish(new PublishRequest({ event, topic, key: "repo#3", sequenceId: "7" }));
    expect(clientOptions).toEqual({ serviceUrl: "pulsar://127.0.0.1:26650" });
    expect(producerOptions).toMatchObject({
      topic,
      initialSequenceId: 0,
      schema: { schemaType: "Json" }
    });
    const nativeSchema = producerOptions?.schema;
    if (typeof nativeSchema !== "object" || nativeSchema === null || !("schema" in nativeSchema)) {
      throw new Error("expected native JSON schema metadata");
    }
    if (typeof nativeSchema.schema !== "string") throw new Error("expected string Avro JSON schema");
    expect(nativeSchema.schema).toContain("head_sha");
    expect(sentMessage).toMatchObject({
      partitionKey: "repo#3",
      sequenceId: 7,
      properties: { omega_sequence_id: "7" }
    });
    expect(Buffer.isBuffer(sentMessage?.data)).toBe(true);
    expect(sentMessage?.data).toEqual(Buffer.from(event.toBytes()));

    const subscriber = await bus.subscribe(topic, {
      subscriptionName: "native-worker",
      mode: "Key_Shared",
      receiverQueueSize: 12,
      maxInFlight: 4
    });
    const firstDelivery = await subscriber.receive();
    if (firstDelivery === undefined) throw new Error("expected native delivery");
    expect(consumerOptions[0]).toMatchObject({
      topic,
      subscription: "native-worker",
      subscriptionType: "KeyShared",
      subscriptionInitialPosition: "Earliest",
      receiverQueueSize: 12,
      receiverQueueSizeAcrossPartitions: 4,
      batchIndexAckEnabled: true,
      keySharedPolicy: { keyShareMode: "AutoSplit" }
    });
    expect(firstDelivery.metadata.sequenceId).toBe("7");
    await firstDelivery.ack();
    expect(acknowledgedMessage).toBe(nativeMessage);
    const secondDelivery = await subscriber.receive();
    if (secondDelivery === undefined) throw new Error("expected second native delivery");
    await secondDelivery.nack();
    expect(negativelyAcknowledgedMessage).toBe(nativeMessage);
    for (const mode of ["Shared", "Failover", "Exclusive"] as const) {
      const additionalSubscriber = await bus.subscribe(topic, {
        subscriptionName: `native-${mode.toLowerCase()}`,
        mode,
        receiverQueueSize: 12,
        maxInFlight: 4
      });
      await additionalSubscriber.close();
    }
    expect(consumerOptions.map((options) => options.subscriptionType)).toEqual([
      "KeyShared",
      "Shared",
      "Failover",
      "Exclusive"
    ]);

    const reader = await bus.read(topic, { startAt: new Date(1234), receiverQueueSize: 5 });
    expect(readerOptions).toMatchObject({ topic, receiverQueueSize: 5 });
    const startMessageId = readerOptions?.startMessageId;
    if (typeof startMessageId !== "object" || startMessageId === null) {
      throw new Error("expected native earliest MessageId");
    }
    expect(String(startMessageId)).toBe("earliest");
    expect(seekTimestamps).toEqual([1234]);
    const readDelivery = await reader.receive();
    expect(readDelivery?.event.id).toBe(event.id);
    await reader.seek(2345);
    expect(seekTimestamps).toEqual([1234, 2345]);
    await reader.close();
    await subscriber.close();
    await bus.close();
  });
  it("durably dead-letters malformed messages before acknowledging them", async () => {
    const sourceTopic = "persistent://omega/sdlc/pr";
    const event = makeEvent();
    // Generated per run so no credential-shaped literal is committed.
    const secretPart = randomBytes(9).toString("hex");
    const messages = [
      {
        data: Uint8Array.from([0x7b]),
        properties: { ce_id: "poison", omega_retry_history: "[]", token: ["Bearer", secretPart].join(" ") },
        partitionKey: "repo#3",
        messageId: "poison-1"
      },
      { data: event.toBytes(), properties: event.toProperties(), partitionKey: "repo#3", messageId: "valid-1" }
    ];
    const calls: string[] = [];
    const deadLetters: { topic: string; data: Uint8Array; properties: Readonly<Record<string, string>> }[] = [];
    const bus = new PulsarEventBus({
      client: {
        createProducer: async (topic) => ({
          send: async (message) => {
            calls.push(`publish:${topic}`);
            deadLetters.push({ topic, data: message.data, properties: message.properties });
            return { messageId: "dlq-1" };
          },
          close: async () => undefined
        }),
        subscribe: async () => ({
          receive: async () => {
            const message = messages.shift();
            if (message === undefined) throw new Error("fake consumer exhausted");
            return message;
          },
          acknowledge: async (messageId) => {
            calls.push(`ack:${String(messageId)}`);
          },
          negativeAcknowledge: () => calls.push("nack"),
          close: async () => undefined
        }),
        createReader: async () => ({
          readNext: async () => ({ data: event.toBytes(), messageId: "read-1" }),
          seek: async () => undefined,
          close: async () => undefined
        }),
        close: async () => undefined
      }
    });

    const subscriber = await bus.subscribe(sourceTopic, { subscriptionName: "poison-worker", mode: "Shared" });
    const delivery = await subscriber.receive();
    expect(delivery?.event.id).toBe(event.id);
    expect(calls).toEqual([
      "publish:persistent://omega/system/dlq.poison-worker",
      "ack:poison-1"
    ]);
    expect(deadLetters[0]?.data).toEqual(Uint8Array.from([0x7b]));
    expect(deadLetters[0]?.properties.omega_poison).toBe("true");
    expect(deadLetters[0]?.properties.omega_origin_topic).toBe(sourceTopic);
    const history = JSON.parse(deadLetters[0]?.properties.omega_retry_history ?? "[]") as {
      attempt: number;
      reason: string;
    }[];
    expect(history).toHaveLength(1);
    expect(history[0]?.attempt).toBe(1);
    expect(history[0]?.reason).not.toContain(secretPart);
    expect(deadLetters[0]?.properties.token).not.toContain(secretPart);
    await delivery?.ack();
    expect(calls.at(-1)).toBe("ack:valid-1");
    await bus.close();
  });


  it("publishes auth-family payloads with native Pulsar encryption, including retry topics", async () => {
    const { directory, publicKeyPath, privateKeyPath } = await writeKeyPair();
    try {
      const authEvent = makeEvent("omega.auth.session.login_started");
      const producerOptions: { topic: string; options: PulsarProducerOptions }[] = [];
      const subscribeOptions: PulsarSubscribeOptions[] = [];
      let sent:
        | { data: Uint8Array; properties: Readonly<Record<string, string>>; partitionKey: string }
        | undefined;
      const bus = new PulsarEventBus({
        client: {
          createProducer: async (topic, options) => {
            producerOptions.push({ topic, options });
            return {
              send: async (message) => {
                sent = { data: message.data, properties: message.properties, partitionKey: message.partitionKey };
                return { messageId: "auth-published" };
              },
              close: async () => undefined
            };
          },
          subscribe: async (options) => {
            subscribeOptions.push(options);
            return {
              receive: async () => {
                if (sent === undefined) throw new Error("auth event has not been published");
                // The native client decrypted the payload and reports the
                // encryption context of the broker message.
                return {
                  data: sent.data,
                  properties: sent.properties,
                  partitionKey: sent.partitionKey,
                  messageId: "auth-received",
                  encryption: { encrypted: true, decryptionFailed: false }
                };
              },
              acknowledge: async () => undefined,
              negativeAcknowledge: () => undefined,
              close: async () => undefined
            };
          },
          createReader: async () => ({
            readNext: async () => ({ data: authEvent.toBytes(), messageId: "reader-message" }),
            seek: async () => undefined,
            close: async () => undefined
          }),
          close: async () => undefined
        },
        config: new EventStreamConfig({
          serviceUrl: "pulsar://127.0.0.1:26650",
          encryptionKeyFile: publicKeyPath,
          privateKeyFile: privateKeyPath
        })
      });

      await bus.publish(
        new PublishRequest({
          event: authEvent,
          topic: "persistent://omega/system/retry.auth-worker",
          key: "box:gh"
        })
      );
      expect(producerOptions).toHaveLength(1);
      expect(producerOptions[0]?.options.encryption).toEqual({
        keyName: AUTH_ENCRYPTION_KEY_NAME,
        publicKeyPath
      });
      expect(AUTH_ENCRYPTION_KEY_NAME).toBe("omega-auth");
      // Native Pulsar encrypts on the wire; the SDK hands over CloudEvent bytes
      // and no application-level marker that the Python SDK would not honour.
      expect(sent?.data).toEqual(authEvent.toBytes());
      expect(sent?.properties.omega_encrypted).toBeUndefined();

      const subscriber = await bus.subscribe("persistent://omega/system/retry.auth-worker", {
        subscriptionName: "auth-worker",
        mode: "Shared"
      });
      expect(subscribeOptions[0]?.decryption).toEqual({ privateKeyPath });
      const delivery = await subscriber.receive();
      expect(delivery?.event.id).toBe(authEvent.id);
      await delivery?.ack();
      await bus.close();
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("encrypts redelivery of auth-origin events and leaves other topics in plaintext", async () => {
    const { directory, publicKeyPath, privateKeyPath } = await writeKeyPair();
    try {
      const producers: { topic: string; options: PulsarProducerOptions }[] = [];
      const bus = new PulsarEventBus({
        client: {
          createProducer: async (topic, options) => {
            producers.push({ topic, options });
            return { send: async () => ({}), close: async () => undefined };
          },
          subscribe: async () => {
            throw new Error("not used");
          },
          createReader: async () => {
            throw new Error("not used");
          },
          close: async () => undefined
        },
        config: new EventStreamConfig({
          serviceUrl: "pulsar://127.0.0.1:26650",
          encryptionKeyFile: publicKeyPath,
          privateKeyFile: privateKeyPath
        })
      });
      const event = makeEvent();
      await bus.publish(new PublishRequest({ event, topic: "persistent://omega/sdlc/pr", key: "repo#3" }));
      await bus.publish(
        new PublishRequest({
          event,
          topic: "persistent://omega/system/dlq.auth-worker",
          key: "repo#3",
          properties: { omega_origin_topic: "persistent://omega/auth/session" }
        })
      );
      expect(producers.map((entry) => entry.options.encryption?.keyName)).toEqual([undefined, "omega-auth"]);
      expect(requiresAuthEncryption("omega.sdlc.pr.pushed", "persistent://omega/system/retry.gate", {})).toBe(false);
      expect(requiresAuthDecryption("persistent://omega/auth/session")).toBe(true);
      expect(requiresAuthDecryption("persistent://omega/system/dlq.gate")).toBe(true);
      expect(requiresAuthDecryption("persistent://omega/sdlc/pr")).toBe(false);
      await bus.close();
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("refuses auth-family deliveries that did not arrive natively encrypted", async () => {
    const { directory, publicKeyPath, privateKeyPath } = await writeKeyPair();
    try {
      const authEvent = makeEvent("omega.auth.session.login_started");
      const acknowledgements: string[] = [];
      const bus = new PulsarEventBus({
        client: {
          createProducer: async () => ({ send: async () => undefined, close: async () => undefined }),
          subscribe: async () => ({
            receive: async () => ({
              data: authEvent.toBytes(),
              messageId: "plaintext-auth",
              encryption: { encrypted: false, decryptionFailed: false }
            }),
            acknowledge: async (messageId) => {
              acknowledgements.push(String(messageId));
            },
            negativeAcknowledge: () => acknowledgements.push("nack"),
            close: async () => undefined
          }),
          createReader: async () => ({
            readNext: async () => ({ data: authEvent.toBytes(), messageId: "reader-message" }),
            seek: async () => undefined,
            close: async () => undefined
          }),
          close: async () => undefined
        },
        config: new EventStreamConfig({
          serviceUrl: "pulsar://127.0.0.1:26650",
          encryptionKeyFile: publicKeyPath,
          privateKeyFile: privateKeyPath
        })
      });
      const subscriber = await bus.subscribe("persistent://omega/auth/session", {
        subscriptionName: "auth-worker",
        mode: "Shared"
      });
      await expect(subscriber.receive()).rejects.toThrow(AuthenticationError);
      const reader = await bus.read("persistent://omega/auth/session");
      // No native encryption context reported at all also fails closed.
      await expect(reader.receive()).rejects.toThrow(AuthenticationError);
      expect(acknowledgements).toEqual([]);
      await bus.close();
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("keeps encrypted auth messages unacked when key refresh is required", async () => {
    const { directory, publicKeyPath, privateKeyPath } = await writeKeyPair();
    try {
      const authEvent = makeEvent("omega.auth.session.login_started");
      const acknowledgements: string[] = [];
      const failures: AuthenticationError[] = [];
      const bus = new PulsarEventBus({
        client: {
          createProducer: async () => ({
            send: async () => undefined,
            close: async () => undefined
          }),
          subscribe: async () => ({
            receive: async () => ({
              data: authEvent.toBytes(),
              messageId: "auth-message",
              encryption: { encrypted: true, decryptionFailed: true }
            }),
            acknowledge: async (messageId) => {
              acknowledgements.push(String(messageId));
            },
            negativeAcknowledge: () => acknowledgements.push("nack"),
            close: async () => undefined
          }),
          createReader: async () => ({
            readNext: async () => ({ data: authEvent.toBytes(), messageId: "reader-message" }),
            seek: async () => undefined,
            close: async () => undefined
          }),
          close: async () => undefined
        },
        config: new EventStreamConfig({
          serviceUrl: "pulsar://127.0.0.1:26650",
          encryptionKeyFile: publicKeyPath,
          privateKeyFile: privateKeyPath
        }),
        onAuthenticationFailure: (error) => failures.push(error)
      });
      const subscriber = await bus.subscribe("persistent://omega/auth/session", {
        subscriptionName: "auth-worker",
        mode: "Shared"
      });
      await expect(subscriber.receive()).rejects.toThrow(AuthenticationError);
      expect(acknowledgements).toEqual([]);
      expect(failures).toHaveLength(1);
      expect(bus.healthy).toBe(false);
      await bus.close();
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("keeps poison messages from auth topics encrypted on the dead-letter topic", async () => {
    const { directory, publicKeyPath, privateKeyPath } = await writeKeyPair();
    try {
      const producers: { topic: string; options: PulsarProducerOptions }[] = [];
      const acknowledgements: string[] = [];
      let delivered = false;
      const bus = new PulsarEventBus({
        client: {
          createProducer: async (topic, options) => {
            producers.push({ topic, options });
            return { send: async () => ({}), close: async () => undefined };
          },
          subscribe: async () => ({
            receive: async () => {
              if (delivered) return new Promise<never>(() => undefined);
              delivered = true;
              return {
                data: new TextEncoder().encode("not-a-cloudevent"),
                properties: { ce_id: "poison-auth" },
                messageId: "poison-auth-message",
                encryption: { encrypted: true, decryptionFailed: false }
              };
            },
            acknowledge: async (messageId) => {
              acknowledgements.push(String(messageId));
            },
            negativeAcknowledge: () => undefined,
            close: async () => undefined
          }),
          createReader: async () => {
            throw new Error("not used");
          },
          close: async () => undefined
        },
        config: new EventStreamConfig({
          serviceUrl: "pulsar://127.0.0.1:26650",
          encryptionKeyFile: publicKeyPath,
          privateKeyFile: privateKeyPath
        })
      });
      const subscriber = await bus.subscribe("persistent://omega/auth/session", {
        subscriptionName: "auth-worker",
        mode: "Shared"
      });
      const controller = new AbortController();
      const pending = subscriber.receive(controller.signal);
      for (let attempt = 0; attempt < 50 && acknowledgements.length === 0; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 5));
      }
      controller.abort();
      await pending.catch(() => undefined);
      expect(producers).toHaveLength(1);
      expect(producers[0]?.topic).toBe("persistent://omega/system/dlq.auth-worker");
      expect(producers[0]?.options.encryption).toEqual({ keyName: "omega-auth", publicKeyPath });
      expect(acknowledgements).toEqual(["poison-auth-message"]);
      await bus.close();
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("requires both key files before subscribing to auth or redelivery topics", async () => {
    let subscribed = 0;
    const failures: AuthenticationError[] = [];
    const bus = new PulsarEventBus({
      client: {
        createProducer: async () => ({ send: async () => undefined, close: async () => undefined }),
        subscribe: async () => {
          subscribed += 1;
          throw new Error("must not subscribe without decryption keys");
        },
        createReader: async () => {
          throw new Error("must not read without decryption keys");
        },
        close: async () => undefined
      },
      onAuthenticationFailure: (error) => failures.push(error)
    });
    await expect(
      bus.subscribe("persistent://omega/auth/session", { subscriptionName: "auth-worker", mode: "Shared" })
    ).rejects.toThrow(AuthenticationError);
    await expect(bus.read("persistent://omega/system/dlq.gate")).rejects.toThrow(AuthenticationError);
    expect(subscribed).toBe(0);
    expect(failures).toHaveLength(2);
    await bus.close();
  });

  it("maps native encryption options onto the pinned pulsar-client API", async () => {
    const { directory, publicKeyPath, privateKeyPath } = await writeKeyPair();
    try {
      const authEvent = makeEvent("omega.auth.session.login_started");
      const producerConfigs: Readonly<Record<string, unknown>>[] = [];
      const consumerConfigs: Readonly<Record<string, unknown>>[] = [];
      const readerConfigs: Readonly<Record<string, unknown>>[] = [];
      const nativeMessage = {
        getData: () => Buffer.from(authEvent.toBytes()),
        getProperties: () => ({ ...authEvent.toProperties() }),
        getPartitionKey: () => "box:gh",
        getMessageId: () => ({ toString: () => "1:1" }),
        getPublishTimestamp: () => 1,
        getRedeliveryCount: () => 0,
        getEncryptionContext: () => ({ keys: [{ key: "omega-auth", value: Buffer.alloc(0) }], isDecryptionFailed: false })
      };
      class NativeClientFake {
        async createProducer(options: Readonly<Record<string, unknown>>) {
          producerConfigs.push(options);
          return { send: async () => ({ toString: () => "sent" }), flush: async () => null, close: async () => null };
        }
        async subscribe(options: Readonly<Record<string, unknown>>) {
          consumerConfigs.push(options);
          return {
            receive: async () => nativeMessage,
            acknowledge: async () => null,
            negativeAcknowledge: () => undefined,
            close: async () => null
          };
        }
        async createReader(options: Readonly<Record<string, unknown>>) {
          readerConfigs.push(options);
          return { readNext: async () => nativeMessage, seekTimestamp: async () => null, close: async () => null };
        }
        async close() {
          return null;
        }
      }
      class MessageIdFake {
        static earliest() {
          return { toString: () => "earliest" };
        }
      }
      const bus = await createPulsarEventBus({
        config: new EventStreamConfig({
          serviceUrl: "pulsar://127.0.0.1:26650",
          encryptionKeyFile: publicKeyPath,
          privateKeyFile: privateKeyPath
        }),
        loader: async () => ({ default: { Client: NativeClientFake, MessageId: MessageIdFake } })
      });
      await bus.publish(
        new PublishRequest({ event: authEvent, topic: "persistent://omega/auth/session", key: "box:gh" })
      );
      expect(producerConfigs[0]).toMatchObject({
        topic: "persistent://omega/auth/session",
        publicKeyPath,
        encryptionKey: "omega-auth",
        cryptoFailureAction: "FAIL"
      });
      const subscriber = await bus.subscribe("persistent://omega/auth/session", {
        subscriptionName: "auth-worker",
        mode: "Shared"
      });
      expect(consumerConfigs[0]).toMatchObject({ privateKeyPath, cryptoFailureAction: "FAIL" });
      const delivery = await subscriber.receive();
      expect(delivery?.event.id).toBe(authEvent.id);
      const reader = await bus.read("persistent://omega/auth/session");
      expect(readerConfigs[0]).toMatchObject({ privateKeyPath, cryptoFailureAction: "FAIL" });
      expect((await reader.receive())?.event.id).toBe(authEvent.id);
      await bus.close();
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("maps broker authentication failures without retrying creation or exposing details", async () => {
    let attempts = 0;
    let waits = 0;
    let failure: AuthenticationError | undefined;
    const bus = new PulsarEventBus({
      client: {
        createProducer: async () => {
          attempts += 1;
          throw new Error("Unauthorized");
        },
        subscribe: async () => ({
          receive: async () => ({ data: new Uint8Array(), messageId: 1 }),
          acknowledge: async () => undefined,
          negativeAcknowledge: () => undefined,
          close: async () => undefined
        }),
        createReader: async () => ({
          readNext: async () => ({ data: new Uint8Array(), messageId: 1 }),
          seek: async () => undefined,
          close: async () => undefined
        }),
        close: async () => undefined
      },
      creationRetry: { wait: async () => { waits += 1; } },
      onAuthenticationFailure: (error) => { failure = error; }
    });
    await expect(
      bus.publish(new PublishRequest({ event: makeEvent(), topic: "persistent://omega/sdlc/pr", key: "repo#3" }))
    ).rejects.toThrow(AuthenticationError);
    expect(attempts).toBe(1);
    expect(waits).toBe(0);
    expect(failure).toBeInstanceOf(AuthenticationError);
    expect(bus.healthy).toBe(false);
    await bus.close();
  });
  it("checks token file permissions at every read and keeps config repr secret-free", async () => {
    const directory = await mkdtemp(join(tmpdir(), "eventstream-config-"));
    const token = join(directory, "token");
    try {
      // Generated per run so no credential-shaped literal is committed.
      const tokenValue = ["xai", randomBytes(8).toString("hex")].join("-");
      await writeFile(token, tokenValue, { mode: 0o600 });
      await chmod(token, 0o600);
      const config = new EventStreamConfig({
        serviceUrl: "pulsar://127.0.0.1:26650",
        tokenFile: token
      });
      expect(await config.readSecret("tokenFile")).toBe(tokenValue);
      expect(config.toString()).not.toContain(tokenValue);
      await writeFile(token, "rotated-token", { mode: 0o600 });
      await chmod(token, 0o600);
      expect(await config.readSecret("tokenFile")).toBe("rotated-token");
      await chmod(token, 0o644);
      await expect(config.readSecret("tokenFile")).rejects.toThrow(ConfigurationError);
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("rejects plaintext secret environment variables and accepts only file paths", () => {
    expect(() => EventStreamConfig.fromEnv({ OMEGA_EVENTSTREAM_TOKEN: "not-a-path" })).toThrow(
      ConfigurationError
    );
    expect(() => EventStreamConfig.fromEnv({ OMEGA_EVENTSTREAM_API_KEY: "not-a-path" })).toThrow(
      ConfigurationError
    );
    const config = EventStreamConfig.fromEnv({
      OMEGA_EVENTSTREAM_TOKEN_FILE: "/run/credentials/eventstream-token"
    });
    expect(config.tokenFile).toBe("/run/credentials/eventstream-token");
    // Same *_FILE names as the Python EventStreamConfig (ENCRYPTION_KEY_FILE /
    // PRIVATE_KEY_FILE), so one deployment secret layout serves both SDKs.
    const keyed = EventStreamConfig.fromEnv({
      OMEGA_EVENTSTREAM_ENCRYPTION_KEY_FILE: "/run/credentials/omega-auth.pub.pem",
      OMEGA_EVENTSTREAM_PRIVATE_KEY_FILE: "/run/credentials/omega-auth.key.pem"
    });
    expect(keyed.encryptionKeyFile).toBe("/run/credentials/omega-auth.pub.pem");
    expect(keyed.privateKeyFile).toBe("/run/credentials/omega-auth.key.pem");
    expect(String(keyed)).not.toContain("/run/credentials");
    expect(() => EventStreamConfig.fromEnv({ OMEGA_EVENTSTREAM_PRIVATE_KEY: "inline" })).toThrow(ConfigurationError);
    expect(() => new EventStreamConfig({
      serviceUrl: "pulsar://broker.example:6650",
      adminUrl: "http://admin.example:8080",
      tokenFile: "/run/credentials/token",
      tlsCaFile: "/run/credentials/ca"
    })).toThrow(ConfigurationError);
    expect(() => new EventStreamConfig({
      serviceUrl: "pulsar+ssl://user:password@broker.example:6651",
      adminUrl: "https://admin.example:8443",
      tokenFile: "/run/credentials/token",
      tlsCaFile: "/run/credentials/ca"
    })).toThrow(ConfigurationError);
  });

  it("gives encrypted and plain producers on one retry/DLQ topic distinct broker names", async () => {
    // Greptile PR #86: both modes reused one producerName on the same topic,
    // and Pulsar rejects the second live producer with a duplicate name.
    const { directory, publicKeyPath, privateKeyPath } = await writeKeyPair();
    try {
      const live = new Set<string>();
      const created: { topic: string; name: string | undefined; encrypted: boolean }[] = [];
      const bus = new PulsarEventBus({
        client: {
          createProducer: async (topic, options) => {
            const identity = `${topic}\0${String(options.producerName)}`;
            if (live.has(identity)) throw new Error(`Producer with name '${String(options.producerName)}' is already connected to topic`);
            live.add(identity);
            created.push({ topic, name: options.producerName, encrypted: options.encryption !== undefined });
            return { send: async () => ({}), close: async () => undefined };
          },
          subscribe: async () => {
            throw new Error("not used");
          },
          createReader: async () => {
            throw new Error("not used");
          },
          close: async () => undefined
        },
        config: new EventStreamConfig({
          serviceUrl: "pulsar://127.0.0.1:26650",
          encryptionKeyFile: publicKeyPath,
          privateKeyFile: privateKeyPath
        })
      });
      for (const topic of ["persistent://omega/system/retry.escalator", "persistent://omega/system/dlq.escalator"]) {
        await bus.publish(
          new PublishRequest({
            event: makeEvent(),
            topic,
            key: "repo#3",
            producerName: "escalator",
            properties: { omega_origin_topic: "persistent://omega/auth/session" }
          })
        );
        await bus.publish(
          new PublishRequest({ event: makeEvent(), topic, key: "repo#3", producerName: "escalator" })
        );
      }
      expect(created).toEqual([
        { topic: "persistent://omega/system/retry.escalator", name: "escalator-encrypted", encrypted: true },
        { topic: "persistent://omega/system/retry.escalator", name: "escalator", encrypted: false },
        { topic: "persistent://omega/system/dlq.escalator", name: "escalator-encrypted", encrypted: true },
        { topic: "persistent://omega/system/dlq.escalator", name: "escalator", encrypted: false }
      ]);
      expect(nativeProducerName("escalator", true)).toBe("escalator-encrypted");
      expect(nativeProducerName("escalator", false)).toBe("escalator");
      await bus.close();
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("restores authentication health after a protected publish succeeds again", async () => {
    // Greptile PR #86: only decoding reset the flag, so a publisher-only bus
    // stayed unhealthy after its key file was repaired.
    const { directory, publicKeyPath, privateKeyPath } = await writeKeyPair();
    try {
      let sends = 0;
      const bus = new PulsarEventBus({
        client: {
          createProducer: async () => ({
            send: async () => {
              sends += 1;
              return {};
            },
            close: async () => undefined
          }),
          subscribe: async () => {
            throw new Error("not used");
          },
          createReader: async () => {
            throw new Error("not used");
          },
          close: async () => undefined
        },
        config: new EventStreamConfig({
          serviceUrl: "pulsar://127.0.0.1:26650",
          encryptionKeyFile: publicKeyPath,
          privateKeyFile: privateKeyPath
        })
      });
      const authRequest = new PublishRequest({
        event: makeEvent("omega.auth.session.login_started"),
        topic: "persistent://omega/auth/session",
        key: "box:gh"
      });
      await chmod(privateKeyPath, 0o644);
      await expect(bus.publish(authRequest)).rejects.toThrow(AuthenticationError);
      expect(bus.healthy).toBe(false);
      // A plain publish proves nothing about the key material.
      await bus.publish(new PublishRequest({ event: makeEvent(), topic: "persistent://omega/sdlc/pr", key: "repo#3" }));
      expect(bus.healthy).toBe(false);
      await chmod(privateKeyPath, 0o600);
      await bus.publish(authRequest);
      expect(sends).toBe(2);
      expect(bus.healthy).toBe(true);
      await bus.close();
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });
});
