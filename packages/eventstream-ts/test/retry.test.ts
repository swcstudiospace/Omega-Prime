import { describe, expect, it } from "vitest";
import { CloudEvent, PublishRequest } from "../src/envelope.js";
import { AuthenticationError, PermanentError } from "../src/errors.js";
import { decorrelatedJitter, withCreationBackoff } from "../src/backoff.js";
import { ManualClock, MemoryEventBus } from "../src/memory.js";
import {
  acknowledgeIfNotTargeted,
  applyRetryOutcome,
  retryTopicsFor,
  runSharedRetryHandoff,
  shouldProcessTargeted
} from "../src/retry.js";
import type { EventSubscriber, MessageDelivery } from "../src/ports.js";

const event = new CloudEvent({
  specversion: "1.0",
  id: "ce-retry",
  source: "test://retry",
  type: "omega.sdlc.pr.pushed",
  datacontenttype: "application/json",
  dataschema: "urn:omega:schema:omega.sdlc.pr.pushed:1",
  data: { repo: "repo", number: 5, head_sha: "head", actor: "test", before_sha: "base" },
  omegakey: "repo#5"
});

function delivery(
  events: string[],
  topic = "persistent://omega/sdlc/pr",
  properties: Readonly<Record<string, string>> = { ce_id: event.id }
): MessageDelivery {
  return {
    event,
    metadata: { topic, key: "repo#5", properties, redelivered: false, publishedAt: 1 },
    ack: async () => {
      events.push("ack");
    },
    nack: async () => {
      events.push("nack");
    }
  };
}

describe("retry policy", () => {
  it("samples decorrelated jitter before applying the hard cap", () => {
    expect(decorrelatedJitter(300, () => 0.75)).toBe(600);
    expect(decorrelatedJitter(100, () => 0.5)).toBe(150.5);
    expect(decorrelatedJitter(1, () => 0)).toBe(1);
  });

  it("bounds adapter creation retries and stops on non-retryable failures", async () => {
    const waits: number[] = [];
    let attempts = 0;
    const recovered = await withCreationBackoff(
      async () => {
        attempts += 1;
        if (attempts < 3) throw new Error("temporary");
        return "created";
      },
      {
        attempts: 3,
        random: () => 0.5,
        wait: async (seconds) => {
          waits.push(seconds);
        }
      }
    );
    expect(recovered).toBe("created");
    expect(attempts).toBe(3);
    expect(waits).toEqual([2, 3.5]);

    attempts = 0;
    waits.length = 0;
    await expect(
      withCreationBackoff(
        async () => {
          attempts += 1;
          throw new AuthenticationError("invalid credential");
        },
        {
          attempts: 3,
          wait: async (seconds) => {
            waits.push(seconds);
          },
          shouldRetry: (error) => !(error instanceof AuthenticationError)
        }
      )
    ).rejects.toThrow(AuthenticationError);
    expect(attempts).toBe(1);
    expect(waits).toEqual([]);
  });

  it("waits for retry and DLQ receipts before acknowledging source deliveries", async () => {
    const calls: string[] = [];
    const source = delivery(calls);
    const publisher = {
      publish: async (request: { topic: string; deliverAfterSeconds?: number }) => {
        calls.push(`publish:${request.topic}:${request.deliverAfterSeconds ?? 0}`);
        await Promise.resolve();
        calls.push("receipt");
        return { topic: request.topic, eventId: event.id, publishedAt: 1 };
      }
    };
    await applyRetryOutcome({
      delivery: source,
      outcome: { kind: "retry", afterSeconds: 2, reason: "temporary" },
      publisher,
      subscription: "gate",
      attempt: 1,
      previousDelaySeconds: 300,
      rng: () => 0.75
    });
    expect(calls).toEqual([
      "publish:persistent://omega/system/retry.gate:600",
      "receipt",
      "ack"
    ]);
  });

  it("leaves the source unacked when retry or DLQ publication fails", async () => {
    for (const outcome of [
      { kind: "retry", reason: "retry" },
      { kind: "dead-letter", reason: "poison" }
    ] as const) {
      const calls: string[] = [];
      await expect(
        applyRetryOutcome({
          delivery: delivery(calls),
          outcome,
          publisher: { publish: async () => Promise.reject(new Error("offline")) },
          subscription: "gate",
          attempt: outcome.kind === "retry" ? 1 : 5,
          previousDelaySeconds: 1,
          rng: () => 0.2
        })
      ).rejects.toThrow();
      expect(calls).toEqual([]);
    }
  });

  it("routes direct retries and runs singleton handoff through a separate receiver", async () => {
    expect(retryTopicsFor("persistent://omega/sdlc/pr", "gate", "Shared")).toEqual([
      "persistent://omega/sdlc/pr",
      "persistent://omega/system/retry.gate"
    ]);
    expect(retryTopicsFor("persistent://omega/sdlc/pr", "merge-executor", "Failover")).toEqual([
      "persistent://omega/sdlc/pr"
    ]);
    expect(shouldProcessTargeted({ omega_retry_target: "gate" }, "gate")).toBe(true);
    expect(shouldProcessTargeted({ omega_retry_target: "gate" }, "review")).toBe(false);

    const calls: string[] = [];
    const sourceTopic = "persistent://omega/sdlc/pr";
    const retryDelivery = delivery(calls, "persistent://omega/system/retry.merge-executor", {
      ce_id: event.id,
      omega_origin_topic: sourceTopic,
      omega_retry_history: '[{"attempt":2,"reason":"temporary"}]'
    });
    let received = false;
    const subscriber: EventSubscriber = {
      inFlightCount: 1,
      receive: async () => {
        if (received) return undefined;
        received = true;
        return retryDelivery;
      },
      close: async () => undefined
    };
    const publisher = {
      publish: async (request: { topic: string; properties: Readonly<Record<string, string>> }) => {
        calls.push(`publish:${request.topic}:${request.properties.omega_retry_target}`);
        return { topic: request.topic, eventId: event.id, publishedAt: 2 };
      }
    };
    await runSharedRetryHandoff(subscriber, publisher, sourceTopic, "merge-executor");
    expect(calls).toEqual([`publish:${sourceTopic}:merge-executor`, "ack"]);
  });

  it("keeps authentication failures out of the business retry/DLQ path", async () => {
    const calls: string[] = [];
    const result = await applyRetryOutcome({
      delivery: delivery(calls),
      outcome: { kind: "authentication-failure", error: new AuthenticationError("expired") },
      publisher: { publish: async () => ({ topic: "unused", eventId: event.id, publishedAt: 1 }) },
      subscription: "gate",
      attempt: 3,
      previousDelaySeconds: 2,
      rng: () => 0.5
    });
    expect(result.kind).toBe("authentication-failure");
    expect(calls).toEqual([]);
    const permanent = await applyRetryOutcome({
      delivery: delivery(calls),
      outcome: { kind: "dead-letter", reason: new PermanentError("poison").message },
      publisher: {
        publish: async (request: { topic: string }) => {
          calls.push(request.topic);
          return { topic: request.topic, eventId: event.id, publishedAt: 1 };
        }
      },
      subscription: "gate",
      attempt: 1,
      previousDelaySeconds: 1,
      rng: () => 0.5
    });
    expect(permanent.kind).toBe("dead-letter");
    expect(calls.at(-2)).toBe("persistent://omega/system/dlq.gate");
    expect(calls.at(-1)).toBe("ack");
  });
  it("runs delayed retry through attempt five for all consumer modes", async () => {
    const sourceTopic = "persistent://omega/sdlc/pr";
    const subscription = "merge-executor";
    for (const mode of ["Shared", "Key_Shared", "Failover", "Exclusive"] as const) {
      const clock = new ManualClock(0);
      const bus = new MemoryEventBus({ clock });
      const retryTopic = `persistent://omega/system/retry.${subscription}`;
      const dlqTopic = `persistent://omega/system/dlq.${subscription}`;
      expect(retryTopicsFor(sourceTopic, subscription, mode)).toEqual(
        mode === "Shared" || mode === "Key_Shared" ? [sourceTopic, retryTopic] : [sourceTopic]
      );
      const main = await bus.subscribe(sourceTopic, { subscriptionName: subscription, mode });
      const retryReceiver = await bus.subscribe(retryTopic, {
        subscriptionName: subscription,
        mode: "Shared"
      });
      const deadLetters = await bus.subscribe(dlqTopic, {
        subscriptionName: `${subscription}-dlq`,
        mode: "Shared"
      });
      const observer = mode === "Failover" || mode === "Exclusive"
        ? await bus.subscribe(sourceTopic, { subscriptionName: "observer", mode: "Shared" })
        : undefined;
      await bus.publish(new PublishRequest({ event, topic: sourceTopic, key: "repo#5" }));
      let current = await main.receive();
      if (current === undefined) throw new Error(`source message was missing for ${mode}`);
      if (observer !== undefined) {
        const initial = await observer.receive();
        if (initial === undefined) throw new Error("observer did not receive the initial source event");
        await initial.ack();
      }
      for (let attempt = 1; attempt <= 5; attempt += 1) {
        if (attempt > 1 && observer !== undefined) {
          expect(await acknowledgeIfNotTargeted(current, subscription)).toBe(true);
        }
        const result = await applyRetryOutcome({
          delivery: current,
          outcome: { kind: "retry", reason: `failure-${attempt}` },
          publisher: bus,
          subscription,
          attempt,
          previousDelaySeconds: 1,
          rng: () => 0
        });
        if (attempt === 5) {
          expect(result.kind).toBe("dead-letter");
          break;
        }
        expect(result.kind).toBe("retry");
        if (observer === undefined) {
          const pending = retryReceiver.receive();
          clock.advanceBy(1_000);
          const next = await pending;
          if (next === undefined) throw new Error(`retry message was missing for ${mode}`);
          current = next;
        } else {
          let received = false;
          const oneDeliveryReceiver: EventSubscriber = {
            get inFlightCount() {
              return retryReceiver.inFlightCount;
            },
            receive: async (signal) => {
              if (received) return undefined;
              const next = await retryReceiver.receive(signal);
              if (next !== undefined) received = true;
              return next;
            },
            close: () => retryReceiver.close()
          };
          const handoff = runSharedRetryHandoff(oneDeliveryReceiver, bus, sourceTopic, subscription);
          clock.advanceBy(1_000);
          await handoff;
          const targetedForObserver = await observer.receive();
          if (targetedForObserver === undefined) throw new Error("observer missed the targeted handoff event");
          expect(await acknowledgeIfNotTargeted(targetedForObserver, "observer")).toBe(false);
          const next = await main.receive();
          if (next === undefined) throw new Error(`targeted event was missing for ${mode}`);
          current = next;
        }
        expect(current.event.id).toBe(event.id);
        expect(current.metadata.properties.omega_attempt).toBe(String(attempt + 1));
        expect(current.metadata.properties.omega_origin_topic).toBe(sourceTopic);
      }
      const deadLetter = await deadLetters.receive();
      if (deadLetter === undefined) throw new Error(`dead-letter event was missing for ${mode}`);
      expect(deadLetter.metadata.properties.omega_attempt).toBe("5");
      const history = JSON.parse(deadLetter.metadata.properties.omega_retry_history ?? "[]") as {
        attempt: number;
        reason: string;
      }[];
      expect(history.map((entry) => entry.attempt)).toEqual([1, 2, 3, 4, 5]);
      await deadLetter.ack();
      await bus.close();
    }
  });
});
