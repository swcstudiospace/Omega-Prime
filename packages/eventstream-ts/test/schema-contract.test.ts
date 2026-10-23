import { readFile, readdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { Ajv2020 } from "ajv/dist/2020.js";
import * as formatsModule from "ajv-formats";
import { describe, expect, it } from "vitest";
import { CloudEvent } from "../src/envelope.js";
import type { CloudEventInit } from "../src/envelope.js";
import { redactValue } from "../src/errors.js";
import { CONCRETE_EVENT_COUNT, EVENT_TYPES, getEventType, registerAuditEvent, schemaLocation, validatePayload } from "../src/taxonomy.js";

type JsonObject = Record<string, unknown>;
type Definition = JsonObject & { $id?: string; examples?: unknown[]; "x-omega-invalid-examples"?: unknown[] };
type Bundle = JsonObject & {
  $schema: string;
  $id: string;
  $defs: Record<string, Definition>;
};
type CatalogEntry = { type: string; topic: string; keyRule: string };
type ContractFixtures = {
  catalog: CatalogEntry[];
  envelope: {
    structuredJson: string;
    redactedStructuredJson: string;
    properties: Record<string, string>;
    redaction: { input: unknown; extraSecrets: string[]; expected: unknown };
  };
};

const expectedFamilies = ["envelope", "sdlc.pr", "sdlc.review", "ci", "auth", "job", "sandbox", "task", "mem", "control", "hitl", "audit"] as const;
const expectedConcreteTypes = [
  "omega.sdlc.pr.opened", "omega.sdlc.pr.pushed", "omega.sdlc.pr.ready", "omega.sdlc.pr.converted_to_draft", "omega.sdlc.pr.merged", "omega.sdlc.pr.closed",
  "omega.sdlc.review.greptile_scored", "omega.sdlc.review.approved", "omega.sdlc.review.changes_requested", "omega.sdlc.review.commented",
  "omega.ci.run.started", "omega.ci.run.passed", "omega.ci.run.failed", "omega.ci.branch.passed", "omega.ci.branch.failed",
  "omega.auth.session.login_started", "omega.auth.session.login_succeeded", "omega.auth.session.login_failed", "omega.auth.session.expired", "omega.auth.session.logout",
  "omega.job.queued", "omega.job.started", "omega.job.succeeded", "omega.job.failed", "omega.job.cancelled",
  "omega.sandbox.created", "omega.sandbox.started", "omega.sandbox.ready", "omega.sandbox.unhealthy", "omega.sandbox.oom", "omega.sandbox.exited", "omega.sandbox.removed",
  "omega.task.state_changed", "omega.task.heartbeat", "omega.mem.hindsight.retained", "omega.mem.hindsight.teach_failed",
  "omega.control.signal.gate", "omega.control.signal.blocked", "omega.control.signal.review", "omega.control.action.merge_requested", "omega.control.action.merged",
  "omega.control.action.merge_refused", "omega.control.action.fix_requested", "omega.control.deadline.greptile_review_due", "omega.control.deadline.heartbeat_due",
  "omega.control.deadline.hitl_followup_due", "omega.hitl.asked", "omega.hitl.answered"
] as const;

function expectedDefinitions(family: string): string[] {
  if (family === "envelope") return ["CloudEvent"];
  if (family === "audit") return ["omega.audit.*"];
  return expectedConcreteTypes.filter((type) => familyForType(type) === family).slice().sort();
}

async function readFixtures(): Promise<ContractFixtures> {
  return JSON.parse(await readFile(new URL("./fixtures/contracts.json", import.meta.url), "utf8")) as ContractFixtures;
}

async function loadBundles(): Promise<Map<string, Bundle>> {
  const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
  const schemaDirectory = resolve(packageRoot, "../../omega_prime/eventstream/schemas");
  const files = (await readdir(schemaDirectory)).filter((name) => name.endsWith(".v1.json")).sort();
  expect(files).toEqual(expectedFamilies.map((family) => `${family}.v1.json`).sort());
  const bundles = new Map<string, Bundle>();
  for (const file of files) {
    const bundle = JSON.parse(await readFile(resolve(schemaDirectory, file), "utf8")) as Bundle;
    const family = file.slice(0, -".v1.json".length);
    expect(bundle.$schema).toBe("https://json-schema.org/draft/2020-12/schema");
    expect(bundle.$id).toBe(`urn:omega:schema:${family}:1`);
    expect(bundle.$defs).toBeDefined();
    expect(Object.keys(bundle.$defs).sort(), `${family} definitions`).toEqual(expectedDefinitions(family));
    expect(Object.keys(bundle.$defs).length, `${family} definitions are present`).toBeGreaterThan(0);
    bundles.set(family, bundle);
  }
  return bundles;
}

function familyForType(type: string): string {
  if (type.startsWith("omega.sdlc.pr.")) return "sdlc.pr";
  if (type.startsWith("omega.sdlc.review.")) return "sdlc.review";
  if (type.startsWith("omega.ci.")) return "ci";
  if (type.startsWith("omega.auth.")) return "auth";
  if (type.startsWith("omega.job.")) return "job";
  if (type.startsWith("omega.sandbox.")) return "sandbox";
  if (type.startsWith("omega.task.")) return "task";
  if (type.startsWith("omega.mem.")) return "mem";
  if (type.startsWith("omega.control.")) return "control";
  if (type.startsWith("omega.hitl.")) return "hitl";
  if (type.startsWith("omega.audit.")) return "audit";
  throw new Error(`unknown type family ${type}`);
}
function ajvDefinitionUri(bundleId: string | undefined, definition: string): string {
  if (bundleId === undefined) throw new Error(`schema bundle missing for ${definition}`);
  return `${bundleId}#/$defs/${definition.replaceAll("~", "~0").replaceAll("/", "~1")}`;
}


function handwrittenValidate(family: string, definition: string, value: unknown): void {
  if (family === "envelope") {
    if (value !== null && typeof value === "object" && "type" in value && typeof value.type === "string" &&
        /^omega\.audit\.[a-z][a-z0-9_]*$/.test(value.type)) {
      registerAuditEvent({ type: value.type });
    }
    new CloudEvent(value as CloudEventInit);
  } else if (family === "audit") {
    const member = definition.startsWith("omega.audit.") && definition !== "omega.audit.*"
      ? definition
      : "omega.audit.schema_contract";
    registerAuditEvent({ type: member });
    validatePayload(member, value);
  } else {
    validatePayload(definition, value);
  }
}


describe("Draft 2020-12 cross-language schema contract", () => {
  it("asserts exact concrete taxonomy membership and mappings independently", async () => {
    const fixtures = await readFixtures();
    expect(CONCRETE_EVENT_COUNT).toBe(48);
    expect(Object.keys(EVENT_TYPES).sort()).toEqual([...expectedConcreteTypes].sort());
    expect(fixtures.catalog.map((item) => item.type).sort()).toEqual([...expectedConcreteTypes].sort());
    for (const item of fixtures.catalog) {
      const entry = getEventType(item.type);
      expect(entry.concrete).toBe(true);
      expect(entry.topic).toBe(item.topic);
      expect(entry.keyRule).toBe(item.keyRule);
      expect(entry.schemaRef).toBe(`urn:omega:schema:${item.type}:1`);
    }
  });

  it("validates every definition and canonical fixture with Draft 2020-12 and handwritten validators", async () => {
    const bundles = await loadBundles();
    const ajv = new Ajv2020({ allErrors: true, strict: true, allowMatchingProperties: true, allowUnionTypes: true });
    ajv.addKeyword({ keyword: "x-omega-invalid-examples", schemaType: "array", valid: true });
    formatsModule.default.default(ajv);
    for (const bundle of bundles.values()) ajv.addSchema(bundle);
    const auditBundle = bundles.get("audit");
    for (const example of auditBundle?.$defs["omega.audit.*"]?.examples ?? []) {
      if (example !== null && typeof example === "object" && "type" in example && typeof example.type === "string") {
        registerAuditEvent({ type: example.type });
      }
    }


    for (const [family, bundle] of bundles) {
      for (const [definitionName, definition] of Object.entries(bundle.$defs)) {
        const validExamples = definition.examples;
        const invalidExamples = definition["x-omega-invalid-examples"];
        expect(validExamples?.length, `${family}/${definitionName} valid examples`).toBeGreaterThan(0);
        expect(invalidExamples?.length, `${family}/${definitionName} invalid examples`).toBeGreaterThan(0);
        const validate = ajv.getSchema(`${bundle.$id}#/$defs/${definitionName}`);
        if (validate === undefined) throw new Error(`schema definition did not compile: ${family}/${definitionName}`);
        for (const value of validExamples ?? []) {
          expect(validate(value), `${family}/${definitionName} valid fixture`).toBe(true);
          expect(() => handwrittenValidate(family, definitionName, value), `${family}/${definitionName} handwritten valid`).not.toThrow();
        }
        for (const value of invalidExamples ?? []) {
          expect(validate(value), `${family}/${definitionName} invalid fixture`).toBe(false);
          expect(() => handwrittenValidate(family, definitionName, value), `${family}/${definitionName} handwritten invalid`).toThrow();
        }
      }
    }
  });

  it("resolves each concrete type URN to its family JSON Pointer definition", async () => {
    const bundles = await loadBundles();
    const fixtures = await readFixtures();
    const ajv = new Ajv2020({ allErrors: true, strict: true, allowMatchingProperties: true, allowUnionTypes: true });
    ajv.addKeyword({ keyword: "x-omega-invalid-examples", schemaType: "array", valid: true });
    formatsModule.default.default(ajv);
    for (const bundle of bundles.values()) ajv.addSchema(bundle);
    expect(fixtures.catalog.map((item) => item.type).sort()).toEqual([...expectedConcreteTypes].sort());
    for (const item of fixtures.catalog) {
      const location = schemaLocation(item.type);
      const type = getEventType(item.type);
      expect(location.family).toBe(familyForType(item.type));
      expect(location.definition).toBe(item.type);
      expect(type.topic).toBe(item.topic);
      expect(type.keyRule).toBe(item.keyRule);
      const schemaRef = `urn:omega:schema:${item.type}:1`;
      expect(type.schemaRef).toBe(schemaRef);
      const bundle = bundles.get(location.family);
      expect(bundle?.$defs[location.definition], item.type).toBeDefined();
      const target = ajvDefinitionUri(bundle?.$id, location.definition);
      expect(ajv.getSchema(target), item.type).toBeDefined();
      expect(target).toBe(`${bundle?.$id}#/$defs/${item.type}`);
      const definition = bundle?.$defs[location.definition];
      if (definition === undefined) throw new Error(`schema definition missing: ${item.type}`);
      if (definition.$id === undefined) {
        ajv.addSchema({ $id: schemaRef, $ref: target });
      } else {
        expect(definition.$id).toBe(schemaRef);
      }
      const validate = ajv.getSchema(schemaRef);
      if (validate === undefined) throw new Error(`type schema URN did not resolve: ${item.type}`);
      for (const value of definition.examples ?? []) {
        expect(validate(value), `${item.type} URN valid fixture`).toBe(true);
        expect(() => handwrittenValidate(location.family, location.definition, value)).not.toThrow();
      }
      for (const value of definition["x-omega-invalid-examples"] ?? []) {
        expect(validate(value), `${item.type} URN invalid fixture`).toBe(false);
        expect(() => handwrittenValidate(location.family, location.definition, value)).toThrow();
      }
    }
    expect(bundles.get("audit")?.$defs["omega.audit.*"]).toBeDefined();
    const audit = registerAuditEvent({ type: "omega.audit.contract_resolution" });
    expect(audit.concrete).toBe(false);
    expect(schemaLocation(audit.type)).toEqual({ family: "audit", definition: "omega.audit.*" });
  });

  it("checks portable CloudEvent bytes, properties, and redaction expectations", async () => {
    const fixtures = await readFixtures();
    const document = JSON.parse(fixtures.envelope.structuredJson) as JsonObject;
    const event = new CloudEvent(document as unknown as CloudEventInit);
    expect(event.toProperties()).toEqual(fixtures.envelope.properties);
    expect(new TextDecoder().decode(event.toBytes())).toBe(fixtures.envelope.redactedStructuredJson);
    expect(redactValue(fixtures.envelope.redaction.input, fixtures.envelope.redaction.extraSecrets)).toEqual(fixtures.envelope.redaction.expected);
  });
});

