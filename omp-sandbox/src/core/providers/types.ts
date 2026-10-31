import type { ChildProcessWithoutNullStreams } from "node:child_process";

export type TierName = "openshell-podman" | "openshell" | "bwrap" | "agentos-local";

/** One isolated place where omp + GSD run. Paths passed to exec/spawn are sandbox-side paths. */
export interface SandboxTier {
  readonly name: TierName;
  readonly id: string;
  /** Sandbox-side project directory (GSD + omp cwd). */
  readonly workdir: string;
  /** Sandbox-side root that agentOS mounts at /mnt/sandbox. */
  readonly sandboxRoot: string;
  /** Human-readable isolation summary for receipts. */
  readonly isolation: string;
  start(env: Record<string, string>): Promise<void>;
  /** Host directory bind-mounted read-write at `workdir`, when the job brought its own worktree. */
  readonly workspaceHostPath?: string | null;
  /**
   * Host path for writing into the mounted worktree, tied to the directory checked at start (a
   * `/proc/self/fd/<n>` path of the pinned descriptor), so renaming the worktree's name or swapping it
   * for a symlink after the check cannot redirect host-side writes. Null when the tier has no pin yet.
   */
  workspaceHostDir?(): string | null;
  /** Base URL + token of the Sandbox Agent server inside the sandbox (agentOS external-sandbox contract). */
  sandboxAgent(): { baseUrl: string; token: string };
  /** Long-lived process with piped stdio (omp --mode rpc). */
  spawnStdio(argv: string[], opts?: { cwd?: string; env?: Record<string, string> }): ChildProcessWithoutNullStreams;
  /**
   * Resolves once everything `proc` started inside the sandbox is gone, not just the local process.
   * Rejects if that cleanup failed, in which case the workdir may still be changing. Tiers whose
   * process tree ends with the local process may omit it.
   */
  settled?(proc: ChildProcessWithoutNullStreams): Promise<void>;
  exec(argv: string[], opts?: { cwd?: string; env?: Record<string, string>; timeoutMs?: number; input?: string }): Promise<{ code: number | null; stdout: string; stderr: string }>;
  /** Resources this tier owns that must be gone after dispose (pids, sandbox names, dirs). */
  owned(): Record<string, unknown>;
  dispose(): Promise<{ leftovers: string[] }>;
}

export class TierUnavailable extends Error {
  constructor(public tier: TierName, public detail: string) { super(`${tier} unavailable: ${detail}`); }
}

export type TierOptions = { workspaceHostPath?: string | null };
