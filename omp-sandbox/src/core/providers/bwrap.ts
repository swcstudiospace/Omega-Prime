// bwrap tier: labelled fallback when the OpenShell MicroVM driver cannot boot on this host.
// Read-only /usr and /etc, empty /home except read-only omp/Bun/Node toolchains, a private writable
// sandbox root (/sbx: work, home, bin), private /tmp, new pid/ipc/uts namespaces, die-with-parent.
// NOT isolated: network (shared, needed for the loopback auth-broker and the model APIs) and the kernel.
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { existsSync, mkdirSync, symlinkSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { randomBytes } from "node:crypto";
import { STATE, NODE24_BIN, BUN_EXE, OMP_BIN, SANDBOX_AGENT_BIN, HOME, realBin, toolchainRoot } from "../config.ts";
import { freePort } from "../auth.ts";
import { execCapture, pidAlive, rid, waitHttp, launched } from "./util.ts";
import { TierUnavailable, type SandboxTier } from "./types.ts";
import { pinDir, unpinDir, PathRefused, type PinnedDir } from "../paths.ts";

const BWRAP = "/usr/bin/bwrap";
/** Child fd that carries the pinned worktree into bwrap (stdin/out/err are 0-2). */
const PIN_FD = 3;

/** Read-only binds for the omp and Bun the host broker uses, wherever they are installed (deduplicated, nested ones dropped). */
export function toolchainBinds(bins: string[] = [OMP_BIN, BUN_EXE]): string[] {
  const roots = [...new Set(bins.map(toolchainRoot))].filter((r) => existsSync(r)).sort();
  const kept = roots.filter((r) => !roots.some((o) => o !== r && r.startsWith(o + "/")) && r !== "/usr" && !r.startsWith("/usr/"));
  return kept.flatMap((r) => ["--ro-bind", r, r]);
}

export class BwrapTier implements SandboxTier {
  readonly name = "bwrap" as const;
  readonly id = rid("bw");
  readonly sandboxRoot = "/sbx";
  readonly workdir = "/sbx/work";
  readonly isolation = "bubblewrap: ro /usr,/etc; tmpfs /home + ro toolchains; private /sbx and /tmp; unshared pid/ipc/uts; shared network and kernel";
  readonly hostRoot: string;
  readonly workspaceHostPath: string | null;
  #env: Record<string, string> = {};
  #agent: ChildProcessWithoutNullStreams | null = null;
  #agentUrl = ""; #agentToken = randomBytes(18).toString("hex");
  #children = new Set<ChildProcessWithoutNullStreams>();
  /** The checked worktree, held open: every bwrap binds this descriptor (fd 3), never the path name again. */
  #pin: PinnedDir | null = null;

  constructor(hostRoot?: string, workspaceHostPath?: string | null) { this.hostRoot = hostRoot ?? join(STATE, "ws", this.id); this.workspaceHostPath = workspaceHostPath ?? null; }

  #argv(cmd: string[], env: Record<string, string>, cwd = this.workdir) {
    const a = [
      "--ro-bind", "/usr", "/usr", "--ro-bind", "/etc", "/etc",
      "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64", "--symlink", "usr/sbin", "/sbin",
      "--tmpfs", "/home",
      ...toolchainBinds(),
      "--ro-bind", dirname(NODE24_BIN), dirname(NODE24_BIN),
      "--ro-bind", dirname(SANDBOX_AGENT_BIN), "/opt/sandbox-agent",
      "--bind", this.hostRoot, this.sandboxRoot,
      // A job worktree replaces the private work dir (later binds win); the rest of /sbx stays private.
      // Bound from the pinned descriptor, so a worktree swapped for a symlink after the check cannot redirect the mount.
      ...(this.workspaceHostPath ? ["--bind-fd", String(PIN_FD), this.workdir] : []),
      "--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev",
      "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--hostname", "omp-sandbox", "--die-with-parent",
      "--clearenv",
    ];
    const base: Record<string, string> = {
      HOME: "/sbx/home", USER: "sandbox", LANG: "C.UTF-8", TERM: "dumb",
      PATH: `/sbx/bin:${NODE24_BIN}:/opt/sandbox-agent:/usr/local/bin:/usr/bin:/bin`,
      npm_config_cache: "/sbx/home/.npm", npm_config_update_notifier: "false",
      OMP_SANDBOX: "1",
    };
    for (const [k, v] of Object.entries({ ...base, ...this.#env, ...env })) a.push("--setenv", k, v);
    a.push("--chdir", cwd, "--", ...cmd);
    return a;
  }

  async start(env: Record<string, string>) {
    if (!existsSync(BWRAP)) throw new TierUnavailable("bwrap", "bubblewrap not installed");
    if (!existsSync(SANDBOX_AGENT_BIN)) throw new TierUnavailable("bwrap", `sandbox-agent binary missing at ${SANDBOX_AGENT_BIN}`);
    this.#env = env;
    this.pinWorkspace();
    for (const d of ["work", "home", "bin"]) mkdirSync(join(this.hostRoot, d), { recursive: true });
    const link = (target: string, name: string) => { const p = join(this.hostRoot, "bin", name); if (!existsSync(p)) symlinkSync(target, p); };
    // Link the real launchers (the configured omp and the Bun that runs it); their folders are bound read-only above.
    link(realBin(BUN_EXE), "bun"); link(realBin(OMP_BIN), "omp");
    const probe = await execCapture(BWRAP, this.#argv(["sh", "-c", "omp --version && node -v && test ! -e " + join(HOME, ".omp") + " && echo NO_HOST_OMP_STORE"], {}), { timeoutMs: 60_000, extraFds: this.#fds() });
    if (probe.code !== 0 || !probe.stdout.includes("NO_HOST_OMP_STORE")) throw new TierUnavailable("bwrap", `probe failed: ${probe.stderr.slice(-400)}`);
    const port = await freePort();
    this.#agentUrl = `http://127.0.0.1:${port}`;
    this.#agent = spawn(BWRAP, this.#argv(["sandbox-agent", "server", "--host", "127.0.0.1", "--port", String(port), "--token", this.#agentToken, "--no-telemetry"], {}, "/sbx"), { stdio: ["pipe", "pipe", "pipe", ...this.#fds()] }) as ChildProcessWithoutNullStreams;
    this.#agent.stdout.resume(); this.#agent.stderr.resume();
    await launched(this.#agent, "bwrap sandbox-agent", waitHttp(`${this.#agentUrl}/v1/health`, this.#agentToken, 30_000));
  }

  /** Hold the checked worktree open (done by start()); from here on every bwrap binds this descriptor. */
  pinWorkspace() {
    if (!this.workspaceHostPath || this.#pin) return;
    try { this.#pin = pinDir(this.workspaceHostPath, "workspace_host_path"); }
    catch (e) { throw e instanceof PathRefused ? new TierUnavailable("bwrap", e.message) : e; }
  }

  sandboxAgent() { return { baseUrl: this.#agentUrl, token: this.#agentToken }; }

  spawnStdio(argv: string[], opts: { cwd?: string; env?: Record<string, string> } = {}) {
    const p = spawn(BWRAP, this.#argv(argv, opts.env ?? {}, opts.cwd), { stdio: ["pipe", "pipe", "pipe", ...this.#fds()] }) as ChildProcessWithoutNullStreams;
    this.#children.add(p); p.once("exit", () => this.#children.delete(p));
    return p;
  }

  exec(argv: string[], opts: { cwd?: string; env?: Record<string, string>; timeoutMs?: number; input?: string } = {}) {
    return execCapture(BWRAP, this.#argv(argv, opts.env ?? {}, opts.cwd), { timeoutMs: opts.timeoutMs ?? 600_000, input: opts.input, extraFds: this.#fds() });
  }

  /** The pinned worktree descriptor for the child's fd 3 (bwrap closes it after binding, so the job never holds it). */
  #fds(): number[] {
    if (!this.workspaceHostPath) return [];
    if (!this.#pin) throw new Error("bwrap workspace is not pinned (start() not run or already disposed)");
    return [this.#pin.fd];
  }

  owned() { return { hostRoot: this.hostRoot, sandboxAgentPid: this.#agent?.pid ?? null, children: [...this.#children].map((c) => c.pid) }; }

  async dispose() {
    const pids = [this.#agent?.pid, ...[...this.#children].map((c) => c.pid)].filter(Boolean) as number[];
    for (const c of [...this.#children]) c.kill("SIGTERM");
    this.#agent?.kill("SIGTERM");
    await new Promise((r) => setTimeout(r, 1500));
    for (const pid of pids) if (pidAlive(pid)) try { process.kill(pid, "SIGKILL"); } catch { /* gone */ }
    await new Promise((r) => setTimeout(r, 300));
    const leftovers = pids.filter(pidAlive).map((p) => `pid ${p}`);
    if (!process.env.OMP_SANDBOX_KEEP_WS) rmSync(this.hostRoot, { recursive: true, force: true });
    if (existsSync(this.hostRoot) && !process.env.OMP_SANDBOX_KEEP_WS) leftovers.push(this.hostRoot);
    unpinDir(this.#pin); this.#pin = null;
    return { leftovers };
  }
}
