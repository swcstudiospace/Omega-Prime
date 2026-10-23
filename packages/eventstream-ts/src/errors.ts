export const REDACTED = "[REDACTED]" as const;
export function isPlainRecord(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value) as unknown;
  return prototype === Object.prototype || prototype === null;
}

const SECRET_PATTERNS = [
  /sk-[A-Za-z0-9_.-]{8,}/g,
  /xai-[A-Za-z0-9]{8,}/g,
  /gh[pousr]_[A-Za-z0-9_]{8,}/g,
  // Fine-grained GitHub PATs; mirrors omega_prime.credentials.redact.
  /github_pat_[A-Za-z0-9_]{20,}/g,
  /AKIA[0-9A-Z]{16}/g
] as const;
const BEARER_PATTERN = /(Bearer )([^\s]+)/g;
const ASSIGNMENT_PATTERN = /(api[_-]?key|token|secret)(\s*[:=]\s*)(["']?)[^\s"',}]+/gi;

export function redactText(value: string, extra: readonly string[] = []): string {
  let result = value;
  for (const secret of [...extra].sort((left, right) => right.length - left.length)) {
    if (secret.length > 0) result = result.split(secret).join(REDACTED);
  }
  for (const pattern of SECRET_PATTERNS) result = result.replace(pattern, REDACTED);
  result = result.replace(BEARER_PATTERN, (_match, scheme: string) => `${scheme}${REDACTED}`);
  result = result.replace(
    ASSIGNMENT_PATTERN,
    (_match, name: string, separator: string, quote: string) =>
      `${name}${separator}${quote}${REDACTED}`
  );
  return result;
}

export function redactValue(value: unknown, extra: readonly string[] = []): unknown {
  if (typeof value === "string") return redactText(value, extra);
  if (Array.isArray(value)) return value.map((item) => redactValue(item, extra));
  if (isPlainRecord(value)) {
    const copy: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value)) copy[key] = redactValue(item, extra);
    return copy;
  }
  return value;
}

export class EventStreamError extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(redactText(message), options);
    this.name = new.target.name;
  }
}

export class ContractError extends EventStreamError {}
export class SchemaError extends ContractError {}
export class ConfigurationError extends EventStreamError {}
export class AuthenticationError extends EventStreamError {}
export class RetryableError extends EventStreamError {}
export class PermanentError extends EventStreamError {}
export class PulsarError extends EventStreamError {}

export function safeErrorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return redactText(message);
}

/** Fallback reason when a failure carries no usable message (Python `_safe_reason` parity). */
export const UNSPECIFIED_FAILURE_REASON = "unspecified failure";

/**
 * Redacted, trimmed, nonempty failure reason bounded to 256 characters.
 *
 * Mirrors omega_prime.eventstream.retry._safe_reason: blocked-signal payloads
 * and failure-history rows require a nonempty `reason`, so `new Error()` or
 * `PermanentError("")` must still produce a publishable dead letter.
 */
export function safeFailureReason(reason: unknown): string {
  return (safeErrorMessage(reason).trim() || UNSPECIFIED_FAILURE_REASON).slice(0, 256);
}
