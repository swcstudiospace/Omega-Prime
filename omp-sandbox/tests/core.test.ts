import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { OmpRpcClient, StaleAnswerError } from "../src/core/omp-rpc.ts";
import { mockAttestation } from "../src/core/attest.ts";

test("ask answered once, stale + cancelled + unknown answers rejected", async () => {
  const proc = spawn(process.execPath, [new URL("./fake-omp.mjs", import.meta.url).pathname], { stdio: ["pipe", "pipe", "pipe"] });
  const c = new OmpRpcClient(proc as any, "fake");
  await c.command("set_ask_dialog", { enabled: true });
  const qs: any[] = []; c.on("question", (q) => qs.push(q));
  const done = c.prompt("hi");
  while (qs.length < 2) await new Promise((r) => setTimeout(r, 20));
  await new Promise((r) => setTimeout(r, 50));
  assert.throws(() => c.answer("q2", { text: "yes" }), StaleAnswerError);      // cancelled by omp
  assert.throws(() => c.answer("nope", {}), StaleAnswerError);                  // unknown
  c.answer("q1", { selected: ["French"] });
  assert.throws(() => c.answer("q1", { selected: ["French"] }), StaleAnswerError); // already answered
  const r = await done;
  assert.equal(r.status, "completed");
  assert.deepEqual([...c.models], ["fake/m1"]);
  await c.close();
  assert.throws(() => c.answer("q1", {}), StaleAnswerError);                   // closed session
});

test("attestation is always mock", () => {
  const a = mockAttestation("bwrap");
  assert.equal(a.attestation, "mock"); assert.equal(a.hardware, false);
});

test("each plan has its own plan/build pair; zai can be primary", async () => {
  const { stageRoute, STAGE_MODELS } = await import("../src/core/config.ts");
  assert.deepEqual(stageRoute("plan"), { plan: "chatgpt", primary: "openai-codex/gpt-6.1-sol", fallback: "zai/glm-5.3", fallbackPlan: "zai" });
  assert.deepEqual(stageRoute("execute"), { plan: "chatgpt", primary: "openai-codex/gpt-6-luna", fallback: "zai/glm-5.3-flash", fallbackPlan: "zai" });
  assert.deepEqual(stageRoute("plan", "zai"), { plan: "zai", primary: "zai/glm-5.3", fallback: null, fallbackPlan: null });
  assert.deepEqual(stageRoute("execute", "zai"), { plan: "zai", primary: "zai/glm-5.3-flash", fallback: null, fallbackPlan: null });
  assert.equal(STAGE_MODELS.execute.fallback, "zai/glm-5.3-flash");
  assert.equal(STAGE_MODELS.plan.fallback, "zai/glm-5.3");
  assert.throws(() => stageRoute("plan", "anthropic"), /unknown plan/);
  assert.throws(() => stageRoute("ship" as any), /unknown stage/);
});
