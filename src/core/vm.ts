// agentOS (library mode, @rivet-dev/agentos-core 0.4.3) VM with the sandbox attached as an external
// sandbox: the sandbox root is mounted at /mnt/sandbox and `agentos-sandbox` host functions run
// commands inside the sandbox. omp itself cannot run in the VM (Bun-only, no Bun in agentOS).
import { AgentOs } from "@rivet-dev/agentos-core";
import { SandboxAgent } from "sandbox-agent";
import type { SandboxTier } from "./providers/types.ts";

export const SANDBOX_MOUNT = "/mnt/sandbox";

export async function bootVm(tier: SandboxTier) {
  const { baseUrl, token } = tier.sandboxAgent();
  const client = await SandboxAgent.connect({ baseUrl, token });
  const t0 = Date.now();
  const vm = await AgentOs.create({
    permissions: { fs: "allow", network: "allow", childProcess: "allow", env: "allow", hostFunction: "allow" },
    sandbox: { client: client as any, sandboxRoot: tier.sandboxRoot, mountPath: SANDBOX_MOUNT, dispose: true },
  } as any);
  return { vm, bootMs: Date.now() - t0 };
}

/** Observable round trip: VM writes into the sandbox mount, sandbox reads it, VM runs a sandbox command. */
export async function vmRoundTrip(vm: any, tier: SandboxTier) {
  const rel = tier.workdir.slice(tier.sandboxRoot.length).replace(/^\/+/, "");
  const marker = `agentos->sandbox ${Date.now()}`;
  await vm.filesystem.writeFile(`${SANDBOX_MOUNT}/${rel}/.agentos-probe.txt`, marker);
  const seen = await tier.exec(["cat", `${tier.workdir}/.agentos-probe.txt`], { timeoutMs: 30_000 });
  const viaHostFn = await vm.process.exec(`agentos-sandbox run-command --command uname --args -n`, { output: { capture: "all" } });
  const ls = await vm.process.exec(`ls ${SANDBOX_MOUNT}/${rel}`, { output: { capture: "all" } });
  return {
    vmWrote: marker, sandboxRead: seen.stdout.trim(), match: seen.stdout.trim() === marker,
    hostFnExit: viaHostFn.exitCode, hostFnStdout: String(viaHostFn.stdout ?? "").trim().slice(0, 300),
    vmLsSandbox: String(ls.stdout ?? "").trim().split("\n").slice(0, 20),
  };
}
