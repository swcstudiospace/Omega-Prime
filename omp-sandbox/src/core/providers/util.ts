import { spawn, type ChildProcess, type SpawnOptions } from "node:child_process";
import { randomBytes } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";

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

/**
 * Whether the process group `pgid` still holds a process we started, identified by `marker` ("NAME=value") in its
 * environment. While any member is alive the kernel cannot hand the group id to a new process, so a match proves
 * the group is still ours; no match means it ended (and the id may since have been reused by an unrelated job).
 * Falls back to `groupAlive` where /proc is unavailable.
 */
export function ownsGroup(pgid: number, marker: string, proc = "/proc"): boolean {
  let pids: string[];
  try { pids = readdirSync(proc).filter((d) => /^\d+$/.test(d)); } catch { return groupAlive(pgid); }
  for (const pid of pids) {
    try {
      const stat = readFileSync(`${proc}/${pid}/stat`, "utf8");
      // Fields after the parenthesised comm: state ppid pgrp ...
      const pgrp = Number(stat.slice(stat.lastIndexOf(")") + 2).split(" ")[2]);
      if (pgrp !== pgid) continue;
      if (readFileSync(`${proc}/${pid}/environ`, "utf8").split("\0").includes(marker)) return true;
    } catch { /* exited or not readable: not ours */ }
  }
  return false;
}

/**
 * Wait for `ready`, but reject as soon as `p` fails to launch (ENOENT, EACCES) or exits first, so a broken binary
 * goes through the caller's normal failed-startup cleanup instead of an unhandled child `error` killing Node.
 * A later `error` on the long-lived child is absorbed for the same reason (its exit is observed elsewhere).
 */
export async function launched<T>(p: ChildProcess, what: string, ready: Promise<T>): Promise<T> {
  let fail: (e: Error) => void = () => {};
  const died = new Promise<never>((_, rej) => { fail = rej; });
  const onError = (e: Error) => fail(new Error(`${what} could not start: ${e.message}`));
  const onExit = (code: number | null, sig: string | null) => fail(new Error(`${what} exited during startup (${code ?? sig})`));
  p.on("error", onError); p.once("exit", onExit);
  try { return await Promise.race([ready, died]); }
  finally { p.off("error", onError); p.off("exit", onExit); p.on("error", () => {}); }
}
