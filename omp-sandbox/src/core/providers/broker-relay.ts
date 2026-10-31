// Auth-broker relay for OpenShell tiers. OpenShell network policy never authorizes loopback
// destinations, so sandboxed omp cannot dial the host broker on 127.0.0.1. Instead the core runs a
// tiny TCP relay INSIDE the sandbox (127.0.0.1:<port>, sandbox-local loopback is allowed) over one
// authenticated `openshell sandbox exec` stdio channel, and the host side pipes each relayed
// connection to the real broker. No host port is exposed to the sandbox network; the broker bearer
// token travels only inside the sandbox's 0600 env file, never on argv or in logs.
//
// Frames (one per line):  sandbox->host  "O <id>" | "D <id> <b64>" | "C <id>" | "READY <port>"
//                         host->sandbox  "D <id> <b64>" | "C <id>"
import { connect, type Socket } from "node:net";
import { createInterface } from "node:readline";
import type { ChildProcessWithoutNullStreams } from "node:child_process";

/** Node script run inside the sandbox (`node -e`). Port comes from OMP_SBX_RELAY_PORT. */
export const SANDBOX_RELAY_JS = String.raw`
const net = require("net"), rl = require("readline");
const port = Number(process.env.OMP_SBX_RELAY_PORT); const socks = new Map(); let n = 0;
const send = (s) => process.stdout.write(s + "\n");
const srv = net.createServer((c) => {
  const id = String(++n); socks.set(id, c); send("O " + id);
  c.on("data", (b) => send("D " + id + " " + b.toString("base64")));
  c.on("close", () => { if (socks.delete(id)) send("C " + id); });
  c.on("error", () => {});
});
srv.listen(port, "127.0.0.1", () => send("READY " + port));
rl.createInterface({ input: process.stdin }).on("line", (l) => {
  const [op, id, b64] = l.split(" "); const c = socks.get(id); if (!c) return;
  if (op === "D") c.write(Buffer.from(b64 || "", "base64"));
  else if (op === "C") { socks.delete(id); c.end(); }
}).on("close", () => process.exit(0));
`;

export class BrokerRelay {
  #socks = new Map<string, Socket>();
  stats = { connections: 0, bytesIn: 0, bytesOut: 0 };
  constructor(readonly proc: ChildProcessWithoutNullStreams, readonly target: { host: string; port: number }) {}

  /** Resolve once the in-sandbox listener reports READY. */
  start(timeoutMs = 60_000) {
    const p = this.proc; let stderr = "";
    p.stderr.on("data", (b) => { stderr = (stderr + b).slice(-2000); });
    const send = (s: string) => { if (!p.stdin.destroyed) p.stdin.write(s + "\n"); };
    return new Promise<void>((resolve, reject) => {
      const t = setTimeout(() => reject(new Error(`broker relay not ready in ${timeoutMs}ms: ${stderr.slice(-300)}`)), timeoutMs);
      p.once("exit", (c) => { clearTimeout(t); reject(new Error(`broker relay exited (${c}): ${stderr.slice(-300)}`)); for (const s of this.#socks.values()) s.destroy(); });
      createInterface({ input: p.stdout }).on("line", (l) => {
        const [op, id, b64] = l.split(" ");
        if (op === "READY") { clearTimeout(t); resolve(); return; }
        if (op === "O") {
          this.stats.connections++;
          const s = connect(this.target.port, this.target.host); this.#socks.set(id, s);
          s.on("data", (b) => { this.stats.bytesOut += b.length; send(`D ${id} ${b.toString("base64")}`); });
          s.on("close", () => { if (this.#socks.delete(id)) send(`C ${id}`); });
          s.on("error", () => {});
        } else if (op === "D") { const b = Buffer.from(b64 ?? "", "base64"); this.stats.bytesIn += b.length; this.#socks.get(id)?.write(b); }
        else if (op === "C") { const s = this.#socks.get(id); this.#socks.delete(id); s?.end(); }
      });
    });
  }

  stop() { for (const s of this.#socks.values()) s.destroy(); this.#socks.clear(); try { this.proc.stdin.end(); } catch {} this.proc.kill("SIGTERM"); }
}
