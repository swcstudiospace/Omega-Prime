// Drive one GSD stage through the HOST omp in RPC mode (used to plan/build this repo itself).
// usage: node scratch/host-stage.ts <cwd> <model> <logfile> <message-file>
import { spawn } from "node:child_process";
import { readFileSync, appendFileSync } from "node:fs";
import { OmpRpcClient, textOf } from "../src/core/omp-rpc.ts";
const [cwd, model, log, ...msgFiles] = process.argv.slice(2);
const L = (o: unknown) => appendFileSync(log, JSON.stringify({ t: new Date().toISOString(), ...(o as object) }) + "\n");
const proc = spawn("omp", ["--mode", "rpc", "--no-title", "--no-ui", "--model", model, "--thinking", "max", "--approval-mode", "yolo", "--max-time", "5400s"], { cwd, stdio: ["pipe", "pipe", "pipe"] });
const c = new OmpRpcClient(proc as any, "host-omp");
c.on("question", (q) => { L({ ev: "question", q }); setTimeout(() => { try { L({ ev: "auto-answer", r: c.answerRecommended(q) }); } catch (e) { L({ ev: "answer-err", e: String(e) }); } }, 500); });
c.on("frame", (f) => { if (["prompt_result", "agent_end", "notice", "response"].includes(f.type) || (f.type === "message_end" && f.message?.role === "assistant")) L({ ev: "frame", type: f.type, model: f.message?.model, provider: f.message?.provider, status: f.status, text: f.type === "message_end" ? textOf(f.message).slice(-3000) : undefined, err: f.error ?? f.message?.errorMessage, cmd: f.command }); if (f.type === "tool_execution_start") L({ ev: "tool", name: f.toolName }); });
c.on("stderr", (s) => L({ ev: "stderr", s: String(s).slice(0, 500) }));
await c.command("set_ask_dialog", { enabled: true });
for (const f of msgFiles) {
  const message = readFileSync(f, "utf8");
  L({ ev: "start", model, cwd, file: f, bytes: message.length });
  const r = await c.prompt(message);
  L({ ev: "done", file: f, status: r.status, error: r.error, models: [...c.models] });
  if (r.status !== "completed") break;
}
await c.close();
