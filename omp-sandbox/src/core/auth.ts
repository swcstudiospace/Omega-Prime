// Host-side omp auth-broker. The ChatGPT/Z.ai logins stay in the host store (~/.omp/agent/agent.db);
// the broker keeps refresh tokens and hands sandboxed omp short-lived access tokens over loopback.
// The bearer token is read into memory only; it is never logged, written into the repo, or printed.
import { spawn, execFile, type ChildProcess } from "node:child_process";
import { promisify } from "node:util";
import { createServer } from "node:net";
import { OMP_BIN } from "./config.ts";

const run = promisify(execFile);

export async function freePort(): Promise<number> {
  return new Promise((res, rej) => {
    const s = createServer(); s.unref(); s.on("error", rej);
    s.listen(0, "127.0.0.1", () => { const p = (s.address() as any).port; s.close(() => res(p)); });
  });
}

export class AuthBroker {
  proc: ChildProcess | null = null;
  url = ""; #token = "";
  get env() { return { OMP_AUTH_BROKER_URL: this.url, OMP_AUTH_BROKER_TOKEN: this.#token }; }

  async start() {
    const { stdout } = await run(OMP_BIN, ["auth-broker", "token"], { timeout: 30_000 });
    this.#token = stdout.trim();
    if (!/^\S{16,}$/.test(this.#token)) throw new Error("auth-broker: could not read a bearer token (value not shown)");
    const port = await freePort();
    this.url = `http://127.0.0.1:${port}`;
    this.proc = spawn(OMP_BIN, ["auth-broker", "serve", `--bind=127.0.0.1:${port}`], { stdio: ["ignore", "pipe", "pipe"], detached: false });
    await new Promise<void>((resolve, reject) => {
      const t = setTimeout(() => reject(new Error("auth-broker: did not start in 30s")), 30_000);
      const onData = (b: Buffer) => { if (b.toString().includes("auth-broker listening")) { clearTimeout(t); resolve(); } };
      this.proc!.stdout!.on("data", onData); this.proc!.stderr!.on("data", onData);
      this.proc!.once("exit", (c) => { clearTimeout(t); reject(new Error(`auth-broker exited early (${c})`)); });
      // A missing or non-executable omp emits `error` (not `exit`); reject so `up` rolls back instead of crashing.
      this.proc!.once("error", (e) => { clearTimeout(t); reject(new Error(`auth-broker could not start: ${e.message}`)); });
    });
    this.proc?.on("error", () => {});
    return this;
  }

  async stop() {
    const p = this.proc; this.proc = null; this.#token = "";
    // A broker killed by a signal has exitCode null and signalCode set; its exit event has already fired.
    if (!p || p.exitCode !== null || p.signalCode !== null) return;
    const gone = new Promise((r) => p.once("exit", r));
    p.kill("SIGTERM"); const t = setTimeout(() => p.kill("SIGKILL"), 5000);
    await gone; clearTimeout(t);
  }
}
