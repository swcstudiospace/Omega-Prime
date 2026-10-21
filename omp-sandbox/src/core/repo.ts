// Real-repo support for the sandbox: seed a ref from a host repo (git bundle over the tier's upload or exec stdin),
// run allowlisted build/test commands, and export the new commits back (git bundle via the tier's download). No host mounts.
export type SeedRepo = { path: string; ref: string; authorName?: string; authorEmail?: string };
/** Logical sandbox home in API arguments (`stage[].to`); each tier maps it onto its own home (sandboxHome). */
export const LOGICAL_HOME = "/sandbox/home";
/** Binaries run_check may start (argv[0]); no shells. */
export const CHECK_BINS = ["npm", "npx", "node", "python3", "git"];
/** Default bound for one run_check call (a watch-mode or hung test cannot outlive it). */
export const CHECK_TIMEOUT_MS = 3_600_000;

/** The sandbox-side home of a tier: `<sandboxRoot>/home` (/sandbox/home on OpenShell, /sbx/home on bwrap, the private host dir on agentos-local). */
export function sandboxHome(t: { sandboxRoot: string }) { return `${t.sandboxRoot.replace(/\/+$/, "")}/home`; }

/** Map a logical `/sandbox/home/...` path onto the tier's own home. */
export function sandboxPath(t: { sandboxRoot: string }, logical: string) {
  if (logical !== LOGICAL_HOME && !logical.startsWith(`${LOGICAL_HOME}/`)) throw new Error(`not a sandbox home path: ${logical}`);
  return sandboxHome(t) + logical.slice(LOGICAL_HOME.length);
}

/** A seedable branch name. `refs/heads/<b>` is shortened to `<b>`; any other full ref, a tag-like or detached
 *  form, or a name git would reject is refused — the sandbox must check out a writable branch so its new commits
 *  move the ref that export_commits reads. */
export function seedRef(ref: string) {
  const short = ref.startsWith("refs/heads/") ? ref.slice("refs/heads/".length) : ref;
  const ok = /^[A-Za-z0-9._\/-]{1,200}$/.test(short) && !short.startsWith("refs/") && !short.startsWith("-") && !short.startsWith("/")
    && !short.endsWith("/") && !short.endsWith(".") && !short.endsWith(".lock") && !short.includes("..") && !short.includes("//")
    && !short.split("/").some((c) => c.startsWith(".")) && short !== "HEAD";
  if (!ok) throw new Error(`seed_repo.ref must be a branch name (got ${JSON.stringify(ref)})`);
  return short;
}

/** sh -c script: $1 branch, $2 author name, $3 author email, $4 bundle path. Checks the branch out as a real local
 *  branch (refusing a detached HEAD), then prints the base commit and the untracked count last. */
export const repoSeedScript = [
  "set -e",
  "git init -q",
  'git bundle verify -q "$4" >/dev/null',
  'git config user.name "$2"', 'git config user.email "$3"', "git config commit.gpgsign false",
  'git fetch -q "$4" "refs/heads/$1:refs/heads/$1"',
  'git checkout -q -f "$1"',
  '[ "$(git symbolic-ref -q HEAD)" = "refs/heads/$1" ] || { echo "seed checkout is not on branch $1" >&2; exit 3; }',
  "printf '.claude/\\n.omp/\\n.agentos-probe.txt\\n' >> .git/info/exclude",
  'rm -f "$4"',
  "git rev-parse HEAD",
  "git status --porcelain | wc -l",
].join("\n");
