import { describe, expect, it } from "vitest";
import { CloudEvent, PublishRequest } from "../src/envelope.js";
import { MemoryEventBus, ManualClock } from "../src/memory.js";

const event = (id: string) =>
  new CloudEvent({
    specversion: "1.0",
    id,
    source: "test://memory",
    type: "omega.sdlc.pr.pushed",
    datacontenttype: "application/json",
    dataschema: "urn:omega:schema:omega.sdlc.pr.pushed:1",
    data: { repo: "repo", number: 7, head_sha: "head", actor: "test", before_sha: "base" },
    omegakey: "repo#7"
  });

const request = (id: string, deliverAfterSeconds?: number) =>
  new PublishRequest({
    event: event(id),
    topic: "persistent://omega/sdlc/pr",
    key: "repo#7",
    ...(deliverAfterSeconds === undefined ? {} : { deliverAfterSeconds })
  });

describe("memory EventBus", () => {
  it("publishes, delivers and acknowledges an immutable event", async () => {
    const bus = new MemoryEventBus();
    const subscriber = await bus.subscribe("persistent://omega/sdlc/pr", {
      subscriptionName: "gate",
      mode: "Shared"
    });
    await bus.publish(request("ce-1"));
    const delivery = await subscriber.receive();
    expect(delivery?.event.id).toBe("ce-1");
    expect(delivery?.metadata.key).toBe("repo#7");
    expect(delivery?.metadata.properties.ce_id).toBe("ce-1");
    await delivery?.ack();
    expect(subscriber.inFlightCount).toBe(0);
    await bus.close();
  });

  it("wakes delayed delivery only when the injected clock advances", async () => {
    const clock = new ManualClock(0);
    const bus = new MemoryEventBus({ clock });
    const subscriber = await bus.subscribe("persistent://omega/sdlc/pr", {
      subscriptionName: "delayed",
      mode: "Shared"
    });
    await bus.publish(request("ce-later", 5));
    const receive = subscriber.receive();
    expect(bus.delayedCount).toBe(1);
    clock.advanceBy(4_999);
    expect(bus.readyCount).toBe(0);
    clock.advanceBy(1);
    expect((await receive)?.event.id).toBe("ce-later");
    await bus.close();
  });

  it("redelivers unacked work and preserves per-key order under back-pressure", async () => {
    const bus = new MemoryEventBus();
    const subscriber = await bus.subscribe("persistent://omega/sdlc/pr", {
      subscriptionName: "ordered",
      mode: "Key_Shared",
      maxInFlight: 1
    });
    await bus.publish(request("ce-1"));
    await bus.publish(request("ce-2"));
    const first = await subscriber.receive();
    expect(first?.event.id).toBe("ce-1");
    const redeliveryPending = subscriber.receive();
    await first?.nack();
    const redelivery = await redeliveryPending;
    expect(redelivery?.event.id).toBe("ce-1");
    expect(redelivery?.metadata.redelivered).toBe(true);
    const secondPending = subscriber.receive();
    await redelivery?.ack();
    const second = await secondPending;
    expect(second?.event.id).toBe("ce-2");
    await second?.ack();
    await bus.close();
  });

  it("supports reader seek and abortable pending receive without polling", async () => {
    const bus = new MemoryEventBus();
    await bus.publish(request("ce-1"));
    await bus.publish(request("ce-2"));
    const reader = await bus.read("persistent://omega/sdlc/pr");
    await reader.seek(1);
    expect((await reader.receive())?.event.id).toBe("ce-2");
    const controller = new AbortController();
    const pending = reader.receive(controller.signal);
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    await reader.close();
    await bus.close();
  });
  it("closing one receiver releases only its own holds on a shared subscription", async () => {
    const bus = new MemoryEventBus();
    const options = { subscriptionName: "shared-gate", mode: "Shared" as const };
    const busy = await bus.subscribe("persistent://omega/sdlc/pr", options);
    const idle = await bus.subscribe("persistent://omega/sdlc/pr", options);
    const third = await bus.subscribe("persistent://omega/sdlc/pr", options);
    await bus.publish(request("ce-1"));
    const active = await busy.receive();
    expect(active?.event.id).toBe("ce-1");
    await idle.close();
    const controller = new AbortController();
    const pending = third.receive(controller.signal);
    await Promise.resolve();
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(busy.inFlightCount).toBe(1);
    await active?.ack();
    expect(busy.inFlightCount).toBe(0);
    await bus.close();
  });

  it("rejects stale settlement after the receiver's hold was released", async () => {
    const bus = new MemoryEventBus();
    const options = { subscriptionName: "stale-gate", mode: "Shared" as const };
    const first = await bus.subscribe("persistent://omega/sdlc/pr", options);
    const second = await bus.subscribe("persistent://omega/sdlc/pr", options);
    await bus.publish(request("ce-1"));
    const stale = await first.receive();
    await first.close();
    const redelivered = await second.receive();
    expect(redelivered?.event.id).toBe("ce-1");
    expect(redelivered?.metadata.redelivered).toBe(true);
    await expect(stale?.ack()).rejects.toThrow(/stale settlement/);
    expect(second.inFlightCount).toBe(1);
    await redelivered?.ack();
    await bus.close();
  });
  it("keeps the event's CloudEvents properties authoritative over caller properties", async () => {
    const bus = new MemoryEventBus();
    const subscriber = await bus.subscribe("persistent://omega/sdlc/pr", { subscriptionName: "props", mode: "Shared" });
    await bus.publish(
      new PublishRequest({
        event: event("ce-real"),
        topic: "persistent://omega/sdlc/pr",
        key: "repo#7",
        properties: { ce_id: "ce-stale", omega_note: "kept" }
      })
    );
    const delivery = await subscriber.receive();
    expect(delivery?.metadata.properties.ce_id).toBe("ce-real");
    expect(delivery?.metadata.properties.omega_note).toBe("kept");
    await delivery?.ack();
    await bus.close();
  });
});
