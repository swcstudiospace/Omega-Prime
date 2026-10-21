// GSD-Core inside the sandbox: `npx -y @opengsd/gsd-core@1.18.0 --claude --local` in the sandbox
// workdir (omp loads project .claude/commands as /gsd-* slash commands). First boot installs inside
// the sandbox; the resulting .claude tree is cached host-side and copied into later workspaces.
import { cpSync, existsSync, mkdirSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { STATE, VERSIONS } from "./config.ts";
import type { SandboxTier } from "./providers/types.ts";

const CACHE = join(STATE, "cache", `gsd-core-${VERSIONS.gsdCore}`);

export async function ensureGsd(tier: SandboxTier & { hostRoot?: string }) {
  // On a mounted tier the workdir IS the mounted host tree (the workspace bind hides hostRoot/work inside
  // the sandbox), so every host-side GSD path keys off the workspace when the tier has one.
  const hostWork = tier.workspaceHostPath ?? (tier.hostRoot ? join(tier.hostRoot, "work") : null);
  if (hostWork && existsSync(join(CACHE, ".claude", "commands", "gsd-plan-phase.md"))) {
    cpSync(join(CACHE, ".claude"), join(hostWork, ".claude"), { recursive: true });
    return { source: "cache", cache: CACHE };
  }
  const r = await tier.exec(["npx", "-y", `@opengsd/gsd-core@${VERSIONS.gsdCore}`, "--claude", "--local", "--no-legacy-cleanup"], { timeoutMs: 600_000, input: "" });
  if (r.code !== 0) throw new Error(`GSD-Core install failed in sandbox (exit ${r.code}): ${r.stderr.slice(-600)}`);
  if (hostWork) {
    if (!existsSync(join(hostWork, ".claude", "commands", "gsd-plan-phase.md"))) throw new Error("GSD-Core install produced no .claude/commands/gsd-plan-phase.md");
    mkdirSync(CACHE, { recursive: true });
    cpSync(join(hostWork, ".claude"), join(CACHE, ".claude"), { recursive: true });
  }
  return { source: "installed-in-sandbox", cache: hostWork ? CACHE : null };
}

export function gsdCommandsOnHost(tier: { hostRoot?: string; workspaceHostPath?: string | null }) {
  const work = tier.workspaceHostPath ?? (tier.hostRoot ? join(tier.hostRoot, "work") : null);
  if (!work) return null;
  const d = join(work, ".claude", "commands");
  return existsSync(d) ? readdirSync(d).filter((f) => f.startsWith("gsd-")).length : 0;
}
