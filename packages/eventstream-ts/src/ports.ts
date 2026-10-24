import type { CloudEvent, PublishReceipt, PublishRequest } from "./envelope.js";

export type ConsumerMode = "Key_Shared" | "Shared" | "Failover" | "Exclusive";

export interface DeliveryMetadata {
  readonly topic: string;
  readonly key: string;
  readonly properties: Readonly<Record<string, string>>;
  readonly sequenceId?: string;
  readonly messageId?: string | number;
  readonly redelivered: boolean;
  readonly publishedAt: number;
}

export interface MessageDelivery {
  readonly event: CloudEvent;
  readonly metadata: DeliveryMetadata;
  ack(): Promise<void>;
  nack(): Promise<void>;
}

export interface SubscribeOptions {
  readonly subscriptionName: string;
  readonly mode: ConsumerMode;
  readonly maxInFlight?: number;
  readonly receiverQueueSize?: number;
  readonly initialPosition?: "Earliest" | "Latest";
}

export interface ReadOptions {
  readonly startAt?: number | Date;
  readonly receiverQueueSize?: number;
}

export interface EventSubscriber {
  readonly inFlightCount: number;
  receive(signal?: AbortSignal): Promise<MessageDelivery | undefined>;
  close(): Promise<void>;
}

export interface EventReader {
  receive(signal?: AbortSignal): Promise<MessageDelivery | undefined>;
  seek(position: number | Date): Promise<void>;
  cancel(): Promise<void>;
  close(): Promise<void>;
}

export interface Publisher {
  publish(request: PublishRequest): Promise<PublishReceipt>;
}

export interface EventBus extends Publisher {
  subscribe(topic: string, options: SubscribeOptions): Promise<EventSubscriber>;
  read(topic: string, options?: ReadOptions): Promise<EventReader>;
  close(): Promise<void>;
}

export interface IdempotencyClaim {
  readonly ownerToken: string;
  readonly acquired: boolean;
}

export interface IdempotencyStore {
  claim(subscription: string, eventId: string, ownerToken: string): Promise<IdempotencyClaim>;
  complete(subscription: string, eventId: string, ownerToken: string): Promise<void>;
  release(subscription: string, eventId: string, ownerToken: string): Promise<void>;
}

export interface AdminPort {
  inspect(topic: string): Promise<Readonly<Record<string, unknown>>>;
  close(): Promise<void>;
}

export interface Clock {
  now(): number;
  onAdvance(listener: () => void): () => void;
}

export function createAbortError(): Error {
  const error = new Error("operation was aborted");
  error.name = "AbortError";
  return error;
}

export class EventSignal {
  #generation = 0;
  readonly #waiters = new Set<() => void>();

  get generation(): number {
    return this.#generation;
  }

  signal(): void {
    this.#generation += 1;
    for (const wake of this.#waiters) wake();
    this.#waiters.clear();
  }

  wait(observedGeneration = this.#generation, signal?: AbortSignal): Promise<void> {
    if (this.#generation !== observedGeneration) return Promise.resolve();
    if (signal?.aborted) return Promise.reject(createAbortError());
    return new Promise<void>((resolve, reject) => {
      const cleanup = () => {
        this.#waiters.delete(onSignal);
        signal?.removeEventListener("abort", onAbort);
      };
      const onSignal = () => {
        cleanup();
        resolve();
      };
      const onAbort = () => {
        cleanup();
        reject(createAbortError());
      };
      this.#waiters.add(onSignal);
      signal?.addEventListener("abort", onAbort, { once: true });
      if (this.#generation !== observedGeneration) onSignal();
    });
  }
}
