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
