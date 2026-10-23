import { ContractError } from "./errors.js";
import { CloudEvent, PublishRequest, type PublishReceipt } from "./envelope.js";
import { EventSignal } from "./ports.js";
import type { Publisher } from "./ports.js";

interface AppendInput {
  readonly event: CloudEvent;
  readonly topic: string;
  readonly key: string;
  readonly properties?: Readonly<Record<string, string>>;
  readonly deliverAfterSeconds?: number;
}

interface OutboxRow {
  readonly request: PublishRequest;
  readonly bytes: number;
  sent: boolean;
}

export interface OutboxOptions {
  readonly component: string;
  readonly maxBytes: number;
}

export class OutboxTransaction {
  readonly #outbox: Outbox;
  readonly #staged: AppendInput[] = [];
  #finished = false;

  constructor(outbox: Outbox) {
    this.#outbox = outbox;
  }

  append(event: CloudEvent, topic: string, key: string, properties?: Readonly<Record<string, string>>): void {
    if (this.#finished) throw new ContractError("outbox transaction is closed");
    this.#staged.push({ event, topic, key, ...(properties === undefined ? {} : { properties }) });
  }

  commit(): readonly PublishRequest[] {
    if (this.#finished) throw new ContractError("outbox transaction is closed");
    const requests = this.#outbox.appendBatch(this.#staged);
    this.#finished = true;
    return requests;
  }

  rollback(): void {
    if (this.#finished) throw new ContractError("outbox transaction is closed");
    this.#staged.length = 0;
    this.#finished = true;
  }
}

export class Outbox {
  readonly #component: string;
  readonly #maxBytes: number;
  readonly #rows: OutboxRow[] = [];
  readonly #highWatermarks = new Map<string, bigint>();
  readonly #producerNames = new Map<string, string>();
  readonly #wake = new EventSignal();
  #queuedBytes = 0;
  #closing = false;
  #closed = false;
  #runPromise: Promise<void> | undefined;

  constructor(options: OutboxOptions) {
    if (!/^[a-z0-9][a-z0-9._-]{0,63}$/i.test(options.component)) {
      throw new ContractError("outbox component name is invalid");
    }
    if (!Number.isSafeInteger(options.maxBytes) || options.maxBytes < 1) {
      throw new ContractError("outbox byte limit must be a positive integer");
    }
    this.#component = options.component;
    this.#maxBytes = options.maxBytes;
  }

  get pendingCount(): number {
    return this.#rows.reduce((count, row) => count + (row.sent ? 0 : 1), 0);
  }

  get queuedBytes(): number {
    return this.#queuedBytes;
  }

  get healthSignal(): boolean {
    return this.#queuedBytes >= this.#maxBytes * 0.8;
  }

  get wakeGeneration(): number {
    return this.#wake.generation;
  }

  beginTransaction(): OutboxTransaction {
    if (this.#closing || this.#closed) throw new ContractError("outbox is closed");
    return new OutboxTransaction(this);
  }

  append(event: CloudEvent, topic: string, key: string, properties?: Readonly<Record<string, string>>): PublishRequest {
    if (this.#closing || this.#closed) throw new ContractError("outbox is closed");
    const [request] = this.appendBatch([{ event, topic, key, ...(properties === undefined ? {} : { properties }) }]);
    if (request === undefined) throw new ContractError("outbox append failed");
    return request;
  }

  appendBatch(inputs: readonly AppendInput[]): readonly PublishRequest[] {
    if (this.#closing || this.#closed) throw new ContractError("outbox is closed");
    const rows: OutboxRow[] = [];
    const nextWatermarks = new Map<string, bigint>();
    let bytesToAdd = 0;
    for (const input of inputs) {
      const current = nextWatermarks.get(input.topic) ?? this.#highWatermarks.get(input.topic) ?? 0n;
      const sequenceId = current + 1n;
      const producerName = this.#producerName(input.topic);
      const event = CloudEvent.fromBytes(input.event.toBytes());
      const request = new PublishRequest({
        event,
        topic: input.topic,
        key: input.key,
        sequenceId: sequenceId.toString(),
        producerName,
        ...(input.deliverAfterSeconds === undefined ? {} : { deliverAfterSeconds: input.deliverAfterSeconds }),
        ...(input.properties === undefined ? {} : { properties: input.properties })
      });
      const metadataBytes = new TextEncoder().encode(JSON.stringify({
        topic: request.topic,
        key: request.key,
        sequenceId: request.sequenceId,
        producerName: request.producerName,
        deliverAfterSeconds: request.deliverAfterSeconds,
        properties: request.properties
      })).byteLength;
      const bytes = event.toBytes().byteLength + metadataBytes;
      bytesToAdd += bytes;
      nextWatermarks.set(input.topic, sequenceId);
      rows.push({ request, bytes, sent: false });
    }
    if (this.#queuedBytes + bytesToAdd > this.#maxBytes) {
      throw new ContractError("outbox capacity exceeded");
    }
    this.#rows.push(...rows);
    this.#queuedBytes += bytesToAdd;
    for (const [topic, watermark] of nextWatermarks) this.#highWatermarks.set(topic, watermark);
    if (rows.length > 0) this.#wake.signal();
    return Object.freeze(rows.map((row) => row.request));
  }

  async relayOnce(publisher: Publisher): Promise<readonly PublishReceipt[]> {
    if (this.#closed) throw new ContractError("outbox is closed");
    const receipts: PublishReceipt[] = [];
    for (const row of this.#rows) {
      if (row.sent) continue;
      const receipt = await publisher.publish(row.request);
      row.sent = true;
      this.#queuedBytes -= row.bytes;
      receipts.push(receipt);
    }
    return Object.freeze(receipts);
  }

  cleanupSent(): number {
    const before = this.#rows.length;
    for (let index = this.#rows.length - 1; index >= 0; index -= 1) {
      if (this.#rows[index]?.sent) this.#rows.splice(index, 1);
    }
    return before - this.#rows.length;
  }

  connected(): void {
    if (!this.#closed) this.#wake.signal();
  }

  async run(publisher: Publisher, signal?: AbortSignal): Promise<void> {
    if (this.#runPromise !== undefined) throw new ContractError("outbox relay is already running");
    this.#runPromise = this.#runRelay(publisher, signal);
    return this.#runPromise;
  }

  async close(publisher: Publisher): Promise<void> {
    if (this.#closed) return;
    this.#closing = true;
    this.#wake.signal();
    if (this.#runPromise !== undefined) await this.#runPromise;
    if (this.pendingCount > 0) await this.relayOnce(publisher);
    this.#closed = true;
  }

  #producerName(topic: string): string {
    const existing = this.#producerNames.get(topic);
    if (existing !== undefined) return existing;
    const suffix = topic.replace(/^persistent:\/\/omega\//, "").replace(/[^a-zA-Z0-9]+/g, "-").replace(/^-|-$/g, "");
    const name = `${this.#component}-${suffix}`;
    this.#producerNames.set(topic, name);
    return name;
  }

  async #runRelay(publisher: Publisher, signal?: AbortSignal): Promise<void> {
    while (!this.#closed && !signal?.aborted) {
      const version = this.#wake.generation;
      try {
        await this.relayOnce(publisher);
      } catch (error) {
        if (this.#closing) throw error;
        try {
          await this.#wake.wait(version, signal);
        } catch {
          if (signal?.aborted) return;
          throw error;
        }
        continue;
      }
      if (this.#closing) return;
      try {
        await this.#wake.wait(version, signal);
      } catch {
        if (signal?.aborted) return;
        throw new ContractError("outbox relay was interrupted");
      }
    }
  }
}
