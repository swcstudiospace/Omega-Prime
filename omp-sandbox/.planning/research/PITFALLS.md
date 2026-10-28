# Pitfalls Research

**Project:** Ove's Local omp Sandbox  
**Domain:** Owned Rust → Node → agentOS VM → external Linux sandbox → Bun/omp RPC → GSD workflows  
**Researched:** 2026-10-21  
**Confidence:** HIGH for the cited selected-version/public-distribution contracts and source gaps; runtime compatibility, isolation, authentication, and end-to-end acceptance remain UNVERIFIED.

## Evidence Rules and Delivery Ownership

This is research, not implementation or runtime proof. No builds, tests, linters, formatters, installations, token commands, or service startup were run. No authentication stores, token files, private prompt logs, `.state/stages`, receipts, or `settings.local.json` were inspected. Protected workspaces were not used as sources. Consumer-visible tests below are **proposed acceptance gates**, not results.

The supplied [project contract](../PROJECT.md) is authoritative for scope. Existing source is an unvalidated starting point. Distinguish:

- **Recorded observation:** the supplied minimal bwrap namespace probe succeeded; it proves that probe only.
- **Recorded failure:** [PROGRESS.md](../../PROGRESS.md), line 6, records OpenShell MicroVM termination by signal 11 and a KVM kernel fault. **Do not reproduce it.** Its recorded cleanup is not proof of the application's cleanup implementation.
- **Source gap:** code paths described below were inspected but not exercised. An inferred leak or failure mode is a risk, not a newly observed runtime failure.
- **External contract:** source URLs are version-pinned where available. Installed omp package metadata identifies 18.8.4; this does not independently prove the separately resolved executable's version or successful execution.

For roadmap ownership, use these **capability-phase labels**, not assumed final phase numbers:

| Capability phase | Owns |
|---|---|
| **Contracts and compatibility** | Exact package/distribution contracts, Rust graph compatibility, schemas, security boundaries |
| **Sandbox and owned lifecycle** | Provider create/exec/upload/download/delete, constrained paths, tier disclosure, resource ownership and rollback |
| **Gateway authentication and preparation** | Host broker/gateway boundary, reachable restricted endpoint, clean runtime environment, pinned tools and live skill discovery |
| **Interactive workflow runtime** | Correlated RPC, models, questions, cancellation, outcome safety and no replay |
| **Anda tool boundary and supervision** | Real Engine/server registration, authorization, Rust child ownership and bounded failure handling |
| **Operational evidence and receipts** | Honest receipts, operator commands, real end-to-end demonstration, failure/degraded evidence |

## Resolved Authentication Contract

The required path is **not** “give sandbox omp a broker token.” The selected [omp 18.8.4 authentication documentation][omp-auth] states that direct broker clients are trusted: broker snapshots expose OAuth **access tokens and stored API keys**, although real refresh tokens are redacted. The gateway is the boundary that lets less-trusted clients make model requests without receiving provider credentials.

### Required topology

```text
Host-owned canonical credential store
                │
       omp auth-broker serve
                │ broker URL + broker bearer; trusted host only
       omp auth-gateway serve
                │ restricted inference API + separate gateway bearer
Sandbox Bun/omp with transport: pi-native
                │
          GSD skill execution
```

- `omp auth-gateway serve` requires `OMP_AUTH_BROKER_URL`, or `auth.broker.url` in the host agent configuration. Broker token resolution is environment, then `auth.broker.token`, then the documented token-file location. Only the trusted gateway/broker-side process receives that configuration. Gateway `serve` fetches a **live** broker snapshot; it does not prove readiness by reading a cached client snapshot. [Selected documentation][omp-auth]; [selected CLI source][omp-gateway-cli].
- Sandbox model configuration uses the documented provider fields `transport: pi-native`, `baseUrl`, and `apiKey`. Do **not** invent an automatic `OMP_AUTH_GATEWAY_URL` switch or use broker environment variables as a substitute. [Selected model documentation][omp-models]; [published schema][omp-model-schema].
- For pi-native, `baseUrl` is the **gateway origin/base**, not an OpenAI SDK URL already ending in `/v1`: the client appends `/v1/pi/stream`. It forwards a qualified original `provider/model` and sends the gateway bearer in `Authorization`. [Selected transport source][omp-native-client].

Illustrative sandbox-owned `models.yml` shape—placeholders are not secret values or runtime proof:

```yaml
providers:
  openai-codex:
    transport: pi-native
    baseUrl: https://<approved-gateway-origin>
    apiKey: OMP_SANDBOX_GATEWAY_BEARER
  zai:
    transport: pi-native
    baseUrl: https://<approved-gateway-origin>
    apiKey: OMP_SANDBOX_GATEWAY_BEARER
```

The launcher must check that the referenced environment value is set and nonempty without printing it: omp otherwise treats an unresolved `apiKey` environment-variable name as the literal key. The sandbox bearer remains a secret even though it is **not a provider access or refresh token**. [Credential precedence and environment-name semantics][omp-models].

### Endpoint restrictions are part of the boundary

**Proposed project policy:** allow the sandbox only the gateway origin and the exact required routes: `POST /v1/pi/stream`, plus narrowly justified `GET /healthz` and `GET /v1/models` probes if used. Deny broker snapshot/credential routes, other hosts and private services, cloud metadata, and direct model-provider access. Keep package-download access separately scoped to preparation, rather than silently granting it to the running agent indefinitely. These are project restrictions, not claims that omp's gateway token already provides per-stage authorization.

The selected gateway serves many modalities and all credential-backed catalog models; a gateway bearer is not inherently restricted to Sol, Luna, and GLM. Its documented account pools are **routing, not authorization**. It has no raw provider passthrough and ignores host `models.yml` endpoint overrides when building its served catalog, but this does not replace sandbox egress or application model restrictions. [Gateway catalog and account-pool contracts][omp-auth].

OpenShell's selected schema provides L7 method/path rules, but inspected endpoints default to **audit**, not blocking; `enforcement: enforce` is required to reject disallowed requests. Ordinary outbound loopback/link-local/unspecified destinations are blocked. The same schema documents `host.openshell.internal` as a route to gateway-host services. The exact selected-driver mapping and listener reachability must be established; do not assume guest `127.0.0.1` is host loopback, and do not “fix” the boundary with an unauthenticated wildcard listener. [OpenShell 0.1.4 schema][os-policy]; [security guidance][os-security].

Default broker bind is `127.0.0.1:8765`; default gateway bind is `127.0.0.1:4000`. `/v1/healthz` and `/healthz` respectively are unauthenticated liveness routes. Gateway `status` checks configuration/broker access, **not** the running gateway listener; `check` also constructs its own broker-backed store rather than testing that listener. Thus neither a health response nor a status/check result proves the sandbox's authenticated inference path. `--no-auth` removes the bearer check and does not itself enforce a loopback bind. [Selected authentication documentation][omp-auth].

## Critical Pitfalls

### 1. Direct broker access masquerades as credential-safe gateway access

**What goes wrong:** sandbox processes obtain provider access tokens/API keys, can invoke credential-management APIs, or acquire copied refresh-token state that jeopardizes the host login. A broker snapshot being described as “redacted” is misread as safe for an untrusted sandbox.

**Evidence and detection:** [src/core/auth.ts](../../src/core/auth.ts), lines 18–37, starts only `auth-broker` and exposes `OMP_AUTH_BROKER_URL` and `OMP_AUTH_BROKER_TOKEN` as tier environment. The comment that the broker hands out short-lived tokens describes a materially weaker path than the required gateway. Direct snapshot exposure is an explicit [upstream contract][omp-auth], not a speculative vulnerability. No token command was executed in this research.

**Prevention:** replace the sandbox-facing direct-broker configuration with the documented pi-native gateway configuration above; give broker credentials only to trusted host-side helpers. Never read, copy, mount, package, migrate, or upload the host login database. Do not rotate tokens, import credentials, invoke login/logout, or create a second credential vault as a provisioning shortcut. Broker remains the refresh-token writer; do not treat refresh redaction as sandbox authorization. [Broker ownership and refresh coordination][omp-auth].

**Consumer-visible acceptance:** a real stage succeeds through the gateway while sandbox attempts to reach broker snapshot/credential routes are denied. Use synthetic canaries and redacted request metadata—not real token dumps—to establish that no broker/provider credentials enter sandbox environment, files, arguments, output, or receipts. Host logins remain owned and usable; any later host-availability check must use an approved nonsecret operational path, not database inspection or forced refresh experimentation.

**Owning phase:** Gateway authentication and preparation; security contract fixed in Contracts and compatibility.

### 2. A reachable proxy is mistaken for restricted authenticated access

**What goes wrong:** a health check passes against the wrong process; gateway traffic cannot reach host loopback; an arbitrary free port does not match policy; or an allowed host permits every method/path because L7 rules are absent or audit-only.

**Evidence and detection:** [sandbox/policy.yaml](../../sandbox/policy.yaml) allows direct `chatgpt.com`, `auth.openai.com`, and `api.z.ai` traffic and a broker alias on port `18990`. [auth.ts](../../src/core/auth.ts) chooses an unrelated free port and passes `127.0.0.1`. There is no gateway route/configuration cutover. [waitHttp](../../src/core/providers/util.ts) accepts any response below HTTP 500, including 401 and 404. OpenShell documents [host/port/binary and L7 enforcement distinctions][os-security].

**Prevention:** establish one explicit host↔sandbox route per tier, synchronize its address/port with reviewable policy, require the expected success status/body and authenticated operation, keep `--no-auth` disabled, and separate liveness from auth readiness. Use exact binary identities and `enforcement: enforce` for required method/path restrictions. Do not broadly allow provider hosts or the host LAN just to obtain a successful smoke result. Any non-loopback exposure requires an explicit protected transport decision. [Transport-security responsibility][omp-auth]; [OpenShell endpoint constraints][os-policy].

**Consumer-visible acceptance:** wrong bearer returns authorization failure; the expected gateway inference route works; a valid bearer cannot use the sandbox route to reach broker APIs, disallowed gateway methods/paths, direct provider endpoints, or an owned synthetic unrelated host service. A fake 404/401 health responder must not produce `ready`. OpenShell checks remain explicitly unexecuted on this host.

**Owning phase:** Gateway authentication and preparation, with Sandbox and owned lifecycle enforcing the network boundary.

### 3. A namespace probe, private HOME, or agentOS VM is sold as complete isolation

**What goes wrong:** host files, inherited secrets, sockets, or network services become available to model-generated code, while a status label implies OpenShell-equivalent or confidential protection.

**Evidence and detection:** [bwrap.ts](../../src/core/providers/bwrap.ts) correctly uses `--clearenv`, but mounts all `/etc`, the entire configured Bun installation, toolchain directories, and shares the host network. Its arguments differ from the successful minimal `--unshare-all` probe. [local.ts](../../src/core/providers/local.ts) builds an allowlisted environment but executes plain host processes. [openshell.ts](../../src/core/providers/openshell.ts) uses `{ ...process.env }` for CLI children. Upstream bubblewrap explicitly says its protection is determined by the caller's arguments and that it is **not a complete sandbox policy**. [bubblewrap 0.12.0][bwrap-readme].

**Prevention:** use an explicit environment allowlist at every process boundary; pass only the gateway bearer where necessary. Do not mount host homes, auth stores, SSH/cloud/D-Bus sockets, or broad user toolchain roots when only immutable runtime files are needed. Review mounted contents and symlink targets. Prove the selected application's filesystem/process/network restrictions, not just namespace creation. A shared-network bwrap tier cannot claim the OpenShell endpoint policy is enforced; either establish an authorized userspace/network boundary or leave that security gate visibly unmet. Never change privileges, groups, KVM configuration, or install container tooling silently.

**Consumer-visible acceptance:** synthetic parent secret variables are absent; an owned canary outside allowed mounts cannot be read or written; an external workspace write is blocked; unrelated local services are unreachable where network confinement is claimed; child processes are confined and terminate with the owned sandbox. No test needs to open a real credential file. Status always reports actual tier and limits; `agentos-local` says host execution/no isolation.

**Owning phase:** Sandbox and owned lifecycle; capability disclosure remains required in Operational evidence and receipts.

### 4. Workspace confinement checks strings, not filesystem objects

**What goes wrong:** upload, download, cwd, VM mount access, cleanup, or archive extraction escapes the owned workspace through `..`, absolute paths, prefix collisions, symlinks, or links introduced after an initial check.

**Evidence and detection:** the current [SandboxTier](../../src/core/providers/types.ts) and [OpenShell adapter](../../src/core/providers/openshell.ts) do not yet implement the complete constrained transfer surface required by the project. Arbitrary cwd/env objects are accepted by tier methods. This is an incomplete-contract risk, not proof of an exercised traversal exploit. OpenShell's own policy path rules reject relative/traversal paths, but those rules do not validate a custom provider's host-side transfer/cleanup paths. [Selected schema][os-policy].

**Prevention:** define host root and sandbox root separately; reject absolute/traversal/unsupported path forms at the provider boundary; verify containment by path components and resolved filesystem objects, not `startsWith`. For newly created destinations, validate the resolved existing parent and create without following attacker-controlled links; reject escaping symlinks during traversal/extraction. Apply the same invariant to delete and cleanup. Do not delete a caller-selected host root merely because it was stored in an instance field. If race-safe confinement cannot be guaranteed, fail closed rather than claim it.

**Consumer-visible acceptance:** transfers, cwd, VM filesystem access, and cleanup reject `../`, sibling-prefix paths, absolute escapes, nested symlinks to an owned outside-canary directory, dangling-link creation escapes, and symlink-swap attempts. Successful ordinary nested transfers still work. The outside canary remains unchanged, and pre-existing resources are untouched.

**Owning phase:** Sandbox and owned lifecycle; repeat through the Anda tool boundary when arguments become externally callable.

### 5. Prompts and answers cross into shell syntax or loosely typed control operations

**What goes wrong:** user/model text is interpolated into `sh -c`, a here-document, argv fragments, an environment file, or a command name; malformed operations select an unintended tier/model or overlapping stages mutate the same workspace.

**Evidence and detection:** current [omp-rpc.ts](../../src/core/omp-rpc.ts) uses `JSON.stringify` for prompt/answer frames, and OpenShell's `exec "$@"` wrapper preserves argv—positive patterns to retain. However [server.ts](../../src/core/server.ts) accepts `any` request arguments and runs async line handlers concurrently; [core.ts](../../src/core/core.ts) accepts arbitrary stage command/model data without a single-active-stage guard; [tiers.ts](../../src/core/providers/tiers.ts) turns every unrecognized tier name into `LocalTier`. These are source gaps, not a claim that current prompt text has been executed by a shell. omp RPC is explicitly a [custom JSONL data protocol][omp-rpc-doc].

**Prevention:** keep command launch fixed/argv-based and carry all prompt/answer content exclusively as JSON data on the established channel. Validate supported operations, IDs, finite/bounded values, answer shape, tier enum, stage→skill mapping, and model policy before side effects. Reject overlapping workspace stages and startup/shutdown races; cancellation/status/questions must remain serviceable while a stage runs. Do not let an invalid tier silently become host execution.

**Consumer-visible acceptance:** send synthetic strings containing quotes, backticks, `$()`, newlines, Unicode, and JSON-looking text. They round-trip unchanged as data, and no owned shell-canary command executes. Invalid stages/tiers/models/unsupported operations return correlated errors. Two simultaneous run/start/down requests do not create overlapping mutating owners or silently degrade.

**Owning phase:** Contracts and compatibility defines schemas; Interactive workflow runtime and Anda tool boundary and supervision enforce them.

### 6. Non-TTY CLI exec is assumed to be a lossless, unlimited interactive transport

**What goes wrong:** prompts wait for EOF before startup, stdout/stderr merge, banners corrupt JSON, a relay close is misreported as exit 0, or a long-lived session exceeds transport limits after partial work.

**Resolved external contract:** selected OpenShell 0.1.4 source explicitly waits only a **200 ms grace period** for piped stdin, then selects interactive streaming if it stays open. It separates stdout/stderr, requires an exit event, and drains the stream to detect final relay failure. Importantly, piped input is capped at **4 MiB cumulatively across prefix plus streamed remainder**, with an explicit warning that partial input may already have been processed. This is a source-grounded transport limit, not a local experiment. [OpenShell CLI `run.rs`, exec admission and limits][os-exec]; [same source, interactive helper around lines 2567–2840][os-exec].

**Evidence and detection:** [OpenShellTier.spawnStdio](../../src/core/providers/openshell.ts) has no live acceptance proof. [omp-rpc.ts](../../src/core/omp-rpc.ts) buffers a partial line without a size limit, ignores malformed lines as `noise`, does not negotiate protocol v2, and does not handle spawn/input errors comprehensively. [Rust child forwarding](../../crates/omp-sandbox-anda/src/core_child.rs) also has an unbounded channel. Selected omp v1 advertises a 1 MiB physical-frame limit; opt-in v2 permits validated chunk reassembly up to the advertised 64 MiB. Do not implement v2 halfway or assume a buffered shell exec is interactive RPC. [omp framing contract][omp-rpc-doc].

**Prevention:** use explicit no-TTY/no-login-shell argv, a long-lived pipe, separate stderr diagnostics, and byte-bounded strict frame decoding. Keep diagnostics off protocol stdout. Distinguish remote exit status, signal, timeout, transport error, and malformed frame. Respect the selected protocol and aggregate OpenShell input budgets; reject before exceeding known limits rather than silently truncate or reconnect/replay a stage. Verify child execution timeouts are compatible with human waiting. Do not import the Bun-only omp SDK into Node as a shortcut.

**Consumer-visible acceptance:** on authorized working compute, a disposable child emits a JSON line on stdout and a distinct stderr marker **before stdin closes**, accepts at least two spaced input frames, then returns a deliberate nonzero exit. Both byte streams and exit result survive Rust→Node→provider→child unchanged. Fragmented/malformed/oversized frames, broken pipes, missing exit status, partial output then failure, and aggregate-input exhaustion become explicit failures, never completion or replay. On this host, keep the OpenShell transport gate pending; use the chosen degraded tier for its own real-path test.

**Owning phase:** Sandbox and owned lifecycle establishes exec; Interactive workflow runtime establishes framing and bounded requests.

### 7. Failed startup and shutdown abandon resources or hang indefinitely

**What goes wrong:** broker survives failed VM/tool preparation; `set_ask_dialog` failure leaks omp; one disposal exception prevents all remaining cleanup; wrapper EOF waits forever; or only the direct child is killed while grandchildren continue.

**Evidence and detection:** [Core.up](../../src/core/core.ts) acquires broker, tier, VM, preparation, and probes without an encompassing rollback. `#drive` does not close its client in a `finally` covering all command failures. `down` can escape on tier disposal before broker shutdown, and catches some abort errors without preserving them. [AuthBroker.start](../../src/core/auth.ts) can time out after spawning without rollback. [OmpRpcClient.close](../../src/core/omp-rpc.ts) waits for exit after signals without a final bounded result. [Rust main](../../crates/omp-sandbox-anda/src/main.rs) requests shutdown on EOF but has no encompassing termination deadline. These are source-grounded risk paths, not reproduced leaks.

**Prevention:** assign each acquired resource one owner, record it immediately, unwind in reverse order on every failure, and continue cleanup even when individual disposals fail. Bound startup, requests, cancellation, graceful close, escalation, and final wait. Preserve unresolved resource identities rather than clearing ownership on an unverified stop. Distinguish expected subprocess exit from transport breakage. Dispose only owned children/sandboxes/helpers; a pre-existing gateway or broker remains external. omp's own EOF contract drains accepted work and disposes its session, but that does not implement wrapper/provider cleanup. [omp shutdown contract][omp-rpc-doc].

**Consumer-visible acceptance:** controlled failure after each acquisition step, missing child executable, child crash, pipe break, wrapper EOF, SIGTERM, and shutdown during a question all settle within documented bounds. Output names cleanup errors and remaining owned IDs/PIDs/paths. Unrelated synthetic pre-existing processes/resources survive. An actual final cleanup check is required; a `kill()` call or `proc = null` is not proof.

**Owning phase:** Sandbox and owned lifecycle; Anda tool boundary and supervision owns Rust→Node behavior; Operational evidence and receipts owns failure reporting.

### 8. An uncertain create is retried or forgotten while fallback starts another backend

**What goes wrong:** backend creation succeeds remotely but the CLI response is lost or times out. The caller marks creation unsuccessful, skips deletion, and starts another sandbox or a weaker tier. Ownership becomes ambiguous.

**Evidence and detection:** [OpenShellTier.start](../../src/core/providers/openshell.ts) marks `#created = true` only after `create` returns code 0; `dispose` only deletes when that flag is true. [startTier](../../src/core/providers/tiers.ts) suppresses disposal errors and proceeds to the next tier. A CLI timeout cannot prove the create had no side effect. This is an uncertainty risk, not evidence that an orphan currently exists.

**Prevention:** register the intended unique owned name/request before calling create. On timeout/disconnect, enter an explicit unknown-creation state, query the exact owned identity, and reconcile before any retry/fallback. If existence cannot be determined, report the intended resource as unresolved; do not claim cleanup succeeded or automatically replay create. Never use a broad `sandbox list` string substring as the sole identity/ownership check, and never delete arbitrary pre-existing sandboxes. OpenShell's documented runtime lifecycle is a backend capability to integrate, not proof the wrapper owns every resource. [Selected runtime architecture][os-architecture].

**Consumer-visible acceptance:** a controlled response-loss scenario creates one uniquely named owned resource but withholds success. Status reports uncertainty and that exact identity; no second create/fallback mutation occurs. Reconciliation either safely adopts/disposes the owned resource or reports the exact unresolved blocker. Cleanup-failure evidence persists when switching tiers is not safe.

**Owning phase:** Sandbox and owned lifecycle, before automatic tier selection is accepted.

### 9. Model fallback replays a partially executed GSD stage

**What goes wrong:** after a timeout, provider error, partial output, or lost connection, the system resends the same planning/execution prompt to another model and duplicates file writes, commands, commits, or external actions.

**Evidence and detection:** [Core.#drive](../../src/core/core.ts) uses `r.status !== "completed" && c.models.size === 0` as `failedEarly`, closes the client, then recursively drives the **same message** on GLM. The model set is populated by assistant-message events; its emptiness is not a side-effect-free proof. Selected omp already has provider-specific retry/replay safety boundaries and explicitly warns to stop retrying after visible effects. [Provider endpoint constraints][omp-endpoint-constraints].

**Prevention:** no automatic mutating stage replay after dispatch or uncertain admission. Select an authorized fallback before stage dispatch only when a non-mutating preflight establishes that the primary is unavailable; disclose the selection while retaining stage identity. Once work may have started, mark the outcome failed/aborted/uncertain, preserve enough nonsecret state for inspection, and require an explicit new user action to resume or start another attempt. Do not stack a wrapper retry policy over omp's exhausted retry result. [Prompt-result error semantics][omp-rpc-doc].

**Consumer-visible acceptance:** a disposable stage writes an owned marker, then encounters a controlled transport/provider failure. Exactly one mutation occurs; no second model session receives a replay. Status and mock receipt state the original stage ID, requested/effective model when known, failure/uncertainty, and any proposed next action. A pre-dispatch permitted fallback test retains plan/execute identity and visibly reports GLM.

**Owning phase:** Interactive workflow runtime; it is a release-blocking outcome-safety gate.

### 10. Requested model labels are mistaken for effective model routing

**What goes wrong:** planning runs on Luna, execution runs on Sol, a session resumes a saved different model, a generic gateway alias hides provider identity, or arbitrary caller input bypasses the decided routing/fallback policy.

**Evidence and detection:** [config.ts](../../src/core/config.ts) declares the required primary/fallback map, but [Core.runStage](../../src/core/core.ts) accepts `a.model` and the readiness probe tests only one planning session. Seeing a model in initial `get_state` does not prove every stage/subagent used it. The selected gateway client preserves original qualified provider/model identity; bare model IDs are ambiguous in the gateway catalog. [Pi-native transport][omp-native-client]; [gateway catalog][omp-auth].

**Prevention:** enforce plan→`openai-codex/gpt-6.1-sol`, execute→`openai-codex/gpt-6-luna`, and only disclosed `zai/glm-5.3` fallback. Verify effective model from the live stage session before dispatch and from actual assistant/provider events as available; treat unavailable or inconsistent routing as an explicit failure. Do not let hidden configuration reloads, saved session state, model roles, arbitrary overrides, or gateway defaults silently change stage identity. Inspect GSD's effective subagent routing separately if a skill launches workers; a root model flag alone is not evidence of worker routing. [omp runtime model and session behavior][omp-models]; [RPC session restoration contract][omp-rpc-doc].

**Consumer-visible acceptance:** real planning and autonomous execution each show the exact requested and effective provider/model. A controlled primary-unavailable preflight either fails honestly or selects/discloses only GLM before dispatch. Unsupported override inputs are rejected. A stale/saved different-model session cannot be accepted as the requested stage without a verified explicit correction.

**Owning phase:** Interactive workflow runtime; Operational evidence and receipts captures routing evidence without credentials.

### 11. Human waiting is confused with a hung transport, and timeout becomes implicit approval

**What goes wrong:** a real question never reaches the caller; a general request deadline kills an otherwise healthy human wait; stale answers cross sessions; empty input silently chooses “yes”; or an omp question times out and automatically chooses the recommended option while the wrapper still says `waiting`.

**Evidence and detection:** [omp-rpc.ts](../../src/core/omp-rpc.ts) preserves pending IDs and rejects stale replies—good patterns—but drops wire `timeout`, omits important question fields, defaults unanswered asks to recommendations and confirms to yes, and has an unlimited prompt-result wait. [core.ts](../../src/core/core.ts) does not project `question_cancelled` into its waiting state. The child also receives a fixed `--max-time 3600s`. Crucially, selected omp documents that an `ask` timeout—including a host reply with `cancelled:true,timedOut:true`—selects recommended/first answers; signal cancellation emits `cancel` with `targetId`. [Selected UI contract][omp-rpc-doc].

**Prevention:** retain session, stage, request and per-question identity; expose options, free text, multi-select and actual deadline; validate answer order/IDs/options; reject overlap, duplicate, stale and wrong-session answers. Keep control-plane timeout/liveness separate from human-wait policy. Never manufacture affirmative input from malformed/empty answers. Surface upstream cancellation/defaulting honestly; define a fail-closed policy for consent-sensitive questions before their defaulting deadline. If selected GSD/omp behavior cannot provide the promised human control, that contract remains unresolved rather than being represented as indefinite waiting.

**Consumer-visible acceptance:** a real GSD question is observed through the entire path, status becomes `waiting`, and a correctly correlated answer resumes the same session. Status/abort remain responsive during the wait. Duplicate, wrong-stage/session, expired and post-cancel answers are rejected. A short controlled timeout/cancellation records its actual upstream consequence and removes the pending dialog; it cannot be represented as a human approval.

**Owning phase:** Interactive workflow runtime; Anda tool boundary and supervision preserves identity across follow-up tool calls.

### 12. Command acceptance, agent yield, or a happy final sentence is treated as workflow completion

**What goes wrong:** the wrapper announces completion on `response.success`, tears down background work after a yielded turn, hangs waiting for a `prompt_result` that will never arrive for a local command, or calls a conversational “done” a completed GSD stage without the expected artifacts/outcomes.

**Resolved external contract:** selected omp distinguishes admission response, correlated `prompt_result`, and session quiescence. A synchronous local command may complete in `response.data.agentInvoked:false` with **no** later `prompt_result`. For agent work, `prompt_result.status:"completed"` reports yield; if `sessionSettled:false`, background work may still wake the agent, and `session_settled` follows when it drains. [Selected RPC completion contract][omp-rpc-doc].

**Evidence and detection:** [OmpRpcClient.prompt](../../src/core/omp-rpc.ts) always waits for a later `prompt_result`. [Core.#drive](../../src/core/core.ts) maps completed prompt status directly to completed stage and closes omp without checking `sessionSettled`. These are concrete contract mismatches, not reproduced failures.

**Prevention:** model admission, local completion, yielded turn, pending async work, waiting, workflow result, failure, and quiescence separately. Wait for `session_settled`/verified `get_state.isSettled` before recycling a session. Require the stage's real GSD outcome as well as transport success; a missing planning artifact, a human question, an unknown slash command treated as text, or a failure message must not pass. Do not turn expected asynchronous behavior into retry/replay.

**Consumer-visible acceptance:** prove successful local-command completion does not hang; a provider failure after `success:true` is failed; a yielded stage with pending background work stays nonterminal until settled; actual planning/execution artifacts or task effects are inspected in the disposable owned workspace. A reply that merely asks for input does not count as completion.

**Owning phase:** Interactive workflow runtime; Operational evidence and receipts owns the final workflow-evidence gate.

### 13. Pinned files and a decorative VM replace real VM/provider/GSD readiness

**What goes wrong:** tools exist on the host but not in the launched sandbox; the VM is booted but never mediates the required external-sandbox operations; cache markers claim readiness while skills are missing; or a bare slash command becomes plain text instead of the decided Claude skill invocation.

**Evidence and detection:** [vm.ts](../../src/core/vm.ts) uses the supported externally provisioned-client variant plus casts, not the project's required `AgentOs.create({sandbox:{provider}})` integration. Selected 0.4.3 types support **both** forms: the client form is not inherently invalid, but it does not satisfy this project decision. `vmRoundTrip` returns mismatch/nonzero data instead of making all required failures readiness failures. [Selected core interface][agentos-sandbox-contract]; [selected adapter interface][agentos-provider-adapter]. Preserve the existing user edit to this file.

[gsd.ts](../../src/core/gsd.ts) recognizes a cache by a legacy `.claude/commands/gsd-plan-phase.md` file. [Core.#probeOmp](../../src/core/core.ts) requires execute-phase rather than autonomous, and `runStage` constructs bare `/${gsdCommand}`. [bootstrap.sh](../../sandbox/bootstrap.sh) uses a marker and stages PATH/HOME in a profile, while normal OpenShell execution disables the login shell. Installed files and profile text are not proof of the launched child's effective environment. omp supports explicit `/skill:` dispatch via its documented [RPC skill pipeline][omp-skill-dispatch].

**Prevention:** create the real selected agentOS VM with the required provider adapter, prove `/mnt/sandbox` file access and documented host functions against that same backend, and make mismatch/nonzero outcomes fail readiness. Prepare exact Node 24.21, Bun, selected omp, and GSD 1.18.0 within the owned sandbox. Explicitly set launched PATH/HOME. Install using the decided `npx @opengsd/gsd-core@1.18.0 --claude --local` path; validate cache contents by version and required skills, not one historical filename. Require live `get_available_commands` to expose `gsd-plan-phase` and `gsd-autonomous`, then use `/skill:gsd-plan-phase` and `/skill:gsd-autonomous`.

**Consumer-visible acceptance:** a VM-originated file is observed in the external sandbox and a VM host-function command operates there, with matching marker and checked exit status. Sandbox process versions and live skill inventory are shown. Removing Bun, omp, a required skill, or the provider breaks readiness distinctly. Host-only GSD output, command counts, cache markers, or VM boot timings cannot satisfy this gate.

**Owning phase:** Contracts and compatibility; Sandbox and owned lifecycle; Gateway authentication and preparation.

### 14. An Anda-shaped forwarding CLI is mistaken for an embedded Anda Engine and authenticated server

**What goes wrong:** feature flags, a tool-name list, or successful JSON forwarding are presented as the required Anda integration; server authentication is equated with per-stage authorization; an HTTP 200 carrying an RPC error is reported as success; or unverified compiler assumptions drive a silent toolchain upgrade.

**Evidence and detection:** [Cargo.toml](../../crates/omp-sandbox-anda/Cargo.toml) has only `serde_json`; `anda` and `tee` are empty features. [main.rs](../../crates/omp-sandbox-anda/src/main.rs) forwards JSON, not registered Engine tool calls through `anda_engine_server`. The comment asserting a Rust version requirement is not a dependency-graph result.

Published `anda_engine` **0.17.4** identifies source commit `b42da9388048b89cb50e04a4c04ac2952cdada43`. Its [workspace manifest][anda-selected-cargo] uses edition 2024 and independently versioned members; edition support does not prove the full graph builds on this host. The inspected published server release is **0.17.0**, not 0.17.4; the server 0.17.4 docs URL returned 404. Do not assume all Anda crates share an engine version. [Engine provenance][anda-selected-vcs]; [server release/contract][anda-server].

Selected server documentation says anonymous requests are gated by engine visibility, malformed credentials fail rather than become anonymous, execution/parameter errors can be an RPC error envelope under **HTTP 200**, bearer CWT does not enforce audience/scope, and direct-key signatures do not inherently provide request freshness or engine binding. Those are limits to integrate, not protections to invent. [Server authentication contract][anda-server].

**Prevention:** embed the actual selected Engine, register and expose only the intended tools, enforce caller ownership of stage/session and engine identity, and use documented server authentication/authorization APIs. Anda performs **no separate inference**. Bound the Node child and propagate cancellation/failure. Resolve and build the real pinned graph during implementation on the authorized toolchain; if incompatible, report the exact dependency/compiler prerequisite and obtain approval instead of upgrading or replacing releases silently. Set `RequestMeta::engine` as documented and do not claim signatures prevent mutating-call replay by themselves.

**Consumer-visible acceptance:** discover/call `run_gsd_stage`, `answer_question`, and `sandbox_status` through the actual Anda server; malformed/unauthorized/wrong-owner calls cannot mutate or answer another session. Correctly distinguish HTTP transport success from RPC error. A real stage crosses Rust→Node→VM/provider→omp, while Anda emits no inference requests. Empty feature flags and a standalone CLI list do not count.

**Owning phase:** Contracts and compatibility gates dependencies; Anda tool boundary and supervision owns the integration and authorization.

### 15. Mock receipts or enclave vocabulary become false confidential-compute claims

**What goes wrong:** a local SHA-256, random nonce, feature flag, namespace, OpenShell VM, or successful workflow is advertised as hardware attestation; failure/degraded paths omit the mock label; or receipts leak prompts, answers, tokens, and provider response details.

**Evidence and detection:** [attest.ts](../../src/core/attest.ts) honestly sets `attestation:"mock"`, `kind:"mock"`, and `hardware:false`. Its measurement hashes tier/version/configuration text, not hardware-measured execution. [Rust attestation.rs](../../crates/omp-sandbox-anda/src/attestation.rs) remains mock even with `tee`. Preserve that honesty. However [core.ts](../../src/core/core.ts) writes question text, answers, assistant text excerpts and raw error strings into stage receipts, and creates startup evidence only after full success. No existing receipts were inspected; completeness is unknown.

**Prevention:** every locally emitted/persisted startup, success, failure, abort, degraded run, shutdown and cleanup-failure receipt must visibly say `attestation=mock`, actual tier, and hardware verification absent. Include requested/effective model, correlated outcome, skipped capabilities and unresolved owned resources, but minimize prompt/answer/response content. Redact errors before output; keep artifacts private and write bounded/atomic evidence without allowing a receipt-write error to conceal the primary failure or cleanup state. Never fabricate a successful runtime receipt from source inspection. A configuration hash may support reproducibility bookkeeping; it is not a quote, signature chain, or attested provenance.

**Consumer-visible acceptance:** success, preparation failure, unavailable backend, auth/model failure, malformed child output, abort, degraded execution, and incomplete cleanup all yield appropriately labeled mock evidence. Synthetic secret/prompt markers do not leak to stdout/stderr/receipts except explicitly permitted private task outputs. Requesting hardware proof locally reports unavailable, not a renamed mock. There is no SGX, GPU TEE, Nitro, confidential-VM, or production verification claim.

**Owning phase:** Operational evidence and receipts, with truthfulness enforced from the earliest startup/error paths.

## Confidential-Compute Target: Keep the Two Hosts and Versions Separate

This is a documentation/architecture boundary, **not local infrastructure delivery**.

1. **Selected Anda is an optional-capability runtime.** Both the [0.17.4 published-source architecture][anda-selected-architecture] and [current main architecture][anda-main-architecture] explicitly replace the older deployment-first story in which ICP/TEE/IC-TEE were mandatory runtime blocks. Engine registration, contexts, tools, and authorization are the runtime center; blockchain/TEE/store integrations are optional capabilities. Do not infer mandatory Nitro deployment—or achieved TEE protection—from the package description saying “powered by ICP and TEEs.”
2. **The IC-TEE/ICP/Nitro target is documented in the related integration, not established by merely embedding Anda.** The [IC-TEE project][ic-tee] names an enclave daemon, host daemon, on-chain identity canister, Nitro attestation library, and Nitro gateway. Its [Nitro gateway documentation][ic-tee-gateway] describes attestation-derived ICP identity, IC-COSE configuration/fixed identity/TLS, and a host daemon that relays requests/logs. These are **current-main ecosystem references**, not a selected, provisioned, or verified production dependency set for this sandbox. Storage/state integration must likewise be specified and verified separately; do not silently assume it exists.
3. **Nitro enclave ≠ OpenShell KVM/container host.** AWS documents a highly constrained Linux VM partitioned from a supported Nitro parent, with allocated CPU/memory, **no external network, no persistent storage, no SSH/interactive access**, and parent communication over **vsock**. The parent owns Nitro CLI lifecycle and relay infrastructure. The project's target therefore places the trusted IC-TEE application in its enclave and OpenShell on an appropriate **separate confidential-VM execution host**; it does not ask a Nitro enclave to act as a general nested-KVM/container host. [AWS overview][aws-nitro]; [AWS concepts][aws-nitro-concepts].
4. **OpenShell isolation does not attest its underlying host.** Selected OpenShell describes a trusted supervisor outside the workload boundary; its VM driver runs the supervisor on the host and communicates with the guest using authenticated vsock. A confidential host's measured boot, memory protection, verifier, workload policy, and credential-release decisions need their own evidence. No local bwrap, agentOS or OpenShell result supplies it. [OpenShell 0.1.4 architecture][os-architecture].
5. **A real quote needs verification and authorization policy.** AWS attestation is signed by the Nitro Hypervisor and carries PCR measurements; an external service must validate the intended measurements/policy. Debug/console enclaves have zero PCR measurements. A future design must specify trust roots, approved image/identity policy, freshness/session binding, transport, and secret release; a mock nonce/hash is not a substitute. [AWS attestation][aws-attestation]. No such infrastructure is provisioned or proved here.
6. **`dTEE` is Ove's architecture label.** Use it only as that project label, not as an upstream Anda API, standardized attestation type, or delivered hardware capability. This naming rule is from [PROJECT.md](../PROJECT.md). Do not claim that an enclave around the wrapper protects prompts later sent to a gateway, sandbox, or external model provider; each downstream trust boundary remains explicit.

## Technical Debt Patterns

| Shortcut | Immediate attraction | Long-term cost | When acceptable |
|---|---|---|---|
| Pass broker URL/token directly to sandbox | Fewest helpers | Exposes provider credentials and broad credential API authority | Never for this contract |
| Make every connection work with `--no-auth`, wildcard egress, or audit-only policy | Quick demo | Removes the boundary being delivered | Never as a passing acceptance configuration |
| Treat `any` casts as selected-package integration | Avoid type work | Hides lifecycle and method mismatches | Not for the provider acceptance gate |
| Cache by `.bootstrap-v1` or one legacy command filename | Fast reuse | Tool/skill drift becomes false readiness | Only as a hint followed by exact version/live discovery checks |
| Retry a prompt because no model event was seen | Masks intermittent errors | Duplicate mutations and changed model/data exposure | Never after uncertain stage admission |
| Read/mount a host home/toolchain tree for convenience | Reuses tools | Hidden credentials, sockets and mutable dependencies enter sandbox | Never for host homes/auth; narrow immutable runtime mounts only after review |
| Emit whole questions/answers/transcripts in receipts | Easy debugging | Privacy leaks and artifact retention burden | Only explicitly necessary, private/redacted evidence; default receipts must minimize content |
| Use Anda-shaped flags/tool names without the library | Wrapper can compile | Fails actual Engine/server requirement | Never as delivered Anda functionality |
| Label all backends “sandbox” with no capability difference | Simple UI | Host execution is mistaken for isolation | Never |

## Integration Gotchas

| Integration | Common mistake | Correct approach |
|---|---|---|
| omp gateway | Set broker env in sandbox, or append `/v1` to pi-native base | Host broker-backed gateway; sandbox provider transport `pi-native`; base appends `/v1/pi/stream`. [Contract][omp-models] |
| Gateway readiness | Trust unauthenticated health, `status`, or `check` | Test expected listener route/auth from the actual sandbox, followed by a real permitted inference. [Contract][omp-auth] |
| OpenShell policy | Allow a host but assume HTTP paths are restricted | Explicit inspected protocol/rules with `enforcement: enforce`; review special host route and SSRF restrictions. [Schema][os-policy] |
| OpenShell exec | Closed stdin smoke proves interactive RPC | Demonstrate output before EOF, later inputs, stderr separation, exit/failure, and aggregate input budget. [Source][os-exec] |
| omp RPC | `success:true` or one `agent_end` means finished | Correlated result plus local-completion special case and session quiescence. [RPC][omp-rpc-doc] |
| Ask dialogs | Host timeout means “no” | Selected ask timeout chooses recommendations; surface and control this consequence. [RPC][omp-rpc-doc] |
| agentOS 0.4.3 | Client variant is invalid, or equivalent to the decided provider path | Both APIs exist; this project deliberately requires the provider variant and real mounted/host-function behavior. [Types][agentos-sandbox-contract] |
| Anda crates | Engine 0.17.4 implies server 0.17.4 | Members are independently versioned; pin and resolve compatible published crates. [Workspace][anda-selected-cargo] |
| Anda server | HTTP 200 or valid CWT authorizes every tool/stage | Decode RPC result, apply engine/caller ownership and documented auth limits. [Server][anda-server] |
| IC-TEE docs | Copy infrastructure setup examples onto this host | Architecture reference only; no privileged networking/toolchain/container/cloud changes are authorized. [Target docs][ic-tee-gateway] |

## Performance and Boundedness Traps

| Trap | First meaningful failure boundary | Prevention |
|---|---|---|
| Unlimited partial JSONL buffer/unbounded forwarding channel | First child that never sends newline or outruns the reader | Byte limits, bounded queues/backpressure, explicit protocol failure—not silent loss |
| OpenShell long-lived piped stdin | Selected 0.1.4 source caps cumulative input at 4 MiB | Account for the aggregate session budget; reject before overflow; no reconnect/replay. [Source][os-exec] |
| Ignoring omp protocol limits | v1 physical frame above advertised 1 MiB; v2 chunks without a complete decoder | Respect negotiated framing and maximum reassembly; test boundaries. [RPC][omp-rpc-doc] |
| One generic deadline for everything | First slow startup, long reasoning call, or human answer wait | Separate bounded startup/control/transport/shutdown policies and explicit human-wait handling |
| Capturing all bootstrap output for transport | First interactive omp session | Use streaming stdio; buffered `execCapture` is for finite commands, not a replacement for RPC |
| Serial main-loop waits or unbounded output draining | Continuous child events or blocked cleanup | Keep status/answer/abort responsive; bound shutdown and use ownership-aware child supervision |

## Security Mistakes

| Mistake | Risk | Required prevention |
|---|---|---|
| Copy a login database or refresh-token snapshot | Host refresh-token lineage can be invalidated; credentials escape ownership | Broker/gateway only; never inspect/copy/mount the host store |
| Put gateway/broker bearer in argv, URLs, errors, env dumps or staged artifacts | Secrets reach process listings/diagnostics/model tools | Trusted handoff, narrow environment, no dumps, redaction; keep broker bearer outside sandbox |
| Equate account pool/provider settings with server authorization | A less-trusted client can access broader endpoints/models | Enforce endpoint/model/caller boundaries; account pools are routing only. [Docs][omp-auth] |
| Prefix-only workspace checks | Traversal, symlink and cleanup escape | Filesystem-object containment and safe creation/extraction at the provider boundary |
| Treat sandbox output as trusted protocol or instructions | Corrupted frames, forged success, leaked diagnostics | Schema/size/correlation validation; separate stderr; terminal-state invariants |
| Claim a confidential host hides external inference data | User misunderstands gateway/provider exposure | Explicit data-flow/trust-boundary disclosure; no direct Anda inference or false end-to-end confidentiality claim |

## UX Pitfalls

| Pitfall | User impact | Better behavior |
|---|---|---|
| Emit `ready` at server process startup | User submits work before auth/VM/skills are usable | Separate protocol-listening from prerequisite/application readiness |
| Silent fallback to host execution or GLM | Changed security/model without informed visibility | Explicit selected tier/model and skipped reasons before work |
| Return only a stage ID with no live state | User cannot tell question wait from a hang | Correlated events, status, pending questions, real terminal outcome |
| Leave cancelled question marked waiting | User answers a dead dialog | Cancellation event/state update; reject stale replies |
| Say “aborted” before children/resource state settles | User believes mutation stopped when it may continue | Distinguish abort requested, bounded termination, uncertainty and cleanup failure |
| Say “cleanup successful” while ownership was cleared optimistically | User cannot recover leftovers safely | Show remaining exact owned identities and failures |
| Present a mock receipt as security certification | User overestimates protection | Prominent `attestation=mock`, `hardware=false`, actual tier and limitations |

## “Looks Done But Isn't” Evidence Checklist

All items remain acceptance work; no checkmark below claims a result from this research.

- [ ] **Provider integration:** actual selected agentOS VM created through `sandbox.provider`; required mount and documented host-function round trips succeed against the same external sandbox.
- [ ] **Owned backend:** create/exec/upload/download/delete and failure reconciliation work; path escapes and pre-existing-resource deletion are rejected.
- [ ] **OpenShell transport:** interactive output-before-EOF, separated stderr, remote nonzero exit and relay-failure behavior are demonstrated on authorized working compute. The recorded KVM-fault host is not retried.
- [ ] **Degraded tier:** actual backend restrictions are verified with synthetic canaries, and unmet capabilities are disclosed. Minimal namespace creation is not this test.
- [ ] **Authentication:** sandbox uses pi-native gateway, not broker env/direct provider credentials; host canonical store stays untouched; exact endpoint restrictions and reachable protected route are established.
- [ ] **Host login safety:** no database/token copying, forced refresh experimentation, login/logout/migration, token logging or unauthorized rotation; credentials remain under host ownership.
- [ ] **Tools:** effective sandbox Node/Bun/omp/GSD versions match the selected versions, not merely manifest text or host installations.
- [ ] **Skills:** live inventory contains both required Claude skills; explicit `/skill:` invocation starts the intended real workflow.
- [ ] **Routing:** planning Sol, execution Luna, and any permitted GLM selection are observed/disclosed in their live sessions while retaining stage identity.
- [ ] **Questions:** a genuine workflow question traverses Rust→Node→sandbox→omp and back; correct answer resumes; duplicate/wrong-session/stale replies fail; timeout behavior is honest.
- [ ] **Completion:** command acknowledgment, local-only completion, yield, background quiescence and actual GSD outcomes are distinguished.
- [ ] **No replay:** partial mutation followed by failure is not automatically resubmitted to another model or session.
- [ ] **Anda:** actual engine tools and server auth/authorization are used; no direct inference is introduced; the real dependency graph builds on an authorized toolchain.
- [ ] **Lifecycle:** missing prerequisites, startup failure, child crash, EOF, signal, abort, shutdown and cleanup failure settle with exact ownership results.
- [ ] **Receipts:** every local outcome is visibly mock, actual tier/model/outcome are recorded, synthetic secrets/private prompt markers are absent, and unresolved resources remain visible.
- [ ] **End to end:** one disposable owned planning task and one real autonomous execution task traverse the entire required path, with live follow-up control and inspected actual outcomes—not host omp runs, mocks, compilation, or a fabricated receipt.

## Recovery Strategies

| Failure | Recovery cost | Safe recovery |
|---|---|---|
| Credential material reaches sandbox/artifact | HIGH | Stop owned work, contain further exposure, report exactly what boundary failed without reproducing secrets. Credential/token remediation requires explicit operator authorization; do not rotate/copy/migrate blindly. |
| OpenShell create result unknown | MEDIUM/HIGH | Reconcile the exact intended owned name/ID; no second create or fallback until ownership is known or explicitly reported unresolved. |
| Stage transport fails after possible mutation | HIGH | Stop/inspect owned session and workspace, preserve minimal nonsecret uncertainty evidence, never replay automatically. Resume/retry only as a new explicit action. |
| Startup fails after partial acquisition | LOW/MEDIUM | Reverse-order bounded cleanup, continuing past individual errors; return remaining owned resources. |
| Human question expires/cancels | LOW/MEDIUM | Remove pending request, report actual upstream outcome/defaulting, reject late answer; do not represent timeout as a user answer. |
| Cleanup cannot complete | MEDIUM | Keep exact ownership ledger and `cleanup_failure`; retry only targeted owned cleanup with authorization, never broad host teardown. |
| Recorded OpenShell KVM kernel fault | HIGH | Keep this local compute path disabled; use an authorized disclosed degraded tier. Only an independently authorized appropriate host can establish OpenShell runtime evidence. |
| Rust dependency graph incompatible | MEDIUM | Report exact crate/compiler prerequisite; obtain consent for any toolchain/version change. Do not downgrade integration to empty flags. |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention owner | Evidence gate |
|---|---|---|
| 1. Broker credential exposure | Gateway authentication and preparation | Real gateway-only inference; broker/provider credentials absent from sandbox |
| 2. Reachability versus policy | Gateway authentication and preparation + Sandbox and owned lifecycle | Authenticated correct listener, denied methods/paths/hosts, no fake-ready responder |
| 3. Inflated isolation | Sandbox and owned lifecycle | Application canary tests and honest capability/tier labels |
| 4. Traversal/symlinks | Sandbox and owned lifecycle | Transfer/cwd/mount/delete escape tests with outside canaries |
| 5. Data/control confusion | Contracts and compatibility + Interactive workflow runtime | Exact schema, literal-data round trip, unsupported/overlap rejection |
| 6. Interactive framing/limits | Sandbox and owned lifecycle + Interactive workflow runtime | Output before EOF, separated streams, remote exit/relay failure, bounded protocol/input |
| 7. Lifecycle leaks/hangs | Sandbox and owned lifecycle + Anda tool boundary and supervision | Failure-at-acquisition, EOF/signal/crash, bounded cleanup and remaining identities |
| 8. Uncertain create | Sandbox and owned lifecycle | Lost-response reconciliation without duplicate create or unsafe fallback |
| 9. Mutating replay | Interactive workflow runtime | Partial-write/failure does not trigger a second stage/model attempt |
| 10. Effective model drift | Interactive workflow runtime | Live session/provider evidence for Sol/Luna/disclosed GLM |
| 11. Question/defaulting errors | Interactive workflow runtime + Anda tool boundary and supervision | Real live answer, stale rejection, responsive control, actual timeout consequence |
| 12. False completion | Interactive workflow runtime | Local-only, provider error, unsettled background and real workflow outcome tests |
| 13. Decorative VM/preparation | Contracts and compatibility + Gateway authentication and preparation | Required provider/mount/host functions, actual versions and skill discovery |
| 14. Faux Anda/auth | Anda tool boundary and supervision | Actual Engine/server calls and ownership authorization on a resolved graph |
| 15. Mock/privacy confusion | Operational evidence and receipts | All outcome classes mock-labeled, redacted, ownership-aware evidence |

## Blockers and Confidence

| Unresolved contract / blocker | Confidence in identifying it | Gate to close it |
|---|---|---|
| Full Rust/Anda dependency compatibility under rustc/cargo 1.85.1 | HIGH that it is unproved; no build claim | Resolve/build the exact real graph during implementation. Do not infer incompatibility solely from edition 2024 or the repository's comment. |
| Sandbox→gateway reachable protected route for each tier | HIGH source gap | Actual selected-tier authenticated inference and endpoint-denial evidence; no token/store inspection |
| OpenShell compute on this host | HIGH recorded stop condition | Do not retry KVM fault. Record unavailable compute and unexecuted OpenShell lifecycle/policy/transport tests; validate only on separately authorized appropriate compute. |
| bwrap application-level network/process/filesystem policy | HIGH that the minimal probe is insufficient | Synthetic canary and real-application tests using the final arguments; shared-network source path is not an endpoint fence |
| OpenShell 4 MiB aggregate piped-input lifetime | HIGH selected-source contract | Bounded input accounting and controlled boundary/failure behavior; no silent replay/restart |
| GSD skill discovery, actual questions and autonomous outcomes | HIGH source mismatches; runtime UNVERIFIED | Live sandbox discovery and real disposable GSD stage results |
| Session yield versus settled behavior | HIGH documented mismatch | Correct protocol state machine and background/local-only acceptance tests |
| All failure/degraded receipt paths and cleanup | HIGH source gaps; existing receipt contents uninspected | Exercise all named outcome classes and inspect only implementation-authorized new evidence |
| Production IC-TEE/Nitro/confidential-host security | HIGH architecture boundary; deployment UNVERIFIED/out of scope | Separate future deployment design, hardware evidence, verifier/identity/state/secret-release policy and authorization |

## Sources

### Selected omp 18.8.4 documentation and distribution source

- [Auth broker and gateway][omp-auth] — credential exposure boundary, host configuration, endpoint lists, cache/account pools, status/check limits, transport security.
- [Models][omp-models] — pi-native transport, provider fields, key precedence and missing-environment literal fallback.
- [Provider schema][omp-model-schema] — selected implementation of `transport: pi-native`.
- [Gateway CLI][omp-gateway-cli] — live broker-backed gateway construction.
- [Pi-native client][omp-native-client] — endpoint construction, bearer handling, qualified model identity.
- [RPC protocol][omp-rpc-doc] — framing, admission/local completion, yield/settled, dialog timeout/cancel semantics and EOF behavior.
- [Skill dispatch implementation][omp-skill-dispatch] — explicit skill lookup/admission rather than an assumed bare slash path.
- [Provider endpoint constraints][omp-endpoint-constraints] — protocol families and replay/side-effect safety.

Installed public-source counterparts were read under `/home/box/.bun/install/global/node_modules/@oh-my-pi/`; no auth stores were sources.

### Selected sandbox packages

- [agentOS core 0.4.3 sandbox declarations][agentos-sandbox-contract].
- [agentOS sandbox 0.4.3 provider adapter declarations][agentos-provider-adapter].
- Installed package identities and corresponding declarations were inspected locally under this project's `node_modules`, not in protected sibling workspaces.

### OpenShell and bubblewrap

- [OpenShell stable 0.1.4 release][os-release].
- [OpenShell 0.1.4 architecture][os-architecture].
- [OpenShell 0.1.4 runtime architecture][os-runtimes].
- [OpenShell 0.1.4 policy schema][os-policy].
- [OpenShell 0.1.4 security guidance][os-security]. The selected documentation has differing runtime-focused explanations in places; use the specific driver/source and real authorized tests rather than claiming all prose descriptions are independently validated.
- [OpenShell 0.1.4 CLI exec source][os-exec] — see constants around 1852–1863; `sandbox_exec_grpc` around 1974; streaming helper around 2567–2840.
- [bubblewrap 0.12.0 README][bwrap-readme] — caller-defined policy and mount/network/terminal limitations.

### Anda / IC-TEE / AWS

- [Anda engine 0.17.4 published VCS metadata][anda-selected-vcs] — exact source commit, used instead of a guessed same-version tag.
- [Anda selected-commit architecture][anda-selected-architecture] and [workspace manifest][anda-selected-cargo].
- [Anda current-main architecture][anda-main-architecture] — comparative context, not a substitute for the selected release.
- [anda_engine_server 0.17.0 published README/auth contract][anda-server].
- [IC-TEE current-main overview][ic-tee] and [Nitro gateway target][ic-tee-gateway] — ecosystem target documentation only.
- [AWS Nitro Enclaves overview/requirements][aws-nitro], [concepts][aws-nitro-concepts], and [cryptographic attestation][aws-attestation] — authoritative environmental and attestation limits.

[omp-auth]: https://raw.githubusercontent.com/can1357/oh-my-pi/v18.8.4/docs/auth-broker-gateway.md
[omp-models]: https://raw.githubusercontent.com/can1357/oh-my-pi/v18.8.4/docs/models.md
[omp-model-schema]: https://github.com/can1357/oh-my-pi/blob/v18.8.4/packages/coding-agent/src/config/models-config-schema-bundle.ts
[omp-gateway-cli]: https://github.com/can1357/oh-my-pi/blob/v18.8.4/packages/coding-agent/src/cli/auth-gateway-cli.ts
[omp-native-client]: https://github.com/can1357/oh-my-pi/blob/v18.8.4/packages/ai/src/providers/pi-native-client.ts
[omp-rpc-doc]: https://raw.githubusercontent.com/can1357/oh-my-pi/v18.8.4/docs/rpc.md
[omp-skill-dispatch]: https://github.com/can1357/oh-my-pi/blob/v18.8.4/packages/coding-agent/src/modes/rpc/rpc-mode.ts
[omp-endpoint-constraints]: https://raw.githubusercontent.com/can1357/oh-my-pi/v18.8.4/docs/provider-endpoint-constraints.md
[agentos-sandbox-contract]: https://unpkg.com/@rivet-dev/agentos-core@0.4.3/dist/sandbox.d.ts
[agentos-provider-adapter]: https://unpkg.com/@rivet-dev/agentos-sandbox@0.4.3/dist/provider.d.ts
[os-release]: https://github.com/NVIDIA/OpenShell/releases/tag/v0.1.4
[os-architecture]: https://raw.githubusercontent.com/NVIDIA/OpenShell/v0.1.4/docs/about/architecture.mdx
[os-runtimes]: https://raw.githubusercontent.com/NVIDIA/OpenShell/v0.1.4/docs/how-it-works/sandboxes/runtimes.mdx
[os-policy]: https://raw.githubusercontent.com/NVIDIA/OpenShell/v0.1.4/docs/how-it-works/policies/schema.mdx
[os-security]: https://raw.githubusercontent.com/NVIDIA/OpenShell/v0.1.4/docs/security/best-practices.mdx
[os-exec]: https://github.com/NVIDIA/OpenShell/blob/v0.1.4/crates/openshell-cli/src/run.rs
[bwrap-readme]: https://github.com/containers/bubblewrap/blob/v0.12.0/README.md
[anda-selected-vcs]: https://docs.rs/crate/anda_engine/0.17.4/source/.cargo_vcs_info.json
[anda-selected-architecture]: https://raw.githubusercontent.com/ldclabs/anda/b42da9388048b89cb50e04a4c04ac2952cdada43/docs/architecture.md
[anda-selected-cargo]: https://raw.githubusercontent.com/ldclabs/anda/b42da9388048b89cb50e04a4c04ac2952cdada43/Cargo.toml
[anda-main-architecture]: https://raw.githubusercontent.com/ldclabs/anda/main/docs/architecture.md
[anda-server]: https://docs.rs/crate/anda_engine_server/0.17.0/source/README.md
[ic-tee]: https://github.com/ldclabs/ic-tee
[ic-tee-gateway]: https://github.com/ldclabs/ic-tee/tree/main/src/ic_tee_nitro_gateway
[aws-nitro]: https://docs.aws.amazon.com/enclaves/latest/user/nitro-enclave.html
[aws-nitro-concepts]: https://docs.aws.amazon.com/enclaves/latest/user/nitro-enclave-concepts.html
[aws-attestation]: https://docs.aws.amazon.com/enclaves/latest/user/set-up-attestation.html

---
*Research-only deliverable. Source review establishes risks/contracts, not application acceptance or hardware trust.*
