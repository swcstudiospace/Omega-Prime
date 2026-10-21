# Architecture Research

**Document:** `.planning/research/ARCHITECTURE.md`  
**Scope:** Local Rust/Anda → Node/agentOS → external Linux sandbox → Bun/omp → GSD integration.  
**Confidence:** High for the inspected package/release contracts; medium for the proposed integration design; application behavior and isolation remain unverified.  
**Evidence boundary:** This is source research, not an implementation or runtime acceptance report. No builds, tests, installs, startup, inference, or OpenShell compute were run. Supplied local observations are taken as observations, not independently reproduced.

## 1. Architecture Decision

Build a small owned control plane, not a second agent inference stack:

1. Rust embeds `anda_engine = 0.17.4`, registers actual typed tools, and exposes them through matching published `anda_engine_server = 0.17.0`.
2. Rust owns one Node 24.21 core child and its control transport.
3. Node owns its agentOS VM, external-sandbox provider, workspace, omp session, pending questions, and acquired auxiliary processes.
4. The agentOS VM uses the real external-sandbox integration: mounted filesystem and documented sandbox process host functions. It does not supply the Linux/Bun runtime for omp. Rivet documents these two mechanisms for embedded library VMs, not hosted VMs. [A1][A2]
5. Bun runs `omp --mode rpc` inside the selected external Linux sandbox. Omp's RPC protocol is its own JSONL protocol, not JSON-RPC 2.0. [O1]
6. Provider credentials remain behind an auth broker and auth gateway. The sandbox gets a gateway capability, never a broker capability or a copied login database. A direct broker client can receive access tokens and stored API keys; a gateway client does not receive provider credentials. [O2]
7. Local startup, failed/degraded runs, and cleanup receipts always report `attestation=mock`, as required by the project contract.

Version selection is intentional: Anda member crates are independently versioned. Engine 0.17.4 depends on core 0.17.1; server 0.17.0 declares engine/core `0.17` compatibility. Propose exact application dependency pins `=0.17.4`, `=0.17.1`, and `=0.17.0`, followed by an approved full dependency-resolution/build gate. This is source-level compatibility, not a successful local build. [D1][D2][D3]

## 2. System Overview and Trust Boundaries

```text
Operator / local CLI / authenticated Anda client
                       |
        Anda HTTP signed CBOR/JSON RPC
        (or the project's local CLI interface)
                       |
+-----------------------------------------------------------+
| Rust wrapper                                               |
| tool-only Anda engine + anda_engine_server                  |
| authenticated caller binding, tool schemas, Node ownership |
| NO model/provider inference client                         |
+----------------------+------------------------------------+
                       | project-owned control NDJSON
                       | requests / responses / events
+----------------------+------------------------------------+
| Node 24 core                                               |
| startup controller; stage/session/question state           |
| resource ledger; endpoint/path policy; cleanup              |
|                                                           |
|  +------------------+       +---------------------------+  |
|  | agentOS 0.4.3 VM |       | external provider owner   |  |
|  | /mnt/sandbox    |------>| Sandbox Agent HTTP bridge |  |
|  | sandbox host fn |------>| process + filesystem API  |  |
|  +------------------+       +---------------------------+  |
|              |                       |                    |
|              +-- observable VM/sandbox round trip          |
|                                                           |
|  omp session controller -- OpenShell exec stdio ----------+--+
+-----------------------------------------------------------+  |
                                                               |
+--------------------------------------------------------------v--+
| External Linux sandbox: OpenShell / authorized bwrap / host tier |
| owned workspace; Node/Bun/omp; local GSD 1.18.0 Claude skills     |
| omp RPC stdout distinct from stderr; no host login store         |
+------------------------------+----------------------------------+
                               | approved inference routes only
                    +----------v-------------+
                    | owned/reused auth      |
                    | gateway                |
                    +----------+-------------+
                               | trusted host-only broker access
                    +----------v-------------+
                    | canonical auth broker  |
                    | existing login vault   |
                    +----------+-------------+
                               | provider requests via gateway
                         selected providers
```

There are **two Node-to-sandbox integration paths**, not two inference engines:

- The VM path proves and uses the real mounted filesystem and sandbox host-function bridge.
- The session path carries long-lived omp stdin/stdout/stderr through the backend's verified exec transport. It need not run Bun inside agentOS. The VM cannot be called integrated merely because it was booted alongside a working host process. [A1][O1]

The Sandbox Agent HTTP bridge is necessary for the selected AgentOS filesystem implementation. Five OpenShell lifecycle/transfer commands alone do not implement that HTTP filesystem service. AgentOS serializes a standard client's endpoint configuration into its `sandbox_agent` mount plugin. [A3]

### Component Responsibilities and Ownership

| Component | Owns | Does not own / must not infer |
|---|---|---|
| Rust process supervisor | Its Node child, control request correlation, child deadlines, authenticated caller-to-stage authorization | Pre-existing broker/gateway processes, arbitrary host processes, provider credentials |
| Anda engine/server | Tool registration, tool invocation, caller authentication/visibility policy, public tool metadata | Omp session state or workflow completion; the server is a stateless forwarder [D4][D5] |
| Node core | Startup state, VM handle, provider handle, session/stage/question tables, owned-resource ledger | Global omp/GSD installation or protected workspaces |
| AgentOS VM | Its virtual processes/filesystem and external mount references | A Linux/Bun environment; whole-host isolation |
| AgentOS sandbox provider | One client acquisition and lifecycle hook for one VM | A second, independently created sandbox for the same core |
| OpenShell backend | Its uniquely named/labeled sandbox, owned CLI children and service forward, transfer staging | Gateway administration, all sandboxes, or pre-existing forwards |
| Sandbox Agent server | Filesystem and process service inside that sandbox | Canonical provider credentials or GSD stage identity |
| Omp session | Actual omp protocol state, model execution, UI requests, session settlement | Outer tool-call success, cleanup success, hardware attestation [O1] |
| GSD skills | Planning/execution workflow and actual workflow artifacts | Transport health or resource ownership |
| Auth broker | Canonical credentials and OAuth refreshes | Untrusted sandbox execution [O2] |
| Auth gateway | Broker-backed credential resolution and supported inference routes | Permission to expose broker snapshot/credential routes to the sandbox [O2] |
| Receipt writer | Redacted outcome, selected tier/model, failures, remaining owned resources, mock label | A private transcript, token dump, or hardware attestation claim |

## 3. Exact AgentOS 0.4.3 Contracts

### Provider and Client Are Different Contracts

The installed declarations and version-pinned published declarations agree on the following interfaces. These are **AgentOS contracts**, not the similarly named `sandbox-agent` backend interface. [A2]

```ts
export interface AgentOsSandboxProcessResult {
  stdout?: string;
  stderr?: string;
  exitCode?: number | null;
  timedOut?: boolean;
  durationMs?: number;
}

export interface AgentOsSandboxProcessInfo {
  id: string;
  command?: string;
  args?: string[];
  status?: string;
  exitCode?: number | null;
  pid?: number | null;
}

export interface AgentOsSandboxProcessLogs {
  entries: Array<{
    data: string;
    encoding?: "base64" | string;
    stream?: "stdout" | "stderr" | "combined" | string;
    timestampMs?: number;
  }>;
}

export interface AgentOsSandboxClient {
  dispose?(): Promise<void> | void;
  runProcess(options: {
    command: string;
    args?: string[];
    cwd?: string;
    env?: Record<string, string>;
    timeoutMs?: number;
  }): Promise<AgentOsSandboxProcessResult>;
  createProcess(options: {
    command: string;
    args?: string[];
    cwd?: string;
    env?: Record<string, string>;
  }): Promise<AgentOsSandboxProcessInfo>;
  listProcesses(): Promise<{ processes: AgentOsSandboxProcessInfo[] }>;
  stopProcess(id: string): Promise<AgentOsSandboxProcessInfo>;
  killProcess(id: string): Promise<AgentOsSandboxProcessInfo>;
  getProcessLogs(id: string, options?: {
    stream?: "stdout" | "stderr" | "combined";
    tail?: number;
  }): Promise<AgentOsSandboxProcessLogs>;
  sendProcessInput(id: string, input: {
    data: string;
    encoding: "base64";
  }): Promise<unknown>;
}

export interface AgentOsSandboxProvider {
  start(): Promise<AgentOsSandboxClient>;
}
```

`AgentOsOptions.sandbox` accepts provider or client object form. Supported shared options are `mountPath?`, `sandboxRoot?`, `timeoutMs?`, `maxFullReadBytes?`, and `readOnly?`. Provider form requires `provider`. Client form requires `client` and optionally accepts `dispose?: boolean | (() => void | Promise<void>)`. The mount defaults to `/mnt/sandbox`; the external root defaults to `/`. Client-form disposal defaults to false. [A2]

The supported public creation/lifecycle calls are:

```ts
AgentOs.create(options?: AgentOsOptions): Promise<AgentOs>
vm.dispose(): Promise<void>
```

The declaration also contains an internal optional defaults-profile argument; application code should use the public options object, not that internal parameter. `sandbox: { provider }` is the decided integration shape. [A4]

Documented usage is:

```ts
const vm = await AgentOs.create({
  sandbox: { provider },
});
try {
  // Use vm.filesystem and vm.process against the sandbox bridge.
} finally {
  await vm.dispose();
}
```

The Rivet guide demonstrates `vm.filesystem.writeFile/readFile` through `/mnt/sandbox`, `vm.process.exec("agentos-sandbox run-command ...")`, and `agentos-sandbox list-processes`. Those documented behaviors still require live acceptance with the pinned release. [A1]

### Documented Host Functions

`createSandboxHostFunctions({ client, ...sharedOptions })` creates the `sandbox` collection. Its supported function names are:

| Host function | Client operation | Important mapping |
|---|---|---|
| `runCommand` | `runProcess(options)` | command/args/cwd/env/timeoutMs; stdout/stderr/exitCode/timedOut/durationMs |
| `createProcess` | `createProcess(options)` | Returns process identity/status |
| `listProcesses` | `listProcesses()` | Returns process metadata |
| `stopProcess` | `stopProcess(id)` | Graceful process control |
| `killProcess` | `killProcess(id)` | Forced process control |
| `getProcessLogs` | `getProcessLogs(id, {stream, tail})` | Maps entries into host-function logs; base64 log entries are decoded |
| `sendInput` | `sendProcessInput(id, {data, encoding:"base64"})` | Encodes the supplied UTF-8 text as base64 |

The implementation gives `runCommand` a 120,000 ms host-function timeout. `sandboxRoot` configures the mount base path; the process functions forward command options to the client. A mount root or read-only mount flag must not be mistaken for confinement of all sandbox command execution. Backend policy and command/path restrictions remain necessary. [A2][A3]

### The Provider Adapter Package

The exact adapter declaration is:

```ts
export type SandboxAgentProviderOptions =
  Omit<SandboxAgentStartOptions, "sandbox" | "sandboxId">;

export declare function sandboxAgentProvider(
  backend: SandboxAgentBackend,
  options?: SandboxAgentProviderOptions,
): AgentOsSandboxProvider;
```

It calls `SandboxAgent.start({ ...options, sandbox: backend })`. The returned proxy binds methods to the real client and maps **`dispose` to `destroySandbox`**. Thus VM disposal of this adapter means backend sandbox destruction, not merely disconnecting an HTTP client. [A5]

The installed transitive backend package is `sandbox-agent` 0.4.2. Its separate provider contract is: [A6]

```ts
interface SandboxProvider {
  name: string;
  create(): Promise<string>;
  destroy(sandboxId: string): Promise<void>;
  reconnect?(sandboxId: string): Promise<void>;
  pause?(sandboxId: string): Promise<void>;
  kill?(sandboxId: string): Promise<void>;
  getUrl?(sandboxId: string): Promise<string>;
  getFetch?(sandboxId: string): Promise<typeof globalThis.fetch>;
  ensureServer?(sandboxId: string): Promise<void>;
  defaultCwd?: string;
}
```

`getUrl` or `getFetch` is needed by Sandbox Agent. For **this AgentOS mount integration, choose a real `getUrl` endpoint**: AgentOS's filesystem serialization requires a nonempty `client.baseUrl`, although `AgentOsSandboxClient` does not declare that property. A fetch-only backend cannot be assumed to satisfy this mount. The runtime also serializes token/default headers to the trusted mount configuration; those are not receipt fields. [A3][A6]

**Recommended project adapter:** implement the OpenShell backend once, including its real Sandbox Agent endpoint, then use `sandboxAgentProvider(backend, options)` to obtain the actual `AgentOsSandboxProvider`. Keep the required OpenShell exec/upload/download operations as explicit project backend capabilities; they are not invented methods on the upstream `SandboxProvider` interface.

### Disposal and Acquisition Rules

- Provider form awaits `provider.start()` and installs a hook invoking `client.dispose?.()`. Client form installs a hook only if explicitly requested. [A3]
- If provider `start()` fails before returning a client, it must retain responsibility for resources acquired during that failed start; AgentOS cannot dispose a client it never received. [A3]
- AgentOS VM disposal attempts VM teardown and sandbox hooks, collecting errors rather than making a successful sandbox cleanup depend on a successful VM teardown. [A7]
- The adapter's destructive client disposal makes **one owner** mandatory. Normal shutdown calls `vm.dispose()`; it must not independently destroy the same sandbox from an unrelated second owner. The core may retain an idempotent emergency cleanup handle for that same owned resource.
- Keep the resource ledger outside SDK client fields. Sandbox Agent startup cleanup can suppress backend-destroy failures, and destructive disposal clears provider attachment fields; those paths cannot be the only record of an outstanding resource. [A8]

### Pinned-Release Hazard: Host Functions Resolve Too Early

Installed and published `agent-os.js` 0.4.3 resolve `options.hostFunctions` at lines 1698–1700, then call `resolveSandboxOptions` at line 1704, then continue using the earlier resolved collection. The sandbox resolver injects the `sandbox` collection only afterward. This is a concrete source-ordering discrepancy with the documented automatic registration behavior; **it is not a reproduced runtime result**. [A3][A7]

Do not bless the existing `as any` workaround as the contract. Implementation options must remain typed and version-specific:

1. Keep `sandbox: { provider }`.
2. If runtime acceptance confirms the ordering defect, explicitly register the complete standard sandbox host-function collection before creation using a strongly typed late-bound client facade.
3. The provider captures the one real client when its `start()` runs; all seven facade operations delegate to that client after acquisition. The actual client—not the facade lacking endpoint metadata—is returned to AgentOS for its mount.
4. Use the existing exported `createSandboxHostFunctions` helper; do not implement a second command schema or start a second sandbox.
5. Treat that bridge as a proposed pinned-release mitigation requiring dedicated acceptance, not as already working behavior. An approved package correction would be a separate version decision.

## 4. Exact OpenShell v0.1.4 CLI Boundary

All OpenShell conclusions below use tag **`v0.1.4`**, resolved by GitHub to commit `5a7728c304b5528e954e9ae900cf21f84e1a12b1`. They do not assume current `main`. [S0]

### Supported Operations

Use an explicit global `--gateway` or `--gateway-endpoint` and explicit `--workspace` on every owned operation. Avoid active-gateway/last-used-sandbox ambient selection. These are supported global flags; TLS verification must not be disabled to make an operation pass. [S1]

| Required capability | Exact CLI shape | Semantics and constraints |
|---|---|---|
| Create | `openshell [GLOBAL_FLAGS] sandbox create --name NAME --detach --no-tty --no-auto-providers --policy POLICY --restart-policy never --output json [--from SOURCE] [--label KEY=VALUE] [-- COMMAND ARG...]` | `--detach` starts canonical main without attaching. Sandboxes are retained by default. Structured output returns metadata including immutable `id`, `name`, `workspace`, labels and phase. The image/main process must keep the sandbox usable for later exec. [S1][S2][S3] |
| Execute | `openshell [GLOBAL_FLAGS] sandbox exec NAME --workdir DIR --timeout SECONDS --no-tty --no-login-shell [--env KEY=VALUE] -- COMMAND ARG...` | Positional `NAME` or `-n/--name NAME`, not both. `--timeout 0` disables the backend command timer. Environment flags are documented for non-secrets. [S1][S4] |
| Upload | `openshell [GLOBAL_FLAGS] sandbox upload NAME LOCAL_PATH [DEST] [--no-git-ignore]` | Destination defaults to sandbox working directory. Git filtering applies inside repositories unless explicitly disabled. Uploads are non-transactional. [S1][S5] |
| Download | `openshell [GLOBAL_FLAGS] sandbox download NAME SANDBOX_PATH [DEST]` | Host destination defaults to `.`. Sandbox source must resolve within the canonical sandbox working directory. [S1][S5][S6] |
| Delete | `openshell [GLOBAL_FLAGS] sandbox delete NAME [NAME...]` | `--all` exists upstream but is forbidden for project cleanup. Success may mean deletion accepted and still pending. Already absent is successful; authorization and workspace errors remain errors. [S1][S5][S7] |

For completeness, create's pinned declaration also supports `--template`, repeatable `--upload`, `--no-git-ignore`, hidden deprecated `--keep`, `--no-keep`, `--editor`, optional-count `--gpu`, `--cpu`, `--memory`, `--driver-config-json`, repeatable `--provider`, `--forward`, `--expose`, `--expose-authorization-mode`, `--tty`, `--auto-providers`, repeatable `--env`, `--no-credential-warnings`, and `--approval-mode manual|auto`; output accepts table/JSON/YAML. These flags are **not blanket project authorization**. The project does not need GPU, editor, provider auto-import, restart replay, or public service exposure. [S1]

Create `--upload` **conflicts with a trailing main command**. The pinned page contains a later transfer example showing both, but its explicit lifecycle prose, CLI declaration, and runtime guard reject that combination. Follow the checked CLI/source contract: create a retained usable sandbox, upload, then exec the workload; or select an image containing the files. [S1][S2][S3]

### Exec Streaming: Supported on Paper, Not Locally Proven

The CLI entry point calls `sandbox_exec_grpc`, not the older SSH helper with the similar name. It verifies the sandbox is Ready, sends argv/workdir/environment/timeout, and streams separate stdout and stderr payloads to their corresponding local streams. The CLI propagates the remote exit code only after observing an Exit event and successfully draining the response stream; missing Exit or failed final gRPC status is failure. [S1][S4]

Pinned behavior relevant to omp:

- Non-TTY piped stdin waits at most **200 ms** for EOF; if still open, exec begins and forwards later input. An always-open supervisor pipe therefore does not inherently wait forever for EOF before launching. [S2][S4]
- **Piped stdin is capped at 4 MiB cumulatively for the entire CLI exec**, including initial prefix and later chunks. This is not a per-NDJSON-frame allowance. Crossing it can occur after partial command execution. [S4]
- The initial command/environment request has a **1 MiB** message budget. [S4]
- Keep `--no-tty`: terminal allocation is not a lossless JSON transport guarantee and can merge terminal output. [S1][S2]
- Keep `--no-login-shell` for managed transport; default execution sources login startup files. The no-login mode still uses `bash -c`, so explicitly constrain `BASH_ENV` and inherited environment rather than claiming all startup influence disappears. [S2]
- Slow output readers backpressure the command. A background descendant keeping stdout/stderr open beyond the documented 30-second post-command drain limit produces output-delivery failure. [S2]
- Closing input is distinct from cancelling exec. Read remaining output and final status after stdin EOF. [S2][S4]
- The documented exec command does not automatically replay execution after failure. Internal owner-routing fallback in the source is not permission for application-stage retry. [S2][S4]

**Architecture consequence:** before advertising OpenShell as an omp transport, prove a live, long-lived, no-PTY bidirectional NDJSON exchange with delayed input, separately identifiable stdout/stderr, nonzero exit, EOF/drain, cancellation, and process cleanup on authorized compute. Source inspection establishes intended semantics only. The recorded local VM kernel fault is a hard stop: do not retry that compute path here.

Track bytes written over the complete omp exec, including control commands and answers. The current 4 MiB cap makes an unlimited-duration/unlimited-input workflow claim false. A stage reaching the bound must fail explicitly without replay; do not switch to a PTY to evade the cap. A larger transport would require a separately approved and verified contract, not an undocumented flag.

### Output and Exit Framing

The project backend API should keep `{stream:"stdout", bytes}`, `{stream:"stderr", bytes}`, and process close/exit observations separate. Never merge the streams or interpret stderr as omp frames. Omp stdout has its own JSONL framing; outer Node/Rust control stdout has a different schema.

The CLI's local nonzero exit alone cannot always distinguish a remote nonzero command from a CLI/transport failure. Record `cliExitCode`, signal, and outcome certainty separately; do not invent a remote exit code from diagnostic text. AgentOS process operations can use the real Sandbox Agent process result. Where exact remote command-exit provenance is required beyond CLI semantics, an explicit project-owned remote completion envelope is needed and must be validated; no such envelope is an OpenShell API. Until then, ambiguous failure is `outcome_unknown`, never success or a replay trigger. [S4]

### Transfers and Canonical Workspace

- Upload streams tar with bounded buffering and negotiated `openshell-upload-v1`; old supervisors are rejected before file data. Cancellation/failure can leave extracted partial files. [S5][S8]
- Directory upload normally preserves the source basename and merges/overwrites into an existing destination. A single unfiltered file uses cp-style placement; Git-filtered uploads treat destination as a directory. [S5]
- Upload preserves symlinks, including dangling links; a destination symlink to a directory can direct writes into its target. Git filtering is not a security allowlist. [S5]
- Download discovers the sandbox's canonical working directory and rejects lexical/symlink source escape. An exec `--workdir` is not evidence that the canonical download root changed. Directory download extracts contents into the selected host destination. [S5][S6]
- Upstream transfer helpers have their own bounded transport retry behavior: four attempts with two-second delay for retryable failures. This is not transactional rollback or stage replay. Add no generic retry layer around them. [S6]

**Project policy:** use an explicit manifest of owned non-secret transfer sources, bounded sizes, canonical source/destination checks, no traversal, and no symlink escape. Download into a fresh owned staging directory and validate before exposing artifacts. Never upload the host home, login database, auth material, `.state` private transcripts, or broad repository caches. Choose and verify the image's canonical workspace before promising `/sandbox/work` download support; the current source's chosen path alone establishes nothing about the image's SSH working directory.

### Sandbox Agent Endpoint and Forwarding

The current invocation `forward start NAME LOCAL:REMOTE` does not match v0.1.4. `forward start` takes `PORT [NAME]`, where PORT is `[bind_address:]port`, not an arbitrary local/remote port map. The pinned CLI provides a different, explicit service transport: [S1]

```text
openshell [GLOBAL_FLAGS] forward service NAME \
  --target-host 127.0.0.1 --target-port REMOTE_PORT \
  --local 127.0.0.1:LOCAL_PORT
```

Use the owned foreground service-forward child for the real Sandbox Agent HTTP endpoint and verify endpoint readiness from the embedding process/sidecar. This avoids pretending the standard provider is fulfilled by `getFetch` alone. A service forward runs **host → sandbox**; it does not by itself make a host auth gateway reachable from the sandbox. The latter needs its own approved connectivity design and proof. [S1][A3]

### Deletion Is a State, Not a Print Message

`delete` can return Completed, Accepted, or AlreadyAbsent. Accepted returns successfully while cleanup is pending. Keep the immutable resource ID, gateway, workspace, name, and ownership label until bounded observation confirms disappearance. Use structured exact-identity lookups, not `list.stdout.includes(name)`. If identity cannot be checked or deletion does not complete by deadline, retain `cleanup_failed`/remaining resources. Never delete a same-named replacement owned by somebody else. [S3][S7]

## 5. Anda Registration, Exposure, and Authentication

### Actual Tool Registration

Engine 0.17.4's supported builder methods include: [D2]

```rust
pub fn register_tool<T>(self, tool: Arc<T>) -> Result<Self, BoxError>
where T: Tool<BaseCtx> + Send + Sync + 'static;

pub fn export_tools(self, tools: Vec<String>) -> Self;
pub fn with_management(self, management: Arc<dyn Management>) -> Self;
pub async fn empty(self) -> Result<Engine, BoxError>;
pub async fn build(self, default_agent: String) -> Result<Engine, BoxError>;

pub async fn tool_call(
    &self,
    caller: Principal,
    input: ToolInput<Json>,
) -> Result<ToolOutput<Json>, BoxError>;
```

The implementation has mutability annotations on consumed parameters; the signatures above preserve their public parameter and return types. `build(default_agent)` requires that agent to be registered and automatically exports it. `empty()` assembles an engine without selecting a default agent; it is the appropriate documented management/tool-only path here. Do not register a mock model or an unrelated inference agent to make construction succeed. Default construction has no external model provider. [D2]

Implement real `anda_core::Tool<BaseCtx>` types for `run_gsd_stage`, `answer_question`, and `sandbox_status`; each shares the same supervised core handle. Core 0.17.1 requires: [D6]

```rust
type Args: DeserializeOwned + Send;
type Output: Serialize;
fn name(&self) -> String;
fn description(&self) -> String;
fn definition(&self) -> FunctionDefinition;
fn call(
    &self,
    ctx: BaseCtx,
    args: Self::Args,
    resources: Vec<Resource>,
) -> impl Future<Output = Result<ToolOutput<Self::Output>, BoxError>> + Send;
```

`init` has a default implementation. Tool names are validated and must be unique. Publish the three required tool names with `export_tools` and verify their discovery and invocation; registration alone is not public exposure. The engine only warns for an unresolved exported tool name because dynamic tools may load later, so a misspelled export is not necessarily a build failure. [D2][D6]

Use typed argument structures and `ToolOutput::new` or the full typed output. `ToolInput` contains `name`, `args`, optional resources, and optional `RequestMeta`. `RequestMeta.user` is explicitly unverified metadata; do not use it as the authenticated owner of a stage. Bind ownership to the principal established by server authentication. [D7]

### Server APIs and Wire Contract

The matching server's supported APIs are: [D4]

```rust
ServerBuilder::new()
    .with_addr(addr: String)
    .with_engines(
        engines: BTreeMap<Principal, Arc<Engine>>,
        default_engine: Option<Principal>,
    )
    .with_ed25519_pubkeys(pubkeys: Vec<VerifyingKey>)
    .with_middleware(middleware)
    .with_request_middleware(function);

pub fn build_router(self) -> Result<Router, BoxError>;
pub async fn serve(
    self,
    signal: impl Future<Output = ()> + Send + 'static,
) -> Result<(), BoxError>;
```

The chained argument labels above describe method signatures, not a compilable call expression. Every registered map key must equal `Engine::id()`; the explicit default must exist. The server defaults to loopback and supports embedding its router. Keep the application loopback-only. [D4]

**This is not the proposed child NDJSON protocol:**

- HTTP execution is `POST /{engine-principal-or-default}` with method `tool_call`, `agent_run`, or `information`.
- `RPCRequest` has `method: String` and encoded `params: ByteBufB64`; tool-call parameters decode as a one-element tuple `(ToolInput<Json>,)`.
- Request Content-Type selects CBOR or JSON for both the outer envelope and encoded inner payload. JSON does not mean arbitrary JSON-RPC 2.0 fields or a raw `{name,args}` HTTP body.
- `RPCResponse` is a Result carrying encoded success bytes or an error string. Execution/parameter errors normally use HTTP 200 with an error envelope; HTTP status alone cannot establish tool success.
- The server documents a default 2 MiB request-body limit. Public `.well-known` endpoints expose exported metadata; they are not a live omp event stream. [D4][D5][D8]

Return stage acceptance promptly. For HTTP clients, `sandbox_status` supplies pending questions/current state, optionally with a project-defined event cursor in its result; do not claim that Anda server 0.17.0 supplies an existing stage/question streaming endpoint. Local CLI events can be driven by Rust's child NDJSON reader.

### Authentication and Authorization Must Both Be Configured

- `BaseManagement { controller, managers, visibility: Visibility::Private }` restricts execution to a non-anonymous controller/managers. Anonymous is never considered a manager, even when configured as controller. Protected visibility also does not automatically grant a non-manager direct tool execution. [D9][D2]
- `SignedEnvelope` is supported through `Authorization: ICP ...` or legacy `ic-auth-*` headers. Signed RPCs require an explicit digest matching SHA3-256 of the received body. Supplied malformed/invalid credentials fail rather than silently becoming anonymous. [D5]
- Bearer authentication means **CWT**, not an arbitrary opaque shared token: configure trusted Ed25519 keys with `with_ed25519_pubkeys`; valid principal `sub` and `exp` are required. Server documentation warns that bearer tokens are not body/engine bound and that `aud`/scope are not enforced. [D5]
- `ApiKeyMiddleware::new(expected_key)` checks `x-api-key`; `exempt_path` and `exempt_prefix` configure bypasses. It is an additional ingress guard, not a principal resolver. An API key alone does not make a private engine's anonymous caller authorized. [D10][D9]
- `RequestMeta::engine` is validated when present; require it in this application's trusted client path. Direct-key signatures themselves do not provide freshness or automatic engine binding. Do not describe the upstream signature scheme as replay-proof. [D2][D5]

**Recommended local contract:** private engine, explicit operator principal authorization, verified ICP request signatures (or a deliberately configured CWT issuer), loopback bind, and stage/session ownership checks in the project tools. Do not use Public visibility merely to make a demo invocation work. Do not make the protected login database or provider tokens part of this identity setup.

### Rust Toolchain Gate

The engine release is Rust edition 2024, but edition support alone is insufficient. Its published `lib.rs` contains `if let ... && let ...` chains; those stabilized in Rust **1.88.0**, later than the supplied rustc 1.85.1. The server release also uses let chains. Therefore the selected unmodified source is not compatible with that compiler's stable language support. This is source evidence, not a local build failure. [D11][D12]

The existing local manifest comment claiming an exact `>=1.95` floor is not established by this research. Full graph MSRV/native-library compatibility is still unresolved. Obtain an explicitly authorized compatible toolchain before the real Anda build gate; do not silently upgrade, downgrade Anda, or call an empty Cargo feature an integration.

## 6. Project-Owned NDJSON Control Protocol — Proposed Design

**Everything in this section is a proposal owned by this project. It is not an existing Anda API, an OpenShell API, or an omp API.**

The Rust ↔ Node pipe needs a small versioned envelope with independent response and event correlation:

```json
{"v":1,"kind":"request","id":"r-1","op":"run_gsd_stage","args":{"stage":"plan","task":"Prompt text is data"}}
{"v":1,"kind":"response","id":"r-1","ok":true,"result":{"stageId":"st-1","sessionId":"ss-1","status":"starting"}}
{"v":1,"kind":"event","seq":12,"event":"question","stageId":"st-1","sessionId":"ss-1","questionId":"q-1","data":{"method":"ask","questions":[]}}
{"v":1,"kind":"request","id":"r-2","op":"answer_question","args":{"stageId":"st-1","sessionId":"ss-1","questionId":"q-1","answer":{"text":"Operator answer"}}}
```

The empty example question array illustrates an envelope, not an accepted real ask payload; real question schemas must be validated from the originating omp request.

### Operations and Correlation

| Proposed operation | Meaning |
|---|---|
| `up` | Start once or return current startup status; concurrent duplicate startup cannot acquire a second sandbox |
| `run_gsd_stage` | Accept one `plan`/`execute` stage in the owned workspace; return identity, not workflow success |
| `answer_question` | Answer exactly one still-pending question for the matching stage and session |
| `sandbox_status` | Report startup, selected tier, effective model, session/stage/question state, cleanup and owned resources |
| `abort` | Begin cancellation and report its actual outcome separately from receipt of the abort request |
| `down` | Stop acceptance, cancel active work, drain/stop sessions, dispose VM/provider, release owned helpers, report leftovers |

Do not permit arbitrary tool names, model names, commands, filesystem roots, or operation fallthrough through this channel. Stage-to-skill/model routing is a core allowlist, not caller-provided shell syntax.

- Each request ID is unique within one child epoch. Responses echo it exactly; unsolicited events have monotonic sequence numbers and stage/session identity.
- Rust owns the pending request map; Node is authoritative for execution/resource state. Rust may cache views but cannot mark a stage completed from a child exit or a successful HTTP tool response.
- `stageId`, omp `sessionId`, omp prompt request ID, and UI request ID are different identities. Preserve their mapping explicitly.
- A new child has a new epoch. Answers and responses from a previous child epoch are stale.
- Use one writer per stdout pipe, strict UTF-8/JSON decoding, maximum physical line bytes, bounded pending requests and output queues, and real write backpressure. Reject malformed/oversized frames before unbounded allocation.
- Core stdout is protocol only. Library logs, bootstrap output, and subprocess stderr go to separately bounded/redacted diagnostic paths, never into protocol framing.
- Duplicate mutating request IDs are rejected; there is no automatic replay/deduplication framework. A timeout after write can mean an accepted operation with unknown acknowledgement; status reconciliation must not resubmit it.

Proposed initial control limits: 1 MiB maximum physical frame including newline, 64 in-flight control requests, bounded event/diagnostic retention, and 30-second ordinary request/transport deadlines. These are **project design defaults**, not upstream promises. Prompts and answers exceeding the selected transport's limits must be rejected before submission, with explicit errors rather than truncation.

## 7. Separate State Machines

A single `ready/running/done` flag cannot represent this system.

| State owner | Proposed states | Key invariant |
|---|---|---|
| Core startup | unconfigured → checking_prerequisites → creating_sandbox → booting_vm → preparing_workspace → discovering_skills → ready; failed | Process readiness is not application readiness |
| Stage | starting → planning/executing → waiting → planning/executing → completed/failed; aborting → aborted | One active stage per owned workspace; preserve plan/execute identity through fallback |
| Omp session | starting → ready → busy/waiting → settling → ready/closed; broken | A yielded prompt need not mean settled session |
| Question | pending → responding → answered; cancelled/expired | Only the matching pending question/session accepts an answer |
| Transport | opening → healthy → closing/closed; failed/outcome_unknown | EOF, parser error, write failure and timeout are not success |
| Resource ledger | acquiring → owned → releasing → released; acquisition_unknown/release_failed | Identity and cleanup responsibility survive partial startup failure |
| Shutdown | stopping → stopped or cleanup_failed | Completed stage and incomplete cleanup can coexist |

A question entering `responding` is not already answered. Validate the complete answer before writing; if the write outcome is uncertain, retain that uncertainty and reconcile with the same session—do not silently resend or default the answer. Omp UI cancellation, timeout, session transition, or terminal stage invalidates its question mapping. Recommended choices are suggestions for the operator, not authorization for the wrapper to answer automatically. [O1]

The resource ledger records only ownership metadata: child/process handles, immutable sandbox ID/name/gateway/workspace/labels, owned paths, acquisition/release state, and bounded errors. It contains no provider credentials, full prompts, answers, or private transcripts. Record acquisition intent before uncertain create and preserve enough metadata to identify only this run's resources after failure.

## 8. Data Flows

### Startup

1. Rust checks the approved Node executable and starts one Node core with piped control stdio and bounded child supervision.
2. Node validates tier authorization, pinned packages/tool availability, approved workspace and endpoint policy. The known kernel-faulting OpenShell path remains unavailable; no probe retries it.
3. Acquire or reuse the authorized host-side broker/gateway boundary. Mark reused processes unowned; stop only explicitly acquired owned processes on shutdown.
4. Create the selected external sandbox once, recording ownership intent and immutable identity. Establish its Sandbox Agent HTTP endpoint and real process/filesystem capabilities.
5. Pass the actual provider to `AgentOs.create({ sandbox: { provider, ... } })`; address the pinned host-function ordering hazard without unsafe casts.
6. Prove VM write → sandbox read and sandbox write → VM read, plus a command through `agentos-sandbox` and a process/list operation. A successful host exec alone does not satisfy this gate. [A1]
7. Prepare Node/Bun/omp and exactly local GSD-Core 1.18.0 using the project-selected `npx @opengsd/gsd-core@1.18.0 --claude --local` path. This installer decision comes from the supplied project contract, not an assumption about another GSD integration.
8. Launch a real sandboxed omp RPC session, wait for its ready frame, handle advertised framing, enable ask dialogs, query state/models and `get_available_commands`, and require `gsd-plan-phase` and `gsd-autonomous`. Omp documents ready, ask opt-in, discovery, and model/state operations. [O1]
9. Report ready only after those gates succeed, with requested/effective model, selected/degraded tier, permitted endpoints, VM bridge evidence and unverified policy limitations.

### Stage Dispatch and Completion

Project routing is fixed:

| Stage | Skill invocation | Requested model |
|---|---|---|
| plan | `/skill:gsd-plan-phase` | `openai-codex/gpt-6.1-sol` |
| execute | `/skill:gsd-autonomous` | `openai-codex/gpt-6-luna` |
| permitted fallback | Same stage/skill identity | `zai/glm-5.3`, explicitly disclosed |

The mapping is a supplied product decision. Verify model availability and effective routing in the actual session/gateway; a model string in argv is not proof of the model that answered. Omp supports `set_model`, `get_state`, `get_available_models`, skill prompts and streamed events. [O1]

Send prompt/task text as an omp JSON `prompt.message`; never build a shell command from it. Discovery must identify the selected commands as real skills and the required invocation path. Do not substitute bare `/gsd-*`, execute-phase, a prompt template with the same display name, or an unrelated skill.

Omp's completion contract is more specific than the current client assumes: [O1]

- A successful prompt response usually acknowledges **admission**, not inference/workflow completion.
- A response with `data.agentInvoked:false` is itself local completion and has no later `prompt_result`.
- Otherwise use that prompt's correlated `prompt_result`; its status is `completed`, `aborted`, or `error`.
- `prompt_result.status="completed"` can represent agent yield while background work remains. Respect `sessionSettled`; otherwise wait for `session_settled` or matching settled state before disposing/recycling the session.
- Workflow acceptance additionally needs the actual GSD stage outcome/artifacts for the disposable task. Neither command admission nor a successful assistant turn alone proves a real planning/execution stage completed.

Model fallback may be selected before submitting stage work when the primary route is unavailable. After stage admission or any uncertain execution, do not replay the prompt on a fallback. Failure with no observed model output does not prove that nothing happened. Preserve stage identity and report the uncertain/failed outcome; any later operator-directed action is explicit.

### Live Question and Answer

1. Omp emits `extension_ui_request` with its UI ID and method.
2. Node validates a supported method, records a pending question under current stage/session, and publishes a question event/status view.
3. Rust authorizes the caller against the stage's authenticated owner and forwards an answer as data.
4. Node rejects unknown stage/session, stale/cancelled/answered UI IDs, invalid option labels, incomplete per-question answers, duplicate submission, malformed input, and unsupported methods.
5. Node builds the exact omp `extension_ui_response` for `ask`, `select`, `confirm`, `input`, or `editor`. Omp supports explicit cancellation and emits `cancel` with `targetId` on signal abort. [O1]
6. Waiting for an operator is a live workflow state. Continue servicing state/abort/shutdown and transport health; do not synthesize an answer because an ordinary control deadline elapsed.

### Authentication Flow

Only the gateway receives broker URL/token. The sandbox receives the gateway endpoint and gateway-only capability through a deliberately protected project mechanism; tokens are never CLI argv, diagnostics, receipts, or provider configuration artifacts containing upstream credentials. Network policy admits the necessary gateway routes and denies broker snapshot/credential access and unrelated endpoints. Auth gateway documentation explicitly says client account pools are routing policy, not an authorization boundary. [O2]

Do not invent `OMP_AUTH_GATEWAY_*` environment variables: the pinned document lists broker env configuration and supports gateway-compatible provider routes; exact sandbox omp model/provider configuration and preservation of effective `openai-codex`/`zai` identity must be proven during integration. No sandbox may fall back to the host login store. Gateway reachability from each namespace/backend and model identity through the gateway are explicit unresolved application contracts.

### Shutdown and Cleanup

1. Atomically stop accepting new stages/answers; snapshot owned resources and pending questions.
2. Request omp abort and observe its actual outcome within the abort budget.
3. Close input when appropriate, continue draining output, and wait for close; escalate only owned process groups/handles after grace. Killing an OpenShell CLI child is not by itself proof that its remote process stopped. [O1][S4]
4. Dispose the VM; its provider hook releases the attached sandbox client/resource. Collect errors, then perform only necessary same-owner emergency cleanup.
5. Stop owned forwards/server children and owned gateway/broker processes; never stop reused ones. Await child close/reaping within bounds.
6. Observe sandbox deletion completion, not merely acceptance, and release/remove only validated owned temporary paths.
7. Emit final mock receipt with independent stage result and cleanup result, remaining resource identities, and any `outcome_unknown`. Do not erase the ledger or return stopped/clean merely because the deadline expired.

Anda `Engine::close()` cancels the engine token and awaits cancellation notification; its source does **not** stop a project-owned Node process or prove external resource cleanup. The wrapper must implement those ownership actions. [D2]

## 9. Bounded Failure Model

Proposed finite budgets must be centralized, visible in status, and reviewable; they are not upstream defaults. Use an overall startup budget plus tighter per-step deadlines, ordinary control-request deadlines, active-execution budget, abort budget, child close/kill grace, and final cleanup observation deadline. A practical initial policy is 30 seconds for ordinary control requests, 60 seconds for health/discovery steps, up to 15 minutes each for backend creation/preparation within an explicit overall startup budget, 60 minutes of active stage execution, and 120 seconds total shutdown. Human-answer waiting is tracked separately from active execution; no default answer is injected when a transport timer fires.

| Failure | Required observable result | Recovery boundary |
|---|---|---|
| Missing Node/Bun/omp/GSD or required skill | Distinct prerequisite/discovery failure, acquired resources listed | Correct prerequisite; do not start inference |
| OpenShell unavailable / supplied kernel fault | Backend unavailable, selected authorized degraded tier disclosed | No local VM retry or privilege/toolchain changes |
| Create timeout or malformed create metadata | Acquisition unknown; retain requested name/labels and any ID | Query only that owned intent; never create again automatically |
| Partial bootstrap/upload/download | Preparation/transfer failure and partial-file ownership | Clean owned staging or inspect explicitly; no claim of rollback |
| VM boot or bridge probe failure | VM/integration failure, not ready | Dispose all acquired resources; no host-only substitute acceptance |
| Auth/model failure before dispatch | Auth/model-unavailable state | Choose only permitted disclosed fallback before submission |
| Failure after admission/partial input | Failed or outcome-unknown stage | Never stage replay based on lack of model messages |
| Invalid/oversized protocol or chunk sequence | Protocol failure; bounded diagnostic without private payload | Close/quarantine affected session; no silent noise suppression |
| Broken pipe/child exit | Reject outstanding requests; invalidate affected questions; preserve resource ledger | Bounded shutdown/reconciliation, not automatic child/stage replay |
| Silence during pending human question | Waiting with question identity and live transport check | Continue waiting or accept explicit abort |
| No response to health/control request | Transport timeout, separate from human wait | Abort/close within budget; report unknown remote outcome if needed |
| Cumulative OpenShell input limit | Explicit transport-input-limit failure before exceeding known bound where possible | No PTY bypass, silent session rotation, or replay |
| Abort acknowledged but work not stopped | Remain aborting, then fail/unknown on deadline | Kill only owned handles and verify remote cleanup |
| Delete accepted but still present | Cleanup pending → cleanup_failed at deadline | Preserve resource identity and operator-visible remaining resource |
| Rust/Node crash | Child failure, unresolved owned resources, all stale questions invalidated | New epoch; reconcile exact ownership before new work |

Omp advertises 1 MiB physical frames and optional v2 chunking up to 64 MiB logical frames. Chunk handling requires strict index/count/byte-length validation and bounded reassembly. The current unbounded line accumulator is not sufficient. Do not import the Bun-only omp SDK into Node merely to obtain a client; implement/use a demonstrably Node-safe bounded protocol boundary following the pinned contract. Continue reading after input closure because upstream output drain can otherwise wait indefinitely. [O1]

## 10. Recommended Project Structure

Preserve the existing layout and user changes; consolidate rather than retaining old and new APIs in parallel.

```text
crates/omp-sandbox-anda/src/
  main.rs           # tool-only engine/server and local CLI entry
  tools.rs          # actual typed Anda tools, authorization boundary
  core_child.rs     # bounded child supervision and NDJSON correlation
  attestation.rs    # visibly mock local receipt metadata only
src/core/
  server.ts         # proposed project control protocol codec/dispatch
  core.ts           # lifecycle and authoritative stage/session state
  vm.ts             # typed AgentOS provider attachment and bridge checks
  omp-rpc.ts        # pinned omp codec, correlation, questions, settlement
  auth.ts           # host-only broker/gateway ownership and capabilities
  gsd.ts            # pinned local install/discovery readiness
  providers/
    types.ts        # project backend/exec/transfer ownership contracts
    openshell.ts    # exact v0.1.4 CLI backend and service forward
    bwrap.ts        # separately proven and honestly degraded tier
    local.ts        # explicitly authorized host-execution tier
    tiers.ts        # authorization-aware selection, no unsafe fallthrough
sandbox/
  bootstrap.sh      # owned sandbox preparation, no host credential copy
  policy.yaml       # reviewable pinned backend policy
```

Additional protocol/state modules are justified only when extracting substantial shared logic from these files; do not introduce a second compatibility layer, generic retry framework, telemetry subsystem, or replacement inference engine.

### Existing-Source Migration Gaps

These are source observations, not runtime failure reproductions:

| Existing file | Gap to resolve |
|---|---|
| `src/core/vm.ts:10–22` | Client attachment and `any` casts instead of the decided typed provider; explicit host-function injection reflects the source-ordering hazard but does not validate the workaround. Preserve the user's existing edit. |
| `src/core/providers/types.ts` | Project tier interface has exec but no complete explicit upload/download surface or uncertain-acquisition model. It is not an upstream AgentOS provider interface. |
| `src/core/providers/openshell.ts:39–84` | No complete download operation; forwarding argv does not match pinned CLI; environment/credentials and canonical workspace need review; created=false on uncertain create can lose ownership; delete/list text check is insufficient. |
| `src/core/auth.ts:18–35` | Broker-only startup and broker env passed to sandbox; no gateway boundary. The source comment describing token handling is not evidence of required credential isolation. |
| `src/core/core.ts` | Bare slash construction, wrong required execution command, concurrent-stage exposure, and recursive model fallback/replay need clean migration. Startup must unwind partially acquired resources and distinguish lifecycle states. |
| `src/core/omp-rpc.ts` | Unbounded buffer/noise acceptance; no advertised v2 decoder; no local-only prompt completion path or settled-session lifecycle; automatic recommended answers; no complete write/error/close supervision. |
| `src/core/server.ts` | Untyped concurrent line handlers, unbounded input, no child epoch/version/size limits; ready event precedes actual startup; repeated EOF/signals can race cleanup. |
| `src/core/providers/tiers.ts` | Unknown tier names fall through to local; all startup exceptions can trigger degradation; cleanup errors are suppressed. Enforce supported tiers and authorization, and do not degrade after uncertain execution or ownership loss. |
| `src/core/providers/bwrap.ts` | Source shares host network and binds `/etc`; the supplied minimal `--unshare-all` probe did not test this application profile. Neither endpoint-only egress nor full application isolation is established. |
| `crates/omp-sandbox-anda/src/tools.rs` and manifest | Strings and empty features are not real Anda Tool registration, engine embedding, server exposure, or authentication. |
| `crates/omp-sandbox-anda/src/core_child.rs`, `main.rs` | Unbounded line/channel readers, no complete per-request supervision, and EOF shutdown without a hard final deadline. |

No private stage/receipt/prompt data was inspected to make these observations.

## 11. Build Order and Acceptance Gates

1. **Resolve hard prerequisites first.** Record the incompatible current Rust language support and the approved toolchain decision; confirm selected release/package provenance without changing global tools. Preserve protected workspaces and the user's VM edit.
2. **Define ownership/protocol/state contracts before connecting everything.** Implement the proposed child envelope, immutable resource ledger, exact tier authorization, independent lifecycle states, cancellation and cleanup deadlines. This prevents later adapters from inventing incompatible meanings of ready/done/dispose.
3. **Make one authorized degraded backend genuinely usable.** Bwrap's minimal namespace probe is insufficient. Prove its actual application mounts, child/process cleanup and permitted endpoint connectivity, or explicitly select host execution with its lack of isolation. Do not silently widen networking to pass inference.
4. **Integrate the actual AgentOS provider and bridge.** Prove endpoint serialization, mounted file round trip, host-function registration and provider disposal with the selected release. Resolve the early-host-function ordering hazard before calling the VM requirement done.
5. **Add the host broker/gateway security boundary and sandbox connectivity.** Prove effective provider/model routing without exposing tokens or making the broker reachable from the sandbox. Gateway use does not automatically establish endpoint enforcement.
6. **Prove sandboxed omp RPC and GSD readiness.** Test ready/framing, exact skill source/invocation, ask-dialog opt-in, state/model discovery, actual planning/execution outcomes, settlement, stale answers and cancellation. No automatic replay.
7. **Land real Anda tools/server on the approved toolchain.** Use typed tools, explicit exports, private management, working signatures/CWT as selected, caller ownership, correct encoded RPC, concurrent status/answers during a run, and bounded wrapper shutdown. No separate model inference.
8. **Implement and validate OpenShell on authorized working compute only.** Source-grounded CLI work can precede access, but create/exec/transfers/policy/lifecycle are not runtime-accepted until exercised there. Do not rerun the recorded faulting local VM. OpenShell unavailability remains explicit even after a degraded end-to-end path succeeds.
9. **Run end-to-end failure and cleanup acceptance.** Real wrapper → Node → VM → sandbox → omp → GSD task, real live question/answer, stale rejection, abort/controlled failure, output framing and remaining-resource reporting. Every receipt remains mock, including failed/degraded runs.

## 12. Scaling and Anti-Patterns

This is a single-operator owned sandbox project. Start with one core, one workspace and one active stage. The first bottlenecks are bounded protocol/output retention, OpenShell's cumulative stdin cap, and exclusive workspace mutation—not horizontal service scaling. Add concurrency only after explicit independent workspace/session/resource ownership exists.

Avoid:

- Treating `AgentOsSandboxProvider`, `AgentOsSandboxClient`, and `sandbox-agent` `SandboxProvider` as interchangeable types.
- Using `as any` to conceal endpoint, lifecycle, or host-function-contract mismatches.
- Treating a sandbox root mount as process/network confinement.
- Importing the Bun-only omp SDK into Node.
- Giving sandbox omp a broker token because it avoids gateway setup.
- Treating success response, prompt yield, session settlement, workflow outcome and cleanup completion as one event.
- Using model fallback as a replay loop after uncertain work.
- Calling an API key middleware principal authentication, or claiming direct signed requests are replay-proof.
- Retrying uncertain create, deleting `--all`, deleting by loose name matching, or forgetting resources when SDK client fields clear.
- Calling a bwrap namespace probe full isolation or an empty Cargo feature real Anda integration.
- Labeling local receipts/hardware as production attested.

## 13. Production TEE Boundary — Target Only

`dTEE` is the project's requested architecture label. Keep the intended IC-TEE/AWS Nitro/ICP identity/configuration/state target separate from the OpenShell confidential-compute host. This research does not establish a deployed IC-TEE system, enclave identity provisioning, SGX/GPU TEE support, or hardware measurements.

AWS documents Nitro Enclaves as highly constrained VMs with parent-local socket connectivity, no persistent storage, no interactive access and no external networking. Therefore do not draw a Nitro enclave as the ordinary Linux/KVM/OpenShell host or assume its processes can directly reach the same HTTP gateway. Production networking, attestation binding, identity provisioning and state integration require their own design and hardware evidence. [T1]

## 14. Key Blockers, Unresolved Contracts, and Confidence

| Item | Finding | Confidence / next evidence required |
|---|---|---|
| AgentOS interfaces and adapter lifecycle | Exact installed/published 0.4.3 contracts resolved | High source confidence; live bridge/disposal still unverified |
| Automatic sandbox host functions | Pinned implementation resolves functions before sandbox injection | High source-order confidence; exact runtime impact/typed mitigation require acceptance |
| OpenShell CLI methods | Exact release flags, stream handling, transfers, service forward and asynchronous deletion resolved | High source confidence; no live compute semantics proven |
| OpenShell long-lived transport | 200 ms open-pipe startup is supported, but total piped input is limited to 4 MiB | High source confidence; capacity must be exposed and a real NDJSON exchange proven |
| OpenShell compute | Supplied local progress records a kernel-faulting VM path | Hard local stop; authorized working compute is required for OpenShell runtime acceptance |
| External filesystem endpoint | Standard serializable baseUrl needed beyond the declared client methods | High source confidence; sidecar reachability and real filesystem API must be tested |
| Image/canonical workspace | Existing `/sandbox/work` paths do not establish the image's canonical download root or long-lived canonical main | Unverified application contract; choose/inspect selected image and prove transfers |
| Auth gateway | Direct broker access exposes credentials; gateway is required | High source confidence; sandbox reachability, exact omp provider configuration and effective model identity remain unverified |
| Degraded networking | Minimal bwrap probe does not prove application restrictions; current source shares networking | No application isolation/egress acceptance; requires actual policy proof or explicit host-tier disclosure |
| Anda matching releases | Engine 0.17.4, core 0.17.1, server 0.17.0 resolved; public registration/auth APIs identified | High source confidence; full graph build and real authenticated tools unverified |
| Rust compiler | Selected source uses let chains stabilized after rustc 1.85.1 | Source-grounded incompatibility; exact full-graph MSRV unresolved; authorized toolchain required |
| Anda identity bootstrap | Private non-anonymous authorization and signed/CWT transport must be configured | API known; approved local operator identity/capability configuration not runtime-proven |
| Workflow completion | Omp response/prompt-result/session settlement are distinct | High contract confidence; actual GSD outcome acceptance remains task/runtime-specific |
| TEE | Only production target and mock-local requirement are established | No local hardware or production claim |

### Verification Handoff

No executable checks were performed by this scout. The implementation owner should plan, not claim already passed: exact pinned TypeScript/provider compatibility checks; full approved Rust dependency build; real provider acquisition/partial-failure/disposal checks; authorized-backend filesystem/process/transfer/policy checks; delayed-input NDJSON/stdout/stderr/exit/EOF and input-limit checks; gateway credential-boundary/effective-model checks; skill discovery and real GSD planning/execution/question/answer checks; stale/overlap/malformed-input rejection; controlled child failure/abort; and bounded cleanup with remaining-resource receipts. OpenShell VM compute must not be retried on this faulting host.

## Sources

### AgentOS

[A1]: https://rivet.dev/agentos/docs/sandboxes.md
[A2]: https://unpkg.com/@rivet-dev/agentos-core@0.4.3/dist/sandbox.d.ts
[A3]: https://unpkg.com/@rivet-dev/agentos-core@0.4.3/dist/sandbox.js
[A4]: https://unpkg.com/@rivet-dev/agentos-core@0.4.3/dist/agent-os.d.ts
[A5]: https://unpkg.com/@rivet-dev/agentos-sandbox@0.4.3/dist/provider.js
[A6]: https://unpkg.com/sandbox-agent@0.4.2/dist/types-DdcvY5CI.d.ts
[A7]: https://unpkg.com/@rivet-dev/agentos-core@0.4.3/dist/agent-os.js
[A8]: https://unpkg.com/sandbox-agent@0.4.2/dist/chunk-TVCDKGSM.js

- [Rivet external sandboxes guide][A1] — official usage; not itself release-pinned, cross-checked against the selected packages.
- [Core 0.4.3 sandbox declarations][A2] and [runtime][A3].
- [Core 0.4.3 VM declarations][A4] and [creation/disposal implementation][A7].
- [Adapter 0.4.3 declaration](https://unpkg.com/@rivet-dev/agentos-sandbox@0.4.3/dist/provider.d.ts) and [implementation][A5].
- [Sandbox Agent 0.4.2 backend contract][A6] and [client lifecycle implementation][A8].
- Installed copies were inspected under the corresponding `node_modules` paths; package manifests identify versions 0.4.3/0.4.3/0.4.2.

### OpenShell v0.1.4

[S0]: https://api.github.com/repos/NVIDIA/OpenShell/git/trees/v0.1.4?recursive=1
[S1]: https://github.com/NVIDIA/OpenShell/blob/v0.1.4/crates/openshell-cli/src/main.rs
[S2]: https://github.com/NVIDIA/OpenShell/blob/v0.1.4/docs/how-it-works/sandboxes/overview.mdx
[S3]: https://github.com/NVIDIA/OpenShell/blob/v0.1.4/crates/openshell-cli/src/run.rs#L494-L1155
[S4]: https://github.com/NVIDIA/OpenShell/blob/v0.1.4/crates/openshell-cli/src/run.rs#L1852-L2840
[S5]: https://github.com/NVIDIA/OpenShell/blob/v0.1.4/docs/how-it-works/sandboxes/overview.mdx#L854-L968
[S6]: https://github.com/NVIDIA/OpenShell/blob/v0.1.4/crates/openshell-cli/src/ssh.rs#L1516-L1825
[S7]: https://github.com/NVIDIA/OpenShell/blob/v0.1.4/crates/openshell-cli/src/run.rs#L3882-L3980
[S8]: https://github.com/NVIDIA/OpenShell/blob/v0.1.4/crates/openshell-cli/src/ssh/upload.rs

- [Pinned tag resolution][S0].
- [CLI flags][S1]: global flags near 437; create/exec/transfers/delete near 1446–1822; forwarding near 2342–2395; actual exec dispatch near 3607–3645.
- [Release sandbox lifecycle/exec/transfer documentation][S2].
- [Create implementation][S3]; structured metadata fields near run.rs 3086.
- [Exec limits, selection and streaming][S4], especially cumulative input accounting near 2650–2680.
- [Transfer documentation][S5], [tar/download/retry implementation][S6], [credited upload implementation][S8].
- [Delete outcomes and cleanup-pending behavior][S7].

### Anda

[D1]: https://docs.rs/crate/anda_engine/0.17.4/source/Cargo.toml.orig
[D2]: https://github.com/ldclabs/anda/blob/b42da9388048b89cb50e04a4c04ac2952cdada43/anda_engine/src/engine.rs
[D3]: https://docs.rs/crate/anda_engine_server/0.17.0/source/Cargo.toml.orig
[D4]: https://github.com/ldclabs/anda/blob/b7cc8aee29c6f41594ce91bb2a5dbae09ee6741b/anda_engine_server/src/lib.rs
[D5]: https://github.com/ldclabs/anda/blob/b7cc8aee29c6f41594ce91bb2a5dbae09ee6741b/anda_engine_server/README.md
[D6]: https://github.com/ldclabs/anda/blob/2c06cfcdf23fda1bd1cf23a25bc4681fa15a4078/anda_core/src/tool.rs
[D7]: https://github.com/ldclabs/anda/blob/2c06cfcdf23fda1bd1cf23a25bc4681fa15a4078/anda_core/src/model.rs#L298-L393
[D8]: https://github.com/ldclabs/anda/blob/b7cc8aee29c6f41594ce91bb2a5dbae09ee6741b/anda_engine_server/src/handler.rs
[D9]: https://github.com/ldclabs/anda/blob/b42da9388048b89cb50e04a4c04ac2952cdada43/anda_engine/src/management.rs
[D10]: https://github.com/ldclabs/anda/blob/b7cc8aee29c6f41594ce91bb2a5dbae09ee6741b/anda_engine_server/src/middleware.rs
[D11]: https://github.com/ldclabs/anda/blob/b42da9388048b89cb50e04a4c04ac2952cdada43/anda_engine/src/lib.rs
[D12]: https://blog.rust-lang.org/2025/06/26/Rust-1.88.0/

- [Engine published release provenance](https://docs.rs/crate/anda_engine/0.17.4/source/.cargo_vcs_info.json): commit `b42da9388048b89cb50e04a4c04ac2952cdada43`.
- [Server published release provenance](https://docs.rs/crate/anda_engine_server/0.17.0/source/.cargo_vcs_info.json): commit `b7cc8aee29c6f41594ce91bb2a5dbae09ee6741b`.
- [Core 0.17.1 published release provenance](https://docs.rs/crate/anda_core/0.17.1/source/.cargo_vcs_info.json): commit `2c06cfcdf23fda1bd1cf23a25bc4681fa15a4078`.
- [Engine manifest][D1], [server manifest][D3], [engine registration/execution/close APIs][D2].
- [Tool trait][D6], [tool/request metadata][D7], and [core RPC envelope](https://github.com/ldclabs/anda/blob/2c06cfcdf23fda1bd1cf23a25bc4681fa15a4078/anda_core/src/http.rs#L56-L104).
- [Server builder][D4], [documented HTTP/auth contract][D5], [authentication/dispatch implementation][D8], [management authorization][D9], [API-key middleware][D10].
- [Engine source language usage][D11], [edition/dependency configuration](https://github.com/ldclabs/anda/blob/b42da9388048b89cb50e04a4c04ac2952cdada43/Cargo.toml), [Rust let-chain stabilization][D12].
- Docs.rs API pages for these engine/server releases failed to build, so their published source/provenance was used instead of substituting older generated API documentation.

### Omp and TEE

[O1]: https://github.com/can1357/oh-my-pi/blob/v18.8.4/docs/rpc.md
[O2]: https://github.com/can1357/oh-my-pi/blob/v18.8.4/docs/auth-broker-gateway.md
[T1]: https://docs.aws.amazon.com/enclaves/latest/user/nitro-enclave.html

- [Omp v18.8.4 RPC][O1] — selected source snapshot matching the existing adapter's declared version; installed/runtime identity still requires later verification.
- [Omp v18.8.4 broker/gateway boundary][O2].
- [AWS Nitro Enclaves constraints][T1] — production boundary context only, not local hardware proof.

Local project requirements and source observations are grounded in `.planning/PROJECT.md` and the named source files, not private runtime artifacts. The architecture template and research persona informed document structure; this work was performed by an actual read-only scout, not a named GSD dispatch process.
