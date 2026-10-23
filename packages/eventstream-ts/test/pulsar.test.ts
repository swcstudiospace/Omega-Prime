import { mkdtemp, chmod, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { CloudEvent, PublishRequest } from "../src/envelope.js";
import { AuthenticationError, ConfigurationError, ContractError } from "../src/errors.js";
import { NATIVE_REDELIVERY_SCHEMA_SAMPLES, NATIVE_TOPIC_SCHEMAS } from "../src/native-schemas.generated.js";
import { EventStreamConfig } from "../src/config.js";
import {
  createPulsarEventBus,
  PulsarEventBus,
  loadPulsarClient,
  nativeJsonMetadataForTopic,
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
  it("refuses auth-family publication without a file-backed key and crypto provider", async () => {
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
    const messages = [
      {
        data: Uint8Array.from([0x7b]),
        properties: { ce_id: "poison", omega_retry_history: "[]", token: "Bearer test-secret" },
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
    expect(history[0]?.reason).not.toContain("test-secret");
    expect(deadLetters[0]?.properties.token).not.toContain("test-secret");
    await delivery?.ack();
    expect(calls.at(-1)).toBe("ack:valid-1");
    await bus.close();
  });


  it("encrypts auth-family payloads even when publishing to a retry topic", async () => {
    const directory = await mkdtemp(join(tmpdir(), "eventstream-auth-"));
    const encryptionKey = join(directory, "key");
    try {
      await writeFile(encryptionKey, "key-material", { mode: 0o600 });
      await chmod(encryptionKey, 0o600);
      const authEvent = makeEvent("omega.auth.session.login_started");
      let sent:
        | { data: Uint8Array; properties: Readonly<Record<string, string>>; partitionKey: string }
        | undefined;
      let encrypted = 0;
      let decrypted = 0;
      const transform = (bytes: Uint8Array) => Uint8Array.from(bytes, (byte) => byte ^ 0xff);
      const bus = new PulsarEventBus({
        client: {
          createProducer: async () => ({
            send: async (message) => {
              sent = {
                data: message.data,
                properties: message.properties,
                partitionKey: message.partitionKey
              };
              return { messageId: "auth-published" };
            },
            close: async () => undefined
          }),
          subscribe: async () => ({
            receive: async () => {
              if (sent === undefined) throw new Error("auth event has not been published");
              return {
                data: sent.data,
                properties: sent.properties,
                partitionKey: sent.partitionKey,
                messageId: "auth-received"
              };
            },
            acknowledge: async () => undefined,
            negativeAcknowledge: () => undefined,
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
          encryptionKeyFile: encryptionKey
        }),
        crypto: {
          encrypt: async (plaintext) => {
            encrypted += 1;
            return transform(plaintext);
          },
          decrypt: async (ciphertext) => {
            decrypted += 1;
            return transform(ciphertext);
          }
        }
      });

      await bus.publish(
        new PublishRequest({
          event: authEvent,
          topic: "persistent://omega/system/retry.auth-worker",
          key: "box:gh"
        })
      );
      expect(sent?.properties.omega_encrypted).toBe("true");
      expect(sent?.data).not.toEqual(authEvent.toBytes());
      const subscriber = await bus.subscribe("persistent://omega/system/retry.auth-worker", {
        subscriptionName: "auth-worker",
        mode: "Shared"
      });
      const delivery = await subscriber.receive();
      expect(delivery?.event.id).toBe(authEvent.id);
      expect(encrypted).toBe(1);
      expect(decrypted).toBe(1);
      await delivery?.ack();
      await bus.close();
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });
  it("keeps encrypted auth messages unacked when key refresh is required", async () => {
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
            properties: { omega_encrypted: "true" },
            messageId: "auth-message"
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
      await writeFile(token, "xai-abcdefgh1234", { mode: 0o600 });
      await chmod(token, 0o600);
      const config = new EventStreamConfig({
        serviceUrl: "pulsar://127.0.0.1:26650",
        tokenFile: token
      });
      expect(await config.readSecret("tokenFile")).toBe("xai-abcdefgh1234");
      expect(config.toString()).not.toContain("xai-abcdefgh1234");
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
});
