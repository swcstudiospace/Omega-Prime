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

export const TIER_NAMES: readonly TierName[] = ["openshell-podman", "openshell", "bwrap", "agentos-local"];

/** Refuse unknown tier names up front: a typo must never fall through to the unisolated local tier. */
export function validateTiers(order: readonly string[]): TierName[] {
  if (!Array.isArray(order) || order.length === 0) throw new Error("tier order is empty");
  const bad = order.filter((n) => !TIER_NAMES.includes(n as TierName));
  if (bad.length) throw new Error(`unknown sandbox tier(s) ${JSON.stringify(bad)}; expected one of ${TIER_NAMES.join(", ")}`);
  return order as TierName[];
}

function makeTier(name: TierName, ws: string | null): SandboxTier {
  switch (name) {
    case "openshell-podman": return new OpenShellPodmanTier(ws);
    case "openshell": return new OpenShellTier();
    case "bwrap": return new BwrapTier(undefined, ws);
    case "agentos-local": return new LocalTier();
  }
}

export async function startTier(env: Record<string, string>, order: TierName[] = (process.env.OMP_SANDBOX_TIERS?.split(",") as TierName[]) ?? ["openshell-podman", "openshell", "bwrap", "agentos-local"], opts: TierOptions = {}) {
  order = validateTiers(order);
  const skipped: Array<{ tier: TierName; reason: string }> = [];
  const ws = opts.workspaceHostPath ?? null;
  for (const name of order) {
    if (ws && !MOUNT_TIERS.has(name)) { skipped.push({ tier: name, reason: "cannot bind-mount workspace_host_path" }); continue; }
    const t = makeTier(name, ws);
    try { await t.start(env); return { tier: t, skipped }; }
    catch (e) {
      skipped.push({ tier: name, reason: e instanceof TierUnavailable ? e.detail : String(e) });
      try { await t.dispose(); } catch { /* best effort */ }
    }
  }
  throw new Error(`no sandbox tier available: ${JSON.stringify(skipped)}`);
}
