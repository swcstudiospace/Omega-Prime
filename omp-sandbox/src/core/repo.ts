// Real-repo support for the sandbox: seed a ref from a host repo (git bundle over exec stdin), run allowlisted
// build/test commands, and export the new commits back (git bundle over exec stdout). No host mounts.
export type SeedRepo = { path: string; ref: string; authorName?: string; authorEmail?: string };
export const SANDBOX_PYDEPS = "/sandbox/home/pydeps";
/** Binaries run_check may start (argv[0]); no shells. */
export const CHECK_BINS = ["npm", "npx", "node", "python3", "git"];
/** sh -c script: $1 ref, $2 author name, $3 author email. Prints the base commit and the untracked count last. */
export const repoSeedScript = [
  "set -e",
  "git init -q",
  "git bundle verify -q /sandbox/home/seed.bundle >/dev/null",
  'git config user.name "$2"', 'git config user.email "$3"', "git config commit.gpgsign false",
  'git fetch -q /sandbox/home/seed.bundle "$1:$1"',
  'git checkout -q -f "$1"',
  "printf '.claude/\\n.omp/\\n.agentos-probe.txt\\n' >> .git/info/exclude",
  "rm -f /sandbox/home/seed.bundle",
  "git rev-parse HEAD",
  "git status --porcelain | wc -l",
].join("\n");
