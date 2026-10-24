import { ContractError } from "./errors.js";

export const MAX_RETRY_DELAY_SECONDS = 600;
export const MAX_BUSINESS_ATTEMPTS = 5;

export function decorrelatedJitter(previousDelaySeconds: number, random: () => number = Math.random): number {
  if (!Number.isFinite(previousDelaySeconds) || previousDelaySeconds < 1) {
    throw new ContractError("previous retry delay must be at least one second");
  }
  const sample = random();
  if (!Number.isFinite(sample) || sample < 0 || sample > 1) {
    throw new ContractError("random source must return a value from zero to one");
  }
  return Math.min(MAX_RETRY_DELAY_SECONDS, 1 + sample * (3 * previousDelaySeconds - 1));
}

export interface CreationRetryOptions {
  readonly attempts?: number;
  readonly wait?: (seconds: number, signal?: AbortSignal) => Promise<void>;
  readonly signal?: AbortSignal;
  readonly random?: () => number;
  readonly shouldRetry?: (error: unknown, attempt: number) => boolean;
}

function waitForCreationBackoff(seconds: number, signal?: AbortSignal): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    let abortListener: (() => void) | undefined;
    const timer = setTimeout(() => {
      if (abortListener !== undefined) signal?.removeEventListener("abort", abortListener);
      resolve();
    }, seconds * 1000);
    if (signal !== undefined) {
      const onAbort = () => {
        clearTimeout(timer);
        signal.removeEventListener("abort", onAbort);
        reject(new DOMException("operation was aborted", "AbortError"));
      };
      abortListener = onAbort;
      if (signal.aborted) {
        onAbort();
        return;
      }
      signal.addEventListener("abort", onAbort, { once: true });
    }
  });
}

export async function withCreationBackoff<T>(
  operation: (attempt: number) => Promise<T>,
  options: CreationRetryOptions
): Promise<T> {
  const attempts = options.attempts ?? 5;
  if (!Number.isInteger(attempts) || attempts < 1 || attempts > MAX_BUSINESS_ATTEMPTS) {
    throw new ContractError("creation retry attempts must be between one and five");
  }
  let previous = 1;
  let lastError: unknown;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    if (options.signal?.aborted) throw new DOMException("operation was aborted", "AbortError");
    try {
      return await operation(attempt);
    } catch (error) {
      lastError = error;
      if (attempt === attempts || options.shouldRetry?.(error, attempt) === false) break;
      const delay = decorrelatedJitter(previous, options.random);
      previous = delay;
      await (options.wait ?? waitForCreationBackoff)(delay, options.signal);
    }
  }
  throw lastError;
}
