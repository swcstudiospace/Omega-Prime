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
const utilMod = await import("../src/core/providers/util.ts");
const { redactSecrets } = utilMod;
const podmanMod = await import("../src/core/providers/openshell-podman.ts");
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

test("an aborted stage still blocks a new one until its omp session has settled", async () => {
  const core: any = fakeCore({ FAKE_OMP_ASK_DELAY_MS: "400" });
  const a = core.runStage({ stage: "plan", gsdCommand: "gsd-plan-phase", task: "t" });
  await new Promise((r) => setTimeout(r, 50));
  const aborting = core.abort(a.stageId);
  assert.equal(core.status(a.stageId).status, "aborted", "status flips to aborted before omp has exited");
  assert.throws(() => core.runStage({ stage: "execute", gsdCommand: "gsd-execute-phase", task: "t2" }), /winding down \(aborted\)/);
  await aborting; await core.stages.get(a.stageId).done;
  const b = core.runStage({ stage: "plan", gsdCommand: "gsd-plan-phase", task: "t3" });
  await core.abort(b.stageId);
});

test("ownsGroup signals only groups that still hold a process carrying our marker", () => {
  const { ownsGroup } = utilMod;
  const proc = mkdtempSync(join(tmpdir(), "omp-sbx-proc-"));
  const fake = (pid: number, pgrp: number, env: string[]) => {
    mkdirSync(join(proc, String(pid)));
    // comm may itself contain spaces and parentheses; fields are read after the last ")".
    writeFileSync(join(proc, String(pid), "stat"), `${pid} (we ird) cmd) S 1 ${pgrp} ${pgrp} 0 -1`);
    writeFileSync(join(proc, String(pid), "environ"), env.join("\0") + "\0");
  };
  fake(101, 101, ["HOME=/x", "OMP_SANDBOX_TIER_ID=lo-mine"]);
  fake(202, 202, ["HOME=/y", "OMP_SANDBOX_TIER_ID=lo-other"]);   // reused id, someone else's job
  fake(303, 101, ["HOME=/z"]);
  assert.equal(ownsGroup(101, "OMP_SANDBOX_TIER_ID=lo-mine", proc), true);
  assert.equal(ownsGroup(202, "OMP_SANDBOX_TIER_ID=lo-mine", proc), false);
  assert.equal(ownsGroup(404, "OMP_SANDBOX_TIER_ID=lo-mine", proc), false, "an ended group is not ours");
});

test("a tier still starting up holds a lease, so shared services are not stopped under it", () => {
  const { otherLeases } = podmanMod;
  const dir = mkdtempSync(join(tmpdir(), "omp-sbx-leases-"));
  writeFileSync(join(dir, "osp-me"), String(process.pid));
  writeFileSync(join(dir, "osp-starting"), String(process.pid));
  writeFileSync(join(dir, "osp-crashed"), "2147483646");
  writeFileSync(join(dir, "osp-garbage"), "not-a-pid");
  assert.deepEqual(otherLeases("osp-me", dir), ["osp-starting"]);
  assert.deepEqual(otherLeases("osp-me", join(dir, "missing")), []);
});

test("redactSecrets hides credential-shaped text", () => {
  // Built at runtime so no credential-shaped literal sits in the source.
  const key = ["sk", "proj", "A".repeat(20)].join("-");
  const kv = ["abcd", "1234", "efgh"].join("");
  const bearer = "abcdefgh" + "ijklmnop";
  const jwt = ["eyJ" + "hbGciOiJI", "eyJ" + "zdWIiOiIx", "sig"].join(".");
  const s = redactSecrets(`err: api_key=${kv} token: ${key} Bearer ${bearer} ${jwt} ok`);
  for (const leak of [kv, key.slice(0, 12), bearer, jwt.slice(0, 12)]) assert.equal(s.includes(leak), false, leak);
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
