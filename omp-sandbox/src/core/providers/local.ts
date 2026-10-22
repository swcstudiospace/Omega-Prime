// agentos-local tier: last-resort, labelled "no isolation". Runs the Sandbox Agent server and omp as
// plain host processes in a private work dir with HOME redirected. Use only when bwrap is missing.
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { copyFileSync, mkdirSync, rmSync, symlinkSync, existsSync } from "node:fs";
import { basename, join } from "node:path";
import { randomBytes } from "node:crypto";
import { STATE, NODE24_BIN, BUN_EXE, OMP_BIN, SANDBOX_AGENT_BIN } from "../config.ts";
import { freePort } from "../auth.ts";
import { execCapture, killTree, launched, ownsGroup, rid, waitHttp } from "./util.ts";
import type { SandboxTier } from "./types.ts";

export class LocalTier implements SandboxTier {
  readonly name = "agentos-local" as const;
  readonly id = rid("lo");
  readonly isolation = "NONE: host processes with a private HOME and work dir (labelled fallback)";
  readonly hostRoot = join(STATE, "ws", this.id);
  get sandboxRoot() { return this.hostRoot; }
  get workdir() { return join(this.hostRoot, "work"); }
  #env: Record<string, string> = {};
  #agent: ChildProcessWithoutNullStreams | null = null;
  #url = ""; #token = randomBytes(18).toString("hex");
  #children = new Set<ChildProcessWithoutNullStreams>();
  /** Every process group this tier started (each spawn leads its own group, so descendants are owned too). */
  #groups = new Set<number>();
  /** Stamped into every process this tier starts, so a group id is only signalled while one of ours still holds it. */
  readonly #marker = `OMP_SANDBOX_TIER_ID=${this.id}`;
  #owns(g: number) { return ownsGroup(g, this.#marker); }
  /** Forget groups with no process of ours left: their ids may be reused by unrelated host jobs. */
  #prune() { for (const g of this.#groups) if (!this.#owns(g)) this.#groups.delete(g); }

  #fullEnv(extra: Record<string, string> = {}) {
    return { HOME: join(this.hostRoot, "home"), PATH: `${join(this.hostRoot, "bin")}:${NODE24_BIN}:/usr/local/bin:/usr/bin:/bin`, LANG: "C.UTF-8", TERM: "dumb", OMP_SANDBOX: "1", ...this.#env, ...extra, OMP_SANDBOX_TIER_ID: this.id };
  }

  async start(env: Record<string, string>) {
    this.#env = env;
    for (const d of ["work", "home", "bin"]) mkdirSync(join(this.hostRoot, d), { recursive: true });
    for (const [t, n] of [[BUN_EXE, "bun"], [OMP_BIN, "omp"]]) { const p = join(this.hostRoot, "bin", n); if (!existsSync(p)) symlinkSync(t, p); }
    const port = await freePort(); this.#url = `http://127.0.0.1:${port}`;
    this.#agent = spawn(SANDBOX_AGENT_BIN, ["server", "--host", "127.0.0.1", "--port", String(port), "--token", this.#token, "--no-telemetry"], { cwd: this.hostRoot, env: this.#fullEnv(), stdio: ["pipe", "pipe", "pipe"], detached: true });
    if (this.#agent.pid) this.#groups.add(this.#agent.pid);
    this.#agent.stdout.resume(); this.#agent.stderr.resume();
    await launched(this.#agent, "sandbox-agent", waitHttp(`${this.#url}/v1/health`, this.#token, 30_000));
  }
  sandboxAgent() { return { baseUrl: this.#url, token: this.#token }; }
  spawnStdio(argv: string[], opts: { cwd?: string; env?: Record<string, string> } = {}) {
    const p = spawn(argv[0], argv.slice(1), { cwd: opts.cwd ?? this.workdir, env: this.#fullEnv(opts.env), stdio: ["pipe", "pipe", "pipe"], detached: true });
    this.#prune();
    if (p.pid) this.#groups.add(p.pid);
    // The group stays tracked after the leader exits while background descendants of ours still hold it.
    this.#children.add(p); p.once("exit", () => { this.#children.delete(p); this.#prune(); }); return p;
  }
  exec(argv: string[], opts: { cwd?: string; env?: Record<string, string>; timeoutMs?: number; input?: string } = {}) {
    // Each exec leads its own group; track it like spawnStdio's so dispose reaps checks still running.
    return execCapture(argv[0], argv.slice(1), { cwd: opts.cwd ?? this.workdir, env: this.#fullEnv(opts.env), timeoutMs: opts.timeoutMs ?? 600_000, input: opts.input, killGroup: true, onSpawn: (pid) => { this.#prune(); this.#groups.add(pid); } });
  }
  /** Sandbox paths are host paths on this tier: transfers are plain copies inside the private root. */
  #inside(path: string) { if (!path.startsWith(`${this.hostRoot}/`) || path.includes("..")) throw new Error(`path outside the tier root: ${path}`); return path; }
  async upload(local: string, dest: string) { copyFileSync(local, this.#inside(dest)); return { code: 0, stdout: "", stderr: "" }; }
  async download(path: string, destDir: string) { copyFileSync(this.#inside(path), join(destDir, basename(path))); return { code: 0, stdout: "", stderr: "" }; }
  owned() { this.#prune(); return { hostRoot: this.hostRoot, sandboxAgentPid: this.#agent?.pid ?? null, children: [...this.#children].map((c) => c.pid), processGroups: [...this.#groups] }; }
  async dispose() {
    // Confirm ownership right before each signal: a group id whose members have all exited may now be someone else's.
    this.#prune(); const groups = [...this.#groups];
    for (const g of groups) if (this.#owns(g)) killTree(g, "SIGTERM");
    await new Promise((r) => setTimeout(r, 1500));
    for (const g of groups) if (this.#owns(g)) killTree(g, "SIGKILL");
    await new Promise((r) => setTimeout(r, 300));
    if (!process.env.OMP_SANDBOX_KEEP_WS) rmSync(this.hostRoot, { recursive: true, force: true });
    this.#prune();
    return { leftovers: groups.filter((g) => this.#owns(g)).map((g) => `process group ${g}`) };
  }
}
