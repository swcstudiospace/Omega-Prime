// Greptile round-1 guards: tier names, prompt-admission failures, overlapping stages, abort during setup, receipt redaction.
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, statSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const STATE = mkdtempSync(join(tmpdir(), "omp-sbx-guards-"));
process.env.OMP_SANDBOX_STATE = STATE;
const FAKE = new URL("./fake-omp.mjs", import.meta.url).pathname;
const { OmpRpcClient } = await import("../src/core/omp-rpc.ts");
const { validateTiers, startTier } = await import("../src/core/providers/tiers.ts");
const { redactSecrets } = await import("../src/core/providers/util.ts");
const { Core } = await import("../src/core/core.ts");

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
