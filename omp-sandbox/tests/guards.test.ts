// Greptile round-1 guards: tier names, prompt-admission failures, overlapping stages, abort during setup, receipt redaction.
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, statSync, existsSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const STATE = mkdtempSync(join(tmpdir(), "omp-sbx-guards-"));
process.env.OMP_SANDBOX_STATE = STATE;
const FAKE = new URL("./fake-omp.mjs", import.meta.url).pathname;
const { OmpRpcClient } = await import("../src/core/omp-rpc.ts");
const { validateTiers, startTier } = await import("../src/core/providers/tiers.ts");
const utilMod = await import("../src/core/providers/util.ts");
const { redactSecrets } = utilMod;
const podmanMod = await import("../src/core/providers/openshell-podman.ts");
const { Core } = await import("../src/core/core.ts");
const { AuthBroker } = await import("../src/core/auth.ts");

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

test("down() refuses new stages and startups for its whole duration", async () => {
  const core: any = new Core();
  let release: () => void = () => {};
  const slow = new Promise<void>((r) => { release = r; });
  const tier: any = { name: "fake", id: "fake", isolation: "none (test)", owned: () => ({}), spawnStdio: () => fakeProc(), dispose: async () => { await slow; return { leftovers: [] }; } };
  core.attachTierForTests(tier);
  const downing = core.down();
  await new Promise((r) => setTimeout(r, 20));   // down() is now waiting on tier cleanup
  assert.throws(() => core.runStage({ stage: "plan", gsdCommand: "gsd-plan-phase", task: "t" }), /shutting down/);
  await assert.rejects(core.up(), /shutting down/);
  assert.equal(core.down(), downing, "a second down() joins the same shutdown");
  release();
  const out = await downing;
  assert.deepEqual(out.leftovers, []);
  assert.throws(() => core.runStage({ stage: "plan", gsdCommand: "gsd-plan-phase", task: "t" }), /not up/);
});

test("AuthBroker.stop() returns at once for a broker already killed by a signal", async () => {
  const b: any = new AuthBroker();
  const p = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], { stdio: "ignore" });
  await new Promise((r) => p.once("spawn", r));
  p.kill("SIGKILL");
  await new Promise((r) => p.once("exit", r));
  assert.equal(p.exitCode, null); assert.equal(p.signalCode, "SIGKILL");
  b.proc = p;
  let t: NodeJS.Timeout | undefined;
  const hung = new Promise((_, rej) => { t = setTimeout(() => rej(new Error("stop() hung")), 2000); });
  await Promise.race([b.stop(), hung]).finally(() => clearTimeout(t));
  assert.equal(b.proc, null);
});

test("a sandbox-agent binary that cannot launch rejects startup instead of crashing Node", async () => {
  const { launched } = utilMod;
  const p = spawn(join(STATE, "no-such-sandbox-agent"), [], { stdio: "ignore" });
  const never = new Promise<void>(() => {});
  await assert.rejects(launched(p, "sandbox-agent", never), /sandbox-agent could not start: .*ENOENT/);
  const q = spawn(process.execPath, ["-e", "process.exit(4)"], { stdio: "ignore" });
  await assert.rejects(launched(q, "sandbox-agent", never), /exited during startup \(4\)/);
  const ok = spawn(process.execPath, ["-e", "setTimeout(() => {}, 200)"], { stdio: "ignore" });
  assert.equal(await launched(ok, "x", Promise.resolve(7)), 7);
});

// Greptile round 4: the fallback tiers find Bun/omp wherever they are installed and link the configured omp.
test("pickBin and onPath find a toolchain outside ~/.bun and honour an explicit setting", async () => {
  const { pickBin, onPath, toolchainRoot } = await import("../src/core/config.ts");
  const d = mkdtempSync(join(tmpdir(), "omp-sbx-bin-"));
  const bun = join(d, "bun"); writeFileSync(bun, "#!/bin/sh\n");
  assert.equal(onPath("bun", `/nonexistent:${d}`), bun);
  assert.equal(onPath("bun", "/nonexistent"), null);
  // A relative PATH entry is skipped: the result is used as an absolute symlink target.
  const prev = process.cwd(); process.chdir(d);
  try { assert.equal(onPath("bun", "."), null); assert.equal(pickBin("./bun", []), bun); } finally { process.chdir(prev); }
  assert.equal(pickBin(undefined, [join(d, "missing"), onPath("bun", d)]), bun);
  assert.equal(pickBin("/explicit/bun", [bun]), "/explicit/bun");
  assert.equal(pickBin(undefined, [join(d, "missing"), null]), join(d, "missing"));
  const pkg = join(d, "pkg", "node_modules", "@scope", "cli", "dist"); mkdirSync(pkg, { recursive: true });
  writeFileSync(join(pkg, "cli.js"), "");
  // Only the node_modules tree is exposed, not the project that holds it.
  assert.equal(toolchainRoot(join(pkg, "cli.js")), join(d, "pkg", "node_modules"));
  assert.equal(toolchainRoot(bun), d);
});

test("bwrap binds the configured omp and Bun folders read-only, deduplicated and skipping /usr", async () => {
  const { toolchainBinds } = await import("../src/core/providers/bwrap.ts");
  const d = mkdtempSync(join(tmpdir(), "omp-sbx-tc-"));
  const inner = join(d, "inner"); mkdirSync(inner);
  writeFileSync(join(d, "bun"), ""); writeFileSync(join(inner, "omp"), "");
  assert.deepEqual(toolchainBinds([join(inner, "omp"), join(d, "bun")]), ["--ro-bind", d, d]);
  assert.deepEqual(toolchainBinds(["/usr/bin/env"]), []);
  assert.deepEqual(toolchainBinds([join(d, "gone", "omp")]), []);
});

test("launched rejects (instead of crashing) when a launcher cannot execute", async () => {
  const p = spawn(join(STATE, "no-such-gateway"), [], { stdio: ["ignore", "pipe", "pipe"] });
  await assert.rejects(utilMod.launched(p, "openshell-gateway", new Promise(() => {})), /openshell-gateway could not start/);
});

// Greptile round-N guards: broken input pipe, remote cleanup before stage_done, shared-service ownership.

test("a closed omp stdin fails the request instead of crashing the core (EPIPE)", async () => {
  // The child closes its stdin at once and keeps running, so our next write hits a broken pipe.
  const proc = spawn("sh", ["-c", "exec 0<&-; sleep 30"], { stdio: ["pipe", "pipe", "pipe"] });
  const c = new OmpRpcClient(proc as any, "fake");
  await new Promise((r) => setTimeout(r, 200));
  await assert.rejects(c.command("get_state", {}, 10_000), /input pipe failed/);
  assert.ok(c.inputError, "input error recorded");
  assert.throws(() => c.send({ type: "abort" }), /input pipe failed/);
  await c.close(1000);
  assert.equal(c.closed, true, "child stopped after the pipe broke");
});

test("stage_done waits for the tier's remote cleanup, and a failed cleanup refuses new work", async () => {
  // Cleanup that takes a while: stage_done must not fire before it finishes.
  let release!: () => void;
  const slow = new Promise<void>((r) => { release = r; });
  let settledAt = 0;
  const core: any = new Core();
  core.attachTierForTests({ name: "fake", id: "fake", isolation: "none (test)", owned: () => ({}), dispose: async () => ({ leftovers: [] }),
    spawnStdio: () => fakeProc(), settled: () => slow.then(() => { settledAt = Date.now(); }) } as any);
  let doneAt = 0; core.on("event", (e: any) => { if (e.event === "stage_done") doneAt = Date.now(); });
  const a = core.runStage({ stage: "plan", gsdCommand: "gsd-plan-phase", task: "t" });
  for (let i = 0; i < 100 && core.status(a.stageId).status !== "waiting"; i++) await new Promise((r) => setTimeout(r, 50));
  core.answer(a.stageId, "q1", {});
  await new Promise((r) => setTimeout(r, 300));
  assert.equal(doneAt, 0, "stage_done held while remote cleanup runs");
  assert.throws(() => core.runStage({ stage: "execute", gsdCommand: "gsd-execute-phase", task: "t2" }), /one stage at a time/);
  release();
  await core.stages.get(a.stageId).done;
  assert.ok(doneAt >= settledAt && settledAt > 0, "stage_done after cleanup");

  // Cleanup that fails: the stage fails and the core takes no further stages on this workdir.
  const bad: any = new Core();
  bad.attachTierForTests({ name: "fake", id: "fake", isolation: "none (test)", owned: () => ({}), dispose: async () => ({ leftovers: [] }),
    spawnStdio: () => fakeProc(), settled: () => Promise.reject(new Error("remote session still running")) } as any);
  const b = bad.runStage({ stage: "plan", gsdCommand: "gsd-plan-phase", task: "t" });
  for (let i = 0; i < 100 && bad.status(b.stageId).status !== "waiting"; i++) await new Promise((r) => setTimeout(r, 50));
  bad.answer(b.stageId, "q1", {});
  await bad.stages.get(b.stageId).done;
  assert.equal(bad.status(b.stageId).status, "failed");
  assert.match(bad.status(b.stageId).error, /still running/);
  assert.throws(() => bad.runStage({ stage: "execute", gsdCommand: "gsd-execute-phase", task: "t2" }), /refusing new work/);
});

test("the remote session kill script confirms the whole session is gone", { skip: process.platform !== "linux" }, async () => {
  const tag = `t${process.pid}${Date.now().toString(36)}`;
  // A session leader with a background descendant, like omp plus a tool it started.
  const s = spawn("setsid", ["-w", "sh", "-c", `echo $$ > /tmp/.ompsbx-${tag}.sid; sleep 60 & sleep 60`], { stdio: "ignore" });
  for (let i = 0; i < 50 && !existsSync(`/tmp/.ompsbx-${tag}.sid`); i++) await new Promise((r) => setTimeout(r, 50));
  const sid = readFileSync(`/tmp/.ompsbx-${tag}.sid`, "utf8").trim();
  const r = await utilMod.execCapture("sh", ["-c", podmanMod.killSessionScript(tag)], { timeoutMs: 20_000 });
  assert.equal(r.code, 0, r.stderr);
  const left = await utilMod.execCapture("pgrep", ["-s", sid]);
  assert.equal(left.code, 1, `session ${sid} still has: ${left.stdout}`);
  assert.equal(existsSync(`/tmp/.ompsbx-${tag}.sid`), false);
  s.kill("SIGKILL");
  // No session file means nothing was started: settled.
  assert.equal((await utilMod.execCapture("sh", ["-c", podmanMod.killSessionScript(`${tag}-none`)])).code, 0);
});

test("the last lease holder stops shared services another tier started; reused pids are left alone", { skip: process.platform !== "linux" }, async () => {
  const file = join(STATE, "services.json");
  const gw = spawn("sleep", ["60"], { stdio: "ignore", detached: true });
  const other = spawn("sleep", ["60"], { stdio: "ignore", detached: true });
  await new Promise((r) => setTimeout(r, 100));
  podmanMod.recordService("gateway", gw.pid, file);
  // Same pid as a live process but a different start time: a reused pid, not our service.
  writeFileSync(file, JSON.stringify({ ...podmanMod.readServices(file), podmanService: { pid: other.pid, startTicks: podmanMod.procStartTicks(other.pid!)! - 1 } }));
  assert.equal(podmanMod.sameService(podmanMod.readServices(file).gateway), true);
  const left = await podmanMod.stopSharedServices(file);
  assert.deepEqual(left, []);
  assert.equal(utilMod.pidAlive(gw.pid), false, "recorded gateway stopped by a tier that never held its handle");
  assert.equal(utilMod.pidAlive(other.pid), true, "unrelated process with a reused pid untouched");
  assert.equal(existsSync(file), false, "ownership record cleared");
  other.kill("SIGKILL");
});

test("a late abort after omp exited rejects once and leaves no timer to crash the core", async () => {
  const unhandled: unknown[] = [];
  const onU = (e: unknown) => unhandled.push(e);
  process.on("unhandledRejection", onU);
  const c = new OmpRpcClient(fakeProc({ FAKE_OMP_EXIT_ON_PROMPT: "1" }), "fake");
  await assert.rejects(c.prompt("hi"));
  await assert.rejects(c.command("abort", {}, 150), /closed/);
  await c.abort();   // the public path swallows the send error
  await new Promise((r) => setTimeout(r, 400));
  process.off("unhandledRejection", onU);
  assert.deepEqual(unhandled, []);
});

test("agentos-local settles a session only after the tools it left behind are stopped", { skip: process.platform !== "linux" }, async () => {
  const { LocalTier } = await import("../src/core/providers/local.ts");
  const t = new LocalTier();
  mkdirSync(t.workdir, { recursive: true });
  const p = t.spawnStdio(["sh", "-c", "sleep 60 & echo $!; exit 0"]);
  let out = ""; p.stdout.on("data", (d) => { out += d; });
  await t.settled(p);
  const bg = Number(out.trim());
  assert.ok(bg > 0, "background tool pid reported");
  assert.equal(utilMod.ownsGroup(p.pid!, `OMP_SANDBOX_TIER_ID=${t.id}`), false, "no process of the session's group is left");
  assert.equal(existsSync(`/proc/${bg}/environ`) && readFileSync(`/proc/${bg}/environ`, "utf8").includes(t.id), false, "the leftover tool was stopped");
  assert.deepEqual((t.owned() as any).processGroups, []);
  await t.dispose();
});

test("parallel wrappers recording shared services never erase each other's entry", { skip: process.platform !== "linux" }, async () => {
  const file = join(STATE, "services-race.json");
  const a = spawn("sleep", ["60"], { stdio: "ignore", detached: true });
  const b = spawn("sleep", ["60"], { stdio: "ignore", detached: true });
  await new Promise((r) => setTimeout(r, 100));
  const mod = new URL("../src/core/providers/openshell-podman.ts", import.meta.url).href;
  const writer = (which: string, pid: number) => new Promise<number | null>((res) => {
    const src = `const m = await import(${JSON.stringify(mod)}); for (let i = 0; i < 40; i++) { m.recordService(${JSON.stringify(which)}, ${pid}, ${JSON.stringify(file)}); }`;
    const w = spawn(process.execPath, ["--experimental-transform-types", "--no-warnings", "--input-type=module", "-e", src], { env: { ...process.env, OMP_SANDBOX_STATE: STATE }, stdio: "inherit" });
    w.once("exit", res);
  });
  for (let round = 0; round < 3; round++) {
    writeFileSync(file, "{}");
    assert.deepEqual(await Promise.all([writer("gateway", a.pid!), writer("podmanService", b.pid!)]), [0, 0]);
    const rec = podmanMod.readServices(file);
    assert.equal(rec.gateway?.pid, a.pid); assert.equal(rec.podmanService?.pid, b.pid);
  }
  assert.equal(existsSync(`${file}.lock`), false, "lock released");
  // While another live wrapper holds the lock, an update waits for it instead of writing over that wrapper's read.
  writeFileSync(file, "{}"); writeFileSync(`${file}.lock`, String(process.pid));
  const waiting = writer("gateway", a.pid!);
  await new Promise((r) => setTimeout(r, 400));
  assert.equal(podmanMod.readServices(file).gateway, undefined, "no write while the lock is held");
  rmSync(`${file}.lock`);
  assert.equal(await waiting, 0);
  assert.equal(podmanMod.readServices(file).gateway?.pid, a.pid);
  // A lock left by a dead holder is broken instead of blocking forever.
  writeFileSync(`${file}.lock`, "999999999");
  podmanMod.recordService("gateway", a.pid, file);
  assert.equal(podmanMod.readServices(file).gateway?.pid, a.pid);
  a.kill("SIGKILL"); b.kill("SIGKILL");
});
