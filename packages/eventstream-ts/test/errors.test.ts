import { describe, expect, it } from "vitest";
import { redactText } from "../src/errors.js";

// Token-shaped values are assembled at runtime so no literal credential lives
// in the repository; each family mirrors omega_prime.credentials.redact.
const tokenVectors: readonly (readonly [string, string])[] = [
  ["openai-style", `sk-${"a1B2".repeat(4)}`],
  ["xai", `xai-${"Zy9x".repeat(4)}`],
  ["classic GitHub", `ghp_${"Q7w8".repeat(5)}`],
  ["fine-grained GitHub PAT", `github_pat_${"11ABCDEFG0".repeat(3)}_${"xYz1".repeat(10)}`],
  ["AWS access key", `AKIA${"ABCD2345".repeat(2)}`]
];

describe("secret redaction parity", () => {
  for (const [family, token] of tokenVectors) {
    it(`redacts ${family} tokens embedded in diagnostics`, () => {
      const redacted = redactText(`publish failed for ${token} at broker`);
      expect(redacted).not.toContain(token);
      expect(redacted).toContain("[REDACTED]");
    });
  }
});
