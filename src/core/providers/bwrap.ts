// bwrap tier: labelled fallback when the OpenShell MicroVM driver cannot boot on this host.
// Read-only /usr and /etc, empty /home except read-only omp/Bun/Node toolchains, a private writable
// sandbox root (/sbx: work, home, bin), private /tmp, new pid/ipc/uts namespaces, die-with-parent.
// NOT isolated: network (shared, needed for the loopback auth-broker and the model APIs) and the kernel.
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { existsSync, mkdirSync, symlinkSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { randomBytes } from "node:crypto";
import { STATE, BUN_INSTALL, NODE24_BIN, BUN_EXE, SANDBOX_AGENT_BIN, HOME } from "../config.ts";
import { freePort } from "../auth.ts";
import { execCapture, pidAlive, rid, waitHttp } from "./util.ts";
import { TierUnavailable, type SandboxTier } from "./types.ts";

const BWRAP = "/usr/bin/bwrap";

export class BwrapTier implements SandboxTier {
  readonly name = "bwrap" as const;
  readonly id = rid("bw");
  readonly sandboxRoot = "/sbx";
  readonly workdir = "/sbx/work";
  readonly isolation = "bubblewrap: ro /usr,/etc; tmpfs /home + ro toolchains; private /sbx and /tmp; unshared pid/ipc/uts; shared network and kernel";
  readonly hostRoot: string;
  #env: Record<string, string> = {};
  #agent: ChildProcessWithoutNullStreams | null = null;
  #agentUrl = ""; #agentToken = randomBytes(18).toString("hex");
  #children = new Set<ChildProcessWithoutNullStreams>();

  constructor(hostRoot?: string) { this.hostRoot = hostRoot ?? join(STATE, "ws", this.id); }

  #argv(cmd: string[], env: Record<string, string>, cwd = this.workdir) {
    const a = [
      "--ro-bind", "/usr", "/usr", "--ro-bind", "/etc", "/etc",
      "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64", "--symlink", "usr/sbin", "/sbin",
      "--tmpfs", "/home",
      "--ro-bind", BUN_INSTALL, BUN_INSTALL,
      "--ro-bind", dirname(NODE24_BIN), dirname(NODE24_BIN),
      "--ro-bind", dirname(SANDBOX_AGENT_BIN), "/opt/sandbox-agent",
      "--bind", this.hostRoot, this.sandboxRoot,
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
    for (const d of ["work", "home", "bin"]) mkdirSync(join(this.hostRoot, d), { recursive: true });
    const link = (target: string, name: string) => { const p = join(this.hostRoot, "bin", name); if (!existsSync(p)) symlinkSync(target, p); };
    link(BUN_EXE, "bun"); link(join(BUN_INSTALL, "bin", "omp"), "omp");
    const probe = await execCapture(BWRAP, this.#argv(["sh", "-c", "omp --version && node -v && test ! -e " + join(HOME, ".omp") + " && echo NO_HOST_OMP_STORE"], {}), { timeoutMs: 60_000 });
    if (probe.code !== 0 || !probe.stdout.includes("NO_HOST_OMP_STORE")) throw new TierUnavailable("bwrap", `probe failed: ${probe.stderr.slice(-400)}`);
    const port = await freePort();
    this.#agentUrl = `http://127.0.0.1:${port}`;
    this.#agent = spawn(BWRAP, this.#argv(["sandbox-agent", "server", "--host", "127.0.0.1", "--port", String(port), "--token", this.#agentToken, "--no-telemetry"], {}, "/sbx"), { stdio: ["pipe", "pipe", "pipe"] });
    this.#agent.stdout.resume(); this.#agent.stderr.resume();
    await waitHttp(`${this.#agentUrl}/v1/health`, this.#agentToken, 30_000);
  }

  sandboxAgent() { return { baseUrl: this.#agentUrl, token: this.#agentToken }; }

  spawnStdio(argv: string[], opts: { cwd?: string; env?: Record<string, string> } = {}) {
    const p = spawn(BWRAP, this.#argv(argv, opts.env ?? {}, opts.cwd), { stdio: ["pipe", "pipe", "pipe"] });
    this.#children.add(p); p.once("exit", () => this.#children.delete(p));
    return p;
  }

  exec(argv: string[], opts: { cwd?: string; env?: Record<string, string>; timeoutMs?: number; input?: string } = {}) {
    return execCapture(BWRAP, this.#argv(argv, opts.env ?? {}, opts.cwd), { timeoutMs: opts.timeoutMs ?? 600_000, input: opts.input });
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
    return { leftovers };
  }
}
