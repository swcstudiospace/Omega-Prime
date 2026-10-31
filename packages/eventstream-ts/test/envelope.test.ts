import { describe, expect, it } from "vitest";
import { CloudEvent } from "../src/envelope.js";
import type { CloudEventInit } from "../src/envelope.js";
import { ContractError } from "../src/errors.js";
import { registerAuditEvent } from "../src/taxonomy.js";

const baseEventInit = (): CloudEventInit => ({
  specversion: "1.0",
  id: "ce-001",
  source: "github://org/repo",
  type: "omega.sdlc.pr.pushed",
  datacontenttype: "application/json",
  dataschema: "urn:omega:schema:omega.sdlc.pr.pushed:1",
  data: {
    repo: "Omega-Prime",
    number: 75,
    head_sha: "abc123",
    actor: "operator",
    before_sha: "000000"
  },
  traceparent: "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01",
  omegakey: "Omega-Prime#75",
  omegatask: "00046"
});
const baseEvent = () => new CloudEvent(baseEventInit());

describe("CloudEvent contract", () => {
  it("round-trips compact structured JSON and filterable properties", () => {
    const event = baseEvent();
    const bytes = event.toBytes();
    expect(bytes).toBeInstanceOf(Uint8Array);
    const decoded = CloudEvent.fromBytes(bytes);
    expect(decoded.data).toEqual(event.data);
    expect(decoded.toProperties()).toEqual({
      ce_id: "ce-001",
      ce_type: "omega.sdlc.pr.pushed",
      ce_source: "github://org/repo",
      traceparent:
        "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01",
      omegakey: "Omega-Prime#75",
      omegatask: "00046"
    });
    expect(Buffer.from(bytes).toString("utf8")).not.toContain("\n");
  });

  it("deeply freezes nested payload arrays and records", () => {
    const event = new CloudEvent({
      specversion: "1.0",
      id: "ce-dlq",
      source: "omega://retry",
      type: "omega.control.signal.blocked",
      datacontenttype: "application/json",
      dataschema: "urn:omega:schema:omega.control.signal.blocked:1",
      data: {
        kind: "dlq",
        origin_topic: "persistent://omega/task/state",
        aggregate_key: "task-7",
        source_ce_id: "ce-source",
        reason: "retry limit reached",
        history: [{ attempt: 1, reason: "temporary" }]
      }
    });
    const history = event.data.history;
    if (!Array.isArray(history)) throw new Error("validated DLQ history was not an array");
    const [failure] = history;
    if (failure === undefined || failure === null || typeof failure !== "object" || Array.isArray(failure)) {
      throw new Error("validated DLQ history entry was not an object");
    }
    expect(Object.isFrozen(event.data)).toBe(true);
    expect(Object.isFrozen(history)).toBe(true);
    expect(Object.isFrozen(failure)).toBe(true);
    expect(Reflect.set(failure, "reason", "changed")).toBe(false);
    expect(failure.reason).toBe("temporary");
  });

  const invalidCases: readonly (readonly [CloudEventInit, RegExp])[] = [
    [{ ...baseEventInit(), specversion: "0.3" }, /specversion/],
    [{ ...baseEventInit(), dataschema: "urn:omega:schema:omega.sdlc.pr.opened:1" }, /dataschema/],
    [{ ...baseEventInit(), type: "omega.deploy.succeeded" }, /registered/],
    [{ ...baseEventInit(), traceparent: "bad" }, /traceparent/],
    [{ ...baseEventInit(), traceparent: "00-00000000000000000000000000000000-0123456789abcdef-01" }, /traceparent/],
    [{ ...baseEventInit(), traceparent: "00-0123456789abcdef0123456789abcdef-0000000000000000-01" }, /traceparent/],
    [{ ...baseEventInit(), traceparent: "ff-0123456789abcdef0123456789abcdef-0123456789abcdef-01" }, /traceparent/]
  ];

  it.each(invalidCases)("rejects invalid envelope metadata safely", (input, reason) => {
    const construct = () => new CloudEvent(input);
    expect(construct).toThrow(ContractError);
    expect(construct).toThrow(reason);
    expect(construct).not.toThrow(/Bearer|private-token/);
  });


  it("redacts sensitive text before serialization and revalidates the payload", () => {
    const event = new CloudEvent({
      ...baseEventInit(),
      data: {
        repo: "Omega-Prime",
        number: 75,
        head_sha: "abc123",
        actor: "Bearer private-token",
        before_sha: "000000"
      }
    });
    const decoded = CloudEvent.fromBytes(event.toBytes());
    expect(decoded.data.actor).toBe("Bearer [REDACTED]");
  });

  it("supports an explicitly registered open audit member", () => {
    const type = "omega.audit.merge_refused";
    const registration = registerAuditEvent({ type });
    const event = new CloudEvent({
      specversion: "1.0",
      id: "audit-1",
      source: "omega://control-plane",
      type,
      datacontenttype: "application/json",
      dataschema: registration.schemaRef,
      data: {
        aggregate: "Omega-Prime#75",
        record_id: "audit-1",
        action: "merge.refused",
        actor: "merge-executor",
        occurred_at: "2026-10-23T06:40:00Z",
        details: { attempt: 2 }
      }
    });
    expect(CloudEvent.fromBytes(event.toBytes()).type).toBe(type);
  });
});
