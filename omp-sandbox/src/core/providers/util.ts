import { spawn, type SpawnOptions } from "node:child_process";
import { randomBytes } from "node:crypto";

export const rid = (p: string) => `${p}-${Date.now().toString(36)}-${randomBytes(3).toString("hex")}`;

/** Replace credential-shaped substrings (API keys, JWTs, bearer/`key=value` secrets, long hex/base64 runs) with [redacted]. */
export function redactSecrets(text: string): string {
  return text
    .replace(/\b(sk|pk|rk|ghp|gho|ghs|github_pat|xox[abpr])[-_][A-Za-z0-9_\-]{12,}/g, "[redacted]")
    .replace(/\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}(\.[A-Za-z0-9_\-]+)?/g, "[redacted]")
    .replace(/\b(bearer)\s+[A-Za-z0-9._~+\/=\-]{12,}/gi, "$1 [redacted]")
    .replace(/\b([A-Za-z0-9_]*(?:token|secret|password|passwd|api[_-]?key|apikey)[A-Za-z0-9_]*)(["']?\s*[:=]\s*["']?)[^\s"',;}]{6,}/gi, "$1$2[redacted]")
    .replace(/\b[A-Fa-f0-9]{40,}\b/g, "[redacted]")
    .replace(/\b[A-Za-z0-9+\/_\-]{48,}={0,2}/g, "[redacted]");
}

/** `killGroup`: run the command as its own process group and kill the whole group on timeout. */
export function execCapture(cmd: string, args: string[], opts: SpawnOptions & { timeoutMs?: number; input?: string; killGroup?: boolean } = {}) {
  return new Promise<{ code: number | null; stdout: string; stderr: string }>((resolve) => {
    const { killGroup, ...spawnOpts } = opts;
    const p = spawn(cmd, args, { ...spawnOpts, detached: !!killGroup, stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "", stderr = "";
    p.stdout!.on("data", (b) => { stdout += b; if (stdout.length > 4_000_000) stdout = stdout.slice(-2_000_000); });
    p.stderr!.on("data", (b) => { stderr += b; if (stderr.length > 1_000_000) stderr = stderr.slice(-500_000); });
    const t = opts.timeoutMs ? setTimeout(() => { stderr += `\n[omp-sandbox] timeout after ${opts.timeoutMs}ms`; if (killGroup && p.pid) killTree(p.pid, "SIGKILL"); else p.kill("SIGKILL"); }, opts.timeoutMs) : null;
    p.on("error", (e) => { stderr += String(e); });
    p.stdin!.on("error", (e) => { stderr += `\n[omp-sandbox] stdin: ${e}`; });
    p.on("close", (code) => { if (t) clearTimeout(t); resolve({ code, stdout, stderr }); });
    if (opts.input !== undefined) p.stdin!.end(opts.input); else p.stdin!.end();
  });
}

export async function waitHttp(url: string, token: string, timeoutMs = 20_000) {
  const t0 = Date.now(); let last = "";
  while (Date.now() - t0 < timeoutMs) {
    try { const r = await fetch(url, { headers: { authorization: `Bearer ${token}` } }); if (r.status < 500) return r.status; last = `HTTP ${r.status}`; }
    catch (e) { last = String(e); }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`not reachable: ${url} (${last})`);
}

export function pidAlive(pid: number | undefined | null) {
  if (!pid) return false;
  try { process.kill(pid, 0); return true; } catch { return false; }
}

/** Signal a whole process group (a child spawned with `detached: true` leads its own group). */
export function killTree(pid: number, signal: NodeJS.Signals = "SIGTERM") {
  try { process.kill(-pid, signal); } catch { try { process.kill(pid, signal); } catch { /* gone */ } }
}

/** Whether any process in the group led by `pid` is still alive. */
export function groupAlive(pid: number | undefined | null) {
  if (!pid) return false;
  try { process.kill(-pid, 0); return true; } catch { return false; }
}
