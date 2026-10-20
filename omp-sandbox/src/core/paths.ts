// Host paths the sandbox may touch. A workspace bind mount and a seed source must be absolute,
// exist, resolve (after symlinks) strictly inside an allowlisted root, and be directories.
import { lstatSync, readdirSync, readFileSync, realpathSync, statSync } from "node:fs";
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
    if (rel !== "" && !rel.startsWith("..") && !isAbsolute(rel)) return real;
  }
  throw new PathRefused(`${what} is not under an allowlisted root (${roots.join(", ")})`);
}

/** Files to seed from `src` as raw bytes (binary-safe). Skips SEED_SKIP names at any depth and every symlink. */
export function seedFiles(src: string): { files: Array<{ rel: string; data: Buffer }>; skipped: string[] } {
  const files: Array<{ rel: string; data: Buffer }> = [];
  const skipped: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(join(src, dir)).sort()) {
      const rel = dir ? `${dir}/${name}` : name;
      if (SEED_SKIP.has(name)) { skipped.push(rel); continue; }
      const st = lstatSync(join(src, rel));
      if (st.isSymbolicLink()) { skipped.push(rel); continue; }
      if (st.isDirectory()) walk(rel);
      else if (st.isFile()) files.push({ rel, data: readFileSync(join(src, rel)) });
      else skipped.push(rel);
    }
  };
  walk("");
  return { files, skipped };
}
