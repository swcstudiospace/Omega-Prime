// Greptile round-1 guards: tier names, prompt-admission failures, overlapping stages, abort during setup, receipt redaction.
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync, spawn } from "node:child_process";
import { cpSync, mkdtempSync, mkdirSync, readFileSync, realpathSync, renameSync, rmSync, statSync, existsSync, symlinkSync, utimesSync, writeFileSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
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
const { ensureGsd, gsdCommandsOnHost, fillCache, clearStaleLock, IN_SANDBOX_COPY, STALE_LOCK_MS } = await import("../src/core/gsd.ts");
const MARKER_REL = join(".claude", "commands", "gsd-plan-phase.md");
const { STATE: CORE_STATE, VERSIONS } = await import("../src/core/config.ts");
const GSD_CACHE = join(CORE_STATE, "cache", `gsd-core-${VERSIONS.gsdCore}`);
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

test("a lease-less CLI sandbox on either gateway keeps the shared Podman service up", () => {
  const { listedSandboxes, gatewaySandboxUsers } = podmanMod;
  const table = "NAME        PHASE\nosp-me      Ready\ncli-made    Ready\n";
  assert.deepEqual(listedSandboxes(table, "osp-me"), ["cli-made"]);
  assert.deepEqual(listedSandboxes("No sandboxes found\n", "osp-me"), []);
  // The mount gateway's registry counts even though no tier holds a lease for its sandbox.
  assert.deepEqual(gatewaySandboxUsers({ code: 0, stdout: table }, "osp-me", true, 18080), ["cli-made"]);
  // A list that fails while the gateway still answers is an unknown user, never "none".
  assert.equal(gatewaySandboxUsers({ code: 1, stdout: "" }, "osp-me", true, 18080).length, 1);
  // Nothing listening: that gateway is gone and holds no one.
  assert.deepEqual(gatewaySandboxUsers({ code: 1, stdout: "" }, "osp-me", false, 18080), []);
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
function gsdTier(workspaceHostPath: string | null, hostRoot: string, exec: SandboxTier["exec"], hostDir: () => string | null = () => workspaceHostPath): SandboxTier & { hostRoot: string } {
  return {
    name: "bwrap", id: "fake-gsd", workdir: "/sbx/work", sandboxRoot: "/sbx", isolation: "none (test)",
    workspaceHostPath, hostRoot, exec, workspaceHostDir: hostDir,
    start: async () => {},
    sandboxAgent: () => ({ baseUrl: "", token: "" }),
    spawnStdio: () => { throw new Error("gsd tests must not spawn"); },
    owned: () => ({}),
    dispose: async () => ({ leftovers: [] }),
  };
}

/** Stands in for the sandbox running IN_SANDBOX_COPY in /sbx/work, which is `ws` seen through the bind. */
function inSandboxCopy(ws: string, argv: string[], opts?: { cwd?: string }) {
  assert.equal(argv[2], IN_SANDBOX_COPY, "runs the in-sandbox check-and-copy script");
  assert.equal(opts?.cwd, "/sbx/work");
  const stage = join(ws, argv[4]);
  if (!existsSync(join(stage, MARKER_REL))) return { code: 1, stdout: "", stderr: "no marker" };
  cpSync(join(stage, ".claude"), join(ws, ".claude"), { recursive: true });
  return { code: 0, stdout: "", stderr: "" };
}

test("ensureGsd and gsdCommandsOnHost target the mounted workspace, never the hidden hostRoot/work", async () => {
  const noExec: SandboxTier["exec"] = async () => { throw new Error("cached/warm path must not exec"); };

  // Cold: the in-sandbox install lands in the workspace through the bind; the host cache fills from it.
  const coldWs = mkdtempSync(join(tmpdir(), "omp-sbx-gsd-cws-"));
  const coldHr = mkdtempSync(join(tmpdir(), "omp-sbx-gsd-chr-"));
  // Cold on a mounted worktree: the install serves this job only. The worktree is shared with the host and
  // other jobs, so neither its own .claude files nor anything written into it while npx runs (even inside
  // the staging folder) may reach the shared cache.
  mkdirSync(join(coldWs, ".claude", "commands"), { recursive: true });
  writeFileSync(join(coldWs, ".claude", "settings.json"), "{\"private\":true}");
  let installCwd = "";
  const install = (hostOf: (cwd: string) => string) => async (_argv: string[], opts?: { cwd?: string }) => {
    installCwd = opts?.cwd ?? "";
    assert.match(installCwd, /^\/sbx\/work\/\.omp-gsd-install-/, "installs in a fresh staging folder under the workdir");
    const staged = hostOf(installCwd.slice("/sbx/work/".length));
    assert.deepEqual(readdirSync(staged), [], "staging folder starts empty");
    mkdirSync(join(staged, ".claude", "commands"), { recursive: true });   // npx writes through the bind
    writeFileSync(join(staged, ".claude", "commands", "gsd-plan-phase.md"), "plan");
    return { code: 0, stdout: "", stderr: "" };
  };
  const mountedInstall = install((rel) => join(coldWs, rel));
  const cold = await ensureGsd(gsdTier(coldWs, coldHr, async (argv, opts) => {
    if (argv[0] === "sh") return inSandboxCopy(coldWs, argv, opts);
    const r = await mountedInstall(argv, opts);
    // Another writer on the shared worktree drops a file into the staging folder mid-install.
    writeFileSync(join(coldWs, installCwd.slice("/sbx/work/".length), ".claude", "commands", "gsd-injected.md"), "evil");
    return r;
  }));
  assert.equal(cold.source, "installed-in-sandbox");
  assert.equal(cold.cache, null, "a mounted install never fills the shared cache");
  assert.equal(existsSync(join(GSD_CACHE, ".claude")), false, "shared cache untouched by the mounted job");
  assert.equal(existsSync(join(coldWs, ".claude", "commands", "gsd-plan-phase.md")), true, "the job still gets the install");
  assert.equal(existsSync(join(coldWs, installCwd.slice("/sbx/work/".length))), false, "staging folder removed");
  assert.equal(existsSync(join(coldHr, "work")), false, "cold path never creates hostRoot/work");

  // Cold on a private work folder (no mount): only the installer's output fills the shared cache.
  const privHr = mkdtempSync(join(tmpdir(), "omp-sbx-gsd-phr-"));
  const priv = await ensureGsd(gsdTier(null, privHr, install((rel) => join(privHr, "work", rel))));
  assert.equal(priv.cache, GSD_CACHE);
  assert.deepEqual(readdirSync(join(GSD_CACHE, ".claude")), ["commands"], "no private settings in the cache");
  assert.deepEqual(readdirSync(join(GSD_CACHE, ".claude", "commands")), ["gsd-plan-phase.md"], "only installer output is cached");

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
  assert.equal(gsdCommandsOnHost({ workspaceHostPath: ws, workspaceHostDir: () => ws, hostRoot }), 2, "counts gsd-*.md in the mounted workspace");
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

test("host-side GSD writes follow the pinned worktree, not a name swapped for a symlink", { skip: process.platform !== "linux" }, async () => {
  const { pinDir, pinnedPath, unpinDir } = await import("../src/core/paths.ts");
  const noExec: SandboxTier["exec"] = async () => { throw new Error("warm path must not exec"); };
  assert.equal(existsSync(join(GSD_CACHE, MARKER_REL)), true, "the earlier GSD test filled the cache");
  const base = realpathSync(mkdtempSync(join(tmpdir(), "omp-sbx-gsd-pin-")));
  const ws = join(base, "job"); const outside = join(base, "host-home"); mkdirSync(ws); mkdirSync(outside);
  const pin = pinDir(ws, "ws");
  try {
    renameSync(ws, join(base, "job-orig")); symlinkSync(outside, ws);   // another job swaps the name after the check
    const r = await ensureGsd(gsdTier(ws, join(base, "hr"), noExec, () => pinnedPath(pin)));
    assert.equal(r.source, "cache");
    assert.equal(existsSync(join(base, "job-orig", MARKER_REL)), true, "the copy lands in the checked directory");
    assert.equal(existsSync(join(outside, ".claude")), false, "never through the swapped name");
  } finally { unpinDir(pin); }
  // A mounted tier that cannot name its pinned directory refuses rather than write by name.
  await assert.rejects(ensureGsd(gsdTier(join(base, "job-orig"), join(base, "hr"), noExec, () => null)), /not pinned/);
  // A `.claude` the job replaced with a symlink is refused, so the copy cannot be steered out of the worktree.
  const ws2 = join(base, "job2"); mkdirSync(ws2); symlinkSync(outside, join(ws2, ".claude"));
  await assert.rejects(ensureGsd(gsdTier(ws2, join(base, "hr"), noExec)), /is a symlink/);
  assert.deepEqual(readdirSync(outside), [], "nothing written through the planted link");
});

test("a mounted install is checked and copied inside the sandbox, never read on the host through the staging name", async () => {
  rmSync(GSD_CACHE, { recursive: true, force: true });   // cold path
  const ws = mkdtempSync(join(tmpdir(), "omp-sbx-gsd-swap-"));
  const other = mkdtempSync(join(tmpdir(), "omp-sbx-gsd-other-"));   // another host checkout with a GSD install
  mkdirSync(join(other, ".claude", "commands"), { recursive: true });
  writeFileSync(join(other, MARKER_REL), "plan"); writeFileSync(join(other, ".claude", "secret.json"), "other job");
  const calls: string[] = [];
  const r = await ensureGsd(gsdTier(ws, mkdtempSync(join(tmpdir(), "omp-sbx-gsd-swhr-")), async (argv, opts) => {
    calls.push(argv[0]);
    if (argv[0] === "sh") return { code: 0, stdout: "", stderr: "" };   // the sandbox did the copy; it cannot see `other`
    const stage = join(ws, (opts?.cwd ?? "").slice("/sbx/work/".length));
    rmSync(stage, { recursive: true, force: true }); symlinkSync(other, stage);   // swapped while npx ran
    return { code: 0, stdout: "", stderr: "" };
  }));
  assert.equal(r.cache, null);
  assert.deepEqual(calls, ["npx", "sh"], "install, then the in-sandbox check-and-copy");
  assert.equal(existsSync(join(ws, ".claude", "secret.json")), false, "the host never copied from the swapped staging name");
  assert.equal(existsSync(join(other, ".claude", "secret.json")), true, "cleanup removed only the link, not the other checkout");
  // A failed in-sandbox check fails the install.
  await assert.rejects(ensureGsd(gsdTier(ws, join(ws, "hr"), async (argv) =>
    ({ code: argv[0] === "sh" ? 1 : 0, stdout: "", stderr: "" }))), /in-sandbox copy exit 1/);
});

test("parallel first installs: a held fill lock skips the shared cache without failing up, and a stale lock clears", async () => {
  const claude = join(mkdtempSync(join(tmpdir(), "omp-sbx-gsd-fill-")), ".claude");
  mkdirSync(join(claude, "commands"), { recursive: true }); writeFileSync(join(claude, "commands", "gsd-plan-phase.md"), "plan");
  rmSync(GSD_CACHE, { recursive: true, force: true });
  const lock = `${GSD_CACHE}.lock`;
  writeFileSync(lock, "other-wrapper-token");      // another wrapper is mid-publish
  try {
    const hr = mkdtempSync(join(tmpdir(), "omp-sbx-gsd-lockhr-"));
    const r = await ensureGsd(gsdTier(null, hr, async (_argv, opts) => {
      const staged = join(hr, "work", (opts?.cwd ?? "").slice("/sbx/work/".length));
      mkdirSync(join(staged, ".claude", "commands"), { recursive: true });
      writeFileSync(join(staged, MARKER_REL), "plan");
      return { code: 0, stdout: "", stderr: "" };
    }));
    assert.equal(r.source, "installed-in-sandbox", "up still succeeds");
    assert.equal(existsSync(join(hr, "work", MARKER_REL)), true, "the job got its own install");
    assert.equal(r.cache, null, "it did not publish while the lock was held");
    assert.equal(existsSync(join(GSD_CACHE, MARKER_REL)), false);
    const old = (Date.now() - STALE_LOCK_MS - 60_000) / 1000;
    utimesSync(lock, old, old);
    assert.equal(fillCache(claude), false, "a stale lock is cleared, not used");
    assert.equal(existsSync(lock), false);
  } finally { rmSync(lock, { recursive: true, force: true }); }
  assert.equal(fillCache(claude), true, "the next fill publishes");
  writeFileSync(join(GSD_CACHE, ".claude", "winner"), "first");
  assert.equal(fillCache(claude), false, "a complete cache another wrapper published wins");
  assert.equal(existsSync(join(GSD_CACHE, ".claude", "winner")), true, "and is never replaced");
});

test("clearing a stale fill lock never removes a live lock that replaced it", () => {
  const lock = `${GSD_CACHE}.lock`;
  mkdirSync(dirname(lock), { recursive: true });
  const old = (Date.now() - STALE_LOCK_MS - 60_000) / 1000;
  try {
    writeFileSync(lock, "crashed-token"); utimesSync(lock, old, old);
    // Between the stale check and the move, another clearer removed it and a new filler took the lock.
    const cleared = clearStaleLock(Date.now(), () => { rmSync(lock, { force: true }); writeFileSync(lock, "live-token"); });
    assert.equal(cleared, false, "the live lock is not reported as cleared");
    assert.equal(readFileSync(lock, "utf8"), "live-token", "and it is back under its name");
    assert.deepEqual(readdirSync(dirname(lock)).filter((f) => f.includes(".lock.stale-")), [], "no lock left aside");
    utimesSync(lock, old, old);
    assert.equal(clearStaleLock(), true, "a genuinely stale lock is cleared");
    assert.equal(existsSync(lock), false);
    writeFileSync(lock, "fresh-token");
    assert.equal(clearStaleLock(), false, "a fresh lock is never touched");
    assert.equal(readFileSync(lock, "utf8"), "fresh-token");
  } finally { rmSync(lock, { force: true }); }
});

test("a fill only releases the lock it still holds, and never throws", () => {
  const claude = join(mkdtempSync(join(tmpdir(), "omp-sbx-gsd-own-")), ".claude");
  mkdirSync(join(claude, "commands"), { recursive: true }); writeFileSync(join(claude, "commands", "gsd-plan-phase.md"), "plan");
  rmSync(GSD_CACHE, { recursive: true, force: true });
  assert.equal(fillCache(join(claude, "missing")), false, "a broken source tree returns false instead of failing up");
  assert.equal(existsSync(`${GSD_CACHE}.lock`), false, "its own lock is released");
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

test("bwrap keeps the tier env (broker token, passed API keys) off argv and in a 0600 sourced file", async () => {
  const { BwrapTier, envFileText, BWRAP_ENV_FILE } = await import("../src/core/providers/bwrap.ts");
  const root = mkdtempSync(join(tmpdir(), "omp-sbx-bw-"));
  const t = new BwrapTier(root);
  const secret = ["zai", "value", "with'quote"].join("-");
  t.writeEnvFile({ ZAI_API_KEY: secret });
  const argv = t.argv(["omp", "--version"], { CI: "1" });
  assert.equal(argv.join("\0").includes(secret), false, "no secret value on the bwrap command line");
  assert.equal(argv.includes("CI"), true, "per-call non-secret env still rides --setenv");
  assert.ok(argv.join(" ").includes(`. ${BWRAP_ENV_FILE}`), "the sandbox command sources the env file");
  const file = join(root, "home", ".omp-sandbox.env");
  assert.equal(statSync(file).mode & 0o777, 0o600);
  // The quoting round-trips through a real POSIX shell.
  const out = execFileSync("sh", ["-c", `. "$1"; printf %s "$ZAI_API_KEY"`, "sh", file], { encoding: "utf8" });
  assert.equal(out, secret);
  assert.throws(() => envFileText({ "BAD-NAME": "v" }), /bad env name/);
});

test("bwrap and agentos-local transfer files inside their own roots, and refuse paths outside", async () => {
  const { BwrapTier } = await import("../src/core/providers/bwrap.ts");
  const root = mkdtempSync(join(tmpdir(), "omp-sbx-bwx-"));
  mkdirSync(join(root, "home"), { recursive: true });
  const t = new BwrapTier(root);
  const src = join(mkdtempSync(join(tmpdir(), "omp-sbx-src-")), "f.bin"); writeFileSync(src, "bytes");
  assert.equal((await t.upload(src, "/sbx/home/f.bin")).code, 0);
  assert.equal(readFileSync(join(root, "home", "f.bin"), "utf8"), "bytes");
  const out = mkdtempSync(join(tmpdir(), "omp-sbx-dl-"));
  assert.equal((await t.download("/sbx/home/f.bin", out)).code, 0);
  assert.equal(readFileSync(join(out, "f.bin"), "utf8"), "bytes");
  await assert.rejects(t.upload(src, "/etc/passwd"), /outside the sandbox root/);
  await assert.rejects(t.download("/sbx/../etc/passwd", out), /outside the sandbox root/);
  const { LocalTier } = await import("../src/core/providers/local.ts");
  const l = new LocalTier(); mkdirSync(join(l.hostRoot, "home"), { recursive: true });
  assert.equal((await l.upload(src, join(l.hostRoot, "home", "g.bin"))).code, 0);
  assert.equal((await l.download(join(l.hostRoot, "home", "g.bin"), out)).code, 0);
  assert.equal(readFileSync(join(out, "g.bin"), "utf8"), "bytes");
  await assert.rejects(l.download("/etc/hostname", out), /outside the tier root/);
  rmSync(l.hostRoot, { recursive: true, force: true });
});

test("agentos-local dispose reaps a check that exec started", async () => {
  const { LocalTier } = await import("../src/core/providers/local.ts");
  const l = new LocalTier(); mkdirSync(l.workdir, { recursive: true });
  const running = l.exec(["sh", "-c", "sleep 30"], { timeoutMs: 0 });
  await new Promise((r) => setTimeout(r, 200));
  const groups = (l.owned() as { processGroups: number[] }).processGroups;
  assert.equal(groups.length, 1, "the exec's process group is tracked");
  const { leftovers } = await l.dispose();
  assert.deepEqual(leftovers, []);
  const r = await running;
  assert.notEqual(r.code, 0, "the check was killed with the tier");
});

test("GSD agent conversion: host and in-sandbox paths write the same files and keep project-owned agents", async () => {
  const { writeOmpAgents, IN_SANDBOX_CONVERT, GENERATED_MARK } = await import("../src/core/gsd.ts");
  const fixture = (dir: string) => {
    mkdirSync(join(dir, ".claude", "agents"), { recursive: true });
    writeFileSync(join(dir, ".claude", "agents", "gsd-planner.md"), "---\nname: gsd-planner\ndescription: \"Plans phases\"\ntools: Read, Bash\n---\nYou plan.\n");
    writeFileSync(join(dir, ".claude", "agents", "gsd-executor.md"), "---\nname: gsd-executor\ndescription: Executes plans\n---\nYou execute.\n");
    writeFileSync(join(dir, ".claude", "agents", "gsd-nodesc.md"), "---\nname: gsd-nodesc\n---\nskipped\n");
    writeFileSync(join(dir, ".claude", "agents", "other.md"), "---\nname: other\ndescription: not gsd\n---\n");
  };
  const host = mkdtempSync(join(tmpdir(), "omp-sbx-agents-h-")); fixture(host);
  const guest = mkdtempSync(join(tmpdir(), "omp-sbx-agents-g-")); fixture(guest);
  assert.equal(writeOmpAgents(host), 2);
  assert.equal(execFileSync(process.execPath, ["-e", IN_SANDBOX_CONVERT], { cwd: guest, encoding: "utf8" }).trim(), "2");
  for (const f of ["gsd-planner.md", "gsd-executor.md"]) {
    const h = readFileSync(join(host, ".omp", "agents", f), "utf8");
    assert.equal(h, readFileSync(join(guest, ".omp", "agents", f), "utf8"), `${f} matches across converters`);
    assert.ok(h.includes(GENERATED_MARK));
  }
  assert.equal(readFileSync(join(host, ".omp", "agents", "gsd-planner.md"), "utf8"), `---\nname: "gsd-planner"\ndescription: "Plans phases"\nspawns: "*"\n---\n${GENERATED_MARK}\nYou plan.\n`);
  assert.equal(existsSync(join(host, ".omp", "agents", "gsd-nodesc.md")), false);
  assert.equal(existsSync(join(host, ".omp", "agents", "other.md")), false);
  // A project's own agent file (no mark) survives both converters; a generated one is refreshed.
  for (const [dir, run] of [[host, () => writeOmpAgents(host)], [guest, () => execFileSync(process.execPath, ["-e", IN_SANDBOX_CONVERT], { cwd: guest })]] as const) {
    writeFileSync(join(dir, ".omp", "agents", "gsd-planner.md"), "---\nname: gsd-planner\ndescription: project tuned\n---\nlocal edits\n");
    writeFileSync(join(dir, ".claude", "agents", "gsd-executor.md"), "---\nname: gsd-executor\ndescription: Executes plans v2\n---\nYou execute.\n");
    run();
    assert.equal(readFileSync(join(dir, ".omp", "agents", "gsd-planner.md"), "utf8"), "---\nname: gsd-planner\ndescription: project tuned\n---\nlocal edits\n");
    assert.match(readFileSync(join(dir, ".omp", "agents", "gsd-executor.md"), "utf8"), /Executes plans v2/);
  }
});

test("host-side GSD agent conversion never reads or writes through links the job planted", async () => {
  const { writeOmpAgents } = await import("../src/core/gsd.ts");
  const outside = mkdtempSync(join(tmpdir(), "omp-sbx-agents-out-"));
  writeFileSync(join(outside, "host-file.md"), "---\nname: gsd-leak\ndescription: host file\n---\nhost-only text\n");
  const desc = "---\nname: gsd-planner\ndescription: Plans\n---\nYou plan.\n";
  // A linked agent file is skipped, never copied into .omp/agents.
  const a = mkdtempSync(join(tmpdir(), "omp-sbx-agents-l1-"));
  mkdirSync(join(a, ".claude", "agents"), { recursive: true });
  writeFileSync(join(a, ".claude", "agents", "gsd-planner.md"), desc);
  symlinkSync(join(outside, "host-file.md"), join(a, ".claude", "agents", "gsd-leak.md"));
  assert.equal(writeOmpAgents(a), 1);
  assert.equal(existsSync(join(a, ".omp", "agents", "gsd-leak.md")), false, "linked source skipped");
  // A linked output file is never written through.
  symlinkSync(join(outside, "host-file.md"), join(a, ".omp", "agents", "gsd-executor.md"));
  writeFileSync(join(a, ".claude", "agents", "gsd-executor.md"), "---\nname: gsd-executor\ndescription: Exec\n---\nx\n");
  writeOmpAgents(a);
  assert.match(readFileSync(join(outside, "host-file.md"), "utf8"), /host-only text/, "host file untouched");
  // A linked .claude/agents, .omp or .omp/agents is refused.
  for (const link of [[".claude", "agents"], [".omp"], [".omp", "agents"]]) {
    const w = mkdtempSync(join(tmpdir(), "omp-sbx-agents-l2-"));
    mkdirSync(join(w, ".claude"), { recursive: true });
    if (link[0] === ".omp") { mkdirSync(join(w, ".claude", "agents")); writeFileSync(join(w, ".claude", "agents", "gsd-planner.md"), desc); }
    if (link.length === 2 && link[0] === ".omp") mkdirSync(join(w, ".omp"));
    symlinkSync(outside, join(w, ...link));
    assert.throws(() => writeOmpAgents(w), /is a symlink/, `${link.join("/")} link refused`);
  }
  assert.deepEqual(readdirSync(outside), ["host-file.md"], "nothing written outside the worktree");
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

test("the mount gateway's last tier stops only that gateway; the shared Podman service record survives", { skip: process.platform !== "linux" }, async () => {
  const file = join(STATE, "services-only.json");
  const gw = spawn("sleep", ["60"], { stdio: "ignore", detached: true });
  const svc = spawn("sleep", ["60"], { stdio: "ignore", detached: true });
  await new Promise((r) => setTimeout(r, 100));
  podmanMod.recordService("gateway", gw.pid, file);
  podmanMod.recordService("podmanService", svc.pid, file);
  const left = await podmanMod.stopSharedServices(file, "/proc", ["gateway"]);
  assert.deepEqual(left, []);
  assert.equal(utilMod.pidAlive(gw.pid), false, "the gateway asked for is stopped");
  assert.equal(utilMod.pidAlive(svc.pid), true, "the Podman service another variant still shares keeps running");
  const rec = podmanMod.readServices(file);
  assert.equal(rec.gateway, undefined, "stopped gateway's record cleared");
  assert.equal(rec.podmanService?.pid, svc.pid, "the Podman service keeps its owner record");
  svc.kill("SIGKILL"); rmSync(file, { force: true });
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
  assert.equal(podmanMod.servicesLockHolder(file), null, "lock released");
  const lockDir = podmanMod.servicesLockDir(file);
  const gens = () => readdirSync(lockDir).filter((e) => /^\d{16}$/.test(e)).sort();
  const nextGen = () => String(Number(gens().at(-1) ?? 0) + 1).padStart(16, "0");
  // While another live wrapper holds the lock, an update waits for it instead of writing over that wrapper's read.
  writeFileSync(file, "{}");
  const held = nextGen(); writeFileSync(join(lockDir, held), String(process.pid));
  const waiting = writer("gateway", a.pid!);
  await new Promise((r) => setTimeout(r, 400));
  assert.equal(podmanMod.readServices(file).gateway, undefined, "no write while the lock is held");
  writeFileSync(join(lockDir, `${held}.done`), "");
  assert.equal(await waiting, 0);
  assert.equal(podmanMod.readServices(file).gateway?.pid, a.pid);
  // A lock left by a dead holder is taken over instead of blocking forever.
  writeFileSync(join(lockDir, nextGen()), "999999999");
  podmanMod.recordService("gateway", a.pid, file);
  assert.equal(podmanMod.readServices(file).gateway?.pid, a.pid);
  // A lock whose pid was reused by an unrelated live process (start time differs) is abandoned too.
  writeFileSync(join(lockDir, nextGen()), `${process.pid} 1 stale`);
  podmanMod.recordService("podmanService", b.pid, file);
  assert.equal(podmanMod.readServices(file).podmanService?.pid, b.pid);
  assert.equal(podmanMod.servicesLockHolder(file), null, "reused-pid lock taken over and released");
  // A holder releases only its own generation: a newer holder that appeared while it ran keeps the lock.
  let other = "";
  podmanMod.withServicesLock(file, () => { other = nextGen(); writeFileSync(join(lockDir, other), `${process.pid} 0 other`); });
  assert.equal(podmanMod.servicesLockHolder(file), `${process.pid} 0 other`);
  writeFileSync(join(lockDir, `${other}.done`), "");
  assert.equal(podmanMod.lockHolderAlive(`${process.pid} ${podmanMod.procStartTicks(process.pid)} n`, 0), true);
  assert.equal(podmanMod.lockHolderAlive("", 5000), false, "an empty lock that stays empty is abandoned");
  // Several waiters that all judge the same abandoned lock: exactly one holds it at a time, none runs alongside another.
  const inside = join(STATE, "services-race.inside");
  const contender = () => new Promise<number | null>((res) => {
    const src = `const m = await import(${JSON.stringify(mod)}); const fs = await import("node:fs");
      for (let i = 0; i < 15; i++) m.withServicesLock(${JSON.stringify(file)}, () => {
        fs.writeFileSync(${JSON.stringify(inside)}, String(process.pid), { flag: "wx" });
        Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 3);
        fs.rmSync(${JSON.stringify(inside)});
      }, 30000);`;
    const w = spawn(process.execPath, ["--experimental-transform-types", "--no-warnings", "--input-type=module", "-e", src], { env: { ...process.env, OMP_SANDBOX_STATE: STATE }, stdio: "inherit" });
    w.once("exit", res);
  });
  for (let round = 0; round < 3; round++) {
    rmSync(inside, { force: true });
    writeFileSync(join(lockDir, nextGen()), "999999999");
    assert.deepEqual(await Promise.all([contender(), contender(), contender(), contender()]), [0, 0, 0, 0], "no two holders overlapped");
  }
  assert.equal(podmanMod.servicesLockHolder(file), null);
  a.kill("SIGKILL"); b.kill("SIGKILL");
});
