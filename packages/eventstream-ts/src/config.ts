import { constants } from "node:fs";
import { open, type FileHandle } from "node:fs/promises";
import { isIP } from "node:net";
import { ConfigurationError, redactValue } from "./errors.js";

export type SecretFileName = "tokenFile" | "tlsCaFile" | "encryptionKeyFile" | "privateKeyFile";

export interface EventStreamConfigOptions {
  readonly serviceUrl?: string;
  readonly adminUrl?: string;
  readonly tokenFile?: string;
  readonly tlsCaFile?: string;
  /** PEM public key used by producers for native Pulsar end-to-end encryption. */
  readonly encryptionKeyFile?: string;
  /** PEM private key used by consumers/readers to decrypt auth-family payloads. */
  readonly privateKeyFile?: string;
  readonly outboxMaxBytes?: number;
  readonly maxInFlight?: number;
  readonly receiverQueueSize?: number;
}

const SECRET_ENV_NAME = /(?:API[_-]?KEY|ACCESS[_-]?KEY|TOKEN|SECRET|PASSWORD|PRIVATE[_-]?KEY|ENCRYPTION[_-]?KEY)$/i;
const DEFAULT_SERVICE_URL = "pulsar://127.0.0.1:26650";
const DEFAULT_ADMIN_URL = "http://127.0.0.1:28080";
const FILE_FIELDS: Readonly<Record<SecretFileName, string>> = Object.freeze({
  tokenFile: "OMEGA_EVENTSTREAM_TOKEN_FILE",
  tlsCaFile: "OMEGA_EVENTSTREAM_TLS_CA_FILE",
  encryptionKeyFile: "OMEGA_EVENTSTREAM_ENCRYPTION_KEY_FILE",
  privateKeyFile: "OMEGA_EVENTSTREAM_PRIVATE_KEY_FILE"
});

function parseUrl(value: string, kind: "service" | "admin"): URL {
  try {
    const parsed = new URL(value);
    const allowed = kind === "service" ? ["pulsar:", "pulsar+ssl:"] : ["http:", "https:"];
    if (!allowed.includes(parsed.protocol) || parsed.username || parsed.password || parsed.hash) {
      throw new Error("invalid URL");
    }
    return parsed;
  } catch {
    throw new ConfigurationError(`${kind} URL is invalid`);
  }
}

function isLoopback(hostname: string): boolean {
  const host = hostname.toLowerCase().replace(/^\[|\]$/g, "");
  return host === "localhost" || host === "::1" || (isIP(host) === 4 && host.startsWith("127."));
}

function optionalPath(value: string | undefined): string | undefined {
  if (value === undefined) return undefined;
  if (value.trim().length === 0 || value.includes("\0")) throw new ConfigurationError("secret file path is invalid");
  return value;
}

export class EventStreamConfig {
  readonly serviceUrl: string;
  readonly adminUrl: string;
  readonly tokenFile: string | undefined;
  readonly tlsCaFile: string | undefined;
  readonly encryptionKeyFile: string | undefined;
  readonly privateKeyFile: string | undefined;
  readonly outboxMaxBytes: number;
  readonly maxInFlight: number;
  readonly receiverQueueSize: number;

  constructor(options: EventStreamConfigOptions = {}) {
    const serviceUrl = options.serviceUrl ?? DEFAULT_SERVICE_URL;
    const adminUrl = options.adminUrl ?? DEFAULT_ADMIN_URL;
    const service = parseUrl(serviceUrl, "service");
    const admin = parseUrl(adminUrl, "admin");
    this.tokenFile = optionalPath(options.tokenFile);
    this.tlsCaFile = optionalPath(options.tlsCaFile);
    this.encryptionKeyFile = optionalPath(options.encryptionKeyFile);
    this.privateKeyFile = optionalPath(options.privateKeyFile);
    const local = isLoopback(service.hostname) && isLoopback(admin.hostname);
    if (!local && (service.protocol !== "pulsar+ssl:" || admin.protocol !== "https:" || this.tokenFile === undefined || this.tlsCaFile === undefined)) {
      throw new ConfigurationError("non-loopback event-stream endpoints require TLS, a token file, and a CA file");
    }
    this.serviceUrl = service.toString().replace(/\/$/, "");
    this.adminUrl = admin.toString().replace(/\/$/, "");
    this.outboxMaxBytes = options.outboxMaxBytes ?? 64 * 1024 * 1024;
    this.maxInFlight = options.maxInFlight ?? 16;
    this.receiverQueueSize = options.receiverQueueSize ?? 1000;
    for (const [name, value] of [
      ["outboxMaxBytes", this.outboxMaxBytes],
      ["maxInFlight", this.maxInFlight],
      ["receiverQueueSize", this.receiverQueueSize]
    ] as const) {
      if (!Number.isSafeInteger(value) || value < 1) throw new ConfigurationError(`${name} must be a positive integer`);
    }
    Object.freeze(this);
  }

  static fromEnv(environment: Readonly<Record<string, string | undefined>> = process.env): EventStreamConfig {
    for (const [name, value] of Object.entries(environment)) {
      // Only eventstream-owned names can block setup; unrelated process secrets
      // (GITHUB_TOKEN, provider API_KEY, etc.) must not prevent configuration.
      if (
        value !== undefined &&
        name.startsWith("OMEGA_EVENTSTREAM_") &&
        SECRET_ENV_NAME.test(name) &&
        !name.endsWith("_FILE")
      ) {
        throw new ConfigurationError("secret environment values are forbidden; configure a *_FILE path");
      }
    }
    const serviceUrl = environment.OMEGA_EVENTSTREAM_URL;
    const adminUrl = environment.OMEGA_EVENTSTREAM_ADMIN_URL;
    const tokenFile = environment[FILE_FIELDS.tokenFile];
    const tlsCaFile = environment[FILE_FIELDS.tlsCaFile];
    const encryptionKeyFile = environment[FILE_FIELDS.encryptionKeyFile];
    const privateKeyFile = environment[FILE_FIELDS.privateKeyFile];
    const outboxMaxBytes = environment.OMEGA_EVENTSTREAM_OUTBOX_MAX_BYTES;
    const maxInFlight = environment.OMEGA_EVENTSTREAM_MAX_IN_FLIGHT;
    const receiverQueueSize = environment.OMEGA_EVENTSTREAM_RECEIVER_QUEUE_SIZE;
    return new EventStreamConfig({
      ...(serviceUrl === undefined ? {} : { serviceUrl }),
      ...(adminUrl === undefined ? {} : { adminUrl }),
      ...(tokenFile === undefined ? {} : { tokenFile }),
      ...(tlsCaFile === undefined ? {} : { tlsCaFile }),
      ...(encryptionKeyFile === undefined ? {} : { encryptionKeyFile }),
      ...(privateKeyFile === undefined ? {} : { privateKeyFile }),
      ...(outboxMaxBytes === undefined ? {} : { outboxMaxBytes: Number(outboxMaxBytes) }),
      ...(maxInFlight === undefined ? {} : { maxInFlight: Number(maxInFlight) }),
      ...(receiverQueueSize === undefined ? {} : { receiverQueueSize: Number(receiverQueueSize) })
    });
  }

  async readSecret(name: SecretFileName): Promise<string> {
    const path = this[name];
    if (path === undefined) throw new ConfigurationError(`${name} file is required`);
    let handle: FileHandle | undefined;
    try {
      handle = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
      const metadata = await handle.stat();
      if (!metadata.isFile() || (metadata.mode & 0o7777) !== 0o600) {
        throw new ConfigurationError("secret file must be a regular file with mode 0600");
      }
      const contents = await handle.readFile({ encoding: "utf8" });
      const secret = contents.replace(/\r?\n$/, "");
      if (secret.length === 0) throw new ConfigurationError("secret file is empty");
      return secret;
    } catch (error) {
      if (error instanceof ConfigurationError) throw error;
      throw new ConfigurationError("secret file could not be read");
    } finally {
      await handle?.close();
    }
  }

  redact(value: unknown, extraSecrets: readonly string[] = []): unknown {
    return redactValue(value, extraSecrets);
  }

  toString(): string {
    return `[EventStreamConfig serviceUrl=${this.serviceUrl} tokenFileConfigured=${this.tokenFile !== undefined} tlsCaConfigured=${this.tlsCaFile !== undefined} encryptionKeyConfigured=${this.encryptionKeyFile !== undefined} privateKeyConfigured=${this.privateKeyFile !== undefined}]`;
  }
}
