// OpenShell tier on the rootless Podman compute driver (NVIDIA OpenShell 0.1.4). Preferred tier on
// hosts without usable KVM (this box is itself a VM; the MicroVM driver crashed it). No VM driver,
// no libkrun, no root: Podman 5.x rootless (pasta networking, cgroups v2) behind a loopback mTLS gateway.
//
// Isolation: per sandbox, OpenShell runs a workload container (network=none, Landlock filesystem
// policy, non-root uid 1000) plus a supervisor container that is the only egress path and enforces
// the default-deny network policy in sandbox/policy-podman.yaml (model providers for Bun/omp, npm
// registry for Node/npx). The host auth-broker is reached through BrokerRelay (sandbox-local
// loopback over the exec channel) because OpenShell never authorizes loopback destinations.
//
// The tier brings up what is missing and tears down only what it started: the Podman API socket
// (`podman system service`), the gateway (`openshell-gateway --compute-driver podman`), the
// workload image (built from sandbox/image/Containerfile if absent), then one sandbox per `up`.
import { spawn, type ChildProcess, type ChildProcessWithoutNullStreams } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, openSync, writeFileSync, readFileSync, rmSync, symlinkSync } from "node:fs";
import { request } from "node:http";
import { dirname, join } from "node:path";
import { homedir } from "node:os";
import { randomBytes } from "node:crypto";
import { OPENSHELL_BIN, OPENSHELL_GATEWAY_BIN, OPENSHELL_TLS, OPENSHELL_GATEWAY_PORT, OPENSHELL_MOUNT_GATEWAY_PORT, PODMAN_BIN, PODMAN_IMAGE, ROOT, STATE, SANDBOX_AGENT_BIN } from "../config.ts";
import { execCapture, pidAlive, rid, waitHttp } from "./util.ts";
import { BrokerRelay, SANDBOX_RELAY_JS } from "./broker-relay.ts";
import { TierUnavailable, type SandboxTier } from "./types.ts";

const OS_ENV = { ...process.env, OPENSHELL_TELEMETRY_ENABLED: "false" } as Record<string, string>;
/** Default-gateway CLI env, or the mount gateway's (selected by endpoint, never by changing the active gateway). */
const osEnv = (port: number | null): Record<string, string> => port === null ? OS_ENV : { ...OS_ENV, OPENSHELL_GATEWAY_ENDPOINT: `https://127.0.0.1:${port}` };

/** Podman driver config that bind-mounts a validated host worktree read-write at /sandbox/work. */
export function mountDriverConfig(hostPath: string, target = "/sandbox/work") {
  return { podman: { mounts: [{ type: "bind", source: hostPath, target, read_only: false }] } };
}

/** Gateway TOML. The mount variant adds exactly the settings bind mounts need; the default gateway keeps them off. */
export function gatewayToml(sock: string, port: number, mount: boolean) {
  return [
    "[openshell]", "version = 2", "",
    "[openshell.gateway]", 'compute_driver = "podman"', "disable_tls = false", `guest_tls_ca = "${OPENSHELL_TLS}/ca.crt"`, "",
    "[openshell.drivers.podman]", `socket_path = "${sock}"`, 'network_name = "openshell"', `default_image = "${PODMAN_IMAGE}"`,
    'image_pull_policy = "if_not_present"', `grpc_endpoint = "https://127.0.0.1:${port}"`, "stop_timeout_secs = 10",
    ...(mount ? ["allow_driver_config = true", "enable_bind_mounts = true", 'userns = "keep-id"', "", "[openshell.drivers.podman.resource_admission]", "enabled = false"] : []), "",
    "[openshell.gateway.gateway_jwt]", `signing_key_path = "${OPENSHELL_TLS}/jwt/signing.pem"`, `public_key_path = "${OPENSHELL_TLS}/jwt/public.pem"`,
    `kid_path = "${OPENSHELL_TLS}/jwt/kid"`, `gateway_id = "omp-sandbox-podman${mount ? "-mnt" : ""}"`, "",
  ].join("\n");
}

/**
 * The CLI reads mTLS client material for an endpoint from <config>/openshell/gateways/https___127.0.0.1_<port>/mtls.
 * Point that at the active gateway's mtls dir (same loopback PKI) with a symlink; key bytes are never copied or read.
 */
function ensureEndpointRegistration(port: number) {
  const base = join(process.env.XDG_CONFIG_HOME ?? join(homedir(), ".config"), "openshell");
  const dir = join(base, "gateways", `https___127.0.0.1_${port}`);
  if (existsSync(join(dir, "mtls"))) return;
  const active = existsSync(join(base, "active_gateway")) ? readFileSync(join(base, "active_gateway"), "utf8").trim() : "";
  if (!active || active.includes("/") || !existsSync(join(base, "gateways", active, "mtls"))) throw new TierUnavailable("openshell-podman", "no active OpenShell gateway registration to borrow loopback mTLS from");
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  symlinkSync(join("..", active, "mtls"), join(dir, "mtls"));
}
const pm = (args: string[], o: { timeoutMs?: number } = {}) => execCapture(PODMAN_BIN, args, { env: process.env as any, ...o });
const GW_STATE = join(STATE, "openshell-podman");
const ENV_FILE = "/sandbox/home/.omp-sandbox.env";
const RELAY_PORT = 18990;           // sandbox-local loopback only
const AGENT_PORT = 2468;            // sandbox-local loopback only, reached via `forward service`
const SECRET = /TOKEN|KEY|SECRET|PASSWORD/i;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function ping(socketPath: string) {
  return new Promise<boolean>((res) => {
    const r = request({ socketPath, path: "/v5.0.0/libpod/_ping", timeout: 3000 }, (s) => { s.resume(); res(s.statusCode === 200); });
    r.on("error", () => res(false)); r.on("timeout", () => { r.destroy(); res(false); }); r.end();
  });
}

async function stopProc(p: ChildProcess | null, graceMs = 10_000) {
  if (!p || p.exitCode !== null || p.signalCode !== null) return;
  const gone = new Promise((r) => p.once("exit", r));
  p.kill("SIGTERM"); const t = setTimeout(() => p.kill("SIGKILL"), graceMs);
  await gone; clearTimeout(t);
}

export async function podmanRootless() {
  if (!existsSync(PODMAN_BIN)) return { ok: false, detail: `podman not installed at ${PODMAN_BIN}` } as const;
  const r = await pm(["info", "--format", "{{.Host.Security.Rootless}}|{{.Host.CgroupsVersion}}|{{.Host.RemoteSocket.Path}}|{{.Version.Version}}|{{.Host.Pasta.Executable}}"], { timeoutMs: 60_000 });
  const [rootless, cg, sock, ver, pasta] = r.stdout.trim().split("|");
  if (r.code !== 0) return { ok: false, detail: `podman info failed: ${r.stderr.slice(-300)}` } as const;
  if (rootless !== "true") return { ok: false, detail: "podman is not rootless (refusing root podman)" } as const;
  if (cg !== "v2") return { ok: false, detail: `cgroups ${cg}, OpenShell podman driver needs v2` } as const;
  if (!ver?.startsWith("5.")) return { ok: false, detail: `podman ${ver}, OpenShell needs 5.x` } as const;
  return { ok: true, socket: sock, version: ver, pasta } as const;
}

export class OpenShellPodmanTier implements SandboxTier {
  readonly name = "openshell-podman" as const;
  readonly id = rid("osp");
  readonly sandboxRoot = "/sandbox";
  readonly workdir = "/sandbox/work";
  readonly isolation = "OpenShell 0.1.4 podman driver: rootless Podman (userns, uid 1000), workload container network=none + Landlock fs policy, supervisor-enforced default-deny egress (sandbox/policy-podman.yaml); shared host kernel";
  #env: Record<string, string> = {};
  #brokerTarget: { host: string; port: number } | null = null;
  #svc: ChildProcess | null = null;           // podman system service, if we started it
  #gw: ChildProcess | null = null;            // openshell-gateway, if we started it
  #relay: BrokerRelay | null = null;
  #fwd: ChildProcessWithoutNullStreams | null = null;
  #agentUrl = ""; #agentToken = randomBytes(18).toString("hex");
  #children = new Set<ChildProcessWithoutNullStreams>();
  #created = false;
  info: Record<string, unknown> = {};
  readonly workspaceHostPath: string | null;
  readonly #port: number;
  readonly #osEnv: Record<string, string>;
  readonly #gwState: string;

  /** `workspaceHostPath` must already be allowlist-resolved (core.ts allowedDir); it selects the mount gateway. */
  constructor(workspaceHostPath?: string | null) {
    this.workspaceHostPath = workspaceHostPath ?? null;
    this.#port = this.workspaceHostPath ? OPENSHELL_MOUNT_GATEWAY_PORT : OPENSHELL_GATEWAY_PORT;
    this.#osEnv = osEnv(this.workspaceHostPath ? this.#port : null);
    this.#gwState = this.workspaceHostPath ? `${GW_STATE}-mnt` : GW_STATE;
  }
  #os(args: string[], o: { timeoutMs?: number; input?: string } = {}) { return execCapture(OPENSHELL_BIN, args, { env: this.#osEnv, ...o }); }

  async start(env: Record<string, string>) {
    if (!existsSync(OPENSHELL_BIN) || !existsSync(OPENSHELL_GATEWAY_BIN)) throw new TierUnavailable("openshell-podman", `OpenShell binaries missing next to ${OPENSHELL_BIN}`);
    const pr = await podmanRootless();
    if (!pr.ok) throw new TierUnavailable("openshell-podman", pr.detail);
    this.info.podman = { version: pr.version, rootless: true, pasta: pr.pasta };
    await this.#ensureSocket(pr.socket!);
    await this.#ensureImage();
    await this.#ensureGateway(pr.socket!);

    // Broker: sandboxed omp talks to 127.0.0.1:RELAY_PORT inside the sandbox.
    const u = env.OMP_AUTH_BROKER_URL ? new URL(env.OMP_AUTH_BROKER_URL) : null;
    this.#brokerTarget = u ? { host: u.hostname, port: Number(u.port) } : null;
    this.#env = { ...env, ...(u ? { OMP_AUTH_BROKER_URL: `http://127.0.0.1:${RELAY_PORT}` } : {}) };

    const mount = this.workspaceHostPath ? ["--driver-config-json", JSON.stringify(mountDriverConfig(this.workspaceHostPath, this.workdir))] : [];
    const c = await this.#os(["sandbox", "create", "--name", this.id, "--from", PODMAN_IMAGE, "--policy", join(ROOT, "sandbox", "policy-podman.yaml"), "--detach", "--no-tty", "--no-auto-providers", ...mount], { timeoutMs: 600_000 });
    if (c.code !== 0) throw new TierUnavailable("openshell-podman", `sandbox create failed: ${(c.stderr || c.stdout).slice(-500)}`);
    this.#created = true;
    if (this.workspaceHostPath) this.info.workspace = { hostPath: this.workspaceHostPath, sandboxPath: this.workdir, mode: "bind-rw", userns: "keep-id", gatewayPort: this.#port };
    await this.#waitReady();
    const ctrs = await this.containers();
    if (!ctrs.some((n) => n.startsWith("openshell-supervisor-")) || !ctrs.some((n) => n.startsWith("openshell-"))) throw new TierUnavailable("openshell-podman", `gateway did not create Podman containers for ${this.id} (is it using another driver?)`);
    this.info.containers = ctrs;

    // Stage env (secrets only via stdin into a 0600 file inside the sandbox; never argv, never logs).
    const envLines = Object.entries(this.#env).map(([k, v]) => `export ${k}='${String(v).replace(/'/g, "'\\''")}'`).join("\n") + "\n";
    const w = await execCapture(OPENSHELL_BIN, this.#raw(["sh", "-c", `umask 077; mkdir -p /sandbox/home /sandbox/work; cat > ${ENV_FILE}`], "/sandbox"), { env: this.#osEnv, input: envLines, timeoutMs: 120_000 });
    if (w.code !== 0) throw new TierUnavailable("openshell-podman", `could not stage sandbox env file via exec stdin: ${w.stderr.slice(-300)}`);

    if (this.#brokerTarget) {
      const rp = spawn(OPENSHELL_BIN, this.#raw(["node", "-e", SANDBOX_RELAY_JS], "/sandbox", { OMP_SBX_RELAY_PORT: String(RELAY_PORT) }), { env: this.#osEnv, stdio: ["pipe", "pipe", "pipe"] });
      this.#relay = new BrokerRelay(rp, this.#brokerTarget);
      await this.#relay.start(60_000);
    }

    mkdirSync(this.#gwState, { recursive: true });
    const agentLog = openSync(join(this.#gwState, `${this.id}-sandbox-agent.log`), "w");
    const agent = spawn(OPENSHELL_BIN, this.#raw(["sandbox-agent", "server", "--host", "127.0.0.1", "--port", String(AGENT_PORT), "--token", this.#agentToken, "--no-telemetry"], "/sandbox", process.env.OMP_SANDBOX_AGENT_LOG ? { RUST_LOG: process.env.OMP_SANDBOX_AGENT_LOG } : {}), { env: this.#osEnv, stdio: ["pipe", agentLog, agentLog] });
    this.#children.add(agent as any);
    this.#fwd = spawn(OPENSHELL_BIN, ["forward", "service", "--target-port", String(AGENT_PORT), "--local", "127.0.0.1:0", this.id], { env: this.#osEnv, stdio: ["pipe", "pipe", "pipe"] });
    const port = await new Promise<number>((resolve, reject) => {
      let buf = ""; const t = setTimeout(() => reject(new TierUnavailable("openshell-podman", `forward service did not start: ${buf.slice(-300)}`)), 60_000);
      const on = (b: Buffer) => { buf += b; const m = buf.match(/Forwarding 127\.0\.0\.1:(\d+)/); if (m) { clearTimeout(t); resolve(Number(m[1])); } };
      this.#fwd!.stdout.on("data", on); this.#fwd!.stderr.on("data", on);
      this.#fwd!.once("exit", (code) => { clearTimeout(t); reject(new TierUnavailable("openshell-podman", `forward service exited (${code}): ${buf.slice(-300)}`)); });
    });
    this.#agentUrl = `http://127.0.0.1:${port}`;
    await waitHttp(`${this.#agentUrl}/v1/health`, this.#agentToken, 60_000);
  }

  async #ensureSocket(sock: string) {
    if (await ping(sock)) { this.info.podmanSocket = { path: sock, startedByTier: false }; return; }
    mkdirSync(dirname(sock), { recursive: true, mode: 0o700 }); mkdirSync(GW_STATE, { recursive: true });
    const log = openSync(join(GW_STATE, "podman-service.log"), "a");
    this.#svc = spawn(PODMAN_BIN, ["system", "service", "--time=0", `unix://${sock}`], { stdio: ["ignore", log, log] });
    for (let i = 0; i < 60; i++) { if (await ping(sock)) { this.info.podmanSocket = { path: sock, startedByTier: true, pid: this.#svc.pid }; return; } await sleep(500); }
    throw new TierUnavailable("openshell-podman", `podman API socket did not answer at ${sock}`);
  }

  async #ensureImage() {
    if ((await pm(["image", "exists", PODMAN_IMAGE])).code === 0) { this.info.image = { ref: PODMAN_IMAGE, built: false }; return; }
    const ctx = join(ROOT, "sandbox", "image");
    copyFileSync(SANDBOX_AGENT_BIN, join(ctx, "sandbox-agent"));
    const b = await pm(["build", "-t", PODMAN_IMAGE, ctx], { timeoutMs: 1_800_000 });
    rmSync(join(ctx, "sandbox-agent"), { force: true });
    if (b.code !== 0) throw new TierUnavailable("openshell-podman", `image build failed: ${b.stderr.slice(-500)}`);
    this.info.image = { ref: PODMAN_IMAGE, built: true };
  }

  async #ensureGateway(sock: string) {
    if (this.workspaceHostPath) ensureEndpointRegistration(this.#port);
    const s = await this.#os(["status"], { timeoutMs: 15_000 });
    if (s.code === 0 && /Connected/.test(s.stdout)) { this.info.gateway = { startedByTier: false, endpoint: `https://127.0.0.1:${this.#port}`, mountGateway: !!this.workspaceHostPath }; return; }
    mkdirSync(this.#gwState, { recursive: true });
    const cfg = join(this.#gwState, "gateway.toml");
    writeFileSync(cfg, gatewayToml(sock, this.#port, !!this.workspaceHostPath));
    const logPath = join(this.#gwState, "gateway.log"); const log = openSync(logPath, "w");
    this.#gw = spawn(OPENSHELL_GATEWAY_BIN, ["--config", cfg, "--compute-driver", "podman", "--tls-cert", `${OPENSHELL_TLS}/server/tls.crt`, "--tls-key", `${OPENSHELL_TLS}/server/tls.key`, "--tls-client-ca", `${OPENSHELL_TLS}/ca.crt`, "--db-url", `sqlite:${join(this.#gwState, "gateway.db")}?mode=rwc`, "--bind-address", "127.0.0.1", "--port", String(this.#port), "--health-port", "0", "--metrics-port", "0"], { env: this.#osEnv, stdio: ["ignore", log, log] });
    for (let i = 0; i < 120; i++) {
      await sleep(500);
      if (this.#gw.exitCode !== null) throw new TierUnavailable("openshell-podman", `gateway exited: ${readFileSync(logPath, "utf8").slice(-500)}`);
      const l = readFileSync(logPath, "utf8");
      if (/Using compute driver driver=podman/.test(l) && /Gateway listener bound/.test(l)) {
        const st = await this.#os(["status"], { timeoutMs: 15_000 });
        if (/Connected/.test(st.stdout)) { this.info.gateway = { startedByTier: true, pid: this.#gw.pid, driver: "podman", endpoint: `https://127.0.0.1:${this.#port}`, mtls: true, mountGateway: !!this.workspaceHostPath }; return; }
      }
    }
    throw new TierUnavailable("openshell-podman", "gateway did not become ready with the podman driver");
  }

  /** Sandboxes on this gateway other than ours. Unknown (gateway unreachable) counts as "none" only if the gateway is gone. */
  async #otherSandboxes(): Promise<string[]> {
    const l = await os(["sandbox", "list"], { timeoutMs: 30_000 });
    if (l.code !== 0) return this.#gw && this.#gw.exitCode === null ? ["<unknown: sandbox list failed>"] : [];
    return l.stdout.split("\n").map((x) => x.trim().split(/\s+/)[0] ?? "").filter((n) => n && n !== this.id && !/^(NAME|No)$/i.test(n));
  }

  async #waitReady() {
    for (let i = 0; i < 240; i++) {
      const l = await this.#os(["sandbox", "list"], { timeoutMs: 30_000 });
      const row = l.stdout.split("\n").find((x) => x.startsWith(this.id + " "));
      if (row && /\bReady\b/.test(row)) return;
      if (row && /Error|Failed/i.test(row)) throw new TierUnavailable("openshell-podman", `sandbox ${this.id}: ${row.trim()}`);
      await sleep(1000);
    }
    throw new TierUnavailable("openshell-podman", `sandbox ${this.id} not Ready`);
  }

  async containers() {
    // Workload container: "openshell-<workspace>--<name>-<uuid>"; its supervisor is "openshell-supervisor-<uuid>".
    const r = await pm(["ps", "-a", "--format", "{{.Names}}"], { timeoutMs: 30_000 });
    const names = r.stdout.split("\n").filter(Boolean); const key = `--${this.id}-`;
    const uuids = names.filter((n) => n.includes(key)).map((n) => n.slice(n.indexOf(key) + key.length));
    return names.filter((n) => n.includes(key) || uuids.some((u) => n.endsWith(`-${u}`)));
  }

  /** Base `openshell sandbox exec` argv (no env-file sourcing). No exec timeout: --timeout 0. */
  #raw(argv: string[], cwd: string, extraEnv: Record<string, string> = {}) {
    const a = ["sandbox", "exec", "-n", this.id, "--no-tty", "--no-login-shell", "--timeout", "0", "--workdir", cwd];
    const base: Record<string, string> = { HOME: "/sandbox/home", PATH: "/opt/bun/bin:/opt/node/bin:/usr/local/bin:/usr/bin:/bin", LANG: "C.UTF-8", TERM: "dumb", OMP_SANDBOX: "1", npm_config_cache: "/sandbox/home/.npm", npm_config_update_notifier: "false", ...extraEnv };
    for (const [k, v] of Object.entries(base)) if (!SECRET.test(k)) a.push("--env", `${k}=${v}`);
    return [...a, "--", ...argv];
  }
  #execArgs(argv: string[], opts: { cwd?: string; env?: Record<string, string> }, sidTag?: string) {
    const pre = `[ -f ${ENV_FILE} ] && . ${ENV_FILE}; `;
    const extra = Object.fromEntries(Object.entries(opts.env ?? {}).filter(([k]) => !SECRET.test(k)));
    const cmd = sidTag
      ? ["setsid", "-w", "sh", "-c", `echo $$ > /tmp/.ompsbx-${sidTag}.sid; ${pre}exec "$@"`, "sh", ...argv]
      : ["sh", "-c", `${pre}exec "$@"`, "sh", ...argv];
    return this.#raw(cmd, opts.cwd ?? this.workdir, extra);
  }

  sandboxAgent() { return { baseUrl: this.#agentUrl, token: this.#agentToken }; }

  spawnStdio(argv: string[], opts: { cwd?: string; env?: Record<string, string> } = {}) {
    const tag = randomBytes(6).toString("hex");
    const p = spawn(OPENSHELL_BIN, this.#execArgs(argv, opts, tag), { env: this.#osEnv, stdio: ["pipe", "pipe", "pipe"] });
    this.#children.add(p);
    // Killing the local exec client does not kill the remote process tree, so on exit we kill the
    // whole remote session (omp + anything its tools started).
    p.once("exit", () => { this.#children.delete(p); if (this.#created) void this.#killSession(tag); });
    return p;
  }
  #killSession(tag: string) {
    const f = `/tmp/.ompsbx-${tag}.sid`;
    return execCapture(OPENSHELL_BIN, this.#raw(["sh", "-c", `[ -f ${f} ] && pkill -KILL -s "$(cat ${f})"; rm -f ${f}; true`], "/sandbox"), { env: this.#osEnv, timeoutMs: 60_000 });
  }

  exec(argv: string[], opts: { cwd?: string; env?: Record<string, string>; timeoutMs?: number; input?: string } = {}) {
    return execCapture(OPENSHELL_BIN, this.#execArgs(argv, opts), { env: this.#osEnv, timeoutMs: opts.timeoutMs, input: opts.input });
  }

  owned() {
    return {
      sandbox: this.#created ? this.id : null, image: PODMAN_IMAGE,
      gatewayPid: this.#gw?.pid ?? null, podmanServicePid: this.#svc?.pid ?? null,
      relayPid: this.#relay?.proc.pid ?? null, relay: this.#relay?.stats ?? null, forwardPid: this.#fwd?.pid ?? null,
      children: [...this.#children].map((c) => c.pid), info: this.info,
    };
  }

  async dispose() {
    const leftovers: string[] = [];
    const pids = [this.#relay?.proc.pid, this.#fwd?.pid, ...[...this.#children].map((c) => c.pid)].filter(Boolean) as number[];
    for (const c of [...this.#children]) c.kill("SIGTERM");
    this.#relay?.stop(); this.#fwd?.kill("SIGTERM");
    if (this.#created) {
      const d = await this.#os(["sandbox", "delete", this.id], { timeoutMs: 180_000 });
      if (d.code !== 0) leftovers.push(`openshell sandbox delete failed: ${(d.stderr || d.stdout).slice(-200)}`);
      let ctrs: string[] = [];
      for (let i = 0; i < 90; i++) { ctrs = await this.containers(); const l = await this.#os(["sandbox", "list"], { timeoutMs: 30_000 }); if (!ctrs.length && !l.stdout.split("\n").some((x) => x.startsWith(this.id + " "))) break; await sleep(1000); }
      if (ctrs.length) leftovers.push(...ctrs.map((n) => `podman container ${n}`));
      const l = await this.#os(["sandbox", "list"], { timeoutMs: 30_000 });
      if (l.stdout.split("\n").some((x) => x.startsWith(this.id + " "))) leftovers.push(`openshell sandbox ${this.id}`);
      const vols = await pm(["volume", "ls", "--format", "{{.Name}}"]);
      leftovers.push(...vols.stdout.split("\n").filter((v) => v.includes(this.id)).map((v) => `podman volume ${v}`));
      this.#created = false;
    }
    await sleep(500);
    for (const pid of pids) if (pidAlive(pid)) { try { process.kill(pid, "SIGKILL"); } catch {} }
    // The gateway and Podman service this tier started are shared with any other wrapper that reused them
    // (`#ensureGateway` adopts a running one). Stop them only when no other sandbox is still registered.
    const others = this.#gw || this.#svc ? await this.#otherSandboxes() : [];
    if (others.length) {
      this.info.sharedServicesLeftRunning = { reason: `${others.length} other sandbox(es) still use the gateway`, gatewayPid: this.#gw?.pid ?? null, podmanServicePid: this.#svc?.pid ?? null };
    } else {
      await stopProc(this.#gw); await stopProc(this.#svc);
      for (const [what, p] of [["gateway", this.#gw], ["podman service", this.#svc]] as const) if (p?.pid && pidAlive(p.pid)) leftovers.push(`${what} pid ${p.pid}`);
    }
    leftovers.push(...pids.filter(pidAlive).map((p) => `pid ${p}`));
    return { leftovers };
  }
}
