// Newline-JSON stdio protocol between the Rust wrapper and the TS core.
// in : {"id":"1","op":"up"|"run_gsd_stage"|"answer_question"|"sandbox_status"|"abort"|"run_check"|"export_commits"|"down","args":{...}}
// out: {"id":"1","ok":true,"result":...} | {"id":"1","ok":false,"error":"...","code":"stale_answer"|"path_refused"?}
//      {"event":"question"|"stage_done"|"fallback",...}   (unsolicited)
// Importing this module is side-effect free (tests import errorReply); the boot below runs only when this file
// is the main module, exactly how crates/omp-sandbox-anda/src/core_child.rs launches it: node
// --experimental-transform-types --no-warnings src/core/server.ts with cwd at the package root.
import { createInterface } from "node:readline";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { Core } from "./core.ts";
import { StaleAnswerError, type AnswerInput } from "./omp-rpc.ts";
import { PathRefused } from "./paths.ts";
import type { Stage } from "./config.ts";
import type { SeedRepo } from "./repo.ts";

/** Pure failure mapping for the protocol: a refused path is distinguishable from a stale answer and from any other failure. */
export function errorReply(e: unknown): { ok: false; error: string; code: string } {
  const error = typeof e === "object" && e !== null && "message" in e ? String(e.message) : String(e);
  return { ok: false, error, code: e instanceof PathRefused ? "path_refused" : e instanceof StaleAnswerError ? "stale_answer" : "error" };
}

/** Wire args for `up` — exactly Core.up's options; Core validates them (allowedDir, mutual exclusion). */
type UpArgs = { tiers?: string[]; seed?: string; workspace_host_path?: string; seed_repo?: SeedRepo; py_deps?: string; stage?: Array<{ from: string; to: string }> };

/** One request line from the Rust Anda wrapper. Args are passed through; Core re-validates them at runtime (allowedDir, stageRoute). */
type WireRequest = { id?: unknown; op: unknown; args?: Record<string, unknown>; access?: { via?: unknown; caller?: unknown } };

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const core = new Core();
  const out = (o: unknown) => process.stdout.write(JSON.stringify(o) + "\n");
  core.on("event", (e) => out(e));
  // Anything else that writes to stdout would corrupt the protocol; route console.* to stderr.
  console.log = (...a: unknown[]) => process.stderr.write(a.map(String).join(" ") + "\n");
  console.info = console.log; console.debug = console.log; console.warn = console.log;

  async function handle(req: WireRequest) {
    const a = req.args ?? {};
    // Set by the Rust Anda layer after it verified the signed envelope (via/caller principal).
    if (req.access) core.access = { via: String(req.access.via ?? ""), caller: String(req.access.caller ?? "") };
    switch (req.op) {
      case "up": return core.up(a as UpArgs);
      case "run_gsd_stage": return core.runStage({ stage: a.stage as Stage, gsdCommand: a.gsd_command as string, task: a.task as string, thinking: a.thinking as string | undefined, model: a.model as string | undefined, plan: a.plan as string | undefined, fallbackPlan: "fallback_plan" in a ? (a.fallback_plan as string | null) : undefined });
      case "answer_question": return core.answer(a.stage_id as string, a.request_id as string, { selected: a.selected as string[] | undefined, text: a.text as string | undefined, perQuestion: a.per_question as AnswerInput["perQuestion"] });
      case "sandbox_status": return core.status(a.stage_id as string | undefined);
      case "abort": return core.abort(a.stage_id as string);
      case "run_check": return core.runCheck({ argv: a.argv as string[], cwd: a.cwd as string | undefined, timeout_ms: a.timeout_ms as number | undefined, label: a.label as string | undefined });
      case "export_commits": return core.exportCommits({ out: a.out as string | undefined });
      case "down": return core.down();
      default: throw new Error(`unknown op ${String(req.op)}`);
    }
  }

  let shuttingDown = false;
  const rl = createInterface({ input: process.stdin });
  rl.on("line", async (line) => {
    if (!line.trim()) return;
    let req: WireRequest;
    try { req = JSON.parse(line) as WireRequest; } catch { return out({ ok: false, error: "invalid json" }); }
    try { out({ id: req.id, ok: true, result: await handle(req) }); if (req.op === "down") { shuttingDown = true; setTimeout(() => process.exit(0), 200); } }
    catch (e) { out({ id: req.id, ...errorReply(e) }); }
  });
  rl.on("close", async () => { if (!shuttingDown) { await core.down().catch(() => {}); process.exit(0); } });
  for (const s of ["SIGTERM", "SIGINT"] as const) process.on(s, async () => { await core.down().catch(() => {}); process.exit(0); });
  out({ event: "ready", pid: process.pid });
}
