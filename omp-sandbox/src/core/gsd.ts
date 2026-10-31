// GSD-Core inside the sandbox: `npx -y @opengsd/gsd-core@1.18.0 --claude --local` (omp loads project
// .claude/commands as /gsd-* slash commands). First boot installs inside the sandbox into a fresh, empty
// staging folder. Only a tier whose work folder is private (no mounted job worktree) fills the shared
// host-side cache: a mounted worktree is shared with the host and other jobs, so whatever they write
// into it during the install must never reach later workspaces. Later workspaces get a copy of the cache.
import { randomBytes } from "node:crypto";
import { cpSync, existsSync, linkSync, lstatSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from "node:fs";
import { basename, join } from "node:path";
import { STATE, VERSIONS } from "./config.ts";
import type { SandboxTier } from "./providers/types.ts";

const CACHE = join(STATE, "cache", `gsd-core-${VERSIONS.gsdCore}`);
const MARKER = join(".claude", "commands", "gsd-plan-phase.md");

const LOCK = `${CACHE}.lock`;
/** A fill lock older than this is a crashed filler's leftover; it is cleared so the next install can fill. */
export const STALE_LOCK_MS = 15 * 60_000;

function readToken(path: string): string | null {
  try { return readFileSync(path, "utf8"); } catch { return null; }
}

/**
 * Clear a crashed filler's lock without ever removing a live one. The stale lock is moved aside with an atomic
 * rename (only one clearer wins) and its token compared with the one read before the move. If a newer, live lock
 * was moved instead (it replaced the stale one in between), it is linked back without replacing any other lock.
 * `beforeMove` is a test seam for that race. Returns true when a stale lock was cleared.
 */
export function clearStaleLock(now = Date.now(), beforeMove: () => void = () => {}): boolean {
  let seen: string | null;
  try {
    if (now - statSync(LOCK).mtimeMs <= STALE_LOCK_MS) return false;
    seen = readToken(LOCK);
  } catch { return false; }
  beforeMove();
  const aside = `${LOCK}.stale-${process.pid}-${randomBytes(4).toString("hex")}`;
  try { renameSync(LOCK, aside); } catch { return false; }       // another clearer won
  if (readToken(aside) === seen) { rmSync(aside, { recursive: true, force: true }); return true; }
  try { linkSync(aside, LOCK); } catch { /* yet another filler holds the name; never replace it */ }
  rmSync(aside, { force: true });
  return false;
}

/**
 * Fill CACHE from a trusted install tree, best effort: it never throws, so `up` never fails because of the
 * shared cache. Wrappers finishing their first installs together serialise on a cross-process lock file
 * (`<cache>.lock`, created with O_EXCL and holding a random token). The check-and-publish runs under it, so a
 * complete cache another wrapper published wins and is never replaced. A wrapper that finds the lock held skips
 * the fill (its own job already has the install) and clears the lock only if it is stale (see `clearStaleLock`).
 * A lock is released only by the holder whose token it still carries.
 * Returns true when this call published the cache.
 */
export function fillCache(claudeDir: string, now = Date.now()): boolean {
  const token = `${process.pid}-${randomBytes(8).toString("hex")}`;
  try {
    mkdirSync(join(STATE, "cache"), { recursive: true });
    writeFileSync(LOCK, token, { flag: "wx", mode: 0o600 });
  } catch (e: any) {
    if (e?.code === "EEXIST") clearStaleLock(now);
    return false;
  }
  try {
    if (existsSync(join(CACHE, MARKER))) return false;
    const tmp = mkdtempSync(`${CACHE}.tmp-`);
    try {
      cpSync(claudeDir, join(tmp, ".claude"), { recursive: true });
      if (existsSync(join(CACHE, MARKER))) return false;          // published meanwhile: it wins
      rmSync(CACHE, { recursive: true, force: true });           // an incomplete leftover
      renameSync(tmp, CACHE);
      return true;
    } finally { rmSync(tmp, { recursive: true, force: true }); }
  } catch {
    return false;
  } finally {
    if (readToken(LOCK) === token) rmSync(LOCK, { force: true });
  }
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

function isLink(p: string): boolean {
  try { return lstatSync(p).isSymbolicLink(); } catch { return false; }
}

/** Refuse any of `dirs` the job replaced with a symlink. */
function refuseLinked(dirs: string[], what: string) {
  for (const d of dirs) if (isLink(d)) throw new Error(`${d} is a symlink; refusing to write ${what} through it`);
}

/** Refuse a `.claude` (or its `commands`) the job replaced with a symlink. */
function refuseLinkedClaude(work: string) {
  refuseLinked([join(work, ".claude"), join(work, ".claude", "commands")], "GSD commands");
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

/** Convert GSD agents to .omp/agents. With a host work folder (the private folder, or a mounted worktree reached
 *  through its pinned descriptor) the host converts, refusing linked agent folders and files just as copyClaude
 *  refuses a linked `.claude`. Without one, the conversion runs inside the sandbox and must produce agents. */
async function convertAgents(tier: SandboxTier, hostWork: string | null): Promise<number> {
  if (hostWork) return writeOmpAgents(hostWork);
  const c = await tier.exec(["node", "-e", IN_SANDBOX_CONVERT], { cwd: tier.workdir, timeoutMs: 60_000, input: "" });
  if (c.code !== 0) throw new Error(`GSD agent conversion failed in sandbox: ${c.stderr.slice(-400)}`);
  const ompAgents = Number(c.stdout.trim().split("\n").pop()) || 0;
  if (!ompAgents) throw new Error("GSD agent conversion produced no .omp/agents (omp would report Unknown agent gsd-*)");
  return ompAgents;
}

export async function ensureGsd(tier: SandboxTier & { hostRoot?: string }) {
  // On a mounted tier the workdir IS the mounted host tree (the workspace bind hides hostRoot/work inside
  // the sandbox), so every host-side GSD path keys off the pinned workspace when the tier has one.
  const hostWork = hostWorkDir(tier);
  const shared = !!tier.workspaceHostPath;
  if (hostWork && existsSync(join(CACHE, MARKER))) {
    copyClaude(join(CACHE, ".claude"), hostWork);
    return { source: "cache", cache: CACHE, ompAgents: await convertAgents(tier, hostWork) };
  }
  const argv = ["npx", "-y", `@opengsd/gsd-core@${VERSIONS.gsdCore}`, "--claude", "--local", "--no-legacy-cleanup"];
  const install = async (cwd?: string) => {
    // Egress policy can take a moment to settle on a fresh sandbox (seen as connect EPERM via the proxy): retry a few times.
    let r = { code: 1, stdout: "", stderr: "" } as Awaited<ReturnType<typeof tier.exec>>;
    for (let i = 0; i < 4; i++) {
      r = await tier.exec(argv, { ...(cwd ? { cwd } : {}), timeoutMs: 600_000, input: "" });
      if (r.code === 0 || !/EPERM|ECONNRESET|ETIMEDOUT|EAI_AGAIN|rejected by your operating system/.test(r.stderr)) break;
      await new Promise((res) => setTimeout(res, 15_000 * (i + 1)));
    }
    if (r.code !== 0) throw new Error(`GSD-Core install failed in sandbox (exit ${r.code}): ${r.stderr.slice(-600)}`);
  };
  if (!hostWork) {
    await install();
    return { source: "installed-in-sandbox", cache: null, ompAgents: await convertAgents(tier, null) };
  }
  // The work folder is the only host-visible folder the sandbox writes, so stage there in a fresh folder
  // the job never had. A mounted worktree is shared (the host and other jobs can write into it while npx
  // runs), so its install serves this job only and is checked and copied inside the sandbox; a private
  // work folder is checked on the host and fills the shared cache.
  mkdirSync(hostWork, { recursive: true });
  const stage = mkdtempSync(join(hostWork, ".omp-gsd-install-"));
  try {
    await install(`${tier.workdir}/${basename(stage)}`);
    if (shared) {
      refuseLinkedClaude(hostWork);
      const c = await tier.exec(["sh", "-c", IN_SANDBOX_COPY, "sh", basename(stage)], { cwd: tier.workdir, timeoutMs: 60_000, input: "" });
      if (c.code !== 0) throw new Error(`GSD-Core install produced no ${MARKER} or .claude is a symlink (in-sandbox copy exit ${c.code}): ${c.stderr.slice(-600)}`);
      return { source: "installed-in-sandbox", cache: null, ompAgents: await convertAgents(tier, hostWork) };
    }
    if (!existsSync(join(stage, MARKER))) throw new Error("GSD-Core install produced no .claude/commands/gsd-plan-phase.md");
    const published = fillCache(join(stage, ".claude"));
    copyClaude(join(stage, ".claude"), hostWork);
    return {
      source: "installed-in-sandbox",
      cache: published || existsSync(join(CACHE, MARKER)) ? CACHE : null,
      ompAgents: await convertAgents(tier, hostWork),
    };
  } finally { rmSync(stage, { recursive: true, force: true }); }
}

export function gsdCommandsOnHost(tier: Pick<SandboxTier, "workspaceHostPath" | "workspaceHostDir"> & { hostRoot?: string }) {
  const work = hostWorkDir(tier);
  if (!work) return null;
  const d = join(work, ".claude", "commands");
  return existsSync(d) ? readdirSync(d).filter((f) => f.startsWith("gsd-")).length : 0;
}

/** omp skips .claude/agents (Claude frontmatter is not its task-agent contract), so the GSD workflows'
 *  `gsd-phase-researcher` / `gsd-planner` / `gsd-executor` ... spawns fail with "Unknown agent". Re-emit each
 *  GSD agent as a project-level .omp/agents/<name>.md with only name + description; omitting `tools` gives
 *  omp's default task-agent tool set (Claude tool names like Read/Bash/mcp__* do not map 1:1). */
/** First body line of every generated agent file: only files carrying it are ever rewritten. */
export const GENERATED_MARK = "<!-- omp-sandbox: generated from .claude/agents; project edits belong in a file without this line -->";

export function writeOmpAgents(hostWork: string): number {
  const src = join(hostWork, ".claude", "agents"); const dst = join(hostWork, ".omp", "agents");
  refuseLinked([src, join(hostWork, ".omp"), dst], "GSD agents");
  if (!existsSync(src)) return 0;
  mkdirSync(dst, { recursive: true });
  let n = 0;
  for (const f of readdirSync(src).filter((x) => /^gsd-[a-z0-9-]+\.md$/.test(x))) {
    const out = join(dst, f);
    if (isLink(join(src, f)) || isLink(out)) continue;   // never read or write through a planted link
    const text = readFileSync(join(src, f), "utf8");
    const m = /^---\n([\s\S]*?)\n---\n?([\s\S]*)$/.exec(text);
    if (!m) continue;
    const field = (k: string) => (new RegExp(`^${k}:\\s*(.*)$`, "m").exec(m[1])?.[1] ?? "").trim().replace(/^["']|["']$/g, "");
    const name = field("name") || f.replace(/\.md$/, ""); const description = field("description");
    if (!description) continue;
    // A project's own .omp/agents file (no generated mark) is kept as is and still counts as available.
    if (existsSync(out) && !readFileSync(out, "utf8").includes(GENERATED_MARK)) { n++; continue; }
    writeFileSync(out, `---\nname: ${JSON.stringify(name)}\ndescription: ${JSON.stringify(description)}\nspawns: "*"\n---\n${GENERATED_MARK}\n${m[2]}`);
    n++;
  }
  return n;
}

/** Same conversion as writeOmpAgents, as a self-contained script run with cwd = the sandbox workdir. */
export const IN_SANDBOX_CONVERT = String.raw`const fs=require("fs"),p=require("path");const src=".claude/agents",dst=".omp/agents";let n=0;const MARK=${JSON.stringify(GENERATED_MARK)};
if(fs.existsSync(src)){fs.mkdirSync(dst,{recursive:true});for(const f of fs.readdirSync(src).filter(x=>/^gsd-[a-z0-9-]+\.md$/.test(x))){
const t=fs.readFileSync(p.join(src,f),"utf8");const m=/^---\n([\s\S]*?)\n---\n?([\s\S]*)$/.exec(t);if(!m)continue;
const g=k=>((new RegExp("^"+k+":\\s*(.*)$","m").exec(m[1])||[])[1]||"").trim().replace(/^["']|["']$/g,"");
const name=g("name")||f.replace(/\.md$/,""),d=g("description");if(!d)continue;const out=p.join(dst,f);
if(fs.existsSync(out)&&!fs.readFileSync(out,"utf8").includes(MARK)){n++;continue;}
fs.writeFileSync(out,"---\nname: "+JSON.stringify(name)+"\ndescription: "+JSON.stringify(d)+"\nspawns: \"*\"\n---\n"+MARK+"\n"+m[2]);n++;}}
console.log(n)`;
