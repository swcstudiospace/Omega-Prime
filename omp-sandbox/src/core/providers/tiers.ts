// Tier selection: OpenShell on rootless Podman first, then OpenShell MicroVM (opt-in, needs working KVM),
// then bwrap, then agentos-local. The chosen tier and every refusal
// reason are returned so receipts say exactly what ran.
import { OpenShellTier } from "./openshell.ts";
import { OpenShellPodmanTier } from "./openshell-podman.ts";
import { BwrapTier } from "./bwrap.ts";
import { LocalTier } from "./local.ts";
import { TierUnavailable, type SandboxTier, type TierName } from "./types.ts";

export async function startTier(env: Record<string, string>, order: TierName[] = (process.env.OMP_SANDBOX_TIERS?.split(",") as TierName[]) ?? ["openshell-podman", "openshell", "bwrap", "agentos-local"]) {
  const skipped: Array<{ tier: TierName; reason: string }> = [];
  for (const name of order) {
    const t: SandboxTier = name === "openshell-podman" ? new OpenShellPodmanTier() : name === "openshell" ? new OpenShellTier() : name === "bwrap" ? new BwrapTier() : new LocalTier();
    try { await t.start(env); return { tier: t, skipped }; }
    catch (e) {
      skipped.push({ tier: name, reason: e instanceof TierUnavailable ? e.detail : String(e) });
      try { await t.dispose(); } catch { /* best effort */ }
    }
  }
  throw new Error(`no sandbox tier available: ${JSON.stringify(skipped)}`);
}
