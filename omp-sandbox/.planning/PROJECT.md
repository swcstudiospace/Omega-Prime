# Ove's Local omp Sandbox

## What This Is

A local, owned execution environment for Ove's uplifted prompts and GSD-Core workflows. A Rust wrapper embeds Anda and supervises a Node 24 TypeScript core; the core boots an embedded agentOS VM and connects it to an external full-Linux sandbox, where Bun runs omp in RPC mode. OpenShell is the intended execution backend; an explicitly degraded bwrap or agentOS-local tier must remain usable when authorized OpenShell compute is unavailable.

This project initialization captures the delivery contract and roadmap. Existing TypeScript code is an unvalidated implementation starting point, not proof of working workflows. Production confidential computing is an architectural target only; every local attestation and receipt must say `attestation=mock`.

## Core Value

Ove can run a real GSD planning or execution stage through the Rust-to-TypeScript-to-sandbox-to-omp path, answer its live questions, inspect its actual model/backend/state, and cancel or shut it down without exposing provider credentials or losing resource ownership.

## Requirements

### Validated

None of the application requirements is validated yet. Installed tools, source files, a successful namespace probe, and GSD initialization queries do not establish application acceptance.

### Active

- [ ] Preserve the existing repository and user changes; disclose exact files before implementation, keep all owned artifacts local, and configure no remote or publication without Ove's explicit consent.
- [ ] Use Node 24.21, `@rivet-dev/agentos-core` 0.4.3, and `@rivet-dev/agentos-sandbox` 0.4.3; attach the provider with `AgentOs.create({ sandbox: { provider } })` against the selected package's actual contracts.
- [ ] Boot an actual agentOS VM and demonstrate a workspace file/command round trip through its external-sandbox integration, including `/mnt/sandbox` and the documented sandbox host functions.
- [ ] Implement a complete custom OpenShell 0.1.4 CLI provider with real create/exec/upload/download/delete behavior, owned-resource tracking, constrained paths/policy, and separately framed stdout/stderr/exit status.
- [ ] Prove interactive newline-JSON exec before calling OpenShell an omp transport; report unavailable compute and unexecuted OpenShell policy/lifecycle checks honestly.
- [ ] Select and disclose a working degraded tier without changing system privileges. Prove bwrap restrictions if selected; agentOS-local is host execution, not OpenShell-equivalent isolation.
- [ ] Prepare Node 24, Bun, omp RPC, and exactly GSD-Core 1.18.0 within the owned sandbox workspace; use the pinned `npx @opengsd/gsd-core@1.18.0 --claude --local` installation path.
- [ ] Verify project-local Claude skill discovery and `get_available_commands`, requiring `gsd-plan-phase` and `gsd-autonomous` before readiness or dispatch.
- [ ] Use existing omp provider logins only through the documented auth-gateway backed by auth-broker, with endpoint restrictions and no access-token/refresh-token material in artifacts or diagnostics.
- [ ] Route `/skill:gsd-plan-phase` to `openai-codex/gpt-6.1-sol` and `/skill:gsd-autonomous` to `openai-codex/gpt-6-luna`; verify effective routing from the live session. Disclose any `zai/glm-5.3` fallback while retaining stage identity.
- [ ] Carry prompts and answers as data through correlated newline JSON; support command discovery, ask-dialog, state, abort, asynchronous events, and real workflow outcomes without interpreting any RPC response as successful completion.
- [ ] Expose live questions and accept answers only for the corresponding pending question/session; reject overlap, stale answers, malformed input, path traversal, and unsupported operations.
- [ ] Embed `anda_engine` 0.17.4 and expose `run_gsd_stage`, `answer_question`, and `sandbox_status` through `anda_engine_server`, using its documented registration and authentication/authorization APIs. Anda performs no separate model inference.
- [ ] Bound startup, subprocess/transport requests, cancellation, child failure, and shutdown while distinguishing human-answer waiting from a broken transport; never silently replay a partially executed stage.
- [ ] Report all startup, execution, waiting, terminal, shutdown, and cleanup-failure states, including remaining owned resources and visibly mock receipts for failures and degraded runs.
- [ ] Provide operational commands for wrapper/core startup, tier selection/status, planning/execution, follow-up answers, abort, and shutdown, with real end-to-end verification and explicit limits.
- [ ] Document the requested dTEE label, IC-TEE/AWS Nitro/ICP target, confidential OpenShell host placement, and the absence of local hardware, SGX, or GPU TEE verification.

### Out of Scope

- Modifying `/home/box/agentos` or `/workspace/omes-bot`, integrating an omes-bot adapter, or migrating desk-agentos — separate workspaces and products are protected.
- Updating host omp/GSD/configuration/authentication or creating provider accounts — existing provider logins must remain owned and usable on the host.
- Reading, mounting, copying, uploading, or packaging `~/.omp/agent/agent.db` — copied single-use refresh tokens can invalidate the host login; use the broker/gateway boundary.
- Hosted `@rivet-dev/agentos`, importing the Bun-only omp SDK into Node, the pi GSD installer, bare `/gsd-*` dispatch, and `omp -p` — they do not implement the decided library/RPC/Claude-skill boundary.
- Docker/Podman/global installation, KVM/group/permission changes, or silent Rust upgrades — require separate explicit consent. Available sudo is not permission.
- OpenShell source builds with rustc 1.85.1 or the GitHub-Packages-only TypeScript SDK — the stable release binary and CLI avoid unsupported compiler/token requirements.
- Real local hardware attestation or production IC-TEE/confidential infrastructure provisioning — hardware and infrastructure are absent.
- Direct Anda inference, unrelated credentials/models, automatic stage replay after uncertain failure, telemetry/retry frameworks, compatibility shims, or substitute workflow skills — outside the requested integration.
- Remote creation, pushes, merges, PRs, or deployments without Ove's explicit authorization — local commits do not authorize publication.

## Context

### Inspected repository baseline

The target already has a git repository, package/lock manifests, TypeScript sources under `src/core/`, provider adapters, sandbox bootstrap/policy files, and project-local GSD files. No `.planning/` artifacts existed at initialization. `git status --short` reported an existing modification to `src/core/vm.ts`; preserve it and exclude it from initialization commits.

The manifests pin both Rivet packages to 0.4.3. Source inspection found that the current VM code uses a sandbox client and `any` casts instead of establishing the requested custom-provider integration; the current core constructs bare slash commands, checks the execute-phase skill rather than requiring autonomous, and has a fallback/replay path that needs outcome-safety review. These are source observations, not reproduced runtime failures. Do not overwrite the user's VM edit to resolve them without a fresh implementation preflight and references review.

`PROGRESS.md` records earlier host and OpenShell actions. In particular, it records an OpenShell VM launch ending in signal 11 with a KVM kernel fault, followed by cleanup. Treat that recorded fault as a stop condition; do not rerun it to confirm and do not infer authorization from previous privileged actions. The log also describes host omp planning attempts; those are not evidence of the required Rust/sandbox path.

### Current safe observations

- Node at `/home/box/tools/node24/bin/node` reports `v24.21.0`; it is also the current `node` resolved on PATH, unlike the older research snapshot.
- Bun reports `1.4.2`; cargo and rustc report `1.85.1`. There is no assumption that Rust edition support proves Anda's entire dependency graph compatible.
- `omp` resolves to `/home/box/.local/bin/omp`. Its inference/auth behavior has not been exercised by this initialization.
- OpenShell, Docker, Podman, and kubectl were not on the inspected PATH. The progress log records a user-local OpenShell release installation outside PATH; absence from PATH alone does not prove the binary absent.
- bwrap reports `0.12.0`. A minimal `--unshare-all` launch with only runtime directories mounted completed with exit code 0, without mounting a host home or credential store. This proves namespace creation for that probe, not application policy enforcement or inference.
- The project-local GSD tool reports `{"packageName":"@opengsd/gsd-core","version":"1.18.0"}`. Initialization metadata reports a brownfield code starting point, no planning artifacts, no nested worktree or subrepositories, and GSD persona files installed under `.claude/agents`.

### Source-verified execution gates

Research is recorded in `.planning/research/`; these corrections supersede incompatible assumptions in the supplied brief without changing its required behavior:

1. **Rust prerequisite:** mandatory `anda_db_schema` 0.14.0 and `anda_db` 0.14.2 declare Rust 1.95. rustc 1.85.1 cannot support the unmodified selected graph. The matching published server/core releases are 0.17.0/0.17.1, not invented 0.17.4 counterparts. A newer compiler is necessary, not proof of a successful application build; no upgrade is authorized. [Published manifest](https://docs.rs/crate/anda_db_schema/0.14.0/source/Cargo.toml)
2. **GSD layout conflict:** GSD 1.18.0 `--claude --local` writes `.claude/commands/gsd-*.md`, whereas the required `/skill:gsd-*` surface needs actual skills. Do not call file commands equivalent or silently generate substitute skills, install globally, change versions, or use the pi path. The requirement remains blocked until an authorized compatible installation/layout is established. [Selected installer](https://github.com/open-gsd/gsd-core/blob/v1.18.0/bin/install.js)
3. **Interactive launch conflict:** omp 18.8.4 enables the built-in tool UI for `rpc-ui`, not plain `rpc`; `set_ask_dialog` changes payload shape, not tool availability. Preserve the requested question behavior and record launch-mode resolution as a gate, rather than silently changing the required invocation. [Launch source](https://github.com/can1357/oh-my-pi/blob/v18.8.4/packages/coding-agent/src/main.ts)
4. **GSD agent compatibility:** omp intentionally excludes direct `.claude/agents` definitions. A real native/plugin role-discovery and workflow-tool compatibility path must be established; skill/menu visibility and inline impersonation of required agents are not acceptance. [Discovery source](https://github.com/can1357/oh-my-pi/blob/v18.8.4/packages/coding-agent/src/task/discovery.ts)
5. **Provider hazards:** the selected agentOS package resolves host functions before sandbox expansion, a source-ordering hazard requiring an actual VM-mediated check. OpenShell 0.1.4 limits cumulative piped stdin to 4 MiB, so a long-lived RPC transport is not assumed unlimited. Neither finding has been reproduced as a live application failure in this initialization. See the exact contracts and source links in `research/ARCHITECTURE.md`.
6. **Authentication topology:** only the trusted gateway receives the broker capability. Sandboxed omp uses the documented `pi-native` transport with a gateway base URL and gateway-only bearer; account pools are routing, not authorization. Current direct-broker source does not establish the required credential boundary. Reachability, endpoint enforcement, and effective provider/model identity remain runtime gates. [Selected documentation](https://github.com/can1357/oh-my-pi/blob/v18.8.4/docs/auth-broker-gateway.md)

During research, scouts also observed newly present `Cargo.toml`, `Cargo.lock`, and `crates/omp-sandbox-anda/` files. Their wrapper declares no actual Anda engine/server dependencies; an empty feature or metadata tool list is not the requested integration. These intervening repository changes are not this initialization's edits and must be preserved.

### Product boundaries

The agentOS VM is an embedded library runtime and does not provide the Linux/Bun runtime omp requires. omp must execute outside the VM in the selected sandbox. The required VM integration remains an observable part of acceptance, not a decorative extra process.

`dTEE` is Ove's architecture label, not a term asserted to be established by the Anda repository. The production target separates an IC-TEE enclave on AWS Nitro, with ICP identity/configuration/state integration, from OpenShell on an appropriate confidential VM host. A Nitro enclave is not a KVM/container host. Neither architecture documentation nor local mock receipts establishes production protection.

## Constraints

- **Execution ownership:** Rust owns the TypeScript child; TypeScript owns its VM, sandbox, omp session, and explicitly acquired broker/gateway processes. Do not delete or stop external/pre-existing resources.
- **Security:** Never inspect the host login database or emit credentials. Prompts, answers, subprocess output, paths, and protocol frames are untrusted; keep private prompt data out of diagnostics/receipts unless necessary and redacted.
- **Model routing:** Planning is Sol, execution is Luna, permitted fallback is GLM-5.3 only. Verify the effective provider/model in the sandbox instead of inheriting assumed settings.
- **Compatibility:** Pin intended versions. Validate the full Rust dependency graph locally during implementation; do not silently upgrade tools or substitute releases when compatibility fails.
- **Policy:** Restrict filesystem/network/process access and transfers to the workspace and approved auth endpoints. Policy changes are reviewable decisions, not silent ways to pass a smoke run.
- **Lifecycle:** Distinguish unconfigured, checking prerequisites, creating sandbox, booting VM, preparing workspace, discovering skills, and ready; planning/executing/waiting/completed/failed/aborting/aborted; stopping/stopped/cleanup failure.
- **Failure semantics:** Missing Node/Bun/omp/GSD/discovery, backend unavailability, model/auth failure, malformed child output, broken pipes, and incomplete cleanup must be distinguishable. No uncertain create retry or stage replay.
- **Verification:** Run actual wrapper/core/VM/sandbox/omp/GSD behavior on a disposable owned task, including a real question/answer, stale rejection, cancellation or controlled failure, framing, and cleanup. Compilation and mocks are not end-to-end proof.
- **Permissions:** No remote, publication, global install, box configuration, privilege, toolchain, or protected-workspace mutation without the required explicit authorization.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Embedded agentOS 0.4.3 with external sandbox provider | Required host integration is library-only; omp needs real Linux outside the VM | Decided; runtime acceptance pending |
| Node 24 core controls Bun omp through RPC | Avoid importing Bun-only SDK code into Node; preserve interactive session control | Decided boundary; plain-rpc versus built-in ask launch contract unresolved |
| Rust Anda wrapper with no direct inference | Expose local tools while keeping all provider access and workflows in omp | Blocked on compiler prerequisite; actual graph/server build remains unverified |
| OpenShell CLI 0.1.4; no token-gated TS SDK | Stable documented CLI avoids GitHub package credentials and unsupported source build | Decided; live compute unavailable/unverified |
| Prefer verified bwrap for the authorized local degraded tier | Minimal namespace launch works; avoids repeating the recorded KVM fault or changing privileges | Probe passed; application isolation/inference pending |
| Exact project-local GSD 1.18.0 Claude skills | Required skill discovery and invocation differ from the pi extension path | Blocked: selected Claude-local installer emits commands, not required skills |
| Gateway/broker authentication only | Protect provider secrets and existing host login ownership | Source contract resolved; gateway wiring, policy, and sandbox inference unverified |
| Every local receipt says attestation=mock | Hardware trust is unavailable; failure/degraded receipts must remain honest | Decided; implementation verification pending |
| Initialization uses automatic defaults and local-only commits | Supplied brief resolves product decisions and permits automatic planning; no publication permission follows | Applied during initialization |

## Evolution

This document evolves at phase transitions and milestone boundaries.

After each phase transition: move requirements to Validated only with recorded behavior evidence; move invalidated requirements to Out of Scope with the reason; add genuinely new scope to Active; record decisions and update the current system description. A phase cannot convert unexecuted OpenShell or production TEE behavior into a verified claim.

After each milestone: review every section, core value, scope boundaries, and evidence. Retain ownership/consent/security constraints unless Ove explicitly changes them.

---
*Last updated: 2026-10-21 after source-grounded initialization research*
