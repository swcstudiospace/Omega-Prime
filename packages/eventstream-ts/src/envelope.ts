import { ContractError, isPlainRecord, redactValue } from "./errors.js";
import { deriveKey, getEventType, isRfc3339DateTime, validatePayload, type JsonValue } from "./taxonomy.js";

export type CloudEventExtension = string | number | boolean;

export interface CloudEventInit {
  readonly specversion: string;
  readonly id: string;
  readonly source: string;
  readonly type: string;
  readonly subject?: string | null;
  readonly time?: string | Date | null;
  readonly datacontenttype: string;
  readonly dataschema: string;
  readonly data: unknown;
  readonly [extension: string]: unknown;
}

export interface CloudEventBytesOptions {
  readonly extraSecrets?: readonly string[];
}

export interface PublishRequestInit {
  readonly event: CloudEvent;
  readonly topic: string;
  readonly key: string;
  readonly sequenceId?: string | bigint;
  readonly producerName?: string;
  readonly deliverAfterSeconds?: number;
  readonly properties?: Readonly<Record<string, string>>;
}

const CORE_NAMES: Readonly<Record<string, true>> = Object.freeze({
  specversion: true,
  id: true,
  source: true,
  type: true,
  subject: true,
  time: true,
  datacontenttype: true,
  dataschema: true,
  data: true
});
const EXTENSION_NAME = /^[a-z][a-z0-9]{0,19}$/;
const TRACEPARENT = /^(?!ff-)([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$/;
const URI_REFERENCE_INVALID = /[^\x21-\x7e]|[<>"{}|\\^`]/;


function assertUriReference(value: string): void {
  if (value.length === 0 || URI_REFERENCE_INVALID.test(value)) {
    throw new ContractError("source must be a URI-reference");
  }
  if (/%(?![0-9a-fA-F]{2})/.test(value)) throw new ContractError("source URI escape is invalid");
  const colon = value.indexOf(":");
  const slash = value.indexOf("/");
  if (colon >= 0 && (slash < 0 || colon < slash)) {
    const scheme = value.slice(0, colon);
    if (!/^[A-Za-z][A-Za-z0-9+.-]*$/.test(scheme)) {
      throw new ContractError("source must be a URI-reference");
    }
  }
}

function timestampString(value: string | Date): string {
  if (value instanceof Date) {
    if (!Number.isFinite(value.getTime())) throw new ContractError("time must be RFC3339");
    return value.toISOString();
  }
  if (!isRfc3339DateTime(value)) {
    throw new ContractError("time must be RFC3339");
  }
  return value;
}

function jsonCopy(value: unknown): JsonValue {
  if (value === null || typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new ContractError("event data must contain finite numbers");
    return value;
  }
  if (Array.isArray(value)) return Object.freeze(value.map(jsonCopy)) as JsonValue[];
  if (isPlainRecord(value)) {
    const copy: Record<string, JsonValue> = Object.create(null) as Record<string, JsonValue>;
    for (const [key, item] of Object.entries(value)) copy[key] = jsonCopy(item);
    return Object.freeze(copy);
  }
  throw new ContractError("event data is not JSON-compatible");
}

export class CloudEvent {
  readonly specversion = "1.0" as const;
  readonly id: string;
  readonly source: string;
  readonly type: string;
  readonly subject?: string;
  readonly time?: string;
  readonly datacontenttype = "application/json" as const;
  readonly dataschema: string;
  readonly data: Readonly<Record<string, JsonValue>>;
  readonly extensions: Readonly<Record<string, CloudEventExtension>>;

  constructor(input: CloudEventInit) {
    if (input.specversion !== "1.0") throw new ContractError("specversion must be 1.0");
    for (const name of ["id", "source", "type", "dataschema"] as const) {
      if (typeof input[name] !== "string" || input[name].trim().length === 0) {
        throw new ContractError(`${name} must be nonempty`);
      }
    }
    if (input.datacontenttype !== "application/json") {
      throw new ContractError("datacontenttype must be application/json");
    }
    assertUriReference(input.source);
    const eventType = getEventType(input.type);
    if (input.dataschema !== eventType.schemaRef) {
      throw new ContractError("dataschema does not match the registered event type");
    }
    if (input.subject !== undefined && (typeof input.subject !== "string" || input.subject.trim().length === 0)) {
      throw new ContractError("subject must be nonempty when present");
    }
    if (input.time === null) throw new ContractError("time cannot be null when supplied");
    if (input.time !== undefined) timestampString(input.time);
    if (!isPlainRecord(input.data)) throw new ContractError("event data must be a JSON object");
    const payload = validateEventPayload(input.type, input.data);
    const extensions: Record<string, CloudEventExtension> = Object.create(null) as Record<string, CloudEventExtension>;
    for (const [name, value] of Object.entries(input)) {
      if (Object.hasOwn(CORE_NAMES, name)) continue;
      if (!EXTENSION_NAME.test(name)) throw new ContractError("invalid CloudEvents extension name");
      if (typeof value !== "string" && typeof value !== "number" && typeof value !== "boolean") {
        throw new ContractError("CloudEvents extension values must be JSON scalars");
      }
      if (typeof value === "number" && !Number.isFinite(value)) {
        throw new ContractError("CloudEvents extension numbers must be finite");
      }
      extensions[name] = value;
    }
    if (extensions.traceparent !== undefined) {
      const match = typeof extensions.traceparent === "string" ? TRACEPARENT.exec(extensions.traceparent) : null;
      if (match === null || match[2] === "00000000000000000000000000000000" || match[3] === "0000000000000000") {
        throw new ContractError("traceparent is invalid");
      }
    }
    if (extensions.omegakey !== undefined && extensions.omegakey !== deriveKey(input.type, payload)) {
      throw new ContractError("omegakey does not match event identity");
    }
    this.id = input.id;
    this.source = input.source;
    this.type = input.type;
    if (input.subject !== undefined) this.subject = input.subject;
    if (input.time !== undefined) this.time = timestampString(input.time);
    this.dataschema = input.dataschema;
    this.data = jsonCopy(payload) as Readonly<Record<string, JsonValue>>;
    this.extensions = Object.freeze(extensions);
    Object.freeze(this);
  }

  toObject(): Record<string, unknown> {
    const result: Record<string, unknown> = {
      specversion: this.specversion,
      id: this.id,
      source: this.source,
      type: this.type,
      datacontenttype: this.datacontenttype,
      dataschema: this.dataschema,
      data: thawJson(this.data)
    };
    if (this.subject !== undefined) result.subject = this.subject;
    if (this.time !== undefined) result.time = this.time;
    Object.assign(result, this.extensions);
    return result;
  }

  toBytes(options: CloudEventBytesOptions = {}): Uint8Array {
    const originalKey = deriveKey(this.type, this.data);
    const original = this.toObject();
    const sanitizedUnknown = redactValue(original, options.extraSecrets ?? []);
    if (!isPlainRecord(sanitizedUnknown) || !isPlainRecord(sanitizedUnknown.data)) {
      throw new ContractError("redacted event must remain a JSON object");
    }
    for (const name of ["specversion", "id", "source", "type", "datacontenttype", "dataschema", "subject", "time"] as const) {
      if (Object.hasOwn(original, name) !== Object.hasOwn(sanitizedUnknown, name) || original[name] !== sanitizedUnknown[name]) {
        throw new ContractError("redaction changed envelope identity metadata");
      }
    }
    const sanitizedPayload = validateEventPayload(this.type, sanitizedUnknown.data);
    if (deriveKey(this.type, sanitizedPayload) !== originalKey) {
      throw new ContractError("redaction changed event routing identity");
    }
    if (typeof sanitizedUnknown.omegakey === "string" && sanitizedUnknown.omegakey !== originalKey) {
      throw new ContractError("redaction changed the event routing key");
    }
    const normalized: Record<string, unknown> = {
      specversion: "1.0",
      id: this.id,
      source: this.source,
      type: this.type,
      datacontenttype: "application/json",
      dataschema: this.dataschema,
      data: sanitizedPayload
    };
    if (this.subject !== undefined) normalized.subject = this.subject;
    if (this.time !== undefined) normalized.time = this.time;
    for (const [name, value] of Object.entries(sanitizedUnknown)) {
      if (!Object.hasOwn(CORE_NAMES, name)) normalized[name] = value;
    }
    new CloudEvent(normalized as CloudEventInit);
    return new TextEncoder().encode(JSON.stringify(normalized));
  }

  toProperties(): Readonly<Record<string, string>> {
    const properties: Record<string, string> = {
      ce_id: this.id,
      ce_type: this.type,
      ce_source: this.source
    };
    if (this.subject !== undefined) properties.ce_subject = this.subject;
    if (this.time !== undefined) properties.ce_time = this.time;
    for (const name of ["traceparent", "omegakey", "omegatask", "omegagraph", "omegaheadsha"] as const) {
      const value = this.extensions[name];
      if (value !== undefined) properties[name] = String(value);
    }
    return Object.freeze(properties);
  }

  static fromBytes(bytes: Uint8Array): CloudEvent {
    if (!(bytes instanceof Uint8Array)) throw new TypeError("structured CloudEvent must be bytes");
    let parsed: unknown;
    try {
      parsed = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) as unknown;
    } catch {
      throw new ContractError("structured CloudEvent is not valid UTF-8 JSON");
    }
    if (!isPlainRecord(parsed)) throw new ContractError("structured CloudEvent must be an object");
    return new CloudEvent(parsed as CloudEventInit);
  }
}

function validateEventPayload(type: string, payload: unknown): Record<string, JsonValue> {
  try {
    return validatePayload(type, payload);
  } catch (error) {
    if (error instanceof ContractError) throw error;
    throw new ContractError("event payload violates its registered schema", { cause: error });
  }
}

function thawJson(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(thawJson);
  if (isPlainRecord(value)) {
    const copy: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value)) copy[key] = thawJson(item);
    return copy;
  }
  return value;
}

export class PublishRequest {
  readonly event: CloudEvent;
  readonly topic: string;
  readonly key: string;
  readonly sequenceId?: string | bigint;
  readonly producerName?: string;
  readonly deliverAfterSeconds?: number;
  readonly properties: Readonly<Record<string, string>>;

  constructor(input: PublishRequestInit) {
    if (!/^persistent:\/\/omega\/[a-z0-9_-]+\/[a-z0-9_.-]+$/.test(input.topic)) {
      throw new ContractError("publish topic is invalid");
    }
    const registeredTopic = getEventType(input.event.type).topic;
    const systemRetryTopic = /^persistent:\/\/omega\/system\/(?:retry|dlq)\.[^/]+$/.test(input.topic);
    if (input.topic !== registeredTopic && !systemRetryTopic) {
      throw new ContractError("publish topic does not match the event type");
    }
    if (input.key !== deriveKey(input.event.type, input.event.data)) {
      throw new ContractError("publish key does not match event identity");
    }
    if (input.sequenceId !== undefined) {
      const value = input.sequenceId;
      if (typeof value === "bigint") {
        if (value < 0n) throw new ContractError("sequence id must be nonnegative");
      } else if (!/^(0|[1-9][0-9]*)$/.test(value)) {
        throw new ContractError("sequence id must be a nonnegative decimal string");
      }
      this.sequenceId = value;
    }
    if (input.producerName !== undefined) {
      if (input.producerName.trim().length === 0) throw new ContractError("producer name must be nonempty");
      this.producerName = input.producerName;
    }
    if (input.deliverAfterSeconds !== undefined) {
      if (!Number.isFinite(input.deliverAfterSeconds) || input.deliverAfterSeconds < 0 || input.deliverAfterSeconds > 600) {
        throw new ContractError("deliver-after must be between 0 and 600 seconds");
      }
      this.deliverAfterSeconds = input.deliverAfterSeconds;
    }
    const sanitizedProperties = redactValue(input.properties ?? {});
    if (!isPlainRecord(sanitizedProperties)) throw new ContractError("publish properties are invalid");
    const properties: Record<string, string> = {};
    for (const [name, value] of Object.entries(sanitizedProperties)) {
      if (typeof value !== "string") throw new ContractError("publish properties must be strings");
      properties[name] = value;
    }
    this.properties = Object.freeze(properties);
    this.event = input.event;
    this.topic = input.topic;
    this.key = input.key;
    Object.freeze(this);
  }
}

export interface PublishReceipt {
  readonly topic: string;
  readonly eventId: string;
  readonly publishedAt: number;
  readonly sequenceId?: string;
}


