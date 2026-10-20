// Paths and pinned versions. Everything lives under the repo's .state/ (gitignored).
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { homedir } from "node:os";

export const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
export const STATE = process.env.OMP_SANDBOX_STATE ?? join(ROOT, ".state");
export const HOME = homedir();
export const VERSIONS = { omp: "18.8.4", gsdCore: "1.18.0", agentosCore: "0.4.3", openshell: "0.1.4" } as const;
export const NODE24_BIN = process.env.OMP_SANDBOX_NODE24 ?? join(HOME, "tools", "node24", "bin");
export const OMP_BIN = process.env.OMP_SANDBOX_OMP ?? join(HOME, ".bun", "bin", "omp");
export const BUN_INSTALL = join(HOME, ".bun");
export const BUN_EXE = process.env.OMP_SANDBOX_BUN ?? "/usr/local/lib/pnpm/5/.pnpm/bun@1.4.2/node_modules/bun/bin/bun.exe";
export const SANDBOX_AGENT_BIN = join(ROOT, "node_modules", "@sandbox-agent", "cli-linux-x64", "bin", "sandbox-agent");
export const OPENSHELL_BIN = process.env.OMP_SANDBOX_OPENSHELL ?? join(HOME, ".local", "opt", "openshell", VERSIONS.openshell, "bin", "openshell");

/** Stage -> model routing (Ove's plan logins via omp). Fallback is used when the primary errors before any work. */
export const STAGE_MODELS = {
  plan: { primary: "openai-codex/gpt-6.1-sol", fallback: "zai/glm-5.3" },
  execute: { primary: "openai-codex/gpt-6-luna", fallback: "zai/glm-5.3" },
} as const;
export type Stage = keyof typeof STAGE_MODELS;
export const MAGIC = "orchestrate workflowz ultrathink";

export const aedt = (d = new Date()) =>
  d.toLocaleString("sv-SE", { timeZone: "Australia/Sydney", hour12: false }).replace(" ", "T") + " AEDT";
