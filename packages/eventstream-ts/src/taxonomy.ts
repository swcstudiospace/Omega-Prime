import { ContractError, isPlainRecord, SchemaError } from "./errors.js";

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };
export type KeyRule =
  | "repo#pr"
  | "repo@branch"
  | "host:tool"
  | "job_id"
  | "sandbox_id"
  | "task_id"
  | "bank"
  | "aggregate"
  | "blocked_branch";

export interface EventType {
  readonly type: string;
  readonly topic: string;
  readonly keyRule: KeyRule;
  readonly schemaRef: string;
  readonly family: string;
  readonly concrete: boolean;
}

export interface AuditEventRegistration {
  readonly type: string;
  readonly topic?: string;
  readonly keyRule?: string;
  readonly schemaRef?: string;
}

export interface SchemaLocation {
  readonly family: string;
  readonly definition: string;
}

export type FieldRule =
  | { readonly kind: "string"; readonly minLength?: number }
  | { readonly kind: "integer"; readonly minimum?: number; readonly maximum?: number; readonly exclusiveMinimum?: number }
  | { readonly kind: "boolean" }
  | { readonly kind: "literal"; readonly value: string | number | boolean }
  | { readonly kind: "enum"; readonly values: readonly (string | number | boolean)[] }
  | { readonly kind: "date-time" }
  | { readonly kind: "array"; readonly items: ObjectShape; readonly minItems?: number }
  | { readonly kind: "details" };

export interface ObjectShape {
  readonly fields: Readonly<Record<string, FieldRule>>;
  readonly optional: readonly string[];
}

const STR: FieldRule = Object.freeze({ kind: "string", minLength: 1 });
const INT_POSITIVE: FieldRule = Object.freeze({ kind: "integer", minimum: 1 });
const INT_NONNEGATIVE: FieldRule = Object.freeze({ kind: "integer", minimum: 0 });
const DATETIME: FieldRule = Object.freeze({ kind: "date-time" });
export function isRfc3339DateTime(value: string): boolean {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(Z|[+-](\d{2}):(\d{2}))$/.exec(value);
  if (match === null) return false;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6]);
  const offsetHour = Number(match[8] ?? 0);
  const offsetMinute = Number(match[9] ?? 0);
  const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const daysInMonth = month === 2 ? (leapYear ? 29 : 28) : [4, 6, 9, 11].includes(month) ? 30 : 31;
  return year > 0 &&
    month >= 1 && month <= 12 &&
    day >= 1 && day <= daysInMonth &&
    hour <= 23 && minute <= 59 && second <= 59 &&
    offsetHour <= 23 && offsetMinute <= 59 &&
    Number.isFinite(Date.parse(value));
}

const commonPr = Object.freeze({ repo: STR, number: INT_POSITIVE, head_sha: STR, actor: STR });
const commonRun = Object.freeze({ repo: STR, number: INT_POSITIVE, run_id: STR, head_sha: STR, actor: STR });
const commonBranch = Object.freeze({ repo: STR, branch: STR, run_id: STR, head_sha: STR, actor: STR });
const commonAuth = Object.freeze({ host: STR, tool: STR, session_id: STR, actor: STR });
const commonJob = Object.freeze({ job_id: STR, actor: STR });
const commonSandbox = Object.freeze({ sandbox_id: STR, actor: STR });
const commonDeadline = Object.freeze({ aggregate: STR, deadline_id: STR, due_at: DATETIME });
const retryRecord: ObjectShape = Object.freeze({
  fields: Object.freeze({ attempt: INT_POSITIVE, reason: STR }),
  optional: Object.freeze([])
});

function shape(fields: Readonly<Record<string, FieldRule>>, optional: readonly string[] = []): ObjectShape {
  return Object.freeze({ fields: Object.freeze({ ...fields }), optional: Object.freeze([...optional]) });
}

const concreteRows: readonly (readonly [string, string, KeyRule])[] = [
  ["omega.sdlc.pr.opened", "sdlc/pr", "repo#pr"],
  ["omega.sdlc.pr.pushed", "sdlc/pr", "repo#pr"],
  ["omega.sdlc.pr.ready", "sdlc/pr", "repo#pr"],
  ["omega.sdlc.pr.converted_to_draft", "sdlc/pr", "repo#pr"],
  ["omega.sdlc.pr.merged", "sdlc/pr", "repo#pr"],
  ["omega.sdlc.pr.closed", "sdlc/pr", "repo#pr"],
  ["omega.sdlc.review.greptile_scored", "sdlc/review", "repo#pr"],
  ["omega.sdlc.review.approved", "sdlc/review", "repo#pr"],
  ["omega.sdlc.review.changes_requested", "sdlc/review", "repo#pr"],
  ["omega.sdlc.review.commented", "sdlc/review", "repo#pr"],
  ["omega.ci.run.started", "sdlc/ci", "repo#pr"],
  ["omega.ci.run.passed", "sdlc/ci", "repo#pr"],
  ["omega.ci.run.failed", "sdlc/ci", "repo#pr"],
  ["omega.ci.branch.passed", "sdlc/ci", "repo@branch"],
  ["omega.ci.branch.failed", "sdlc/ci", "repo@branch"],
  ["omega.auth.session.login_started", "auth/session", "host:tool"],
  ["omega.auth.session.login_succeeded", "auth/session", "host:tool"],
  ["omega.auth.session.login_failed", "auth/session", "host:tool"],
  ["omega.auth.session.expired", "auth/session", "host:tool"],
  ["omega.auth.session.logout", "auth/session", "host:tool"],
  ["omega.job.queued", "job/lifecycle", "job_id"],
  ["omega.job.started", "job/lifecycle", "job_id"],
  ["omega.job.succeeded", "job/lifecycle", "job_id"],
  ["omega.job.failed", "job/lifecycle", "job_id"],
  ["omega.job.cancelled", "job/lifecycle", "job_id"],
  ["omega.sandbox.created", "sandbox/lifecycle", "sandbox_id"],
  ["omega.sandbox.started", "sandbox/lifecycle", "sandbox_id"],
  ["omega.sandbox.ready", "sandbox/lifecycle", "sandbox_id"],
  ["omega.sandbox.unhealthy", "sandbox/lifecycle", "sandbox_id"],
  ["omega.sandbox.oom", "sandbox/lifecycle", "sandbox_id"],
  ["omega.sandbox.exited", "sandbox/lifecycle", "sandbox_id"],
  ["omega.sandbox.removed", "sandbox/lifecycle", "sandbox_id"],
  ["omega.task.state_changed", "task/state", "task_id"],
  ["omega.task.heartbeat", "task/heartbeat", "task_id"],
  ["omega.mem.hindsight.retained", "memory/hindsight", "bank"],
  ["omega.mem.hindsight.teach_failed", "memory/hindsight", "bank"],
  ["omega.control.signal.gate", "control/signal", "repo#pr"],
  ["omega.control.signal.blocked", "control/signal", "blocked_branch"],
  ["omega.control.signal.review", "control/signal", "repo#pr"],
  ["omega.control.action.merge_requested", "control/action", "repo#pr"],
  ["omega.control.action.merged", "control/action", "repo#pr"],
  ["omega.control.action.merge_refused", "control/action", "repo#pr"],
  ["omega.control.action.fix_requested", "control/action", "repo#pr"],
  ["omega.control.deadline.greptile_review_due", "control/deadline", "aggregate"],
  ["omega.control.deadline.heartbeat_due", "control/deadline", "aggregate"],
  ["omega.control.deadline.hitl_followup_due", "control/deadline", "aggregate"],
  ["omega.hitl.asked", "control/hitl", "task_id"],
  ["omega.hitl.answered", "control/hitl", "task_id"]
];

const concreteTypes: Record<string, EventType> = Object.create(null) as Record<string, EventType>;
for (const [type, topic, keyRule] of concreteRows) {
  const family = typeFamily(type);
  concreteTypes[type] = Object.freeze({
    type,
    topic: `persistent://omega/${topic}`,
    keyRule,
    schemaRef: `urn:omega:schema:${type}:1`,
    family,
    concrete: true
  });
}
export const EVENT_TYPES: Readonly<Record<string, EventType>> = Object.freeze(concreteTypes);
const auditTypes: Record<string, EventType> = Object.create(null) as Record<string, EventType>;
const auditPattern = /^omega\.audit\.[a-z0-9]+(?:[._-][a-z0-9]+)*$/;

function typeFamily(type: string): string {
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
  throw new SchemaError("event type has no schema family");
}

export function registerAuditEvent(registration: AuditEventRegistration): EventType {
  const { type } = registration;
  if (!auditPattern.test(type) || type === "omega.audit.*") {
    throw new ContractError("audit type must be a concrete omega.audit member");
  }
  if (registration.topic !== undefined && registration.topic !== "persistent://omega/audit/log") {
    throw new ContractError("audit registration topic is not authorized");
  }
  if (registration.keyRule !== undefined && registration.keyRule !== "aggregate") {
    throw new ContractError("audit registration key rule is not authorized");
  }
  const schemaRef = `urn:omega:schema:${type}:1`;
  if (registration.schemaRef !== undefined && registration.schemaRef !== schemaRef) {
    throw new ContractError("audit registration schema reference is invalid");
  }
  const existing = auditTypes[type];
  if (existing !== undefined) return existing;
  const eventType: EventType = Object.freeze({
    type,
    topic: "persistent://omega/audit/log",
    keyRule: "aggregate",
    schemaRef,
    family: "audit",
    concrete: false
  });
  auditTypes[type] = eventType;
  return eventType;
}

export function getEventType(type: string): EventType {
  const concrete = EVENT_TYPES[type];
  if (concrete !== undefined) return concrete;
  const audit = auditTypes[type];
  if (audit !== undefined) return audit;
  throw new SchemaError("event type is not registered");
}

export function schemaLocation(type: string): SchemaLocation {
  const eventType = getEventType(type);
  return Object.freeze({
    family: eventType.family,
    definition: eventType.family === "audit" ? "omega.audit.*" : eventType.type
  });
}


const stringEnum = (...values: string[]): FieldRule => Object.freeze({ kind: "enum", values });
const stringLiteral = (value: string): FieldRule => Object.freeze({ kind: "literal", value });
const boolLiteral = (value: boolean): FieldRule => Object.freeze({ kind: "literal", value });
const prBase = { ...commonPr };
const payloadShapes: Record<string, ObjectShape> = Object.create(null) as Record<string, ObjectShape>;

function define(type: string, fields: Readonly<Record<string, FieldRule>>, optional: readonly string[] = []): void {
  payloadShapes[type] = shape(fields, optional);
}

define("omega.sdlc.pr.opened", { ...prBase, title: STR, base_ref: STR });
define("omega.sdlc.pr.pushed", { ...prBase, before_sha: STR });
define("omega.sdlc.pr.ready", { ...prBase, draft: boolLiteral(false) });
define("omega.sdlc.pr.converted_to_draft", { ...prBase, draft: boolLiteral(true) });
define("omega.sdlc.pr.merged", { ...prBase, merged_by: STR, merge_sha: STR });
define("omega.sdlc.pr.closed", { ...prBase, merged: { kind: "boolean" }, closed_reason: STR });
define("omega.sdlc.review.greptile_scored", { ...prBase, score: { kind: "integer", minimum: 0, maximum: 100 } });
define("omega.sdlc.review.approved", { ...prBase, reviewer: STR });
define("omega.sdlc.review.changes_requested", { ...prBase, reviewer: STR, summary: STR });
define("omega.sdlc.review.commented", { ...prBase, reviewer: STR, comment_id: STR });
define("omega.ci.run.started", { ...commonRun, status: stringEnum("queued", "in_progress") });
define("omega.ci.run.passed", { ...commonRun, conclusion: stringLiteral("success") });
define("omega.ci.run.failed", { ...commonRun, conclusion: stringEnum("failure", "cancelled", "timed_out") });
define("omega.ci.branch.passed", { ...commonBranch, conclusion: stringLiteral("success") });
define("omega.ci.branch.failed", { ...commonBranch, conclusion: stringEnum("failure", "cancelled", "timed_out") });
define("omega.auth.session.login_started", { ...commonAuth, action: stringLiteral("login") });
define("omega.auth.session.login_succeeded", { ...commonAuth, action: stringLiteral("login"), method: STR });
define("omega.auth.session.login_failed", { ...commonAuth, action: stringLiteral("login"), reason: STR });
define("omega.auth.session.expired", { ...commonAuth, action: stringLiteral("expired"), reason: STR });
define("omega.auth.session.logout", { ...commonAuth, action: stringLiteral("logout") });
define("omega.job.queued", { ...commonJob, state: stringLiteral("queued"), queue: STR });
define("omega.job.started", { ...commonJob, state: stringLiteral("started"), worker: STR });
define("omega.job.succeeded", { ...commonJob, state: stringLiteral("succeeded"), result_ref: STR });
define("omega.job.failed", { ...commonJob, state: stringLiteral("failed"), reason: STR });
define("omega.job.cancelled", { ...commonJob, state: stringLiteral("cancelled"), reason: STR });
define("omega.sandbox.created", { ...commonSandbox, state: stringLiteral("created"), image: STR });
define("omega.sandbox.started", { ...commonSandbox, state: stringLiteral("started"), container_id: STR });
define("omega.sandbox.ready", { ...commonSandbox, state: stringLiteral("ready"), address: STR });
define("omega.sandbox.unhealthy", { ...commonSandbox, state: stringLiteral("unhealthy"), reason: STR });
define("omega.sandbox.oom", { ...commonSandbox, state: stringLiteral("oom"), memory_limit_bytes: { kind: "integer", exclusiveMinimum: 0 } });
define("omega.sandbox.exited", { ...commonSandbox, state: stringLiteral("exited"), exit_code: { kind: "integer", minimum: 0, maximum: 255 } });
define("omega.sandbox.removed", { ...commonSandbox, state: stringLiteral("removed"), reason: STR });
define("omega.task.state_changed", { task_id: STR, previous_state: STR, state: stringEnum("queued", "running", "blocked", "completed", "failed", "cancelled"), sequence: INT_NONNEGATIVE, actor: STR });
define("omega.task.heartbeat", { task_id: STR, sequence: INT_NONNEGATIVE, occurred_at: DATETIME });
define("omega.mem.hindsight.retained", { bank: STR, record_id: STR, text: STR, source: STR });
define("omega.mem.hindsight.teach_failed", { bank: STR, record_id: STR, reason: STR });
define("omega.control.signal.gate", { ...prBase, decision: stringEnum("allow", "block"), reason: STR });
define("omega.control.signal.review", { ...prBase, review_state: stringEnum("pending", "approved", "changes_requested"), review_id: STR });
define("omega.control.action.merge_requested", { ...prBase, requested_by: STR });
define("omega.control.action.merged", { ...prBase, merged_by: STR, merge_sha: STR });
define("omega.control.action.merge_refused", { ...prBase, reason: STR });
define("omega.control.action.fix_requested", { ...prBase, requested_by: STR, reason: STR });
define("omega.control.deadline.greptile_review_due", { ...commonDeadline, head_sha: STR });
define("omega.control.deadline.heartbeat_due", { ...commonDeadline, sequence: INT_NONNEGATIVE });
define("omega.control.deadline.hitl_followup_due", { ...commonDeadline, task_id: STR, sequence: INT_NONNEGATIVE });
define("omega.hitl.asked", { task_id: STR, request_id: STR, question: STR });
define("omega.hitl.answered", { task_id: STR, request_id: STR, answer: STR, answered_by: STR });


function assertJsonValue(value: unknown, path: string): asserts value is JsonValue {
  if (value === null || typeof value === "string" || typeof value === "boolean") return;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new ContractError(`${path} must be finite`);
    return;
  }
  if (Array.isArray(value)) {
    for (const [index, item] of value.entries()) assertJsonValue(item, `${path}[${index}]`);
    return;
  }
  if (isPlainRecord(value)) {
    for (const [key, item] of Object.entries(value)) assertJsonValue(item, `${path}.${key}`);
    return;
  }
  throw new ContractError(`${path} is not JSON-compatible`);
}

function validateRule(value: unknown, rule: FieldRule, path: string): void {
  switch (rule.kind) {
    case "string":
      if (typeof value !== "string" || value.length < (rule.minLength ?? 0)) {
        throw new ContractError(`${path} must be a nonempty string`);
      }
      return;
    case "integer":
      if (typeof value !== "number" || !Number.isInteger(value)) {
        throw new ContractError(`${path} must be an integer`);
      }
      if (rule.minimum !== undefined && value < rule.minimum) throw new ContractError(`${path} is below minimum`);
      if (rule.maximum !== undefined && value > rule.maximum) throw new ContractError(`${path} is above maximum`);
      if (rule.exclusiveMinimum !== undefined && value <= rule.exclusiveMinimum) throw new ContractError(`${path} is below exclusive minimum`);
      return;
    case "boolean":
      if (typeof value !== "boolean") throw new ContractError(`${path} must be boolean`);
      return;
    case "literal":
      if (value !== rule.value) throw new ContractError(`${path} has an invalid literal value`);
      return;
    case "enum":
      if (!rule.values.includes(value as string | number | boolean)) throw new ContractError(`${path} has an invalid value`);
      return;
    case "date-time":
      if (typeof value !== "string" || !isRfc3339DateTime(value)) {
        throw new ContractError(`${path} must be an RFC3339 timestamp`);
      }
      return;
    case "array":
      if (!Array.isArray(value) || value.length < (rule.minItems ?? 0)) throw new ContractError(`${path} must be an array`);
      for (const [index, item] of value.entries()) validateShape(item, rule.items, `${path}[${index}]`);
      return;
    case "details":
      if (!isPlainRecord(value)) throw new ContractError(`${path} must be an object`);
      for (const [key, item] of Object.entries(value)) {
        if (typeof item !== "string" && typeof item !== "boolean" && !(typeof item === "number" && Number.isInteger(item))) {
          throw new ContractError(`${path}.${key} must be a string, integer, or boolean`);
        }
      }
      return;
  }
}

function validateShape(value: unknown, schema: ObjectShape, path: string): Record<string, JsonValue> {
  if (!isPlainRecord(value)) throw new ContractError(`${path} must be an object`);
  const optional = new Set(schema.optional);
  for (const required of Object.keys(schema.fields)) {
    if (!optional.has(required) && !Object.hasOwn(value, required)) throw new ContractError(`${path}.${required} is required`);
  }
  for (const key of Object.keys(value)) {
    const rule = schema.fields[key];
    if (rule === undefined) throw new ContractError(`${path}.${key} is not allowed`);
    validateRule(value[key], rule, `${path}.${key}`);
  }
  for (const [key, item] of Object.entries(value)) assertJsonValue(item, `${path}.${key}`);
  return deepFreezeJson(value);
}

function deepFreezeJson(value: unknown): Record<string, JsonValue> {
  const clone = (input: unknown): JsonValue => {
    if (input === null || typeof input === "string" || typeof input === "boolean" || typeof input === "number") return input;
    if (Array.isArray(input)) return Object.freeze(input.map(clone)) as JsonValue[];
    if (isPlainRecord(input)) {
      const copy: Record<string, JsonValue> = Object.create(null) as Record<string, JsonValue>;
      for (const [key, item] of Object.entries(input)) copy[key] = clone(item);
      return Object.freeze(copy);
    }
    throw new ContractError("payload is not JSON-compatible");
  };
  return clone(value) as Record<string, JsonValue>;
}

const blockedPrShape = shape({
  kind: stringLiteral("pr"),
  ...commonPr,
  reason: STR
});
const blockedDlqShape = shape({
  kind: stringLiteral("dlq"),
  origin_topic: STR,
  aggregate_key: STR,
  source_ce_id: STR,
  reason: STR,
  history: { kind: "array", items: retryRecord, minItems: 1 }
});
const auditShape = shape(
  { aggregate: STR, record_id: STR, action: STR, actor: STR, occurred_at: DATETIME, details: { kind: "details" } },
  ["details"]
);

export function validatePayload(type: string, payload: unknown): Record<string, JsonValue> {
  const eventType = getEventType(type);
  if (eventType.family === "audit") return validateShape(payload, auditShape, type);
  if (type === "omega.control.signal.blocked") {
    if (!isPlainRecord(payload)) throw new ContractError("blocked payload must be an object");
    if (payload.kind === "pr") return validateShape(payload, blockedPrShape, type);
    if (payload.kind === "dlq") return validateShape(payload, blockedDlqShape, type);
    throw new ContractError("blocked payload kind must be pr or dlq");
  }
  const schema = payloadShapes[type];
  if (schema === undefined) throw new SchemaError("event payload schema is unavailable");
  return validateShape(payload, schema, type);
}

export function deriveKey(type: string, payload: unknown): string {
  const valid = validatePayload(type, payload);
  const eventType = getEventType(type);
  switch (eventType.keyRule) {
    case "repo#pr":
      return `${String(valid.repo)}#${String(valid.number)}`;
    case "repo@branch":
      return `${String(valid.repo)}@${String(valid.branch)}`;
    case "host:tool":
      return `${String(valid.host)}:${String(valid.tool)}`;
    case "job_id":
    case "sandbox_id":
    case "task_id":
    case "bank":
    case "aggregate":
      return String(valid[eventType.keyRule]);
    case "blocked_branch":
      if (valid.kind === "pr") return `${String(valid.repo)}#${String(valid.number)}`;
      return `dlq:${JSON.stringify([valid.origin_topic, valid.aggregate_key])}`;
  }
}

export function eventTypesForTopic(topic: string): readonly EventType[] {
  return Object.freeze(Object.values(EVENT_TYPES).filter((entry) => entry.topic === topic));
}

export function payloadShapesFor(type: string): readonly ObjectShape[] {
  if (type === "omega.audit.*") return Object.freeze([auditShape]);
  const eventType = getEventType(type);
  if (eventType.family === "audit") return Object.freeze([auditShape]);
  if (type === "omega.control.signal.blocked") {
    return Object.freeze([blockedPrShape, blockedDlqShape]);
  }
  const result = payloadShapes[type];
  if (result === undefined) throw new SchemaError("event payload schema is unavailable");
  return Object.freeze([result]);
}

export function payloadShapeFor(type: string): ObjectShape {
  const shapes = payloadShapesFor(type);
  if (shapes.length !== 1) throw new SchemaError("event payload has multiple discriminated branches");
  const result = shapes[0];
  if (result === undefined) throw new SchemaError("event payload schema is unavailable");
  return result;
}

export const CONCRETE_EVENT_COUNT = concreteRows.length;
