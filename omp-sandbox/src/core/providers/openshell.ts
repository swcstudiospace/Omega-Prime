// OpenShell tier (NVIDIA OpenShell 0.1.4 CLI). Preferred tier; drives `openshell sandbox create/exec/
// upload/download/delete` against the local gateway (MicroVM driver). On this box the MicroVM driver
// crashes the host kernel (kvm_spurious_fault in vmx_vcpu_create: nested VMX unsupported), so
// availability() refuses unless OMP_SANDBOX_OPENSHELL_OK=1 is set after the host is fixed.
// Live-verified here: CLI install, gateway (mTLS), image pull + rootfs prep. NOT live-verified: boot,
// exec stdio streaming, forward, upload/download (no VM ever came up).
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { existsSync } from "node:fs";
import { randomBytes } from "node:crypto";
import { OPENSHELL_BIN, ROOT } from "../config.ts";
import { freePort } from "../auth.ts";
import { execCapture, launched, rid, waitHttp } from "./util.ts";
import { BrokerRelay, SANDBOX_RELAY_JS } from "./broker-relay.ts";
import { TierUnavailable, type SandboxTier } from "./types.ts";

const OS_ENV = { ...process.env, OPENSHELL_TELEMETRY_ENABLED: "false" } as Record<string, string>;
const os = (args: string[], o: { timeoutMs?: number; input?: string } = {}) => execCapture(OPENSHELL_BIN, args, { env: OS_ENV, ...o });
const ENV_FILE = "/sandbox/home/.omp-sandbox.env";
const RELAY_PORT = 18990;           // guest-local loopback only
const SECRET = /TOKEN|KEY|SECRET|PASSWORD/i;
/** bootstrap.sh installs everything under /sandbox/opt; non-login execs get these paths explicitly. */
export const GUEST_PATH = "/sandbox/opt/bin:/sandbox/opt/node/bin:/sandbox/opt/omp/bin:/sandbox/opt/bun/bin:/usr/local/bin:/usr/bin:/bin";
/** Base env for every guest command (bootstrap, sandbox-agent, relay, stages). Secrets never go here. */
export const GUEST_BASE = { HOME: "/sandbox/home", PATH: GUEST_PATH, LANG: "C.UTF-8", TERM: "dumb", OMP_SANDBOX: "1", npm_config_cache: "/sandbox/home/.npm" };

export async function openshellAvailability(): Promise<{ ok: boolean; detail: string }> {
  if (!existsSync(OPENSHELL_BIN)) return { ok: false, detail: `openshell CLI not at ${OPENSHELL_BIN}` };
  if (process.env.OMP_SANDBOX_OPENSHELL_OK !== "1")
    return { ok: false, detail: "MicroVM driver disabled on this host: libkrun vCPU creation hit `kernel BUG at arch/x86/kvm/x86.c:702` (nested VMX). Set OMP_SANDBOX_OPENSHELL_OK=1 on a host with working KVM." };
  const s = await os(["status"], { timeoutMs: 15_000 });
  if (s.code !== 0 || !/Connected/.test(s.stdout)) return { ok: false, detail: `gateway not connected: ${(s.stderr || s.stdout).slice(-300)}` };
  return { ok: true, detail: "gateway connected" };
}

export class OpenShellTier implements SandboxTier {
  readonly name = "openshell" as const;
  readonly id = rid("os");
  readonly sandboxRoot = "/sandbox";
  readonly workdir = "/sandbox/work";
  readonly isolation = "OpenShell MicroVM (libkrun) + OpenShell policy (sandbox/policy.yaml)";
  #env: Record<string, string> = {};
  #fwd: ChildProcessWithoutNullStreams | null = null;
  #relay: BrokerRelay | null = null;
  #brokerTarget: { host: string; port: number } | null = null;
  #url = ""; #token = randomBytes(18).toString("hex");
  #children = new Set<ChildProcessWithoutNullStreams>();
  #created = false;

  async start(env: Record<string, string>) {
    const a = await openshellAvailability();
    if (!a.ok) throw new TierUnavailable("openshell", a.detail);
    // The host broker listens on host 127.0.0.1, which inside the MicroVM is the guest itself. Guests reach it
    // through the same in-sandbox relay the Podman tier uses, so the staged URL points at the guest-local relay.
    const u = env.OMP_AUTH_BROKER_URL ? new URL(env.OMP_AUTH_BROKER_URL) : null;
    this.#brokerTarget = u ? { host: u.hostname, port: Number(u.port) } : null;
    this.#env = { ...env, ...(u ? { OMP_AUTH_BROKER_URL: `http://127.0.0.1:${RELAY_PORT}` } : {}) };
    const c = await os(["sandbox", "create", "--name", this.id, "--detach", "--no-tty", "--no-auto-providers", "--policy", `${ROOT}/sandbox/policy.yaml`], { timeoutMs: 900_000 });
    if (c.code !== 0) throw new TierUnavailable("openshell", `create failed: ${c.stderr.slice(-500)}`);
    this.#created = true;
    // Stage the env file first (secrets via stdin into a 0600 file; never argv), so every later exec can load it.
    const envLines = Object.entries(this.#env).map(([k, v]) => `export ${k}='${String(v).replace(/'/g, "'\\''")}'`).join("\n") + "\n";
    const w = await execCapture(OPENSHELL_BIN, this.#raw(["sh", "-c", `umask 077; mkdir -p /sandbox/home /sandbox/work; cat > ${ENV_FILE}`], "/sandbox"), { env: OS_ENV, input: envLines, timeoutMs: 60_000 });
    if (w.code !== 0) throw new TierUnavailable("openshell", "could not stage sandbox env file (stdin over exec)");
    const up = await os(["sandbox", "upload", this.id, `${ROOT}/sandbox/bootstrap.sh`, "/sandbox/bootstrap.sh"], { timeoutMs: 120_000 });
    if (up.code !== 0) throw new TierUnavailable("openshell", `upload failed: ${up.stderr.slice(-300)}`);
    // Bootstrap needs no secrets: run it without loading the env file.
    const boot = await execCapture(OPENSHELL_BIN, this.#raw(["bash", "/sandbox/bootstrap.sh"], "/sandbox"), { env: OS_ENV, timeoutMs: 1_800_000 });
    if (boot.code !== 0) throw new TierUnavailable("openshell", `bootstrap failed: ${boot.stderr.slice(-500)}`);
    if (this.#brokerTarget) {
      const rp = spawn(OPENSHELL_BIN, this.#raw(["node", "-e", SANDBOX_RELAY_JS], "/sandbox", { OMP_SBX_RELAY_PORT: String(RELAY_PORT) }), { env: OS_ENV, stdio: ["pipe", "pipe", "pipe"] });
      this.#relay = new BrokerRelay(rp, this.#brokerTarget);
      await launched(rp, "broker relay", this.#relay.start(60_000));
    }
    const port = await freePort();
    const agent = spawn(OPENSHELL_BIN, this.#raw(["sandbox-agent", "server", "--host", "127.0.0.1", "--port", "2468", "--token", this.#token, "--no-telemetry"], "/sandbox"), { env: OS_ENV, stdio: ["pipe", "pipe", "pipe"] });
    this.#children.add(agent); agent.once("exit", () => this.#children.delete(agent));
    this.#fwd = spawn(OPENSHELL_BIN, ["forward", "start", this.id, `${port}:2468`], { env: OS_ENV, stdio: ["pipe", "pipe", "pipe"] });
    this.#url = `http://127.0.0.1:${port}`;
    await launched(agent, "sandbox-agent", launched(this.#fwd, "openshell forward", waitHttp(`${this.#url}/v1/health`, this.#token, 60_000)));
  }
  /** Base `openshell sandbox exec` argv: explicit guest PATH/HOME, non-secret env only, no env-file sourcing. */
  #raw(argv: string[], cwd: string, extraEnv: Record<string, string> = {}) {
    const a = ["sandbox", "exec", "-n", this.id, "--no-tty", "--no-login-shell", "--workdir", cwd];
    for (const [k, v] of Object.entries({ ...GUEST_BASE, ...extraEnv })) if (!SECRET.test(k)) a.push("--env", `${k}=${v}`);
    return [...a, "--", ...argv];
  }
  sandboxAgent() { return { baseUrl: this.#url, token: this.#token }; }
  #execArgs(argv: string[], opts: { cwd?: string; env?: Record<string, string> }) {
    // Non-secret env via --env; secrets only from the guest's 0600 env file. `[ -f ] &&` keeps sh alive if it is absent.
    const extra = Object.fromEntries(Object.entries({ ...this.#env, ...(opts.env ?? {}) }).filter(([k]) => !SECRET.test(k)));
    return this.#raw(["sh", "-c", `[ -f ${ENV_FILE} ] && . ${ENV_FILE}; exec "$@"`, "sh", ...argv], opts.cwd ?? this.workdir, extra);
  }
  spawnStdio(argv: string[], opts: { cwd?: string; env?: Record<string, string> } = {}) {
    const p = spawn(OPENSHELL_BIN, this.#execArgs(argv, opts), { env: OS_ENV, stdio: ["pipe", "pipe", "pipe"] });
    this.#children.add(p); p.once("exit", () => this.#children.delete(p)); return p;
  }
  exec(argv: string[], opts: { cwd?: string; env?: Record<string, string>; timeoutMs?: number; input?: string } = {}) {
    return execCapture(OPENSHELL_BIN, this.#execArgs(argv, opts), { env: OS_ENV, timeoutMs: opts.timeoutMs ?? 600_000, input: opts.input });
  }
  owned() { return { sandbox: this.#created ? this.id : null, forwardPid: this.#fwd?.pid ?? null, relayPid: this.#relay?.proc.pid ?? null }; }
  async dispose() {
    for (const c of [...this.#children]) c.kill("SIGTERM"); this.#fwd?.kill("SIGTERM"); this.#relay?.stop();
    const leftovers: string[] = [];
    if (this.#created) {
      const d = await os(["sandbox", "delete", this.id], { timeoutMs: 120_000 });
      const l = await os(["sandbox", "list"], { timeoutMs: 30_000 });
      if (d.code !== 0 || l.stdout.includes(this.id)) leftovers.push(`openshell sandbox ${this.id}`);
    }
    return { leftovers };
  }
}
