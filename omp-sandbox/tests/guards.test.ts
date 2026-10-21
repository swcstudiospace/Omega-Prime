// Greptile round-1 guards: tier names, prompt-admission failures, overlapping stages, abort during setup, receipt redaction.
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, readFileSync, statSync, existsSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { SandboxTier } from "../src/core/providers/types.ts";

const STATE = mkdtempSync(join(tmpdir(), "omp-sbx-guards-"));
process.env.OMP_SANDBOX_STATE = STATE;
const FAKE = new URL("./fake-omp.mjs", import.meta.url).pathname;
const { OmpRpcClient, StaleAnswerError } = await import("../src/core/omp-rpc.ts");
const { PathRefused } = await import("../src/core/paths.ts");
const { validateTiers, startTier } = await import("../src/core/providers/tiers.ts");
const { redactSecrets } = await import("../src/core/providers/util.ts");
// Dynamic imports are required here (not a style choice): config.ts captures OMP_SANDBOX_STATE into
// STATE at module-evaluation time, and gsd.ts derives its CACHE from it — so they must load only
// after the env override above, which a hoisted static import would precede.
const { Core } = await import("../src/core/core.ts");
const { ensureGsd, gsdCommandsOnHost } = await import("../src/core/gsd.ts");
const { STATE: CORE_STATE, VERSIONS } = await import("../src/core/config.ts");
const GSD_CACHE = join(CORE_STATE, "cache", `gsd-core-${VERSIONS.gsdCore}`);

const fakeProc = (env: Record<string, string> = {}) => spawn(process.execPath, [FAKE], { env: { ...process.env, ...env }, stdio: ["pipe", "pipe", "pipe"] });

/** A Core marked up against a fake tier whose only process is the fake omp. */
function fakeCore(env: Record<string, string> = {}) {
  const core = new Core();
  const tier: any = { name: "fake", id: "fake", isolation: "none (test)", owned: () => ({}), dispose: async () => ({ leftovers: [] }), spawnStdio: () => fakeProc(env) };
  core.attachTierForTests(tier);
  return core;
}

test("unknown tier names are refused instead of falling through to the local tier", async () => {
  assert.throws(() => validateTiers(["bwrapp"]), /unknown sandbox tier/);
  assert.throws(() => validateTiers([]), /empty/);
  assert.deepEqual(validateTiers(["bwrap", "agentos-local"]), ["bwrap", "agentos-local"]);
  await assert.rejects(startTier({}, ["openshel"] as any), /unknown sandbox tier/);
});

test("prompt rejects cleanly (no unhandled rejection) when omp exits before admission", async () => {
  const unhandled: unknown[] = [];
  const onU = (e: unknown) => unhandled.push(e);
  process.on("unhandledRejection", onU);
  const c = new OmpRpcClient(fakeProc({ FAKE_OMP_EXIT_ON_PROMPT: "1" }), "fake");
  await c.command("set_ask_dialog", { enabled: true }, 10_000);
  await assert.rejects(c.prompt("hi"));
  await new Promise((r) => setTimeout(r, 200));
  process.off("unhandledRejection", onU);
  assert.deepEqual(unhandled, []);
  await c.close();
});

test("runStage refuses overlapping stages, receipts are redacted and 0600", async () => {
  const core: any = fakeCore();
  const a = core.runStage({ stage: "plan", gsdCommand: "gsd-plan-phase", task: "t" });
  // Wait until the fake asks its question.
  for (let i = 0; i < 100 && core.status(a.stageId).status !== "waiting"; i++) await new Promise((r) => setTimeout(r, 50));
  assert.equal(core.status(a.stageId).status, "waiting");
  assert.throws(() => core.runStage({ stage: "execute", gsdCommand: "gsd-execute-phase", task: "t2" }), /one stage at a time/);
  core.answer(a.stageId, "q1", { selected: ["French"] });
  await core.stages.get(a.stageId).done;
  const rec = core.stages.get(a.stageId);
  assert.equal(rec.status, "completed");
  assert.equal(rec.client.closed, true, "client closed after the stage");
  const p = join(STATE, "receipts", `${a.stageId}.json`);
  assert.equal(statSync(p).mode & 0o777, 0o600);
  const body = JSON.parse(readFileSync(p, "utf8"));
  assert.equal(JSON.stringify(body).includes("French"), true, "lastText is kept (no secret in it)");
  for (const q of body.questions) { assert.equal("answered" in q, true); assert.equal(typeof q.answered, "boolean"); }
});

test("an abort during set_ask_dialog wins: the prompt is never sent", async () => {
  const log = join(STATE, "prompt.log");
  const core: any = fakeCore({ FAKE_OMP_ASK_DELAY_MS: "400", FAKE_OMP_PROMPT_LOG: log });
  const a = core.runStage({ stage: "plan", gsdCommand: "gsd-plan-phase", task: "t" });
  await new Promise((r) => setTimeout(r, 50));
  await core.abort(a.stageId);
  await core.stages.get(a.stageId).done;
  assert.equal(core.status(a.stageId).status, "aborted");
  await new Promise((r) => setTimeout(r, 600));
  assert.equal(existsSync(log), false, "prompt was not sent after abort");
});

test("redactSecrets hides credential-shaped text", () => {
  const s = redactSecrets("err: api_key=abcd1234efgh token: sk-proj-AAAAAAAAAAAAAAAAAAAA Bearer abcdefghijklmnop eyJhbGciOiJI.eyJzdWIiOiIx.sig ok");
  for (const leak of ["abcd1234efgh", "sk-proj-AAAA", "abcdefghijklmnop", "eyJhbGciOiJI"]) assert.equal(s.includes(leak), false, leak);
  assert.match(s, /ok$/);
});

test("errorReply maps path_refused distinctly from stale_answer and generic errors", async () => {
  // Importing server.ts must run nothing: no readline on the test's stdin, no Core, no ready line on stdout.
  // A regression boots the server inside this suite and hangs the runner instead of passing silently.
  const { errorReply } = await import("../src/core/server.ts");
  assert.deepEqual(errorReply(new PathRefused("nope")), { ok: false, error: "nope", code: "path_refused" });
  assert.equal(errorReply(new StaleAnswerError("q9", "test")).code, "stale_answer");
  assert.deepEqual(errorReply(new Error("boom")), { ok: false, error: "boom", code: "error" });
});

/** Minimal SandboxTier for gsd.ts: only exec and the two host-path fields are read. No spawn. */
function gsdTier(workspaceHostPath: string | null, hostRoot: string, exec: SandboxTier["exec"]): SandboxTier & { hostRoot: string } {
  return {
    name: "bwrap", id: "fake-gsd", workdir: "/sbx/work", sandboxRoot: "/sbx", isolation: "none (test)",
    workspaceHostPath, hostRoot, exec,
    start: async () => {},
    sandboxAgent: () => ({ baseUrl: "", token: "" }),
    spawnStdio: () => { throw new Error("gsd tests must not spawn"); },
    owned: () => ({}),
    dispose: async () => ({ leftovers: [] }),
  };
}

test("ensureGsd and gsdCommandsOnHost target the mounted workspace, never the hidden hostRoot/work", async () => {
  const noExec: SandboxTier["exec"] = async () => { throw new Error("cached/warm path must not exec"); };

  // Cold: the in-sandbox install lands in the workspace through the bind; the host cache fills from it.
  const coldWs = mkdtempSync(join(tmpdir(), "omp-sbx-gsd-cws-"));
  const coldHr = mkdtempSync(join(tmpdir(), "omp-sbx-gsd-chr-"));
  const cold = await ensureGsd(gsdTier(coldWs, coldHr, async () => {
    mkdirSync(join(coldWs, ".claude", "commands"), { recursive: true });   // npx writes through the bind
    writeFileSync(join(coldWs, ".claude", "commands", "gsd-plan-phase.md"), "plan");
    return { code: 0, stdout: "", stderr: "" };
  }));
  assert.equal(cold.source, "installed-in-sandbox");
  assert.equal(existsSync(join(GSD_CACHE, ".claude", "commands", "gsd-plan-phase.md")), true, "host cache filled from the workspace");
  assert.equal(existsSync(join(coldHr, "work")), false, "cold path never creates hostRoot/work");

  // Warm: the cache fills the workspace copy; exec never runs; hostRoot/work stays absent.
  const ws = mkdtempSync(join(tmpdir(), "omp-sbx-gsd-ws-"));
  const hostRoot = mkdtempSync(join(tmpdir(), "omp-sbx-gsd-hr-"));
  const warm = await ensureGsd(gsdTier(ws, hostRoot, noExec));
  assert.equal(warm.source, "cache");
  assert.equal(existsSync(join(ws, ".claude", "commands", "gsd-plan-phase.md")), true, "warm copy landed in the mounted workspace");
  assert.equal(existsSync(join(hostRoot, "work")), false, "mounted run never touches hostRoot/work");

  // gsdCommandsOnHost counts the mounted workspace and ignores the stale hostRoot/work location.
  writeFileSync(join(ws, ".claude", "commands", "gsd-execute-phase.md"), "x");
  mkdirSync(join(hostRoot, "work", ".claude", "commands"), { recursive: true });
  writeFileSync(join(hostRoot, "work", ".claude", "commands", "gsd-stale.md"), "stale");
  assert.equal(gsdCommandsOnHost({ workspaceHostPath: ws, hostRoot }), 2, "counts gsd-*.md in the mounted workspace");
  assert.equal(gsdCommandsOnHost({ hostRoot }), 1, "without a mount the hostRoot/work location still counts");
  assert.equal(gsdCommandsOnHost({}), null);
});

test("passEnv forwards only allowlisted *_API_KEY names that are set, and only when opted in", async () => {
  const { passEnv } = await import("../src/core/core.ts");
  assert.deepEqual(passEnv({}), { names: [], env: {} });
  assert.deepEqual(passEnv({ ZAI_API_KEY: "v" }).names, [], "no opt-in, nothing passes");
  const r = passEnv({ OMP_SANDBOX_PASS_ENV: "ZAI_API_KEY, HOME ,OPENROUTER_API_KEY,GITHUB_TOKEN,ZHIPU_API_KEY", ZAI_API_KEY: "v", HOME: "/h", GITHUB_TOKEN: "t", ZHIPU_API_KEY: "" });
  assert.deepEqual(r.names, ["ZAI_API_KEY"]);
  assert.deepEqual(Object.keys(r.env), ["ZAI_API_KEY"]);
});
