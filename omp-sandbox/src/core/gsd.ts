// GSD-Core inside the sandbox: `npx -y @opengsd/gsd-core@1.18.0 --claude --local` (omp loads project
// .claude/commands as /gsd-* slash commands). First boot installs inside the sandbox into a fresh, empty
// staging folder, so the host-side cache holds only the installer's output, never a job's own .claude
// files; later workspaces get a copy of that cache.
import { cpSync, existsSync, mkdirSync, mkdtempSync, readdirSync, renameSync, rmSync } from "node:fs";
import { basename, join } from "node:path";
import { STATE, VERSIONS } from "./config.ts";
import type { SandboxTier } from "./providers/types.ts";

const CACHE = join(STATE, "cache", `gsd-core-${VERSIONS.gsdCore}`);
const MARKER = join(".claude", "commands", "gsd-plan-phase.md");

/** Fill CACHE from a trusted install tree: build beside it, then rename into place (a racing fill just loses). */
function fillCache(claudeDir: string) {
  mkdirSync(join(STATE, "cache"), { recursive: true });
  const tmp = mkdtempSync(`${CACHE}.tmp-`);
  try {
    cpSync(claudeDir, join(tmp, ".claude"), { recursive: true });
    if (!existsSync(join(CACHE, MARKER))) { rmSync(CACHE, { recursive: true, force: true }); renameSync(tmp, CACHE); }
  } finally { rmSync(tmp, { recursive: true, force: true }); }
}

export async function ensureGsd(tier: SandboxTier & { hostRoot?: string }) {
  // On a mounted tier the workdir IS the mounted host tree (the workspace bind hides hostRoot/work inside
  // the sandbox), so every host-side GSD path keys off the workspace when the tier has one.
  const hostWork = tier.workspaceHostPath ?? (tier.hostRoot ? join(tier.hostRoot, "work") : null);
  if (hostWork && existsSync(join(CACHE, MARKER))) {
    cpSync(join(CACHE, ".claude"), join(hostWork, ".claude"), { recursive: true });
    return { source: "cache", cache: CACHE };
  }
  const argv = ["npx", "-y", `@opengsd/gsd-core@${VERSIONS.gsdCore}`, "--claude", "--local", "--no-legacy-cleanup"];
  if (!hostWork) {
    const r = await tier.exec(argv, { timeoutMs: 600_000, input: "" });
    if (r.code !== 0) throw new Error(`GSD-Core install failed in sandbox (exit ${r.code}): ${r.stderr.slice(-600)}`);
    return { source: "installed-in-sandbox", cache: null };
  }
  // The workspace is the only host-visible folder the sandbox writes, so stage there in a fresh folder
  // the job never had: the cache then holds exactly what the installer wrote.
  mkdirSync(hostWork, { recursive: true });
  const stage = mkdtempSync(join(hostWork, ".omp-gsd-install-"));
  try {
    const r = await tier.exec(argv, { cwd: `${tier.workdir}/${basename(stage)}`, timeoutMs: 600_000, input: "" });
    if (r.code !== 0) throw new Error(`GSD-Core install failed in sandbox (exit ${r.code}): ${r.stderr.slice(-600)}`);
    if (!existsSync(join(stage, MARKER))) throw new Error("GSD-Core install produced no .claude/commands/gsd-plan-phase.md");
    fillCache(join(stage, ".claude"));
    cpSync(join(stage, ".claude"), join(hostWork, ".claude"), { recursive: true });
  } finally { rmSync(stage, { recursive: true, force: true }); }
  return { source: "installed-in-sandbox", cache: CACHE };
}

export function gsdCommandsOnHost(tier: { hostRoot?: string; workspaceHostPath?: string | null }) {
  const work = tier.workspaceHostPath ?? (tier.hostRoot ? join(tier.hostRoot, "work") : null);
  if (!work) return null;
  const d = join(work, ".claude", "commands");
  return existsSync(d) ? readdirSync(d).filter((f) => f.startsWith("gsd-")).length : 0;
}
