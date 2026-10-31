// GSD-Core inside the sandbox: `npx -y @opengsd/gsd-core@1.18.0 --claude --local` (omp loads project
// .claude/commands as /gsd-* slash commands). First boot installs inside the sandbox into a fresh, empty
// staging folder. Only a tier whose work folder is private (no mounted job worktree) fills the shared
// host-side cache: a mounted worktree is shared with the host and other jobs, so whatever they write
// into it during the install must never reach later workspaces. Later workspaces get a copy of the cache.
import { cpSync, existsSync, lstatSync, mkdirSync, mkdtempSync, readdirSync, renameSync, rmSync, statSync } from "node:fs";
import { basename, join } from "node:path";
import { STATE, VERSIONS } from "./config.ts";
import type { SandboxTier } from "./providers/types.ts";

const CACHE = join(STATE, "cache", `gsd-core-${VERSIONS.gsdCore}`);
const MARKER = join(".claude", "commands", "gsd-plan-phase.md");

const LOCK = `${CACHE}.lock`;
/** A fill lock older than this is a crashed filler's leftover; it is cleared so the next install can fill. */
export const STALE_LOCK_MS = 15 * 60_000;

/**
 * Fill CACHE from a trusted install tree, best effort. Wrappers finishing their first installs together
 * serialise on a cross-process lock (an atomic `mkdir`): the check-and-publish runs under it, so a complete
 * cache another wrapper published wins and is never replaced. A wrapper that finds the lock held skips the
 * fill; its own job already has the install, so `up` never fails because of the shared cache.
 * Returns true when this call published the cache.
 */
export function fillCache(claudeDir: string, now = Date.now()): boolean {
  mkdirSync(join(STATE, "cache"), { recursive: true });
  try { mkdirSync(LOCK); }
  catch (e: any) {
    if (e?.code !== "EEXIST") throw e;
    try { if (now - statSync(LOCK).mtimeMs > STALE_LOCK_MS) rmSync(LOCK, { recursive: true, force: true }); } catch { /* raced with its owner */ }
    return false;
  }
  try {
    if (existsSync(join(CACHE, MARKER))) return false;
    const tmp = mkdtempSync(`${CACHE}.tmp-`);
    try {
      cpSync(claudeDir, join(tmp, ".claude"), { recursive: true });
      rmSync(CACHE, { recursive: true, force: true });   // an incomplete leftover; a complete one returned above
      renameSync(tmp, CACHE);
      return true;
    } finally { rmSync(tmp, { recursive: true, force: true }); }
  } finally { rmSync(LOCK, { recursive: true, force: true }); }
}

/**
 * Host-side folder that is the sandbox workdir. On a mounted tier that is the mounted worktree, reached through the
 * tier's pinned descriptor (never the replaceable name). Throws if a mounted tier cannot name its pinned directory.
 */
export function hostWorkDir(tier: Pick<SandboxTier, "workspaceHostPath" | "workspaceHostDir"> & { hostRoot?: string }): string | null {
  if (tier.workspaceHostPath) {
    const pinned = tier.workspaceHostDir?.() ?? null;
    if (!pinned) throw new Error("mounted workspace is not pinned; refusing host-side writes by name");
    return pinned;
  }
  return tier.hostRoot ? join(tier.hostRoot, "work") : null;
}

/** Refuse a `.claude` (or its `commands`) the job replaced with a symlink. */
function refuseLinkedClaude(work: string) {
  for (const d of [join(work, ".claude"), join(work, ".claude", "commands")]) {
    try { if (lstatSync(d).isSymbolicLink()) throw new Error(`${d} is a symlink; refusing to write GSD commands through it`); }
    catch (e: any) { if (e?.code !== "ENOENT") throw e; }
  }
}

/** Copy a trusted `.claude` tree into `work`, refusing a linked `.claude`. */
function copyClaude(from: string, work: string) {
  refuseLinkedClaude(work);
  cpSync(from, join(work, ".claude"), { recursive: true });
}

/**
 * In-sandbox check-and-copy for a mounted worktree: the staging folder sits in a tree other jobs can edit,
 * so the host never reads it by name (it could be swapped for a link to another host checkout). Inside the
 * sandbox a swapped link can only reach what the sandbox already sees.
 */
export const IN_SANDBOX_COPY =
  `set -e; s="$1"; test -f "$s/${MARKER}"; test ! -L .claude; test ! -L .claude/commands; ` +
  `mkdir -p .claude; cp -R "$s/.claude/." .claude/`;

export async function ensureGsd(tier: SandboxTier & { hostRoot?: string }) {
  // On a mounted tier the workdir IS the mounted host tree (the workspace bind hides hostRoot/work inside
  // the sandbox), so every host-side GSD path keys off the pinned workspace when the tier has one.
  const hostWork = hostWorkDir(tier);
  if (hostWork && existsSync(join(CACHE, MARKER))) {
    copyClaude(join(CACHE, ".claude"), hostWork);
    return { source: "cache", cache: CACHE };
  }
  const argv = ["npx", "-y", `@opengsd/gsd-core@${VERSIONS.gsdCore}`, "--claude", "--local", "--no-legacy-cleanup"];
  if (!hostWork) {
    const r = await tier.exec(argv, { timeoutMs: 600_000, input: "" });
    if (r.code !== 0) throw new Error(`GSD-Core install failed in sandbox (exit ${r.code}): ${r.stderr.slice(-600)}`);
    return { source: "installed-in-sandbox", cache: null };
  }
  // The work folder is the only host-visible folder the sandbox writes, so stage there in a fresh folder
  // the job never had. A mounted worktree is shared (the host and other jobs can write into it while npx
  // runs), so its install serves this job only and is checked and copied inside the sandbox; a private
  // work folder is checked on the host and fills the shared cache.
  const shared = !!tier.workspaceHostPath;
  mkdirSync(hostWork, { recursive: true });
  const stage = mkdtempSync(join(hostWork, ".omp-gsd-install-"));
  try {
    const r = await tier.exec(argv, { cwd: `${tier.workdir}/${basename(stage)}`, timeoutMs: 600_000, input: "" });
    if (r.code !== 0) throw new Error(`GSD-Core install failed in sandbox (exit ${r.code}): ${r.stderr.slice(-600)}`);
    if (shared) {
      refuseLinkedClaude(hostWork);
      const c = await tier.exec(["sh", "-c", IN_SANDBOX_COPY, "sh", basename(stage)], { cwd: tier.workdir, timeoutMs: 60_000, input: "" });
      if (c.code !== 0) throw new Error(`GSD-Core install produced no ${MARKER} or .claude is a symlink (in-sandbox copy exit ${c.code}): ${c.stderr.slice(-600)}`);
      return { source: "installed-in-sandbox", cache: null };
    }
    if (!existsSync(join(stage, MARKER))) throw new Error("GSD-Core install produced no .claude/commands/gsd-plan-phase.md");
    const published = fillCache(join(stage, ".claude"));
    copyClaude(join(stage, ".claude"), hostWork);
    return { source: "installed-in-sandbox", cache: published || existsSync(join(CACHE, MARKER)) ? CACHE : null };
  } finally { rmSync(stage, { recursive: true, force: true }); }
}

export function gsdCommandsOnHost(tier: Pick<SandboxTier, "workspaceHostPath" | "workspaceHostDir"> & { hostRoot?: string }) {
  const work = hostWorkDir(tier);
  if (!work) return null;
  const d = join(work, ".claude", "commands");
  return existsSync(d) ? readdirSync(d).filter((f) => f.startsWith("gsd-")).length : 0;
}
