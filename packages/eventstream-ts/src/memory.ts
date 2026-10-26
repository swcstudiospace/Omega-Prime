import { ContractError } from "./errors.js";
import { CloudEvent, PublishRequest, type PublishReceipt } from "./envelope.js";
import { EventSignal, createAbortError } from "./ports.js";
import type {
  Clock,
  DeliveryMetadata,
  EventBus,
  EventReader,
  EventSubscriber,
  MessageDelivery,
  ReadOptions,
  SubscribeOptions
} from "./ports.js";

interface StoredEvent {
  readonly index: number;
  readonly request: PublishRequest;
  readonly publishedAt: number;
  readonly dueAt: number;
  readonly ackedBy: Set<string>;
  readonly redeliveryCounts: Record<string, number>;
}

/** One receiver's hold on a message within a shared subscription. */
interface InFlightEntry {
  readonly record: StoredEvent;
  readonly owner: object;
  readonly lease: symbol;
}

export class ManualClock implements Clock {
  #current: number;
  readonly #listeners = new Set<() => void>();

  constructor(initialTime = 0) {
    if (!Number.isFinite(initialTime)) throw new ContractError("clock time must be finite");
    this.#current = initialTime;
  }

  now(): number {
    return this.#current;
  }

  onAdvance(listener: () => void): () => void {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  }

  advanceBy(milliseconds: number): void {
    if (!Number.isFinite(milliseconds) || milliseconds < 0) {
      throw new ContractError("clock advance must be nonnegative");
    }
    this.#current += milliseconds;
    for (const listener of this.#listeners) listener();
  }
}

class MemoryDelivery implements MessageDelivery {
  readonly event: CloudEvent;
  readonly metadata: DeliveryMetadata;
  readonly #acknowledge: () => void;
  readonly #reject: () => void;
  #settled = false;

  constructor(event: StoredEvent, subscriptionName: string, acknowledge: () => void, reject: () => void) {
    this.event = event.request.event;
    this.metadata = Object.freeze({
      topic: event.request.topic,
      key: event.request.key,
      // Caller properties first, the event's own CloudEvents properties last
      // (matching the Pulsar adapters), so a stale ce_* value cannot contradict
      // the delivered event's identity.
      properties: Object.freeze({
        ...event.request.properties,
        ...event.request.event.toProperties()
      }),
      ...(event.request.sequenceId === undefined ? {} : { sequenceId: String(event.request.sequenceId) }),
      messageId: event.index,
      redelivered: (event.redeliveryCounts[subscriptionName] ?? 0) > 0,
      publishedAt: event.publishedAt
    });
    this.#acknowledge = acknowledge;
    this.#reject = reject;
  }

  async ack(): Promise<void> {
    if (this.#settled) return;
    this.#acknowledge();
    this.#settled = true;
  }

  async nack(): Promise<void> {
    if (this.#settled) return;
    this.#reject();
    this.#settled = true;
  }
}

export interface MemoryEventBusOptions {
  readonly clock?: Clock;
}

export class MemoryEventBus implements EventBus {
  readonly #clock: Clock;
  readonly #wake = new EventSignal();
  readonly #records: StoredEvent[] = [];
  readonly #subscribers = new Set<MemorySubscription>();
  /** In-flight message indexes shared by subscribers of the same name. */
  readonly #sharedInFlight = new Map<string, Map<number, InFlightEntry>>();
  readonly #readers = new Set<MemoryReader>();
  readonly #unsubscribeClock: () => void;
  #closed = false;

  constructor(options: MemoryEventBusOptions = {}) {
    this.#clock = options.clock ?? new ManualClock(Date.now());
    this.#unsubscribeClock = this.#clock.onAdvance(() => this.#wake.signal());
  }

  get readyCount(): number {
    return this.#records.filter((record) => record.dueAt <= this.#clock.now()).length;
  }

  get delayedCount(): number {
    return this.#records.filter((record) => record.dueAt > this.#clock.now()).length;
  }

  get publishedCount(): number {
    return this.#records.length;
  }
  currentTime(): number {
    return this.#clock.now();
  }

  async publish(request: PublishRequest): Promise<PublishReceipt> {
    if (this.#closed) throw new ContractError("memory EventBus is closed");
    const event = CloudEvent.fromBytes(request.event.toBytes());
    const safeRequest = new PublishRequest({ ...request, event });
    const publishedAt = this.#clock.now();
    const record: StoredEvent = {
      index: this.#records.length,
      request: safeRequest,
      publishedAt,
      dueAt: publishedAt + (request.deliverAfterSeconds ?? 0) * 1000,
      ackedBy: new Set<string>(),
      redeliveryCounts: Object.create(null) as Record<string, number>
    };
    this.#records.push(record);
    this.#wake.signal();
    return Object.freeze({
      topic: request.topic,
      eventId: request.event.id,
      publishedAt,
      ...(request.sequenceId === undefined ? {} : { sequenceId: String(request.sequenceId) })
    });
  }

  async subscribe(topic: string, options: SubscribeOptions): Promise<EventSubscriber> {
    if (this.#closed) throw new ContractError("memory EventBus is closed");
    if (!options.subscriptionName.trim()) throw new ContractError("subscription name must be nonempty");
    const maxInFlight = options.maxInFlight ?? 16;
    if (!Number.isInteger(maxInFlight) || maxInFlight < 1) throw new ContractError("maxInFlight must be positive");
    const startIndex = options.initialPosition === "Latest" ? this.#records.length : 0;
    const subscriber = new MemorySubscription(this, topic, options.subscriptionName, options.mode, maxInFlight, startIndex);
    this.#subscribers.add(subscriber);
    return subscriber;
  }

  async read(topic: string, options: ReadOptions = {}): Promise<EventReader> {
    if (this.#closed) throw new ContractError("memory EventBus is closed");
    const reader = new MemoryReader(this, topic, options.startAt ?? 0);
    this.#readers.add(reader);
    return reader;
  }

  async close(): Promise<void> {
    if (this.#closed) return;
    this.#closed = true;
    this.#unsubscribeClock();
    for (const subscriber of this.#subscribers) subscriber.closeFromBus();
    for (const reader of this.#readers) reader.closeFromBus();
    this.#wake.signal();
  }

  version(): number {
    return this.#wake.generation;
  }

  waitForChange(version: number, signal?: AbortSignal): Promise<void> {
    return this.#wake.wait(version, signal);
  }

  recordsFrom(index: number): readonly StoredEvent[] {
    return this.#records.slice(index);
  }

  isClosed(): boolean {
    return this.#closed;
  }

  wake(): void {
    this.#wake.signal();
  }

  removeSubscriber(subscriber: MemorySubscription): void {
    this.#subscribers.delete(subscriber);
  }

  sharedInFlight(topic: string, subscriptionName: string): Map<number, InFlightEntry> {
    const key = `${topic}\0${subscriptionName}`;
    let map = this.#sharedInFlight.get(key);
    if (map === undefined) {
      map = new Map();
      this.#sharedInFlight.set(key, map);
    }
    return map;
  }

  removeReader(reader: MemoryReader): void {
    this.#readers.delete(reader);
  }
}

class MemorySubscription implements EventSubscriber {
  readonly #bus: MemoryEventBus;
  readonly #topic: string;
  readonly #subscriptionName: string;
  readonly #maxInFlight: number;
  readonly #startIndex: number;
  /** Shared by every receiver of this topic + subscription; entries record their owner. */
  readonly #inFlight: Map<number, InFlightEntry>;
  readonly mode: SubscribeOptions["mode"];
  #closed = false;

  constructor(bus: MemoryEventBus, topic: string, name: string, mode: SubscribeOptions["mode"], maxInFlight: number, startIndex: number) {
    this.#bus = bus;
    this.#topic = topic;
    this.#subscriptionName = name;
    this.#inFlight = bus.sharedInFlight(topic, name);
    this.mode = mode;
    this.#maxInFlight = maxInFlight;
    this.#startIndex = startIndex;
  }

  get inFlightCount(): number {
    return this.#ownedCount();
  }

  #ownedCount(): number {
    let count = 0;
    for (const entry of this.#inFlight.values()) if (entry.owner === this) count += 1;
    return count;
  }

  async receive(signal?: AbortSignal): Promise<MessageDelivery | undefined> {
    while (!this.#closed && !this.#bus.isClosed()) {
      if (signal?.aborted) throw createAbortError();
      const version = this.#bus.version();
      const item = this.#nextAvailable();
      if (item !== undefined) return item;
      await this.#bus.waitForChange(version, signal);
    }
    if (signal?.aborted) throw createAbortError();
    return undefined;
  }

  async close(): Promise<void> {
    this.#close(false);
  }

  closeFromBus(): void {
    this.#close(true);
  }

  #close(fromBus: boolean): void {
    if (this.#closed) return;
    this.#closed = true;
    // Release only this receiver's holds; other receivers on the same
    // subscription keep their active messages (no duplicate dispatch).
    for (const [index, entry] of [...this.#inFlight]) {
      if (entry.owner !== this) continue;
      entry.record.redeliveryCounts[this.#subscriptionName] = (entry.record.redeliveryCounts[this.#subscriptionName] ?? 0) + 1;
      this.#inFlight.delete(index);
    }
    this.#bus.removeSubscriber(this);
    if (!fromBus) this.#bus.wake();
  }

  #nextAvailable(): MessageDelivery | undefined {
    if (this.#ownedCount() >= this.#maxInFlight) return undefined;
    const blockedKeys = new Set<string>();
    for (const entry of this.#inFlight.values()) blockedKeys.add(entry.record.request.key);
    for (const record of this.#bus.recordsFrom(this.#startIndex)) {
      if (record.request.topic !== this.#topic || record.ackedBy.has(this.#subscriptionName)) continue;
      if (this.#inFlight.has(record.index)) continue;
      if (record.dueAt > this.#bus.currentTime()) {
        blockedKeys.add(record.request.key);
        continue;
      }
      if (blockedKeys.has(record.request.key)) continue;
      const entry: InFlightEntry = { record, owner: this, lease: Symbol("memory-lease") };
      this.#inFlight.set(record.index, entry);
      const settle = (action: () => void): void => {
        // A hold released by close() (or re-leased to another receiver) is
        // stale: its settlement must not ack or redeliver someone else's work.
        if (this.#inFlight.get(record.index)?.lease !== entry.lease) {
          throw new ContractError("stale settlement: this receiver no longer holds the message");
        }
        action();
        this.#inFlight.delete(record.index);
        this.#bus.wake();
      };
      return new MemoryDelivery(
        record,
        this.#subscriptionName,
        () => settle(() => record.ackedBy.add(this.#subscriptionName)),
        () =>
          settle(() => {
            record.redeliveryCounts[this.#subscriptionName] = (record.redeliveryCounts[this.#subscriptionName] ?? 0) + 1;
          })
      );
    }
    return undefined;
  }

}

class MemoryReader implements EventReader {
  readonly #bus: MemoryEventBus;
  readonly #topic: string;
  #cursor = 0;
  #closed = false;

  constructor(bus: MemoryEventBus, topic: string, startAt: number | Date) {
    this.#bus = bus;
    this.#topic = topic;
    if (startAt instanceof Date) {
      this.#cursor = this.#findDatePosition(startAt.getTime());
    } else {
      this.#setCursor(startAt);
    }
  }

  async receive(signal?: AbortSignal): Promise<MessageDelivery | undefined> {
    while (!this.#closed && !this.#bus.isClosed()) {
      if (signal?.aborted) throw createAbortError();
      const version = this.#bus.version();
      const records = this.#bus.recordsFrom(this.#cursor);
      for (const record of records) {
        this.#cursor = record.index + 1;
        if (record.request.topic !== this.#topic) continue;
        if (record.dueAt > this.#bus.currentTime()) {
          this.#cursor = record.index;
          break;
        }
        return new MemoryDelivery(record, "reader", () => undefined, () => undefined);
      }
      await this.#bus.waitForChange(version, signal);
    }
    if (signal?.aborted) throw createAbortError();
    return undefined;
  }

  async seek(position: number | Date): Promise<void> {
    if (position instanceof Date) this.#cursor = this.#findDatePosition(position.getTime());
    else this.#setCursor(position);
    this.#bus.wake();
  }

  async cancel(): Promise<void> {
    this.#close();
  }

  async close(): Promise<void> {
    this.#close();
  }

  closeFromBus(): void {
    this.#close();
  }

  #setCursor(position: number): void {
    if (!Number.isSafeInteger(position) || position < 0) throw new ContractError("reader position must be a nonnegative integer");
    this.#cursor = Math.min(position, this.#bus.recordsFrom(0).length);
  }

  #findDatePosition(timestamp: number): number {
    if (!Number.isFinite(timestamp)) throw new ContractError("reader time position is invalid");
    const record = this.#bus.recordsFrom(0).find((item) => item.publishedAt >= timestamp);
    return record?.index ?? this.#bus.recordsFrom(0).length;
  }

  #close(): void {
    if (this.#closed) return;
    this.#closed = true;
    this.#bus.removeReader(this);
    this.#bus.wake();
  }
}
