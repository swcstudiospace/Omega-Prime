// GSD-Core inside the sandbox: `npx -y @opengsd/gsd-core@1.18.0 --claude --local` (omp loads project
// .claude/commands as /gsd-* slash commands). First boot installs inside the sandbox into a fresh, empty
// staging folder, so the host-side cache holds only the installer's output, never a job's own .claude
// files; later workspaces get a copy of that cache.
import { cpSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
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
    return { source: "cache", cache: CACHE, ompAgents: writeOmpAgents(hostWork) };
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
    const c = await tier.exec(["node", "-e", IN_SANDBOX_CONVERT], { timeoutMs: 60_000, input: "" });
    if (c.code !== 0) throw new Error(`GSD agent conversion failed in sandbox: ${c.stderr.slice(-400)}`);
    const ompAgents = Number(c.stdout.trim().split("\n").pop()) || 0;
    if (!ompAgents) throw new Error("GSD agent conversion produced no .omp/agents (omp would report Unknown agent gsd-*)");
    return { source: "installed-in-sandbox", cache: null, ompAgents };
  }
  // The workspace is the only host-visible folder the sandbox writes, so stage there in a fresh folder
  // the job never had: the cache then holds exactly what the installer wrote.
  mkdirSync(hostWork, { recursive: true });
  const stage = mkdtempSync(join(hostWork, ".omp-gsd-install-"));
  try {
    await install(`${tier.workdir}/${basename(stage)}`);
    if (!existsSync(join(stage, MARKER))) throw new Error("GSD-Core install produced no .claude/commands/gsd-plan-phase.md");
    fillCache(join(stage, ".claude"));
    cpSync(join(stage, ".claude"), join(hostWork, ".claude"), { recursive: true });
  } finally { rmSync(stage, { recursive: true, force: true }); }
  return { source: "installed-in-sandbox", cache: CACHE, ompAgents: writeOmpAgents(hostWork) };
}

export function gsdCommandsOnHost(tier: { hostRoot?: string; workspaceHostPath?: string | null }) {
  const work = tier.workspaceHostPath ?? (tier.hostRoot ? join(tier.hostRoot, "work") : null);
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
  if (!existsSync(src)) return 0;
  mkdirSync(dst, { recursive: true });
  let n = 0;
  for (const f of readdirSync(src).filter((x) => /^gsd-[a-z0-9-]+\.md$/.test(x))) {
    const text = readFileSync(join(src, f), "utf8");
    const m = /^---\n([\s\S]*?)\n---\n?([\s\S]*)$/.exec(text);
    if (!m) continue;
    const field = (k: string) => (new RegExp(`^${k}:\\s*(.*)$`, "m").exec(m[1])?.[1] ?? "").trim().replace(/^["']|["']$/g, "");
    const name = field("name") || f.replace(/\.md$/, ""); const description = field("description");
    if (!description) continue;
    const out = join(dst, f);
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
