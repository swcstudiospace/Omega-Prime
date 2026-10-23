import { MAX_BUSINESS_ATTEMPTS, MAX_RETRY_DELAY_SECONDS, decorrelatedJitter } from "./backoff.js";
import { ContractError, AuthenticationError, isPlainRecord, PermanentError, safeErrorMessage } from "./errors.js";
import { PublishRequest } from "./envelope.js";
import type { CloudEvent } from "./envelope.js";
import type { ConsumerMode, EventSubscriber, MessageDelivery, Publisher } from "./ports.js";

export type DeliveryOutcome =
  | { readonly kind: "ack" }
  | { readonly kind: "retry"; readonly reason: string; readonly afterSeconds?: number }
  | { readonly kind: "dead-letter"; readonly reason: string }
  | { readonly kind: "authentication-failure"; readonly error: AuthenticationError };

export type RetryResult =
  | { readonly kind: "ack" }
  | { readonly kind: "retry"; readonly delaySeconds: number; readonly topic: string }
  | { readonly kind: "dead-letter"; readonly topic: string }
  | { readonly kind: "authentication-failure" };

export interface ApplyRetryOptions {
  readonly delivery: MessageDelivery;
  readonly outcome: DeliveryOutcome;
  readonly publisher: Publisher;
  readonly subscription: string;
  readonly attempt: number;
  readonly previousDelaySeconds: number;
  readonly rng?: () => number;
}

export interface HandlerOptions extends Omit<ApplyRetryOptions, "outcome"> {
  readonly handler: (event: CloudEvent) => Promise<DeliveryOutcome | void>;
  readonly onAuthenticationError?: (error: AuthenticationError) => Promise<void>;
}

function errorOutcome(error: unknown): DeliveryOutcome {
  if (error instanceof AuthenticationError) return { kind: "authentication-failure", error };
  if (error instanceof PermanentError || error instanceof ContractError) {
    return { kind: "dead-letter", reason: safeErrorMessage(error) };
  }
  return { kind: "retry", reason: safeErrorMessage(error) };
}
function validateSubscriptionName(subscription: string): void {
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(subscription)) {
    throw new ContractError("retry subscription name is invalid");
  }
}

function failureHistory(
  delivery: MessageDelivery,
  failedAttempt: number,
  reason: string
): readonly { attempt: number; reason: string }[] {
  const existing = delivery.metadata.properties.omega_retry_history;
  let parsed: unknown = [];
  if (existing !== undefined) {
    try {
      parsed = JSON.parse(existing) as unknown;
    } catch {
      parsed = [];
    }
  }
  const history: { attempt: number; reason: string }[] = [];
  let previousAttempt = 0;
  if (Array.isArray(parsed)) {
    for (const value of parsed) {
      if (!isPlainRecord(value)) continue;
      const attemptValue = value.attempt;
      if (
        typeof attemptValue !== "number" ||
        !Number.isSafeInteger(attemptValue) ||
        attemptValue <= previousAttempt ||
        attemptValue >= failedAttempt ||
        attemptValue > MAX_BUSINESS_ATTEMPTS ||
        typeof value.reason !== "string"
      ) {
        continue;
      }
      history.push({ attempt: attemptValue, reason: safeErrorMessage(value.reason) });
      previousAttempt = attemptValue;
    }
  }
  history.push({ attempt: failedAttempt, reason: safeErrorMessage(reason) });
  return Object.freeze(history.map((entry) => Object.freeze(entry)));
}

function retryProperties(
  delivery: MessageDelivery,
  failedAttempt: number,
  nextAttempt: number,
  reason: string
): Record<string, string> {
  const properties = delivery.metadata.properties;
  const history = failureHistory(delivery, failedAttempt, reason);
  return {
    ...delivery.event.toProperties(),
    ...properties,
    omega_attempt: String(nextAttempt),
    omega_origin_topic: properties.omega_origin_topic ?? delivery.metadata.topic,
    omega_origin_ce_id: properties.omega_origin_ce_id ?? delivery.event.id,
    omega_last_error: safeErrorMessage(reason),
    omega_retry_history: JSON.stringify(history)
  };
}


export async function applyRetryOutcome(options: ApplyRetryOptions): Promise<RetryResult> {
  const { delivery, subscription, publisher, attempt } = options;
  if (!Number.isInteger(attempt) || attempt < 1 || attempt > MAX_BUSINESS_ATTEMPTS) {
    throw new ContractError("business attempt must be between one and five");
  }
  if (options.outcome.kind === "authentication-failure") return Object.freeze({ kind: "authentication-failure" });
  if (options.outcome.kind === "ack") {
    await delivery.ack();
    return Object.freeze({ kind: "ack" });
  }
  validateSubscriptionName(subscription);
  const mustDeadLetter = options.outcome.kind === "dead-letter" || attempt >= MAX_BUSINESS_ATTEMPTS;
  const reason = options.outcome.reason;
  const nextAttempt = mustDeadLetter ? attempt : attempt + 1;
  const properties = retryProperties(delivery, attempt, nextAttempt, reason);
  const topic = `persistent://omega/system/${mustDeadLetter ? "dlq" : "retry"}.${subscription}`;
  const deliverAfterSeconds = mustDeadLetter
    ? undefined
    : Math.min(
        MAX_RETRY_DELAY_SECONDS,
        Math.max(options.outcome.kind === "retry" ? options.outcome.afterSeconds ?? 0 : 0, decorrelatedJitter(options.previousDelaySeconds, options.rng))
      );
  const request = new PublishRequest({
    event: delivery.event,
    topic,
    key: delivery.metadata.key,
    properties,
    ...(deliverAfterSeconds === undefined ? {} : { deliverAfterSeconds })
  });
  await publisher.publish(request);
  await delivery.ack();
  if (mustDeadLetter) return Object.freeze({ kind: "dead-letter", topic });
  return Object.freeze({ kind: "retry", delaySeconds: deliverAfterSeconds ?? 0, topic });
}

export async function handleDelivery(options: HandlerOptions): Promise<RetryResult> {
  let outcome: DeliveryOutcome;
  try {
    outcome = (await options.handler(options.delivery.event)) ?? { kind: "ack" };
  } catch (error) {
    outcome = errorOutcome(error);
  }
  if (outcome.kind === "authentication-failure") {
    await options.onAuthenticationError?.(outcome.error);
    return applyRetryOutcome({ ...options, outcome });
  }
  return applyRetryOutcome({ ...options, outcome });
}

export function retryTopicsFor(topic: string, subscription: string, mode: ConsumerMode): readonly string[] {
  validateSubscriptionName(subscription);
  if (!topic.startsWith("persistent://omega/") || topic.includes("..")) {
    throw new ContractError("retry subscription coordinates are invalid");
  }
  if (mode === "Shared" || mode === "Key_Shared") {
    return Object.freeze([topic, `persistent://omega/system/retry.${subscription}`]);
  }
  return Object.freeze([topic]);
}

export function shouldProcessTargeted(properties: Readonly<Record<string, string>>, subscription: string): boolean {
  const target = properties.omega_retry_target;
  return target === undefined || target === subscription;
}

export async function acknowledgeIfNotTargeted(
  delivery: MessageDelivery,
  subscription: string
): Promise<boolean> {
  if (shouldProcessTargeted(delivery.metadata.properties, subscription)) return true;
  await delivery.ack();
  return false;
}

export async function forwardRetryDelivery(
  delivery: MessageDelivery,
  publisher: Publisher,
  originalTopic: string,
  subscription: string
): Promise<void> {
  validateSubscriptionName(subscription);
  if (delivery.metadata.topic !== `persistent://omega/system/retry.${subscription}`) {
    throw new ContractError("retry handoff received a message from the wrong topic");
  }
  const origin = delivery.metadata.properties.omega_origin_topic;
  if (origin !== originalTopic) throw new ContractError("retry handoff origin topic does not match");
  const request = new PublishRequest({
    event: delivery.event,
    topic: originalTopic,
    key: delivery.metadata.key,
    properties: {
      ...delivery.event.toProperties(),
      ...delivery.metadata.properties,
      omega_retry_target: subscription
    }
  });
  await publisher.publish(request);
  await delivery.ack();
}

export async function runSharedRetryHandoff(
  subscriber: EventSubscriber,
  publisher: Publisher,
  originalTopic: string,
  subscription: string,
  signal?: AbortSignal
): Promise<void> {
  for (;;) {
    const delivery = await subscriber.receive(signal);
    if (delivery === undefined) return;
    await forwardRetryDelivery(delivery, publisher, originalTopic, subscription);
  }
}

