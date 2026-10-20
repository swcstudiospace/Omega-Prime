import { spawn, type SpawnOptions } from "node:child_process";
import { randomBytes } from "node:crypto";

export const rid = (p: string) => `${p}-${Date.now().toString(36)}-${randomBytes(3).toString("hex")}`;

export function execCapture(cmd: string, args: string[], opts: SpawnOptions & { timeoutMs?: number; input?: string } = {}) {
  return new Promise<{ code: number | null; stdout: string; stderr: string }>((resolve) => {
    const p = spawn(cmd, args, { ...opts, stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "", stderr = "";
    p.stdout!.on("data", (b) => { stdout += b; if (stdout.length > 4_000_000) stdout = stdout.slice(-2_000_000); });
    p.stderr!.on("data", (b) => { stderr += b; if (stderr.length > 1_000_000) stderr = stderr.slice(-500_000); });
    const t = opts.timeoutMs ? setTimeout(() => { stderr += `\n[omp-sandbox] timeout after ${opts.timeoutMs}ms`; p.kill("SIGKILL"); }, opts.timeoutMs) : null;
    p.on("error", (e) => { stderr += String(e); });
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
