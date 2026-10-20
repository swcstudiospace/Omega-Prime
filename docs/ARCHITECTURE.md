# Architecture

## Tiers (src/core/providers)

The core tries each tier in order and records why it skipped any.

1. **openshell**: OpenShell 0.1.4 gateway with `compute_driver="vm"` (libkrun MicroVM).
   - It is coded in `openshell.ts`: create, upload/exec bootstrap, env file over exec stdin, sandbox-agent, `forward start`, delete and list check.
   - On this box it is **refused by default**. The MicroVM vCPU creation triggered `kernel BUG at arch/x86/kvm/x86.c:702` (nested VMX on a VM host).
   - Enable it on a host with working KVM with `OMP_SANDBOX_OPENSHELL_OK=1`. The tier and `sandbox/policy.yaml` are unverified live.
2. **bwrap**: bubblewrap sandbox (this host's verified tier).
   - Read-only /usr and /etc.
   - tmpfs /home, with only the bun and node toolchains mounted read-only. Host `~/.omp` is not visible; the probe asserts this.
   - Private /sbx (work, home, bin) and /tmp.
   - Unshared pid, ipc and uts; `--die-with-parent`; `--clearenv`.
   - Shares the network and the kernel, so it is **not** a VM boundary.
3. **agentos-local**: a host directory with no isolation. Labelled as such; last resort.

## Login (omp auth-broker)

The core runs `omp auth-broker serve` on a free loopback port. The bearer is read into memory and never logged.

Sandboxed omp gets only `OMP_AUTH_BROKER_URL` and `OMP_AUTH_BROKER_TOKEN`:

- The broker keeps refresh tokens. The sandbox receives short-lived access tokens.
- `agent.db` is never copied. The sandbox's own omp store has `auth_credentials=0`.

## agentOS

- `AgentOs.create({sandbox:{client: SandboxAgent, sandboxRoot, mountPath:"/mnt/sandbox"}})` boots in about 120 ms.
- At `up`, the VM writes a probe into the sandbox and the sandbox reads it back.
- The VM also runs `agentos-sandbox run-command`, which returns the sandbox hostname.
- The fixture project is seeded through the VM mount.
- omp itself runs in the sandbox, not in the VM. omp is Bun-only, and agentOS has no Bun.

agentOS 0.4.3 findings (worth raising in Rivet Discord or as an issue):

- `AgentOs.create()` computes the host-function shim commands from `options.hostFunctions` **before** `resolveSandboxOptions()` injects the `sandbox` collection. As a result, `agentos-sandbox` is "command not found".
  - Workaround in `vm.ts`: pass `hostFunctions: { sandbox: createSandboxHostFunctions(...) }` explicitly.
- Inside the VM, `ls /mnt/sandbox/...` fails with `I/O error (os error 29)`, while `vm.filesystem.readdir()` on the same path works.
- npm install scripts were blocked on this box, so `@sandbox-agent/cli-linux-x64`'s binary needed `chmod +x`.

## omp RPC (src/core/omp-rpc.ts)

- Uses `omp --mode rpc` with newline JSON.
- Sends `set_ask_dialog {enabled:true}` before the prompt.
- An `extension_ui_request` (ask, select, confirm or input) becomes a `question` event. `answer_question` sends an `extension_ui_response`.
- Answering an unknown, already-answered or cancelled request, or a request on a closed or terminal session, throws `StaleAnswerError` (`code:"stale_answer"`).
- The models seen are taken from assistant `message_start` and `message_end` frames.

## Rust wrapper (crates/omp-sandbox-anda)

- Plain Rust: std plus serde_json, rustc 1.85.
- Spawns the TS core, allowlists tool names, forwards requests and stamps `attestation:"mock"` on every line.
- When its stdin hits EOF, it sends `down` and waits for the core to exit.
- The `anda` and `tee` features are **seams only**. anda_engine 0.17.4 and anda_core 0.17.1 need rustc 1.95 or later, and a toolchain upgrade was not approved here.
