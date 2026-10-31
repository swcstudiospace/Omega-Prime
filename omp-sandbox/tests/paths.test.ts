import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, mkdirSync, writeFileSync, readFileSync, renameSync, rmSync, statSync, symlinkSync, realpathSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { allowedDir, copySeed, seedFiles, PathRefused, pinDir, pinStillNamed, unpinDir } from "../src/core/paths.ts";
import { ROOT, NODE24_BIN, SANDBOX_AGENT_BIN } from "../src/core/config.ts";
import { SANDBOX_MOUNT, vmRoundTrip } from "../src/core/vm.ts";
import { gatewayToml, mountDriverConfig, podmanPolicyYaml } from "../src/core/providers/openshell-podman.ts";
import { startTier, MOUNT_TIERS } from "../src/core/providers/tiers.ts";
import type { SandboxTier } from "../src/core/providers/types.ts";

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
  assert.deepEqual(skipped.sort(), [".git", ".venv", "a/.git", "link", "node_modules"].sort());
});

/** A fake agentOS VM filesystem that records the exact bytes handed to writeFile. `onMkdir` runs during each await. */
function fakeVmFs(onMkdir: (p: string) => void = () => {}) {
  const written = new Map<string, Buffer>(); const dirs: string[] = [];
  return {
    written, dirs,
    fs: {
      mkdir: async (p: string) => { dirs.push(p); onMkdir(p); },
      writeFile: async (p: string, data: Uint8Array) => { assert.ok(data instanceof Uint8Array, "raw bytes, never a string"); written.set(p, Buffer.from(data)); },
    },
  };
}

test("the seed copy hands the VM the exact binary bytes", async () => {
  const src = join(root, "binseed"); mkdirSync(join(src, "a", "b"), { recursive: true });
  const bin = Buffer.from([0, 255, 254, 0x80, 10, 0xc3, 0x28]);
  writeFileSync(join(src, "a", "b", "blob.bin"), bin);
  writeFileSync(join(src, "README.md"), "hi\n");
  const vm = fakeVmFs();
  const out = await copySeed(src, "/mnt/work", vm.fs);
  assert.deepEqual(out.files, ["README.md", "a/b/blob.bin"]);
  assert.equal(out.bytes, bin.length + 3);
  assert.deepEqual(vm.dirs, ["/mnt/work/a/b"]);
  assert.equal(vm.written.get("/mnt/work/a/b/blob.bin")!.equals(bin), true, "bytes passed to writeFile equal the source bytes");
  assert.equal(vm.written.get("/mnt/work/README.md")!.toString("utf8"), "hi\n");
});

test("a file or parent folder swapped for a symlink during the copy is refused, never followed", async () => {
  const secret = join(outside, "secret.txt"); writeFileSync(secret, "outside-bytes");
  for (const swap of ["file", "parent"] as const) {
    const src = join(root, `race-${swap}`); mkdirSync(join(src, "d"), { recursive: true });
    writeFileSync(join(src, "d", "secret.txt"), "inside");
    const vm = fakeVmFs(() => {
      // Another job edits the workspace while the copy waits on the VM.
      if (swap === "file") { rmSync(join(src, "d", "secret.txt")); symlinkSync(secret, join(src, "d", "secret.txt")); }
      else { renameSync(join(src, "d"), join(src, "d.old")); symlinkSync(outside, join(src, "d")); }
    });
    await assert.rejects(copySeed(src, "/mnt/work", vm.fs), (e: unknown) => e instanceof PathRefused, swap);
    for (const b of vm.written.values()) assert.equal(b.toString("utf8").includes("outside-bytes"), false, `${swap}: no outside bytes reached the VM`);
  }
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

test("vmRoundTrip probes only the tier's private home, never the mounted workdir", async () => {
  for (const sandboxRoot of ["/sbx", "/sandbox"]) {
    const writes: Array<{ path: string; data: string }> = [];
    const vmCmds: string[] = [];
    const tierArgv: string[][] = [];
    let marker = "";
    const vm = {
      filesystem: {
        writeFile: async (path: string, data: string) => { writes.push({ path, data }); marker = data; },
        readdir: async () => [".agentos-probe.txt"],
      },
      process: {
        exec: async (command: string) => {
          vmCmds.push(command);
          return command.includes("uname") ? { exitCode: 0, stdout: "probe-host" } : { exitCode: 0, stdout: ".agentos-probe.txt\n" };
        },
      },
    };
    const tier: SandboxTier = {
      name: "bwrap", id: `fake-rt-${sandboxRoot}`, workdir: `${sandboxRoot}/work`, sandboxRoot, isolation: "none (test)",
      workspaceHostPath: "/host/job-ws",
      start: async () => {},
      sandboxAgent: () => ({ baseUrl: "", token: "" }),
      spawnStdio: () => { throw new Error("round-trip tests must not spawn"); },
      exec: async (argv) => { tierArgv.push(argv); return { code: 0, stdout: argv[0] === "cat" ? marker : "", stderr: "" }; },
      owned: () => ({}),
      dispose: async () => ({ leftovers: [] }),
    };
    const r = await vmRoundTrip(vm, tier);
    assert.equal(r.match, true, `round trip still proven on ${sandboxRoot}`);
    assert.equal(r.hostFnExit, 0);
    assert.deepEqual(writes.map((w) => w.path), [`${SANDBOX_MOUNT}/home/.agentos-probe.txt`], `write target on ${sandboxRoot}`);
    assert.deepEqual(tierArgv[0], ["cat", `${sandboxRoot}/home/.agentos-probe.txt`], `cat target on ${sandboxRoot}`);
    assert.deepEqual(vmCmds, ["agentos-sandbox run-command --command uname --args -n", `ls ${SANDBOX_MOUNT}/home`], `vm commands on ${sandboxRoot}`);
    for (const s of [...writes.map((w) => w.path), ...vmCmds, ...tierArgv.flat()]) {
      assert.equal(s.includes("work") || s.includes("/host/job-ws"), false, `probe must avoid the mounted workdir: ${s}`);
    }
    // Actual probe accesses (write, cat, ls) go through the private home; the uname host-fn writes nothing.
    // The cat argv is pinned exactly by the deepEqual above.
    for (const s of [...writes.map((w) => w.path), `ls ${SANDBOX_MOUNT}/home`]) {
      assert.ok(s.includes("/home"), `every probe access stays under the private home: ${s}`);
    }
  }
});

// Greptile round 3 on sbx-2: a checked worktree swapped for a symlink before (or after) the mount cannot redirect it.
test("pinDir holds the checked directory and refuses a name swapped for a symlink", () => {
  const parent = join(root, "pin-parent"); const child = join(parent, "child"); mkdirSync(child, { recursive: true });
  const checked = allowedDir(child, [root], "ws");
  const pin = pinDir(checked, "ws");
  try {
    assert.equal(pinStillNamed(pin), true);
    const st = statSync(checked); assert.deepEqual([pin.dev, pin.ino], [st.dev, st.ino]);
    // Another job owning `parent` swaps `child` for a link to a folder outside the allowlisted roots.
    renameSync(child, join(parent, "child-orig")); symlinkSync(outside, child);
    assert.equal(pinStillNamed(pin), false, "the name now leads elsewhere");
    assert.throws(() => pinDir(checked, "ws"), (e: any) => e instanceof PathRefused && /changed since it was checked/.test(e.message));
    // A swapped parent folder is caught too.
    rmSync(child); renameSync(join(parent, "child-orig"), child);
    renameSync(parent, join(root, "pin-parent-orig")); symlinkSync(join(root, "pin-parent-orig"), parent);
    assert.throws(() => pinDir(checked, "ws"), /changed since it was checked/);
  } finally { unpinDir(pin); }
});

test("bwrap hands the launcher the pinned descriptor as fd 3, so a later symlink swap does not move the mount (fake launcher)", { skip: process.platform !== "linux" }, async () => {
  const { BwrapTier } = await import("../src/core/providers/bwrap.ts");
  const parent = join(root, "pin-fake"); const ws = join(parent, "job"); mkdirSync(ws, { recursive: true });
  const hostRoot = join(box, "fake-host"); for (const d of ["work", "home", "bin"]) mkdirSync(join(hostRoot, d), { recursive: true });
  const record = join(box, "fake-bwrap.out");
  const fake = join(box, "fake-bwrap.sh");
  writeFileSync(fake, `#!/bin/sh\nprintf '%s\\n' "$@" > '${record}'\nstat -L -c '%d:%i' /proc/self/fd/3 >> '${record}'\n`, { mode: 0o755 });
  const t = new BwrapTier(hostRoot, allowedDir(ws, [root], "ws"), { bwrapBin: fake });
  const want = `${statSync(ws).dev}:${statSync(ws).ino}`;
  t.pinWorkspace();
  try {
    renameSync(ws, join(parent, "job-orig")); symlinkSync(outside, ws);
    const r = await t.exec(["true"], { cwd: "/sbx" });
    assert.equal(r.code, 0, r.stderr);
    const lines = readFileSync(record, "utf8").trim().split("\n");
    const i = lines.indexOf("--bind-fd");
    assert.deepEqual(lines.slice(i, i + 3), ["--bind-fd", "3", "/sbx/work"], "the workdir is bound from fd 3, never by name");
    assert.equal(lines.at(-1), want, "fd 3 is the checked directory, not the symlink's target");
  } finally { await t.dispose(); }
});

// Drives the host's real bwrap (user namespaces, bind mounts), so it is opt-in: the default suite stays hermetic.
const bwrapReady = process.env.OMP_SANDBOX_REAL_MOUNT_TESTS === "1"
  && existsSync("/usr/bin/bwrap") && existsSync(NODE24_BIN) && existsSync(dirname(SANDBOX_AGENT_BIN));
test("bwrap binds the pinned descriptor, so a later symlink swap does not move the mount", { skip: !bwrapReady }, async () => {
  const { BwrapTier } = await import("../src/core/providers/bwrap.ts");
  const parent = join(root, "pin-bwrap"); const ws = join(parent, "job"); mkdirSync(ws, { recursive: true });
  const hostRoot = join(box, "bw-host"); for (const d of ["work", "home", "bin"]) mkdirSync(join(hostRoot, d), { recursive: true });
  const t = new BwrapTier(hostRoot, allowedDir(ws, [root], "ws"));
  assert.throws(() => t.exec(["true"]), /not pinned/, "no pin before start(): refuse rather than bind by name");
  t.pinWorkspace();
  try {
    renameSync(ws, join(parent, "job-orig")); symlinkSync(outside, ws);
    const r = await t.exec(["sh", "-c", "echo pinned > /sbx/work/proof.txt"], { cwd: "/sbx" });
    assert.equal(r.code, 0, r.stderr);
    assert.equal(readFileSync(join(parent, "job-orig", "proof.txt"), "utf8"), "pinned\n", "write lands in the checked directory");
    assert.equal(existsSync(join(outside, "proof.txt")), false, "never in the symlink's target");
  } finally { await t.dispose(); }
});
