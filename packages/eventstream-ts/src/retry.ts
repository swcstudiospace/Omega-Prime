import { MAX_BUSINESS_ATTEMPTS, MAX_RETRY_DELAY_SECONDS, decorrelatedJitter } from "./backoff.js";
import { ContractError, AuthenticationError, isPlainRecord, PermanentError, safeErrorMessage } from "./errors.js";
import { CloudEvent, PublishRequest } from "./envelope.js";
import type { JsonValue } from "./taxonomy.js";
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
  // omega_failure_history is the cross-language name the Python runtime reads
  // and writes; omega_retry_history is the legacy TypeScript alias.
  const existing =
    delivery.metadata.properties.omega_failure_history ?? delivery.metadata.properties.omega_retry_history;
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
    omega_failure_history: JSON.stringify(history),
    omega_retry_history: JSON.stringify(history)
  };
}

/**
 * Build the omega.control.signal.blocked envelope for a dead-lettered delivery.
 *
 * Mirrors omega_prime.eventstream.retry.RetryRouter._dead_letter so DLQ triage
 * and the merge executor see one contract regardless of which SDK failed: PR
 * events become a `kind: "pr"` blocked signal keyed `repo#number`; everything
 * else becomes `kind: "dlq"` with origin topic, aggregate key, source id and
 * bounded failure history, keyed `dlq:["origin","aggregate"]`.
 */
export function buildDeadLetterRequest(
  delivery: MessageDelivery,
  subscription: string,
  attempt: number,
  reason: string
): PublishRequest {
  validateSubscriptionName(subscription);
  const source = delivery.event;
  const properties = delivery.metadata.properties;
  const safeReason = safeErrorMessage(reason);
  const history = failureHistory(delivery, attempt, safeReason);
  const origin = properties.omega_origin_topic ?? delivery.metadata.topic;
  const data = source.data;
  let payload: Record<string, JsonValue>;
  let key: string;
  if (["repo", "number", "head_sha", "actor"].every((name) => Object.hasOwn(data, name))) {
    payload = {
      kind: "pr",
      repo: data.repo as JsonValue,
      number: data.number as JsonValue,
      head_sha: data.head_sha as JsonValue,
      actor: data.actor as JsonValue,
      reason: safeReason
    };
    key = `${String(data.repo)}#${String(data.number)}`;
  } else {
    const aggregate = delivery.metadata.key;
    payload = {
      kind: "dlq",
      origin_topic: origin,
      aggregate_key: aggregate,
      source_ce_id: source.id,
      reason: safeReason,
      history: history.map((entry) => ({ attempt: entry.attempt, reason: entry.reason }))
    };
    key = `dlq:${JSON.stringify([origin, aggregate])}`;
  }
  const traceparent = source.extensions.traceparent;
  const blocked = new CloudEvent({
    specversion: "1.0",
    id: `dlq:${subscription}:${source.id}`,
    source: source.source,
    type: "omega.control.signal.blocked",
    datacontenttype: "application/json",
    dataschema: "urn:omega:schema:omega.control.signal.blocked:1",
    data: payload,
    ...(typeof traceparent === "string" ? { traceparent } : {})
  });
  const forwarded: Record<string, string> = {};
  for (const [name, value] of Object.entries(properties)) {
    if (name === "omega_retry_target" || name.startsWith("ce_")) continue;
    forwarded[name] = value;
  }
  const serializedHistory = JSON.stringify(history);
  return new PublishRequest({
    event: blocked,
    topic: `persistent://omega/system/dlq.${subscription}`,
    key,
    properties: {
      ...forwarded,
      omega_attempt: String(attempt),
      omega_origin_topic: origin,
      omega_origin_ce_id: properties.omega_origin_ce_id ?? source.id,
      omega_source_ce_id: source.id,
      omega_last_error: safeReason,
      omega_failure_history: serializedHistory,
      omega_retry_history: serializedHistory
    }
  });
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
  if (mustDeadLetter) {
    const deadLetter = buildDeadLetterRequest(delivery, subscription, attempt, reason);
    await publisher.publish(deadLetter);
    await delivery.ack();
    return Object.freeze({ kind: "dead-letter", topic: deadLetter.topic });
  }
  const nextAttempt = attempt + 1;
  const properties = retryProperties(delivery, attempt, nextAttempt, reason);
  const topic = `persistent://omega/system/retry.${subscription}`;
  const deliverAfterSeconds = Math.min(
    MAX_RETRY_DELAY_SECONDS,
    Math.max(options.outcome.kind === "retry" ? options.outcome.afterSeconds ?? 0 : 0, decorrelatedJitter(options.previousDelaySeconds, options.rng))
  );
  const request = new PublishRequest({
    event: delivery.event,
    topic,
    key: delivery.metadata.key,
    properties,
    deliverAfterSeconds
  });
  await publisher.publish(request);
  await delivery.ack();
  return Object.freeze({ kind: "retry", delaySeconds: deliverAfterSeconds, topic });
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

