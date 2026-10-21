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
import { copyFileSync, existsSync, mkdirSync, openSync, readdirSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { request } from "node:http";
import { dirname, join } from "node:path";
import { randomBytes } from "node:crypto";
import { OPENSHELL_BIN, OPENSHELL_GATEWAY_BIN, OPENSHELL_TLS, OPENSHELL_GATEWAY_PORT, PODMAN_BIN, PODMAN_IMAGE, ROOT, STATE, SANDBOX_AGENT_BIN } from "../config.ts";
import { execCapture, pidAlive, rid, waitHttp } from "./util.ts";
import { BrokerRelay, SANDBOX_RELAY_JS } from "./broker-relay.ts";
import { TierUnavailable, type SandboxTier } from "./types.ts";

const OS_ENV = { ...process.env, OPENSHELL_TELEMETRY_ENABLED: "false" } as Record<string, string>;
const os = (args: string[], o: { timeoutMs?: number; input?: string } = {}) => execCapture(OPENSHELL_BIN, args, { env: OS_ENV, ...o });
const pm = (args: string[], o: { timeoutMs?: number } = {}) => execCapture(PODMAN_BIN, args, { env: process.env as any, ...o });
const GW_STATE = join(STATE, "openshell-podman");
// One lease file per live tier (named by tier id, holding its wrapper pid). A tier takes its lease before it adopts
// or starts the shared gateway and Podman service, so a tier still starting up counts as a user before it has a sandbox.
const LEASES = join(GW_STATE, "leases");
/** Tier ids, other than `self`, whose wrapper process is still alive and holds a lease on the shared services. */
export function otherLeases(self: string, dir = LEASES): string[] {
  let names: string[] = [];
  try { names = readdirSync(dir); } catch { return []; }
  return names.filter((n) => {
    if (n === self) return false;
    try { return pidAlive(Number(readFileSync(join(dir, n), "utf8").trim())); } catch { return false; }
  });
}
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

  async start(env: Record<string, string>) {
    if (!existsSync(OPENSHELL_BIN) || !existsSync(OPENSHELL_GATEWAY_BIN)) throw new TierUnavailable("openshell-podman", `OpenShell binaries missing next to ${OPENSHELL_BIN}`);
    const pr = await podmanRootless();
    if (!pr.ok) throw new TierUnavailable("openshell-podman", pr.detail);
    this.info.podman = { version: pr.version, rootless: true, pasta: pr.pasta };
    mkdirSync(LEASES, { recursive: true, mode: 0o700 });
    writeFileSync(join(LEASES, this.id), String(process.pid), { mode: 0o600 });
    await this.#ensureSocket(pr.socket!);
    await this.#ensureImage();
    await this.#ensureGateway(pr.socket!);

    // Broker: sandboxed omp talks to 127.0.0.1:RELAY_PORT inside the sandbox.
    const u = env.OMP_AUTH_BROKER_URL ? new URL(env.OMP_AUTH_BROKER_URL) : null;
    this.#brokerTarget = u ? { host: u.hostname, port: Number(u.port) } : null;
    this.#env = { ...env, ...(u ? { OMP_AUTH_BROKER_URL: `http://127.0.0.1:${RELAY_PORT}` } : {}) };

    const c = await os(["sandbox", "create", "--name", this.id, "--from", PODMAN_IMAGE, "--policy", join(ROOT, "sandbox", "policy-podman.yaml"), "--detach", "--no-tty", "--no-auto-providers"], { timeoutMs: 600_000 });
    if (c.code !== 0) throw new TierUnavailable("openshell-podman", `sandbox create failed: ${(c.stderr || c.stdout).slice(-500)}`);
    this.#created = true;
    await this.#waitReady();
    const ctrs = await this.containers();
    if (!ctrs.some((n) => n.startsWith("openshell-supervisor-")) || !ctrs.some((n) => n.startsWith("openshell-"))) throw new TierUnavailable("openshell-podman", `gateway did not create Podman containers for ${this.id} (is it using another driver?)`);
    this.info.containers = ctrs;

    // Stage env (secrets only via stdin into a 0600 file inside the sandbox; never argv, never logs).
    const envLines = Object.entries(this.#env).map(([k, v]) => `export ${k}='${String(v).replace(/'/g, "'\\''")}'`).join("\n") + "\n";
    const w = await execCapture(OPENSHELL_BIN, this.#raw(["sh", "-c", `umask 077; mkdir -p /sandbox/home /sandbox/work; cat > ${ENV_FILE}`], "/sandbox"), { env: OS_ENV, input: envLines, timeoutMs: 120_000 });
    if (w.code !== 0) throw new TierUnavailable("openshell-podman", `could not stage sandbox env file via exec stdin: ${w.stderr.slice(-300)}`);

    if (this.#brokerTarget) {
      const rp = spawn(OPENSHELL_BIN, this.#raw(["node", "-e", SANDBOX_RELAY_JS], "/sandbox", { OMP_SBX_RELAY_PORT: String(RELAY_PORT) }), { env: OS_ENV, stdio: ["pipe", "pipe", "pipe"] });
      this.#relay = new BrokerRelay(rp, this.#brokerTarget);
      await this.#relay.start(60_000);
    }

    const agentLog = openSync(join(GW_STATE, `${this.id}-sandbox-agent.log`), "w");
    const agent = spawn(OPENSHELL_BIN, this.#raw(["sandbox-agent", "server", "--host", "127.0.0.1", "--port", String(AGENT_PORT), "--token", this.#agentToken, "--no-telemetry"], "/sandbox", process.env.OMP_SANDBOX_AGENT_LOG ? { RUST_LOG: process.env.OMP_SANDBOX_AGENT_LOG } : {}), { env: OS_ENV, stdio: ["pipe", agentLog, agentLog] });
    this.#children.add(agent as any);
    this.#fwd = spawn(OPENSHELL_BIN, ["forward", "service", "--target-port", String(AGENT_PORT), "--local", "127.0.0.1:0", this.id], { env: OS_ENV, stdio: ["pipe", "pipe", "pipe"] });
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
    const s = await os(["status"], { timeoutMs: 15_000 });
    if (s.code === 0 && /Connected/.test(s.stdout)) { this.info.gateway = { startedByTier: false, endpoint: `https://127.0.0.1:${OPENSHELL_GATEWAY_PORT}` }; return; }
    mkdirSync(GW_STATE, { recursive: true });
    const cfg = join(GW_STATE, "gateway.toml");
    writeFileSync(cfg, [
      "[openshell]", "version = 2", "",
      "[openshell.gateway]", 'compute_driver = "podman"', "disable_tls = false", `guest_tls_ca = "${OPENSHELL_TLS}/ca.crt"`, "",
      "[openshell.drivers.podman]", `socket_path = "${sock}"`, 'network_name = "openshell"', `default_image = "${PODMAN_IMAGE}"`,
      'image_pull_policy = "if_not_present"', `grpc_endpoint = "https://127.0.0.1:${OPENSHELL_GATEWAY_PORT}"`, "stop_timeout_secs = 10", "",
      "[openshell.gateway.gateway_jwt]", `signing_key_path = "${OPENSHELL_TLS}/jwt/signing.pem"`, `public_key_path = "${OPENSHELL_TLS}/jwt/public.pem"`,
      `kid_path = "${OPENSHELL_TLS}/jwt/kid"`, 'gateway_id = "omp-sandbox-podman"', "",
    ].join("\n"));
    const logPath = join(GW_STATE, "gateway.log"); const log = openSync(logPath, "w");
    this.#gw = spawn(OPENSHELL_GATEWAY_BIN, ["--config", cfg, "--compute-driver", "podman", "--tls-cert", `${OPENSHELL_TLS}/server/tls.crt`, "--tls-key", `${OPENSHELL_TLS}/server/tls.key`, "--tls-client-ca", `${OPENSHELL_TLS}/ca.crt`, "--db-url", `sqlite:${join(GW_STATE, "gateway.db")}?mode=rwc`, "--bind-address", "127.0.0.1", "--port", String(OPENSHELL_GATEWAY_PORT), "--health-port", "0", "--metrics-port", "0"], { env: OS_ENV, stdio: ["ignore", log, log] });
    for (let i = 0; i < 120; i++) {
      await sleep(500);
      if (this.#gw.exitCode !== null) throw new TierUnavailable("openshell-podman", `gateway exited: ${readFileSync(logPath, "utf8").slice(-500)}`);
      const l = readFileSync(logPath, "utf8");
      if (/Using compute driver driver=podman/.test(l) && /Gateway listener bound/.test(l)) {
        const st = await os(["status"], { timeoutMs: 15_000 });
        if (/Connected/.test(st.stdout)) { this.info.gateway = { startedByTier: true, pid: this.#gw.pid, driver: "podman", endpoint: `https://127.0.0.1:${OPENSHELL_GATEWAY_PORT}`, mtls: true }; return; }
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
      const l = await os(["sandbox", "list"], { timeoutMs: 30_000 });
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
    const p = spawn(OPENSHELL_BIN, this.#execArgs(argv, opts, tag), { env: OS_ENV, stdio: ["pipe", "pipe", "pipe"] });
    this.#children.add(p);
    // Killing the local exec client does not kill the remote process tree, so on exit we kill the
    // whole remote session (omp + anything its tools started).
    p.once("exit", () => { this.#children.delete(p); if (this.#created) void this.#killSession(tag); });
    return p;
  }
  #killSession(tag: string) {
    const f = `/tmp/.ompsbx-${tag}.sid`;
    return execCapture(OPENSHELL_BIN, this.#raw(["sh", "-c", `[ -f ${f} ] && pkill -KILL -s "$(cat ${f})"; rm -f ${f}; true`], "/sandbox"), { env: OS_ENV, timeoutMs: 60_000 });
  }

  exec(argv: string[], opts: { cwd?: string; env?: Record<string, string>; timeoutMs?: number; input?: string } = {}) {
    return execCapture(OPENSHELL_BIN, this.#execArgs(argv, opts), { env: OS_ENV, timeoutMs: opts.timeoutMs, input: opts.input });
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
      const d = await os(["sandbox", "delete", this.id], { timeoutMs: 180_000 });
      if (d.code !== 0) leftovers.push(`openshell sandbox delete failed: ${(d.stderr || d.stdout).slice(-200)}`);
      let ctrs: string[] = [];
      for (let i = 0; i < 90; i++) { ctrs = await this.containers(); const l = await os(["sandbox", "list"], { timeoutMs: 30_000 }); if (!ctrs.length && !l.stdout.split("\n").some((x) => x.startsWith(this.id + " "))) break; await sleep(1000); }
      if (ctrs.length) leftovers.push(...ctrs.map((n) => `podman container ${n}`));
      const l = await os(["sandbox", "list"], { timeoutMs: 30_000 });
      if (l.stdout.split("\n").some((x) => x.startsWith(this.id + " "))) leftovers.push(`openshell sandbox ${this.id}`);
      const vols = await pm(["volume", "ls", "--format", "{{.Name}}"]);
      leftovers.push(...vols.stdout.split("\n").filter((v) => v.includes(this.id)).map((v) => `podman volume ${v}`));
      this.#created = false;
    }
    await sleep(500);
    for (const pid of pids) if (pidAlive(pid)) { try { process.kill(pid, "SIGKILL"); } catch {} }
    // The gateway and Podman service this tier started are shared with any other wrapper that reused them
    // (`#ensureGateway` adopts a running one). Stop them only when no other tier holds a lease (that covers a
    // wrapper still starting up, before its sandbox exists) and no other sandbox is still registered.
    rmSync(join(LEASES, this.id), { force: true });
    const leased = this.#gw || this.#svc ? otherLeases(this.id) : [];
    const others = leased.length ? leased : this.#gw || this.#svc ? await this.#otherSandboxes() : [];
    if (others.length) {
      this.info.sharedServicesLeftRunning = { reason: leased.length ? `${leased.length} other tier(s) still hold a lease on the shared services` : `${others.length} other sandbox(es) still use the gateway`, gatewayPid: this.#gw?.pid ?? null, podmanServicePid: this.#svc?.pid ?? null };
    } else {
      await stopProc(this.#gw); await stopProc(this.#svc);
      for (const [what, p] of [["gateway", this.#gw], ["podman service", this.#svc]] as const) if (p?.pid && pidAlive(p.pid)) leftovers.push(`${what} pid ${p.pid}`);
    }
    leftovers.push(...pids.filter(pidAlive).map((p) => `pid ${p}`));
    return { leftovers };
  }
}
