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
import { execCapture, rid, waitHttp } from "./util.ts";
import { TierUnavailable, type SandboxTier } from "./types.ts";

const OS_ENV = { ...process.env, OPENSHELL_TELEMETRY_ENABLED: "false" } as Record<string, string>;
const os = (args: string[], o: { timeoutMs?: number; input?: string } = {}) => execCapture(OPENSHELL_BIN, args, { env: OS_ENV, ...o });

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
  #url = ""; #token = randomBytes(18).toString("hex");
  #children = new Set<ChildProcessWithoutNullStreams>();
  #created = false;

  async start(env: Record<string, string>) {
    const a = await openshellAvailability();
    if (!a.ok) throw new TierUnavailable("openshell", a.detail);
    this.#env = env;
    const c = await os(["sandbox", "create", "--name", this.id, "--detach", "--no-tty", "--no-auto-providers", "--policy", `${ROOT}/sandbox/policy.yaml`], { timeoutMs: 900_000 });
    if (c.code !== 0) throw new TierUnavailable("openshell", `create failed: ${c.stderr.slice(-500)}`);
    this.#created = true;
    const up = await os(["sandbox", "upload", this.id, `${ROOT}/sandbox/bootstrap.sh`, "/sandbox/bootstrap.sh"], { timeoutMs: 120_000 });
    if (up.code !== 0) throw new TierUnavailable("openshell", `upload failed: ${up.stderr.slice(-300)}`);
    const boot = await this.exec(["bash", "/sandbox/bootstrap.sh"], { timeoutMs: 1_800_000 });
    if (boot.code !== 0) throw new TierUnavailable("openshell", `bootstrap failed: ${boot.stderr.slice(-500)}`);
    const envLines = Object.entries(env).map(([k, v]) => `export ${k}='${v.replace(/'/g, "'\\''")}'`).join("\n") + "\n";
    const w = await execCapture(OPENSHELL_BIN, ["sandbox", "exec", "-n", this.id, "--no-tty", "--", "sh", "-c", "umask 077; mkdir -p /sandbox/home; cat > /sandbox/home/.omp-sandbox.env"], { env: OS_ENV, input: envLines, timeoutMs: 60_000 });
    if (w.code !== 0) throw new TierUnavailable("openshell", "could not stage sandbox env file (stdin over exec)");
    const port = await freePort();
    this.#children.add(spawn(OPENSHELL_BIN, ["sandbox", "exec", "-n", this.id, "--no-tty", "--", "sandbox-agent", "server", "--host", "127.0.0.1", "--port", "2468", "--token", this.#token, "--no-telemetry"], { env: OS_ENV, stdio: ["pipe", "pipe", "pipe"] }) as any);
    this.#fwd = spawn(OPENSHELL_BIN, ["forward", "start", this.id, `${port}:2468`], { env: OS_ENV, stdio: ["pipe", "pipe", "pipe"] });
    this.#url = `http://127.0.0.1:${port}`;
    await waitHttp(`${this.#url}/v1/health`, this.#token, 60_000);
  }
  sandboxAgent() { return { baseUrl: this.#url, token: this.#token }; }
  #execArgs(argv: string[], opts: { cwd?: string; env?: Record<string, string> }) {
    const a = ["sandbox", "exec", "-n", this.id, "--no-tty", "--no-login-shell", "--workdir", opts.cwd ?? this.workdir];
    // Non-secret env only via --env; the broker token is passed on stdin-free path below.
    for (const [k, v] of Object.entries({ ...this.#env, ...(opts.env ?? {}) })) if (!/TOKEN|KEY|SECRET/.test(k)) a.push("--env", `${k}=${v}`);
    const secrets = Object.entries({ ...this.#env, ...(opts.env ?? {}) }).filter(([k]) => /TOKEN|KEY|SECRET/.test(k));
    // Secrets go through a sandbox-local env file written by bootstrap (mode 0600), never argv.
    return [...a, "--", ...(secrets.length ? ["sh", "-c", '. /sandbox/home/.omp-sandbox.env 2>/dev/null; exec "$@"', "sh"] : []), ...argv];
  }
  spawnStdio(argv: string[], opts: { cwd?: string; env?: Record<string, string> } = {}) {
    const p = spawn(OPENSHELL_BIN, this.#execArgs(argv, opts), { env: OS_ENV, stdio: ["pipe", "pipe", "pipe"] });
    this.#children.add(p); p.once("exit", () => this.#children.delete(p)); return p;
  }
  exec(argv: string[], opts: { cwd?: string; env?: Record<string, string>; timeoutMs?: number; input?: string } = {}) {
    return execCapture(OPENSHELL_BIN, this.#execArgs(argv, opts), { env: OS_ENV, timeoutMs: opts.timeoutMs ?? 600_000, input: opts.input });
  }
  owned() { return { sandbox: this.#created ? this.id : null, forwardPid: this.#fwd?.pid ?? null }; }
  async dispose() {
    for (const c of [...this.#children]) c.kill("SIGTERM"); this.#fwd?.kill("SIGTERM");
    const leftovers: string[] = [];
    if (this.#created) {
      const d = await os(["sandbox", "delete", this.id], { timeoutMs: 120_000 });
      const l = await os(["sandbox", "list"], { timeoutMs: 30_000 });
      if (d.code !== 0 || l.stdout.includes(this.id)) leftovers.push(`openshell sandbox ${this.id}`);
    }
    return { leftovers };
  }
}
