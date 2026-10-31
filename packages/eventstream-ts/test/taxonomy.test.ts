import { readFile } from "node:fs/promises";
import { describe, expect, it } from "vitest";
import {
  EVENT_TYPES,
  deriveKey,
  getEventType,
  registerAuditEvent,
  validatePayload
} from "../src/taxonomy.js";

type CatalogFixture = {
  catalog: { type: string; topic: string; keyRule: string }[];
  keyCases: { type: string; payload: Record<string, unknown>; expected: string }[];
};

async function fixture(): Promise<CatalogFixture> {
  return JSON.parse(
    await readFile(new URL("./fixtures/contracts.json", import.meta.url), "utf8")
  ) as CatalogFixture;
}

describe("event taxonomy", () => {
  it("matches the independent complete 48-type catalog", async () => {
    const expected = await fixture();
    expect(Object.keys(EVENT_TYPES).sort()).toEqual(expected.catalog.map((entry) => entry.type).sort());
    for (const entry of expected.catalog) {
      expect(EVENT_TYPES[entry.type]).toMatchObject({
        type: entry.type,
        topic: entry.topic,
        keyRule: entry.keyRule,
        schemaRef: `urn:omega:schema:${entry.type}:1`
      });
    }
  });

  it("derives representative keys for every routing rule", async () => {
    const expected = await fixture();
    for (const testCase of expected.keyCases) {
      expect(deriveKey(testCase.type, testCase.payload)).toBe(testCase.expected);
    }
  });

  it("requires explicit validated registration for dynamic audit members", () => {
    expect(() => getEventType("omega.audit.merge_refused")).toThrow();
    const registered = registerAuditEvent({ type: "omega.audit.merge_refused" });
    expect(registered).toMatchObject({
      type: "omega.audit.merge_refused",
      topic: "persistent://omega/audit/log",
      keyRule: "aggregate",
      schemaRef: "urn:omega:schema:omega.audit.merge_refused:1"
    });
    expect(() => registerAuditEvent({ type: "omega.audit.*" })).toThrow();
    expect(() => registerAuditEvent({ type: "omega.audit.Merge" })).toThrow();
  });

  it("validates blocked PR and DLQ branches without cross-branch fields", () => {
    expect(
      deriveKey("omega.control.signal.blocked", {
        kind: "pr",
        repo: "repo",
        number: 2,
        head_sha: "sha",
        actor: "gate",
        reason: "blocked"
      })
    ).toBe("repo#2");
    const dlq = {
      kind: "dlq",
      origin_topic: "persistent://omega/task/state",
      aggregate_key: "task-Ω",
      source_ce_id: "ce-1",
      reason: "failed",
      history: [{ attempt: 1, reason: "unavailable" }]
    };
    expect(deriveKey("omega.control.signal.blocked", dlq)).toBe(
      'dlq:["persistent://omega/task/state","task-Ω"]'
    );
    expect(() =>
      validatePayload("omega.control.signal.blocked", { ...dlq, repo: "fake" })
    ).toThrow();
    expect(() =>
      validatePayload("omega.control.signal.blocked", {
        ...dlq,
        kind: "pr",
        repo: "repo",
        number: 2,
        head_sha: "sha",
        actor: "gate"
      })
    ).toThrow();
  });
});
