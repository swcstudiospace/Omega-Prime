// GSD-Core inside the sandbox: `npx -y @opengsd/gsd-core@1.18.0 --claude --local` in the sandbox
// workdir (omp loads project .claude/commands as /gsd-* slash commands). First boot installs inside
// the sandbox; the resulting .claude tree is cached host-side and copied into later workspaces.
import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { STATE, VERSIONS } from "./config.ts";
import type { SandboxTier } from "./providers/types.ts";

const CACHE = join(STATE, "cache", `gsd-core-${VERSIONS.gsdCore}`);

export async function ensureGsd(tier: SandboxTier & { hostRoot?: string }) {
  const hostWork = tier.hostRoot ? join(tier.hostRoot, "work") : null;
  if (hostWork && existsSync(join(CACHE, ".claude", "commands", "gsd-plan-phase.md"))) {
    cpSync(join(CACHE, ".claude"), join(hostWork, ".claude"), { recursive: true });
    return { source: "cache", cache: CACHE, ompAgents: writeOmpAgents(hostWork) };
  }
  // Egress policy can take a moment to settle on a fresh sandbox (seen as connect EPERM via the proxy): retry a few times.
  let r = { code: 1, stdout: "", stderr: "" } as Awaited<ReturnType<typeof tier.exec>>;
  for (let i = 0; i < 4; i++) {
    r = await tier.exec(["npx", "-y", `@opengsd/gsd-core@${VERSIONS.gsdCore}`, "--claude", "--local", "--no-legacy-cleanup"], { timeoutMs: 600_000, input: "" });
    if (r.code === 0 || !/EPERM|ECONNRESET|ETIMEDOUT|EAI_AGAIN|rejected by your operating system/.test(r.stderr)) break;
    await new Promise((res) => setTimeout(res, 15_000 * (i + 1)));
  }
  if (r.code !== 0) throw new Error(`GSD-Core install failed in sandbox (exit ${r.code}): ${r.stderr.slice(-600)}`);
  if (hostWork) {
    if (!existsSync(join(hostWork, ".claude", "commands", "gsd-plan-phase.md"))) throw new Error("GSD-Core install produced no .claude/commands/gsd-plan-phase.md");
    mkdirSync(CACHE, { recursive: true });
    cpSync(join(hostWork, ".claude"), join(CACHE, ".claude"), { recursive: true });
  }
  let ompAgents = hostWork ? writeOmpAgents(hostWork) : 0;
  if (!hostWork) {
    const c = await tier.exec(["node", "-e", IN_SANDBOX_CONVERT], { timeoutMs: 60_000, input: "" });
    if (c.code !== 0) throw new Error(`GSD agent conversion failed in sandbox: ${c.stderr.slice(-400)}`);
    ompAgents = Number(c.stdout.trim().split("\n").pop()) || 0;
    if (!ompAgents) throw new Error("GSD agent conversion produced no .omp/agents (omp would report Unknown agent gsd-*)");
  }
  return { source: "installed-in-sandbox", cache: hostWork ? CACHE : null, ompAgents };
}

export function gsdCommandsOnHost(tier: { hostRoot?: string }) {
  if (!tier.hostRoot) return null;
  const d = join(tier.hostRoot, "work", ".claude", "commands");
  return existsSync(d) ? readdirSync(d).filter((f) => f.startsWith("gsd-")).length : 0;
}

/** omp skips .claude/agents (Claude frontmatter is not its task-agent contract), so the GSD workflows'
 *  `gsd-phase-researcher` / `gsd-planner` / `gsd-executor` ... spawns fail with "Unknown agent". Re-emit each
 *  GSD agent as a project-level .omp/agents/<name>.md with only name + description; omitting `tools` gives
 *  omp's default task-agent tool set (Claude tool names like Read/Bash/mcp__* do not map 1:1). */
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
    writeFileSync(join(dst, f), `---\nname: ${JSON.stringify(name)}\ndescription: ${JSON.stringify(description)}\nspawns: "*"\n---\n${m[2]}`);
    n++;
  }
  return n;
}

/** Same conversion as writeOmpAgents, as a self-contained script run with cwd = the sandbox workdir. */
const IN_SANDBOX_CONVERT = String.raw`const fs=require("fs"),p=require("path");const src=".claude/agents",dst=".omp/agents";let n=0;
if(fs.existsSync(src)){fs.mkdirSync(dst,{recursive:true});for(const f of fs.readdirSync(src).filter(x=>/^gsd-[a-z0-9-]+\.md$/.test(x))){
const t=fs.readFileSync(p.join(src,f),"utf8");const m=/^---\n([\s\S]*?)\n---\n?([\s\S]*)$/.exec(t);if(!m)continue;
const g=k=>((new RegExp("^"+k+":\\s*(.*)$","m").exec(m[1])||[])[1]||"").trim().replace(/^["']|["']$/g,"");
const name=g("name")||f.replace(/\.md$/,""),d=g("description");if(!d)continue;
fs.writeFileSync(p.join(dst,f),"---\nname: "+JSON.stringify(name)+"\ndescription: "+JSON.stringify(d)+"\nspawns: \"*\"\n---\n"+m[2]);n++;}}
console.log(n)`;
