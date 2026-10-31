// agentOS (library mode, @rivet-dev/agentos-core 0.4.3) VM with the sandbox attached as an external
// sandbox: the sandbox root is mounted at /mnt/sandbox and `agentos-sandbox` host functions run
// commands inside the sandbox. omp itself cannot run in the VM (Bun-only, no Bun in agentOS).
import { AgentOs, createSandboxHostFunctions } from "@rivet-dev/agentos-core";
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
    // Workaround (agentOS 0.4.3): AgentOs.create() derives the guest shim commands from
    // options.hostFunctions *before* resolveSandboxOptions() injects the `sandbox` collection,
    // so `agentos-sandbox` is otherwise "command not found". Passing it explicitly fixes that.
    hostFunctions: { sandbox: createSandboxHostFunctions({ client: client as any, sandboxRoot: tier.sandboxRoot } as any) },
  } as any);
  return { vm, bootMs: Date.now() - t0 };
}

/** Minimal shape of the agentOS VM surface vmRoundTrip exercises (agentos-core is untyped at this boundary). */
type RoundTripVm = {
  filesystem: {
    writeFile(path: string, data: string): Promise<unknown>;
    readdir(path: string): Promise<Array<string | { name: string }>>;
  };
  process: { exec(command: string, opts: { output: { capture: "all" } }): Promise<{ exitCode?: number; stdout?: unknown; stderr?: unknown }> };
};

/**
 * Observable round trip: VM writes into the sandbox's PRIVATE home (sandboxRoot/home — /sbx/home on bwrap,
 * /sandbox/home on openshell-podman), the sandbox reads it back, and the VM runs a sandbox command. The probe
 * never touches the workdir: on a mounted tier that IS the host job directory (Greptile #55, core.ts:49).
 */
export async function vmRoundTrip(vm: RoundTripVm, tier: SandboxTier) {
  const marker = `agentos->sandbox ${Date.now()}`;
  await vm.filesystem.writeFile(`${SANDBOX_MOUNT}/home/.agentos-probe.txt`, marker);
  const seen = await tier.exec(["cat", `${tier.sandboxRoot}/home/.agentos-probe.txt`], { timeoutMs: 30_000 });
  const viaHostFn = await vm.process.exec(`agentos-sandbox run-command --command uname --args -n`, { output: { capture: "all" } });
  const ls = await vm.process.exec(`ls ${SANDBOX_MOUNT}/home`, { output: { capture: "all" } });
  let apiReaddir: string[] | string;
  try { apiReaddir = (await vm.filesystem.readdir(`${SANDBOX_MOUNT}/home`)).map((e) => (typeof e === "string" ? e : e.name)).slice(0, 20); }
  catch (e) { apiReaddir = `error: ${String(e instanceof Error ? e.message : e).slice(0, 200)}`; }
  return {
    vmWrote: marker, sandboxRead: seen.stdout.trim(), match: seen.stdout.trim() === marker,
    hostFnExit: viaHostFn.exitCode, hostFnStdout: String(viaHostFn.stdout ?? "").trim().slice(0, 300),
    vmLsExit: ls.exitCode, vmLsSandbox: String(ls.stdout ?? "").trim().split("\n").slice(0, 20),
    vmLsStderr: String(ls.stderr ?? "").trim().slice(0, 200), apiReaddir,
  };
}
