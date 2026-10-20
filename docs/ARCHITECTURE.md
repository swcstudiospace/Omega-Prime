# Architecture

## Tiers (src/core/providers)

The core tries each tier in order and records why it skipped any.

1. **openshell-podman** (`openshell-podman.ts`, preferred): OpenShell 0.1.4 gateway with `compute_driver="podman"` on **rootless Podman 5.4.2** (pasta networking, cgroups v2, crun, overlay). No KVM, no VM driver, no root.
   - The tier brings up only what is missing and tears down only what it started: the Podman API socket (`podman system service --time=0`), the gateway on loopback mTLS `127.0.0.1:18081` (config written to `.state/openshell-podman/gateway.toml`, PKI from `~/.local/state/openshell-vm/tls`), and the workload image `localhost/omp-sandbox:0.1` (built from `sandbox/image/Containerfile` if absent).
   - Per `up`: `openshell sandbox create --from localhost/omp-sandbox:0.1 --policy sandbox/policy-podman.yaml`. OpenShell runs two containers: the workload (`network=none`, Landlock fs policy, uid 1000) and a supervisor that is the only egress path and enforces default-deny network policy (Bun/omp -> chatgpt.com, auth.openai.com, api.z.ai; Node/npx -> registry.npmjs.org).
   - Everything else goes over the authenticated gateway: `openshell sandbox exec --timeout 0` (stdio, piped stdin, exit codes), and `openshell forward service` for the in-sandbox sandbox-agent (agentOS mount).
   - **Auth-broker relay** (`broker-relay.ts`): OpenShell never authorizes loopback destinations, so the host broker is not a network endpoint. A tiny Node relay listens on sandbox-local `127.0.0.1:18990` and tunnels each connection over one exec stdio channel to the host broker. Nothing on the host is exposed to the sandbox network.
   - Secrets (the broker bearer) go via exec **stdin** into a 0600 env file inside the sandbox, never argv.
   - Killing a local `openshell sandbox exec` client does **not** kill the remote process, so each omp session runs under `setsid` and its whole remote session is killed when the local client exits (abort path).
   - `down`: `sandbox delete`, wait until both Podman containers and the workspace volume are gone, then stop the gateway and Podman socket if the tier started them.
   - Not isolated: the host kernel (containers, not a VM). Rootless Podman's per-user `catatonit` pause process stays by design.
2. **openshell** (MicroVM): OpenShell 0.1.4 gateway with `compute_driver="vm"` (libkrun MicroVM).
   - It is coded in `openshell.ts`: create, upload/exec bootstrap, env file over exec stdin, sandbox-agent, `forward start`, delete and list check.
   - On this box it is **refused by default and must not be started**. The MicroVM vCPU creation triggered `kernel BUG at arch/x86/kvm/x86.c:702` (nested VMX on a VM host).
   - Enable it on a host with working KVM with `OMP_SANDBOX_OPENSHELL_OK=1`. The tier and `sandbox/policy.yaml` are unverified live.
3. **bwrap**: bubblewrap sandbox (fallback when Podman is unavailable).
   - Read-only /usr and /etc.
   - tmpfs /home, with only the bun and node toolchains mounted read-only. Host `~/.omp` is not visible; the probe asserts this.
   - Private /sbx (work, home, bin) and /tmp.
   - Unshared pid, ipc and uts; `--die-with-parent`; `--clearenv`.
   - Shares the network and the kernel, so it is **not** a VM boundary.
4. **agentos-local**: a host directory with no isolation. Labelled as such; last resort.

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
- The `sandbox_agent` mount's `realpath` runs a remote `python3` (with the stdlib `json` module) inside the sandbox. Without it every mount call fails with `stream error: failed to spawn process` (python3 missing) or `No module named 'json'` (python3-minimal). The Podman image installs full `python3`.

## omp RPC (src/core/omp-rpc.ts)

- Uses `omp --mode rpc-ui` with newline JSON (`rpc-ui` is needed: omp 18.8.4 only registers the `ask` tool when it has a UI).
- No time caps: no `--max-time`, prompt waits have no timeout, and OpenShell exec runs with `--timeout 0`.
- Sends `set_ask_dialog {enabled:true}` before the prompt.
- An `extension_ui_request` (ask, select, confirm or input) becomes a `question` event. `answer_question` sends an `extension_ui_response`.
- Answering an unknown, already-answered or cancelled request, or a request on a closed or terminal session, throws `StaleAnswerError` (`code:"stale_answer"`).
- The models seen are taken from assistant `message_start` and `message_end` frames.

## Rust access layer (crates/omp-sandbox-anda, real Anda)

- Rust 1.99.1 via user-local rustup (`rust-toolchain.toml`); the system rustc 1.85.1 is untouched. Edition 2024, rust-version 1.95.
- Crates (pinned exact): anda_engine 0.17.4, anda_core 0.17.1, anda_engine_server 0.17.0, anda_web3_client 0.17.0 (feature `client`, the non-TEE SDK).
- `serve`: spawns the TS core (`core_child.rs`, async, responses by id, other lines queued as events) and builds an Anda `Engine`:
  - Its identity is the engine key. Management is `BaseManagement{controller: client principal, visibility: Private}`.
  - The seven sandbox tools (`up`, `run_gsd_stage`, `answer_question`, `sandbox_status`, `abort`, `down`, `poll_events`) are `Tool<BaseCtx>` impls, registered but not exported.
  - It is served by `anda_engine_server` on loopback only (non-loopback binds are refused), and the server stops after the core exits.
- Auth path for one request:
  1. The client builds `RPCRequest{method:"tool_call", params:(ToolInput{name, args, meta{engine, extra{nonce, ts_ms}}},)}`.
  2. It signs SHA3-256(body) with its Ed25519 key into an ICP `SignedEnvelope` sent in the `Authorization` header, then POSTs to `/default`.
  3. anda_engine_server verifies the envelope against the body, giving 401 on a mismatch.
  4. The Engine checks the caller: anonymous or non-controller callers are refused, and so is a `meta.engine` that isn't this engine.
  5. `ReplayGuard` refuses requests with no nonce or timestamp, a timestamp off by more than 120 s, or a reused nonce.
  6. The tool forwards to the TS core with `access{via:"anda", caller}`. The core records that in every receipt.
- `client`: speaks the stdio JSON-lines protocol used by `tests/e2e.py`, signs every request, long-polls events and runs `anda_auth_probe`. On stdin EOF it sends `down`; it exits once the core and server are gone.
- Attestation: `attestation.rs` always reports `mock`, and replies carry `tee:"none: ..."`. The `tee` feature is a seam (see DTEE.md).
- Tests (`cargo test --release`):
  - keys: 0600 and stable; group/world-readable files and the wrong length are refused.
  - ReplayGuard: missing, short, stale or future timestamps and replays are refused.
  - The tool allowlist.
  - An in-process engine plus server in front of a stub core: a signed controller call is allowed and every probe case is refused.
