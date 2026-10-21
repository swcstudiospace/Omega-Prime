// Newline-JSON stdio protocol between the Rust wrapper and the TS core.
// in : {"id":"1","op":"up"|"run_gsd_stage"|"answer_question"|"sandbox_status"|"abort"|"down","args":{...}}
// out: {"id":"1","ok":true,"result":...} | {"id":"1","ok":false,"error":"...","code":"stale_answer"?}
//      {"event":"question"|"stage_done"|"fallback",...}   (unsolicited)
import { createInterface } from "node:readline";
import { Core } from "./core.ts";
import { StaleAnswerError } from "./omp-rpc.ts";

const core = new Core();
const out = (o: unknown) => process.stdout.write(JSON.stringify(o) + "\n");
core.on("event", (e) => out(e));
// Anything else that writes to stdout would corrupt the protocol; route console.* to stderr.
console.log = (...a: unknown[]) => process.stderr.write(a.map(String).join(" ") + "\n");
console.info = console.log; console.debug = console.log; console.warn = console.log;

async function handle(req: any) {
  const a = req.args ?? {};
  // Set by the Rust Anda layer after it verified the signed envelope (via/caller principal).
  if (req.access) core.access = { via: String(req.access.via ?? ""), caller: String(req.access.caller ?? "") };
  switch (req.op) {
    case "up": return core.up(a);
    case "run_gsd_stage": return core.runStage({ stage: a.stage, gsdCommand: a.gsd_command, task: a.task, thinking: a.thinking, model: a.model, plan: a.plan });
    case "answer_question": return core.answer(a.stage_id, a.request_id, { selected: a.selected, text: a.text, perQuestion: a.per_question });
    case "sandbox_status": return core.status(a.stage_id);
    case "abort": return core.abort(a.stage_id);
    case "run_check": return core.runCheck({ argv: a.argv, cwd: a.cwd, timeout_ms: a.timeout_ms, label: a.label });
    case "export_commits": return core.exportCommits({ out: a.out });
    case "down": return core.down();
    default: throw new Error(`unknown op ${req.op}`);
  }
}

let shuttingDown = false;
const rl = createInterface({ input: process.stdin });
rl.on("line", async (line) => {
  if (!line.trim()) return;
  let req: any; try { req = JSON.parse(line); } catch { return out({ ok: false, error: "invalid json" }); }
  try { out({ id: req.id, ok: true, result: await handle(req) }); if (req.op === "down") { shuttingDown = true; setTimeout(() => process.exit(0), 200); } }
  catch (e: any) { out({ id: req.id, ok: false, error: String(e?.message ?? e), code: e instanceof StaleAnswerError ? "stale_answer" : "error" }); }
});
rl.on("close", async () => { if (!shuttingDown) { await core.down().catch(() => {}); process.exit(0); } });
for (const s of ["SIGTERM", "SIGINT"] as const) process.on(s, async () => { await core.down().catch(() => {}); process.exit(0); });
out({ event: "ready", pid: process.pid });
