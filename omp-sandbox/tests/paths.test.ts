import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, symlinkSync, realpathSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { allowedDir, seedFiles, PathRefused } from "../src/core/paths.ts";
import { gatewayToml, mountDriverConfig } from "../src/core/providers/openshell-podman.ts";
import { startTier, MOUNT_TIERS } from "../src/core/providers/tiers.ts";

const box = realpathSync(mkdtempSync(join(tmpdir(), "omp-sbx-paths-")));
const root = join(box, "wt"); const job = join(root, "job-1"); const outside = join(box, "outside");
for (const d of [root, job, outside]) mkdirSync(d, { recursive: true });
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
  assert.ok(files[1].data.equals(bin));
  assert.deepEqual(skipped.sort(), [".git", ".venv", "a/.git", "link", "node_modules"].sort());
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
