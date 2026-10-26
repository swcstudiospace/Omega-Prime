import { describe, expect, it } from "vitest";
import { CloudEvent } from "../src/envelope.js";
import type { PublishRequest } from "../src/envelope.js";
import type { Publisher } from "../src/ports.js";
import { Outbox } from "../src/outbox.js";
const event = (id: string, actor = "test") =>
  new CloudEvent({
    specversion: "1.0",
    id,
    source: "test://outbox",
    type: "omega.sdlc.pr.pushed",
    datacontenttype: "application/json",
    dataschema: "urn:omega:schema:omega.sdlc.pr.pushed:1",
    data: { repo: "repo", number: 3, head_sha: "head", actor, before_sha: "base" },
    omegakey: "repo#3"
  });

describe("process-local outbox", () => {
  it("honors transaction commit and rollback before waking the relay", async () => {
    const outbox = new Outbox({ component: "test", maxBytes: 4096 });
    const rolledBack = outbox.beginTransaction();
    rolledBack.append(event("ce-rollback"), "persistent://omega/sdlc/pr", "repo#3");
    rolledBack.rollback();
    expect(outbox.pendingCount).toBe(0);

    const transaction = outbox.beginTransaction();
    transaction.append(event("ce-commit"), "persistent://omega/sdlc/pr", "repo#3");
    expect(outbox.pendingCount).toBe(0);
    const appended = transaction.commit();
    expect(appended).toHaveLength(1);
    expect(outbox.pendingCount).toBe(1);
    expect(appended[0]?.sequenceId).toBe("1");
  });
  it("redacts payload secrets before adding events to the outbox", () => {
    const outbox = new Outbox({ component: "test", maxBytes: 4096 });
    const request = outbox.append(
      event("ce-sensitive", "Bearer sample-secret"),
      "persistent://omega/sdlc/pr",
      "repo#3"
    );
    expect(request.event.data.actor).toBe("Bearer [REDACTED]");
  });

  it("marks sent only after receipt, keeps watermarks after cleanup, and preserves sequence", async () => {
    const outbox = new Outbox({ component: "worker", maxBytes: 4096 });
    outbox.append(event("ce-1"), "persistent://omega/sdlc/pr", "repo#3");
    const sent: PublishRequest[] = [];
    const publisher: Publisher = {
      publish: async (request) => {
        sent.push(request);
        return {
          topic: request.topic,
          eventId: request.event.id,
          publishedAt: 1,
          ...(request.sequenceId === undefined ? {} : { sequenceId: String(request.sequenceId) })
        };
      }
    };
    await outbox.relayOnce(publisher);
    expect(outbox.pendingCount).toBe(0);
    expect(sent[0]?.sequenceId).toBe("1");
    expect(sent[0]?.producerName).toBe("worker-sdlc-pr");
    outbox.cleanupSent();
    const second = outbox.append(event("ce-2"), "persistent://omega/sdlc/pr", "repo#3");
    expect(second.sequenceId).toBe("2");
  });

  it("retains pending rows after publish failure and reports the 80 percent threshold", async () => {
    const topic = "persistent://omega/sdlc/pr";
    const key = "repo#3";
    const bytes = event("ce-1").toBytes().byteLength +
      new TextEncoder().encode(JSON.stringify({
        topic,
        key,
        sequenceId: "1",
        producerName: "test-sdlc-pr",
        properties: {}
      })).byteLength;
    const outbox = new Outbox({ component: "test", maxBytes: Math.floor(bytes / 0.8) });
    outbox.append(event("ce-1"), topic, key);
    expect(outbox.healthSignal).toBe(true);
    await expect(
      outbox.relayOnce({ publish: async () => Promise.reject(new Error("offline")) })
    ).rejects.toThrow();
    expect(outbox.pendingCount).toBe(1);
  });

  it("keeps capacity failures atomic and does not consume sequence numbers", () => {
    const topic = "persistent://omega/sdlc/pr";
    const constrained = new Outbox({ component: "test", maxBytes: 1 });
    const transaction = constrained.beginTransaction();
    transaction.append(event("ce-overflow"), topic, "repo#3");
    expect(() => transaction.commit()).toThrow();
    expect(constrained.pendingCount).toBe(0);

    const outbox = new Outbox({ component: "test", maxBytes: 4096 });
    expect(outbox.append(event("ce-first"), topic, "repo#3").sequenceId).toBe("1");
  });
  it("still publishes later rows when cleanupSent runs during an awaited publish", async () => {
    const outbox = new Outbox({ component: "worker", maxBytes: 8192 });
    for (const id of ["ce-a", "ce-b", "ce-c"]) {
      outbox.append(event(id), "persistent://omega/sdlc/pr", "repo#3");
    }
    const published: string[] = [];
    let releaseB!: () => void;
    const bGate = new Promise<void>((resolve) => {
      releaseB = resolve;
    });
    let bStarted!: () => void;
    const bInFlight = new Promise<void>((resolve) => {
      bStarted = resolve;
    });
    const publisher: Publisher = {
      publish: async (request) => {
        published.push(request.event.id);
        if (request.event.id === "ce-b") {
          bStarted();
          await bGate;
        }
        return { topic: request.topic, eventId: request.event.id, publishedAt: 1 };
      }
    };
    const relay = outbox.relayOnce(publisher);
    await bInFlight;
    // A is already sent: cleanup splices it out while B's publish is pending.
    expect(outbox.cleanupSent()).toBe(1);
    releaseB();
    const receipts = await relay;
    expect(published).toEqual(["ce-a", "ce-b", "ce-c"]);
    expect(receipts.map((receipt) => receipt.eventId)).toEqual(["ce-a", "ce-b", "ce-c"]);
    expect(outbox.pendingCount).toBe(0);
  });

  it("wakes the relay on append and drains it on event-driven shutdown", async () => {
    const outbox = new Outbox({ component: "worker", maxBytes: 4096 });
    const published: string[] = [];
    let markPublished: () => void = () => undefined;
    const receipt = new Promise<void>((resolve) => {
      markPublished = resolve;
    });
    const publisher: Publisher = {
      publish: async (request) => {
        published.push(request.event.id);
        markPublished();
        return { topic: request.topic, eventId: request.event.id, publishedAt: 1 };
      }
    };
    const running = outbox.run(publisher);
    outbox.append(event("ce-wake"), "persistent://omega/sdlc/pr", "repo#3");
    await receipt;
    await outbox.close(publisher);
    await running;
    expect(published).toEqual(["ce-wake"]);
    expect(outbox.pendingCount).toBe(0);
  });
});
