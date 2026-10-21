// Folded standalone ops: seed_repo, export_commits (T1), run_check + py_deps staging (T2) and runStage routing (T3).
// Everything runs against the test-only attachTierForTests(tier, seed?) seam and a path-mapping fake tier whose exec
// runs REAL git/tar/sh locally — every /sandbox path is rewritten under one tmp dir, so no podman, no network, and
// nothing lands outside the tmp OMP_SANDBOX_STATE / tmp sandbox mapping.
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync, spawn } from "node:child_process";
import type { ChildProcessWithoutNullStreams } from "node:child_process";
import { copyFileSync, existsSync, mkdtempSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { SandboxTier } from "../src/core/providers/types.ts";

const STATE = mkdtempSync(join(tmpdir(), "omp-sbx-ops-state-"));
const WORKSPACES = mkdtempSync(join(tmpdir(), "omp-sbx-ops-ws-"));
process.env.OMP_SANDBOX_STATE = STATE;
process.env.OMP_SANDBOX_WORKSPACE_ROOTS = WORKSPACES;
const FAKE = new URL("./fake-omp.mjs", import.meta.url).pathname;
// Dynamic imports are required here (not a style choice): config.ts captures OMP_SANDBOX_STATE and paths.ts captures
// OMP_SANDBOX_WORKSPACE_ROOTS at module-evaluation time, so a static import would evaluate them before the overrides.
const { Core } = await import("../src/core/core.ts");
const { PathRefused } = await import("../src/core/paths.ts");
const { STATE: CORE_STATE } = await import("../src/core/config.ts");

const git = (args: string[], cwd: string) => execFileSync("git", args, { cwd, encoding: "utf8" });

/** The seam's up_ result shape: the attach stub plus #up's folded seeding results (core.ts #up fold). */
type UpResult = {
  tier: string;
  seeded: { repo: string; ref: string; base: string; bundleBytes: number; untrackedAfterSeed: number; via: string } | null;
  pyDeps: { dir: string; python_pytest: string } | null;
  staged: unknown[] | null;
};
/** Core.exportCommits' success shape (core.ts:190-210). */
type ExportResult = { head: string; base: string; ref: string; commits: string[]; dirty: string[]; bundle: string; bytes: number; via: string };
/** The fake tier with the one extra method Core.#put's fallback forgoes... rather, the download Core.exportCommits uses. */
type FakeTier = SandboxTier & { download: (remote: string, dir: string) => Promise<{ code: number }> };

let tierSeq = 0;
/** A fake tier: sandbox-side /sandbox paths map onto a per-tier tmp dir; exec runs the command locally.
 *  NO upload method, so Core.#put exercises its exec-stdin base64 fallback; download copies the mapped file. */
function mappedTier(spawnEnv: Record<string, string> = {}) {
  const hostRoot = mkdtempSync(join(tmpdir(), "omp-sbx-ops-sbx-"));
  const hostHome = join(hostRoot, "sandbox", "home");
  const hostWork = join(hostRoot, "sandbox", "work");
  mkdirSync(hostHome, { recursive: true });
  mkdirSync(hostWork, { recursive: true });
  const map = (s: string) => s.split("/sandbox/").join(`${hostRoot}/sandbox/`);
  const run = (argv: string[], o: { cwd?: string; env?: Record<string, string>; timeoutMs?: number; input?: string } = {}) =>
    new Promise<{ code: number | null; stdout: string; stderr: string }>((resolve) => {
      // Every /sandbox path — argv items AND the sh -c script bodies — must rewrite onto this tier's tmp mapping;
      // an unmapped argv would let "sandbox" paths touch the real host /sandbox (T-112-01's mapping-bug leak).
      const p = spawn(argv[0], argv.slice(1).map(map), { cwd: map(o.cwd ?? "/sandbox/work"), env: { ...process.env, ...o.env } });
      let stdout = "", stderr = "";
      p.stdout!.on("data", (b) => (stdout += b));
      p.stderr!.on("data", (b) => (stderr += b));
      const t: NodeJS.Timeout | undefined = o.timeoutMs ? setTimeout(() => p.kill("SIGKILL"), o.timeoutMs) : undefined;
      p.on("error", (e) => (stderr += String(e)));
      p.on("close", (code) => {
        clearTimeout(t);
        resolve({ code, stdout, stderr });
      });
      p.stdin!.end(o.input ?? "");
    });
  const tier: FakeTier = {
    name: "bwrap", id: `ops-${++tierSeq}`, isolation: "none (test)",
    workdir: "/sandbox/work", sandboxRoot: "/sandbox",
    start: async () => {},
    sandboxAgent: () => ({ baseUrl: "", token: "" }),
    exec: run,
    download: async (remote, dir) => { copyFileSync(map(remote), join(dir, remote.split("/").pop()!)); return { code: 0 }; },
    spawnStdio: (_argv, opts) => {
      // The fake omp is a separate OS process (see FAKE); its stdio tuple is all pipes, hence the non-null stream type.
      return spawn(process.execPath, [FAKE], {
        cwd: opts?.cwd ? map(opts.cwd) : hostWork,
        env: { ...process.env, ...spawnEnv },
        stdio: ["pipe", "pipe", "pipe"],
      }) as ChildProcessWithoutNullStreams;
    },
    owned: () => ({}),
    dispose: async () => ({ leftovers: [] }),
  };
  return { tier, hostRoot, hostHome, hostWork, map };
}

/** A real one-commit git repo under the tmp workspace root (allowedDir's allowlist points at WORKSPACES). */
function makeSeedRepo(name: string) {
  const repo = join(WORKSPACES, name);
  mkdirSync(repo, { recursive: true });
  git(["init", "-q", "-b", "main"], repo);
  writeFileSync(join(repo, "README.md"), "seed fixture\n");
  git(["add", "-A"], repo);
  git(["-c", "user.name=ops-test", "-c", "user.email=ops-test@local", "commit", "-qm", "seed: fixture"], repo);
  return repo;
}

test("seed_repo ships a real git bundle through the seam and checks the ref out in the mapped workdir", async () => {
  const repo = makeSeedRepo("seed-src");
  const { tier, hostWork } = mappedTier();
  const core = new Core();

  const up = (await core.attachTierForTests(tier, { seed_repo: { path: repo, ref: "main" } })) as UpResult;
  assert.equal(up.tier, tier.name);
  const seeded = up.seeded!;
  assert.equal(seeded.repo, repo, "seeded summary carries the host path");
  assert.equal(seeded.ref, "main");
  assert.match(seeded.base, /^[0-9a-f]{40}$/, "base is a full sha");
  assert.ok(seeded.bundleBytes > 0, "host-side bundle is non-empty");
  assert.equal(typeof seeded.untrackedAfterSeed, "number");
  assert.equal(existsSync(join(CORE_STATE, "seeds", `${tier.id}.bundle`)), true, "bundle kept under STATE/seeds");
  assert.deepEqual(core.repo, { path: repo, ref: "main", base: seeded.base });
  // The checkout really happened through the stdin-copied bundle: the mapped workdir HEAD is the seeded base.
  assert.equal(git(["rev-parse", "HEAD"], hostWork).trim(), seeded.base);
  assert.equal(readFileSync(join(hostWork, "README.md"), "utf8"), "seed fixture\n");
});

test("seed_repo through the seam refuses paths outside the workspace roots before any git runs", async () => {
  const outside = mkdtempSync(join(tmpdir(), "omp-sbx-ops-outside-"));
  const outsideRepo = join(outside, "r");
  mkdirSync(outsideRepo, { recursive: true });
  git(["init", "-q", "-b", "main"], outsideRepo);
  const { tier } = mappedTier();
  const core = new Core();
  await assert.rejects(
    core.attachTierForTests(tier, { seed_repo: { path: outsideRepo, ref: "main" } }),
    (e: unknown) => e instanceof PathRefused && /allowlisted root/.test((e as Error).message),
  );
  assert.equal(existsSync(join(CORE_STATE, "seeds", `${tier.id}.bundle`)), false, "PathRefused fired before any bundle was created for this tier");
  assert.equal(core.repo, null);
});

test("export_commits returns a size-verified bundle for the seeded repo and refuses unseeded cores", async () => {
  const repo = makeSeedRepo("export-src");
  const { tier, hostRoot, hostWork } = mappedTier();
  const core = new Core();
  const up = (await core.attachTierForTests(tier, { seed_repo: { path: repo, ref: "main" } })) as UpResult;
  const base = up.seeded!.base;

  // A new commit made in the mapped workdir after seeding.
  writeFileSync(join(hostWork, "new.txt"), "exported\n");
  git(["add", "-A"], hostWork);
  git(["-c", "user.name=ops-test", "-c", "user.email=ops-test@local", "commit", "-qm", "ops: export me"], hostWork);
  const head = git(["rev-parse", "HEAD"], hostWork).trim();

  const exportsDir = join(CORE_STATE, "exports");
  const ex = (await core.exportCommits({})) as ExportResult;
  assert.equal(ex.head, head);
  assert.equal(ex.base, base, "export base is the seed base");
  assert.equal(ex.ref, "main");
  assert.ok(ex.commits.length >= 1, "the new commit comes back");
  assert.match(ex.commits[0], /^[0-9a-f]+ ops: export me$/);
  assert.deepEqual(ex.dirty, [], "workdir was clean at export time");
  assert.ok(ex.bundle.startsWith(exportsDir + "/"), "bundle lands under STATE/exports");
  assert.equal(statSync(ex.bundle).size, ex.bytes, "downloaded bundle size matches the reported bytes");
  assert.equal(statSync(join(hostRoot, "sandbox", "home", "export.bundle")).size, ex.bytes, "and matches the in-sandbox wc -c");

  // A core with a tier of the same shape but NO seed keeps repo null and refuses without side effects.
  const before = readdirSync(exportsDir);
  const bare = new Core();
  bare.attachTierForTests(mappedTier().tier);
  assert.equal(bare.repo, null);
  await assert.rejects(bare.exportCommits({}), /no seeded repo/);
  assert.deepEqual(readdirSync(exportsDir), before, "the refusal created no export directory");
});

/** Receipts dir for run_check/export receipts, and the single receipt a runCheck call added since `before`. */
const receiptsDir = join(CORE_STATE, "receipts");
function receiptWrittenSince(before: string[]) {
  const added = (existsSync(receiptsDir) ? readdirSync(receiptsDir) : []).filter((f) => !before.includes(f));
  assert.equal(added.length, 1, `exactly one receipt per check run (saw ${added.length})`);
  return join(receiptsDir, added[0]);
}

test("run_check gates argv and cwd, runs confined in the workdir, and receipts every run at 0600", async () => {
  const { tier, hostWork } = mappedTier();
  const core = new Core();
  core.attachTierForTests(tier);

  // (1) argv[0] outside CHECK_BINS is refused — with the allowlist named — and refused runs write no receipt.
  const beforeRefusal = existsSync(receiptsDir) ? readdirSync(receiptsDir) : [];
  await assert.rejects(
    core.runCheck({ argv: ["curl", "http://example.invalid"] }),
    /run_check: curl is not allowlisted \(npm, npx, node, python3, git\)/,
  );
  assert.deepEqual(existsSync(receiptsDir) ? readdirSync(receiptsDir) : [], beforeRefusal, "a refused argv writes no receipt");

  // (2) a cwd escaping the workdir is refused before anything runs.
  await assert.rejects(
    core.runCheck({ argv: ["node", "-e", "process.stdout.write('hi')"], cwd: "../escape" }),
    /run_check: cwd must stay inside the workdir/,
  );

  // (3) allowlisted run: confined to the tier workdir (the exec's real cwd is the mapped one), receipted at 0600.
  const argv = ["node", "-e", "console.log('ops-ok'); console.log(process.cwd())"];
  const beforeOk = existsSync(receiptsDir) ? readdirSync(receiptsDir) : [];
  const ok = await core.runCheck({ argv, label: "node probe", cwd: "." });
  assert.equal(ok.code, 0);
  assert.equal(ok.cwd, "/sandbox/work/.");
  assert.equal(ok.tier, tier.name);
  assert.match(ok.stdoutTail, /ops-ok/);
  assert.equal(ok.stdoutTail.trim().split("\n")[1], hostWork, "the exec really ran in the mapped workdir");
  const receipt = receiptWrittenSince(beforeOk);
  assert.equal(statSync(receipt).mode & 0o777, 0o600);
  const body = JSON.parse(readFileSync(receipt, "utf8")) as Record<string, unknown>;
  assert.equal(body.label, "node probe");
  assert.deepEqual(body.argv, argv);
  assert.equal(body.cwd, "/sandbox/work/.");
  assert.equal(body.code, 0);
  assert.equal(typeof body.ms, "number");
  assert.match(body.stdoutTail as string, /ops-ok/);
  assert.equal(typeof body.stderrTail, "string");
  assert.equal(typeof body.startedAt, "string");
  assert.equal(typeof body.endedAt, "string");
  assert.equal(body.tier, tier.name);

  // python3 variant with the default cwd: exactly the tier workdir, receipted with the default label.
  const beforePy = existsSync(receiptsDir) ? readdirSync(receiptsDir) : [];
  const py = await core.runCheck({ argv: ["python3", "-c", "print('py-ok')"] });
  assert.equal(py.code, 0);
  assert.equal(py.cwd, "/sandbox/work");
  assert.match(py.stdoutTail, /py-ok/);
  const pyBody = JSON.parse(readFileSync(receiptWrittenSince(beforePy), "utf8")) as Record<string, unknown>;
  assert.equal(pyBody.label, "python3 -c print('py-ok')");
  assert.equal(pyBody.cwd, "/sandbox/work");

  // (4) non-zero exits are reported in the result (not thrown) and receipted with the exit code.
  const beforeFail = existsSync(receiptsDir) ? readdirSync(receiptsDir) : [];
  const fail = await core.runCheck({ argv: ["node", "-e", "process.exit(7)"] });
  assert.equal(fail.code, 7);
  const failBody = JSON.parse(readFileSync(receiptWrittenSince(beforeFail), "utf8")) as Record<string, unknown>;
  assert.equal(failBody.code, 7);
});

test("py_deps stages importable stubs and wires PYTHONPATH through the sandbox env file", async () => {
  const repo = makeSeedRepo("pydeps-src");
  const fixture = mkdtempSync(join(tmpdir(), "omp-sbx-ops-pydeps-"));
  writeFileSync(join(fixture, "pytest.py"), "__version__ = '8.0.0-fixture'\n");
  mkdirSync(join(fixture, "starlette"), { recursive: true });
  writeFileSync(join(fixture, "starlette", "__init__.py"), "starlette = 'fixture'\n");
  mkdirSync(join(fixture, "httpx"), { recursive: true });
  writeFileSync(join(fixture, "httpx", "__init__.py"), "httpx = 'fixture'\n");

  const { tier, hostHome, map } = mappedTier();
  const core = new Core();
  // One attach runs both seeded parts, mirroring #up: the repo ships as a bundle, the stubs ship as a tarball.
  const up = (await core.attachTierForTests(tier, { seed_repo: { path: repo, ref: "main" }, py_deps: fixture })) as UpResult;

  assert.equal(up.seeded!.repo, repo, "the seed part still ran in the same attach");
  assert.equal(up.pyDeps!.dir, "/sandbox/home/pydeps");
  assert.match(up.pyDeps!.python_pytest, /^\S+ 8\.0\.0-fixture$/, "python version + stub pytest version");
  assert.equal(existsSync(join(hostHome, "pydeps", "pytest.py")), true);
  assert.equal(existsSync(join(hostHome, "pydeps", "starlette", "__init__.py")), true);
  assert.equal(existsSync(join(hostHome, "pydeps", "httpx", "__init__.py")), true);
  // The in-sandbox import check above only passed because the stubs are importable through the staged PYTHONPATH;
  // the sandbox env file gains the export line for the (mapped) pydeps dir.
  const envLines = readFileSync(join(hostHome, ".omp-sandbox.env"), "utf8").split("\n");
  assert.equal(envLines.includes(`export PYTHONPATH=${map("/sandbox/home/pydeps")}`), true, "PYTHONPATH export lands in .omp-sandbox.env");
});
