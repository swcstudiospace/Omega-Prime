// Tier selection: OpenShell on rootless Podman first, then OpenShell MicroVM (opt-in, needs working KVM),
// then bwrap, then agentos-local. The chosen tier and every refusal
// reason are returned so receipts say exactly what ran.
import { OpenShellTier } from "./openshell.ts";
import { OpenShellPodmanTier } from "./openshell-podman.ts";
import { BwrapTier } from "./bwrap.ts";
import { LocalTier } from "./local.ts";
import { TierUnavailable, type SandboxTier, type TierName, type TierOptions } from "./types.ts";

/** Tiers that can bind-mount a job's worktree. The others are skipped (with a reason) when one is requested. */
export const MOUNT_TIERS: ReadonlySet<TierName> = new Set(["openshell-podman", "bwrap"]);

export async function startTier(env: Record<string, string>, order: TierName[] = (process.env.OMP_SANDBOX_TIERS?.split(",") as TierName[]) ?? ["openshell-podman", "openshell", "bwrap", "agentos-local"], opts: TierOptions = {}) {
  const skipped: Array<{ tier: TierName; reason: string }> = [];
  const ws = opts.workspaceHostPath ?? null;
  for (const name of order) {
    if (ws && !MOUNT_TIERS.has(name)) { skipped.push({ tier: name, reason: "cannot bind-mount workspace_host_path" }); continue; }
    const t: SandboxTier = name === "openshell-podman" ? new OpenShellPodmanTier(ws) : name === "openshell" ? new OpenShellTier() : name === "bwrap" ? new BwrapTier(undefined, ws) : new LocalTier();
    try { await t.start(env); return { tier: t, skipped }; }
    catch (e) {
      skipped.push({ tier: name, reason: e instanceof TierUnavailable ? e.detail : String(e) });
      try { await t.dispose(); } catch { /* best effort */ }
    }
  }
  throw new Error(`no sandbox tier available: ${JSON.stringify(skipped)}`);
}
