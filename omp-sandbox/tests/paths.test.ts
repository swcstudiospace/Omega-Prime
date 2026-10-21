import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, statSync, symlinkSync, realpathSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { allowedDir, seedFiles, PathRefused } from "../src/core/paths.ts";
import { ROOT } from "../src/core/config.ts";
import { gatewayToml, mountDriverConfig, podmanPolicyYaml } from "../src/core/providers/openshell-podman.ts";
import { startTier, MOUNT_TIERS } from "../src/core/providers/tiers.ts";

const box = realpathSync(mkdtempSync(join(tmpdir(), "omp-sbx-paths-")));
const root = join(box, "wt"); const job = join(root, "job-1"); const outside = join(box, "outside");
for (const d of [root, job, outside]) mkdirSync(d, { recursive: true });
mkdirSync(join(root, "..job", "inner"), { recursive: true });   // strictly-inside names starting with '..'
symlinkSync(outside, join(root, "escape"));
writeFileSync(join(root, "file.txt"), "x");

test("workspace path must be absolute, existing, a directory, strictly under an allowlisted root", () => {
  assert.equal(allowedDir(job, [root], "ws"), job);
  for (const [p, re] of [
    ["relative/job", /absolute/], [`${root}/../outside`, /'\.\.'/], [root, /allowlisted/], [outside, /allowlisted/],
    [join(root, "escape"), /allowlisted/], [join(root, "missing"), /does not exist/], [join(root, "file.txt"), /not a directory/],
    ["", /non-empty/], [`${job}\0x`, /NUL/],
  ] as const) assert.throws(() => allowedDir(p, [root], "ws"), (e: any) => e instanceof PathRefused && re.test(e.message), p);
  assert.throws(() => allowedDir(42 as any, [root], "ws"), PathRefused);
  assert.throws(() => allowedDir(job, [join(box, "nope")], "ws"), /allowlisted/);
});

test("seed is binary-safe and skips .git, .venv, node_modules and symlinks", () => {
  const src = join(root, "seed"); mkdirSync(join(src, "a", ".git"), { recursive: true });
  for (const d of [".git", ".venv/lib", "node_modules/x", "a/b"]) mkdirSync(join(src, d), { recursive: true });
  const bin = Buffer.from([0, 255, 254, 0x80, 10, 0xc3, 0x28]);
  writeFileSync(join(src, "a", "b", "blob.bin"), bin);
  writeFileSync(join(src, "README.md"), "hi\n");
  writeFileSync(join(src, ".git", "HEAD"), "ref"); writeFileSync(join(src, ".venv", "lib", "x.py"), "");
  writeFileSync(join(src, "node_modules", "x", "i.js"), ""); writeFileSync(join(src, "a", ".git", "c"), "");
  symlinkSync("/etc/passwd", join(src, "link"));
  const { files, skipped } = seedFiles(src);
  assert.deepEqual(files.map((f) => f.rel), ["README.md", "a/b/blob.bin"]);
  assert.equal(files[1].path, join(src, "a", "b", "blob.bin"));
  assert.equal(readFileSync(files[1].path).equals(bin), true, "bytes the copier reads are intact on disk");
  assert.deepEqual(skipped.sort(), [".git", ".venv", "a/.git", "link", "node_modules"].sort());
});

test("seedFiles carries rel + absolute source path only (no bytes buffered during the walk)", () => {
  const src = join(root, "walkseed"); mkdirSync(join(src, "d"), { recursive: true });
  writeFileSync(join(src, "d", "f.bin"), Buffer.from([1, 2, 3]));
  writeFileSync(join(src, "top.txt"), "t\n");
  symlinkSync("/etc/passwd", join(src, "link"));
  const { files, skipped } = seedFiles(src);
  assert.deepEqual(files.map((f) => f.rel), ["d/f.bin", "top.txt"]);
  for (const f of files) {
    assert.equal("data" in f, false, "the walk must not buffer file bytes");
    assert.equal(f.path, join(src, f.rel), "entry carries the absolute source path");
    assert.equal(statSync(f.path).isFile(), true);
  }
  assert.deepEqual(skipped, ["link"], "symlink skip semantics unchanged");
});

test("allowedDir accepts strictly-inside dotdot names; every real traversal stays refused", () => {
  assert.equal(allowedDir(join(root, "..job"), [root], "ws"), join(root, "..job"));
  assert.equal(allowedDir(join(root, "..job", "inner"), [root], "ws"), join(root, "..job", "inner"));
  assert.throws(() => allowedDir(join(root, ".."), [root], "ws"), (e: unknown) => e instanceof PathRefused && /allowlisted/.test(e.message), "the root's parent is refused");
  assert.throws(() => allowedDir(`${root}/../outside`, [root], "ws"), (e: unknown) => e instanceof PathRefused && /'\.\.'/.test(e.message), "real '..' component refused");
  assert.throws(() => allowedDir(`${root}/a/../b`, [root], "ws"), (e: unknown) => e instanceof PathRefused && /'\.\.'/.test(e.message));
  assert.throws(() => allowedDir(join(root, "escape"), [root], "ws"), (e: unknown) => e instanceof PathRefused && /allowlisted/.test(e.message), "symlink escape refused");
});

test("mount gateway config only on the mount variant; driver config is a single rw bind at the workdir", () => {
  const plain = gatewayToml("/run/p.sock", 18081, false);
  for (const k of ["allow_driver_config", "enable_bind_mounts", "userns", "resource_admission"]) assert.ok(!plain.includes(k), k);
  const mnt = gatewayToml("/run/p.sock", 18082, true);
  for (const k of ["allow_driver_config = true", "enable_bind_mounts = true", 'userns = "keep-id"', "[openshell.drivers.podman.resource_admission]", "127.0.0.1:18082", 'gateway_id = "omp-sandbox-podman-mnt"']) assert.ok(mnt.includes(k), k);
  assert.deepEqual(mountDriverConfig(job), { podman: { mounts: [{ type: "bind", source: job, target: "/sandbox/work", read_only: false }] } });
});

test("a workspace mount skips tiers that cannot bind-mount it", async () => {
  assert.deepEqual([...MOUNT_TIERS].sort(), ["bwrap", "openshell-podman"]);
  await assert.rejects(startTier({}, ["agentos-local", "openshell"], { workspaceHostPath: job }), /cannot bind-mount workspace_host_path/);
});

test("podmanPolicyYaml substitutes only the run-as values; the checked-in policy stays 1000/1000", () => {
  const gen = podmanPolicyYaml("1001", "1002");
  assert.ok(gen.includes('run_as_user: "1001"'), "generated run_as_user");
  assert.ok(gen.includes('run_as_group: "1002"'), "generated run_as_group");
  for (const k of ["chatgpt.com", "auth.openai.com", "api.z.ai", "registry.npmjs.org", "read_write: [/sandbox, /tmp]"]) {
    assert.ok(gen.includes(k), `default-deny policy survives: ${k}`);
  }
  const stat = readFileSync(join(ROOT, "sandbox", "policy-podman.yaml"), "utf8");
  const genWithoutRunAs = gen.split("\n").filter((l) => !l.includes("run_as_")).join("\n");
  const statWithoutRunAs = stat.split("\n").filter((l) => !l.includes("run_as_")).join("\n");
  assert.equal(genWithoutRunAs, statWithoutRunAs, "the generated policy differs from the static one in exactly the two run-as values");
  assert.ok(stat.includes('run_as_user: "1000"'), "checked-in policy keeps run_as_user 1000");
  assert.ok(stat.includes('run_as_group: "1000"'), "checked-in policy keeps run_as_group 1000");
});
