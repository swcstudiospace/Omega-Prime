// bwrap tier: labelled fallback when the OpenShell MicroVM driver cannot boot on this host.
// Read-only /usr and /etc, empty /home except read-only omp/Bun/Node toolchains, a private writable
// sandbox root (/sbx: work, home, bin), private /tmp, new pid/ipc/uts namespaces, die-with-parent.
// NOT isolated: network (shared, needed for the loopback auth-broker and the model APIs) and the kernel.
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, symlinkSync, rmSync, writeFileSync, chmodSync } from "node:fs";
import { basename, dirname, join } from "node:path";
import { randomBytes } from "node:crypto";
import { STATE, NODE24_BIN, BUN_EXE, OMP_BIN, SANDBOX_AGENT_BIN, HOME, realBin, toolchainRoot } from "../config.ts";
import { freePort } from "../auth.ts";
import { execCapture, pidAlive, rid, waitHttp, launched } from "./util.ts";
import { TierUnavailable, type SandboxTier } from "./types.ts";
import { pinDir, unpinDir, PathRefused, type PinnedDir } from "../paths.ts";

const BWRAP = "/usr/bin/bwrap";
/** Sandbox-side env file: the tier env (broker URL/token, opted-in API keys) is sourced from here, never put on argv. */
export const BWRAP_ENV_FILE = "/sbx/home/.omp-sandbox.env";

/** `export K='v'` lines for a POSIX shell; names must be plain identifiers. */
export function envFileText(env: Record<string, string>) {
  return Object.entries(env).map(([k, v]) => {
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(k)) throw new Error(`bad env name ${JSON.stringify(k)}`);
    return `export ${k}='${String(v).replace(/'/g, `'\\''`)}'\n`;
  }).join("");
}

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
  #agent: ChildProcessWithoutNullStreams | null = null;
  #agentUrl = ""; #agentToken = randomBytes(18).toString("hex");
  #children = new Set<ChildProcessWithoutNullStreams>();
  /** bwrap processes started by exec (checks, probes); --die-with-parent takes their sandboxes down with them. */
  #execPids = new Set<number>();
  /** The checked worktree, held open: every bwrap binds this descriptor (fd 3), never the path name again. */
  #pin: PinnedDir | null = null;

  constructor(hostRoot?: string, workspaceHostPath?: string | null) { this.hostRoot = hostRoot ?? join(STATE, "ws", this.id); this.workspaceHostPath = workspaceHostPath ?? null; }

  /** bwrap argv for one command. Only the fixed base env and the per-call `env` (non-secret: CI, PYTHONPATH) ride
   *  as --setenv; the tier env is sourced inside the sandbox from BWRAP_ENV_FILE so no secret reaches a host
   *  process listing. */
  argv(cmd: string[], env: Record<string, string>, cwd = this.workdir) {
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
    for (const [k, v] of Object.entries({ ...base, ...env })) a.push("--setenv", k, v);
    a.push("--chdir", cwd, "--", "/bin/sh", "-c", `[ -f ${BWRAP_ENV_FILE} ] && . ${BWRAP_ENV_FILE}; exec "$@"`, "sh", ...cmd);
    return a;
  }

  async start(env: Record<string, string>) {
    if (!existsSync(BWRAP)) throw new TierUnavailable("bwrap", "bubblewrap not installed");
    if (!existsSync(SANDBOX_AGENT_BIN)) throw new TierUnavailable("bwrap", `sandbox-agent binary missing at ${SANDBOX_AGENT_BIN}`);
    this.pinWorkspace();
    for (const d of ["work", "home", "bin"]) mkdirSync(join(this.hostRoot, d), { recursive: true });
    this.writeEnvFile(env);
    const link = (target: string, name: string) => { const p = join(this.hostRoot, "bin", name); if (!existsSync(p)) symlinkSync(target, p); };
    // Link the real launchers (the configured omp and the Bun that runs it); their folders are bound read-only above.
    link(realBin(BUN_EXE), "bun"); link(realBin(OMP_BIN), "omp");
    const probe = await execCapture(BWRAP, this.argv(["sh", "-c", "omp --version && node -v && test ! -e " + join(HOME, ".omp") + " && echo NO_HOST_OMP_STORE"], {}), { timeoutMs: 60_000, extraFds: this.#fds() });
    if (probe.code !== 0 || !probe.stdout.includes("NO_HOST_OMP_STORE")) throw new TierUnavailable("bwrap", `probe failed: ${probe.stderr.slice(-400)}`);
    const port = await freePort();
    this.#agentUrl = `http://127.0.0.1:${port}`;
    this.#agent = spawn(BWRAP, this.argv(["sandbox-agent", "server", "--host", "127.0.0.1", "--port", String(port), "--token", this.#agentToken, "--no-telemetry"], {}, "/sbx"), { stdio: ["pipe", "pipe", "pipe", ...this.#fds()] }) as ChildProcessWithoutNullStreams;
    this.#agent.stdout.resume(); this.#agent.stderr.resume();
    await launched(this.#agent, "bwrap sandbox-agent", waitHttp(`${this.#agentUrl}/v1/health`, this.#agentToken, 30_000));
  }

  /** Write the tier env to the private home as a 0600 file (the host side of BWRAP_ENV_FILE). */
  writeEnvFile(env: Record<string, string>) {
    const p = join(this.hostRoot, "home", ".omp-sandbox.env");
    mkdirSync(dirname(p), { recursive: true });
    writeFileSync(p, envFileText(env), { mode: 0o600 }); chmodSync(p, 0o600);
  }

  /** Hold the checked worktree open (done by start()); from here on every bwrap binds this descriptor. */
  pinWorkspace() {
    if (!this.workspaceHostPath || this.#pin) return;
    try { this.#pin = pinDir(this.workspaceHostPath, "workspace_host_path"); }
    catch (e) { throw e instanceof PathRefused ? new TierUnavailable("bwrap", e.message) : e; }
  }

  sandboxAgent() { return { baseUrl: this.#agentUrl, token: this.#agentToken }; }

  /** Sandbox path -> host path: /sbx/... lives under hostRoot (the work dir under a mounted workspace). */
  #host(path: string) {
    if (path.includes("..") || !path.startsWith(`${this.sandboxRoot}/`)) throw new Error(`path outside the sandbox root: ${path}`);
    if (this.workspaceHostPath && (path === this.workdir || path.startsWith(`${this.workdir}/`))) return this.workspaceHostPath + path.slice(this.workdir.length);
    return join(this.hostRoot, path.slice(this.sandboxRoot.length));
  }
  async upload(local: string, dest: string) { copyFileSync(local, this.#host(dest)); return { code: 0, stdout: "", stderr: "" }; }
  async download(path: string, destDir: string) { copyFileSync(this.#host(path), join(destDir, basename(path))); return { code: 0, stdout: "", stderr: "" }; }

  spawnStdio(argv: string[], opts: { cwd?: string; env?: Record<string, string> } = {}) {
    const p = spawn(BWRAP, this.argv(argv, opts.env ?? {}, opts.cwd), { stdio: ["pipe", "pipe", "pipe", ...this.#fds()] }) as ChildProcessWithoutNullStreams;
    this.#children.add(p); p.once("exit", () => this.#children.delete(p));
    return p;
  }

  exec(argv: string[], opts: { cwd?: string; env?: Record<string, string>; timeoutMs?: number; input?: string } = {}) {
    return execCapture(BWRAP, this.argv(argv, opts.env ?? {}, opts.cwd), { timeoutMs: opts.timeoutMs ?? 600_000, input: opts.input, extraFds: this.#fds(), onSpawn: (pid) => this.#execPids.add(pid) })
      .finally(() => { for (const pid of [...this.#execPids]) if (!pidAlive(pid)) this.#execPids.delete(pid); });
  }

  /** The pinned worktree descriptor for the child's fd 3 (bwrap closes it after binding, so the job never holds it). */
  #fds(): number[] {
    if (!this.workspaceHostPath) return [];
    if (!this.#pin) throw new Error("bwrap workspace is not pinned (start() not run or already disposed)");
    return [this.#pin.fd];
  }

  owned() { return { hostRoot: this.hostRoot, sandboxAgentPid: this.#agent?.pid ?? null, children: [...this.#children].map((c) => c.pid), execs: [...this.#execPids].filter(pidAlive) }; }

  async dispose() {
    const execs = [...this.#execPids].filter(pidAlive);
    const pids = [this.#agent?.pid, ...[...this.#children].map((c) => c.pid), ...execs].filter(Boolean) as number[];
    for (const c of [...this.#children]) c.kill("SIGTERM");
    for (const pid of execs) try { process.kill(pid, "SIGTERM"); } catch { /* gone */ }
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
