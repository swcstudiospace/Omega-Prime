// Host paths the sandbox may touch. A workspace bind mount and a seed source must be absolute,
// exist, resolve (after symlinks) strictly inside an allowlisted root, and be directories.
import { closeSync, constants, fstatSync, lstatSync, openSync, readdirSync, readlinkSync, readSync, realpathSync, statSync } from "node:fs";
import { isAbsolute, join, relative } from "node:path";
import { ROOT } from "./config.ts";

/** Roots a per-job worktree may live under. Colon-separated override: OMP_SANDBOX_WORKSPACE_ROOTS. */
export const WORKSPACE_ROOTS: readonly string[] = (process.env.OMP_SANDBOX_WORKSPACE_ROOTS ?? "/workspace/omes-bot-wt").split(":").filter(Boolean);
/** Roots a seed fixture may come from: this package's fixtures, plus the workspace roots. */
export const SEED_ROOTS: readonly string[] = [join(ROOT, "tests", "fixtures"), ...WORKSPACE_ROOTS];
/** Never copied into a sandbox: VCS metadata, virtualenvs, installed node deps. */
export const SEED_SKIP: ReadonlySet<string> = new Set([".git", ".venv", "node_modules"]);

export class PathRefused extends Error {
  readonly code = "path_refused";
}

/** Resolve `p` to its real path if it is an absolute directory strictly under one of `roots`; refuse otherwise. */
export function allowedDir(p: unknown, roots: readonly string[], what: string): string {
  if (typeof p !== "string" || p.length === 0) throw new PathRefused(`${what} must be a non-empty string`);
  if (p.includes("\0")) throw new PathRefused(`${what} contains a NUL byte`);
  if (!isAbsolute(p)) throw new PathRefused(`${what} must be an absolute path`);
  if (p.split("/").includes("..")) throw new PathRefused(`${what} must not contain '..'`);
  let real: string;
  try { real = realpathSync(p); } catch { throw new PathRefused(`${what} does not exist`); }
  if (!statSync(real).isDirectory()) throw new PathRefused(`${what} is not a directory`);
  for (const root of roots) {
    let rr: string;
    try { rr = realpathSync(root); } catch { continue; }
    const rel = relative(rr, real);
    if (rel !== "" && rel !== ".." && !rel.startsWith("../") && !isAbsolute(rel)) return real;
  }
  throw new PathRefused(`${what} is not under an allowlisted root (${roots.join(", ")})`);
}

/** Files to seed from `src`. Path-only entries: bytes are read at copy time (core.ts #seed), never buffered here.
 *  Skips SEED_SKIP names at any depth and every symlink. */
export function seedFiles(src: string): { files: Array<{ rel: string; path: string }>; skipped: string[] } {
  const files: Array<{ rel: string; path: string }> = [];
  const skipped: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(join(src, dir)).sort()) {
      const rel = dir ? `${dir}/${name}` : name;
      if (SEED_SKIP.has(name)) { skipped.push(rel); continue; }
      const st = lstatSync(join(src, rel));
      if (st.isSymbolicLink()) { skipped.push(rel); continue; }
      if (st.isDirectory()) walk(rel);
      else if (st.isFile()) files.push({ rel, path: join(src, rel) });
      else skipped.push(rel);
    }
  };
  walk("");
  return { files, skipped };
}

/**
 * Read one seed file's bytes at copy time without following a link swapped in since the walk. Every parent folder
 * below `src` must still be a real folder (not a symlink), the file is opened with O_NOFOLLOW, and the open
 * descriptor itself must still name `src/rel` (via /proc/self/fd, else by inode against the real path), so a
 * parent replaced between the checks and the open is caught too. `src` must already be allowlist-resolved.
 */
export function readSeedFile(src: string, rel: string): Buffer {
  const parts = rel.split("/");
  if (parts.some((p) => p === "" || p === "." || p === "..")) throw new PathRefused(`seed file ${rel}: bad relative path`);
  for (let i = 1; i < parts.length; i++) {
    const d = lstatSync(join(src, ...parts.slice(0, i)), { throwIfNoEntry: false });
    if (!d || d.isSymbolicLink() || !d.isDirectory()) throw new PathRefused(`seed file ${rel}: a parent folder changed since the walk`);
  }
  const want = join(src, rel);
  let fd: number;
  try { fd = openSync(want, constants.O_RDONLY | constants.O_NOFOLLOW | (constants.O_NONBLOCK ?? 0)); }
  catch (e: any) { throw new PathRefused(`seed file ${rel}: ${e?.code === "ELOOP" ? "replaced by a symlink" : "cannot be opened"} since the walk`); }
  try {
    const st = fstatSync(fd);
    if (!st.isFile()) throw new PathRefused(`seed file ${rel}: no longer a regular file`);
    let where: string | null = null;
    try { where = readlinkSync(`/proc/self/fd/${fd}`); } catch { /* no procfs */ }
    if (where !== null ? where !== want : (() => { const r = statSync(realpathSync(want)); return realpathSync(want) !== want || r.ino !== st.ino || r.dev !== st.dev; })())
      throw new PathRefused(`seed file ${rel}: resolves outside the seed source`);
    const buf = Buffer.alloc(st.size); let off = 0;
    while (off < st.size) { const n = readSync(fd, buf, off, st.size - off, off); if (n === 0) break; off += n; }
    return off === st.size ? buf : buf.subarray(0, off);
  } finally { closeSync(fd); }
}

/** The VM filesystem calls the seed copy uses (agentOS `vm.filesystem`, or a fake in tests). */
export type SeedFs = { mkdir(p: string, o: { recursive: boolean }): Promise<unknown>; writeFile(p: string, data: Uint8Array): Promise<unknown> };

/** Copy `src` (allowlist-resolved) into `dest` on `fs` one file at a time as raw bytes; nothing is buffered beyond one file. */
export async function copySeed(src: string, dest: string, fs: SeedFs) {
  const { files, skipped } = seedFiles(src);
  let bytes = 0;
  for (const f of files) {
    const dir = f.rel.includes("/") ? f.rel.slice(0, f.rel.lastIndexOf("/")) : "";
    if (dir) await fs.mkdir(`${dest}/${dir}`, { recursive: true }).catch(() => {});
    // Read after every await: another job may edit `src` while the copy waits on the VM.
    const data = readSeedFile(src, f.rel);
    bytes += data.length;
    await fs.writeFile(`${dest}/${f.rel}`, new Uint8Array(data.buffer, data.byteOffset, data.length));
  }
  return { files: files.map((f) => f.rel), skipped, bytes };
}
