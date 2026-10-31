# Project Research Summary

**Project:** Ove's Local omp Sandbox
**Domain:** Local Rust/Anda supervision of a Node/agentOS core and external Linux sandbox running Bun/omp/GSD
**Researched:** 2026-10-21
**Confidence:** HIGH for inspected versioned contracts and manifest blockers; UNVERIFIED for application end-to-end behavior

## Executive Summary

The decided process boundary is appropriate: Rust embeds Anda and supervises a Node 24 TypeScript child; Node boots embedded agentOS and attaches an external sandbox provider; Bun/omp executes in that external Linux environment, not in the agentOS VM. Authentication must terminate at an omp auth-gateway backed by the host's trusted auth-broker. The sandbox must never receive the broker capability or provider credentials. Local bwrap is a feasible candidate because a minimal namespace probe succeeded, but that probe does not prove application confinement, gateway policy, or inference.

The supplied brief contains implementation-blocking contract conflicts. Anda engine 0.17.4 has mandatory database/schema dependencies declaring Rust 1.95, incompatible with the installed 1.85.1. GSD 1.18.0's Claude-local installer writes file commands rather than the required skill packages. omp 18.8.4's built-in question UI is wired by `rpc-ui`, not plain `rpc`, and direct `.claude/agents` are intentionally excluded from task-agent discovery. These findings must become explicit gates; changing the compiler, installation/layout, launch requirement, or workflow integration is not implicitly authorized by automatic project initialization.

Existing TypeScript and newly observed Rust files are implementation starting points, not acceptance evidence. Current source passes a direct broker capability to sandbox processes, has incomplete provider/discovery boundaries, can replay a stage on fallback, and can conflate prompt termination with successful workflow completion. Preserve user changes, migrate the whole boundary during implementation, and accept only real wrapper-to-VM-to-sandbox-to-workflow evidence. OpenShell compute and production TEE behavior remain unverified; all local receipts must state `attestation=mock`.

## Key Findings

### Recommended Stack

Use the fixed stack rather than introducing a second agent product:

- **Node 24.21.0 + TypeScript:** host orchestration and project-owned newline JSON; no Bun-only SDK import into Node.
- **`@rivet-dev/agentos-core` and `@rivet-dev/agentos-sandbox` 0.4.3:** embedded VM and actual provider adapter contracts. The transitive `sandbox-agent` backend is a separate interface, not `AgentOsSandboxProvider` itself.
- **OpenShell v0.1.4 CLI:** intended Linux sandbox backend, with release-pinned command/streaming/transfer semantics. The TypeScript SDK's GitHub package-token requirement is deliberately excluded.
- **bwrap degraded local tier:** namespace creation was observed; application isolation and process/network policy still require actual verification. AgentOS-local remains an honestly labelled no-isolation alternative only if needed and executable.
- **Bun 1.4.2 / omp 18.8.4 / GSD 1.18.0:** versions found in the existing integration and distribution. Required model routing remains Sol for planning, Luna for execution, disclosed GLM-5.3 fallback only.
- **Anda engine 0.17.4:** matching researched published core/server are 0.17.1/0.17.0. The complete resolved graph, native prerequisites, and server authentication must be built and exercised after the compiler prerequisite is resolved. An empty Cargo feature is not integration.

See [STACK.md](STACK.md) for normalized manifests, registry evidence, selected lock snapshot caveats, native dependencies, and release drift. Upgrading to a compiler meeting one MSRV does not guarantee a working graph; an upstream docs.rs build failure is a separate warning, not a reproduced local failure.

### Expected Features

**Required table stakes:** truthful startup/status, explicit tier selection, VM-mediated external-sandbox operations, exact runtime/skill discovery gates, safe broker/gateway inference, correlated asynchronous RPC, one active stage/session, real workflow outcome tracking, live questions and stale-answer rejection, abort, bounded child/transport failure, cleanup ownership, and secret-free mock receipts.

**Already requested differentiated capability:** Anda tools exposing the same real session, CLI operation over the supported local tier, an OpenShell upgrade path, and documentation separating local mock trust from intended confidential-host/IC-TEE placement. These are in scope, not optional research additions.

**Excluded:** hosted Rivet, direct Anda inference, alternate model accounts, copied login stores, substitute skills, silent global/privileged installs, automatic mutating stage replay, arbitrary shell/model dispatch, telemetry/retry frameworks, production TEE provisioning, and unauthorized publication.

See [FEATURES.md](FEATURES.md) for exact selected omp message shapes, file-command versus skill metadata, real GSD role compatibility, and consumer-visible scenarios. Command admission, prompt result, session settlement, and artifact-backed workflow success are distinct facts.

### Architecture Approach

Rust owns one Node child and a bounded pending-request/event channel. Node owns one VM, one provider attachment and explicit resource ledger, and one active omp stage/session with independent pending-question identity. A provider creates the external Linux execution environment and exposes a real Sandbox Agent endpoint for the agentOS mount and host functions. OpenShell exec separately carries omp's stdio protocol. All stage prompts and answers cross as data, never shell interpolation.

The project-owned child protocol is a proposed local contract, not an upstream Anda or omp API. Its operations cover startup, `run_gsd_stage`, `answer_question`, `sandbox_status`, abort and shutdown; request IDs, child epoch, stage/session/question IDs and asynchronous event sequence remain distinct. Strict framing, backpressure, size limits, structured failures, endpoint allowlists and redacted diagnostics are part of the boundary.

See [ARCHITECTURE.md](ARCHITECTURE.md) for complete provider/client signatures, Anda registration/server APIs, CLI flags, acquisition/disposal rules, authentication configuration, and proposed bounded protocol. Two selected-release hazards require real checks: agentOS resolves host functions before sandbox expansion; OpenShell piped exec accepts at most 4 MiB of cumulative stdin and is not an unlimited long-lived channel.

### Critical Pitfalls

1. **Direct broker access is not credential-safe gateway access.** Broker clients are trusted and may receive access tokens/API keys. Only the gateway receives the broker capability; sandbox models use the documented `pi-native` transport and a separate gateway bearer. Account pools are routing, not authorization.
2. **Installation/menu visibility is not compatible workflow execution.** Claude-local GSD commands, native skills, task agents and tool dialects differ. Require actual required skill metadata and real role/workflow outcomes; do not manufacture success by using bare commands or inline agent impersonation.
3. **A response is not a completed stage.** A prompt may be admitted, locally handled without inference, yield before session settlement, fail, or finish without valid GSD artifacts. State must represent these outcomes independently.
4. **Missing model output does not make replay safe.** Never replay a stage after admission or uncertain transport outcome. Disclosed fallback is allowed only before work submission or through an explicit safe operator action.
5. **Local processes and mock documents are not confidential computing.** Verify bwrap restrictions, do not inherit credential-bearing homes/environments, keep receipts redacted, and record cleanup failure/remaining ownership rather than reporting false stopped/clean state.

See [PITFALLS.md](PITFALLS.md) for source-linked detection, prevention, real failure scenarios, and capability ownership.

## Implications for Roadmap

The first delivery boundary must be observable supervision, not a compile-only wrapper. It must expose a real Anda status tool that owns the Node child, boots a VM, performs a VM-mediated sandbox operation, identifies the local tier/mock trust, and shuts down owned resources. The Rust graph prerequisite is checked before accepting this boundary.

A second boundary makes the sandbox genuinely ready for authenticated workflows: Node/Bun/omp versions, gateway policy, exact GSD installation, real skill/role discovery and observable prerequisite failures. The GSD installation/discovery conflict must be resolved explicitly before this boundary can pass.

A third boundary delivers real planning and execution through the Anda tool path, with exact routing, actual workflow artifacts, live follow-up answers, cancellation, stale/overlap rejection and no replay. This is the core-value acceptance, not a metadata or model-text echo.

A fourth boundary adds selectable OpenShell behavior against the complete release contract, with transfers, policy, interactive transport and owned cleanup. It remains in scope when compute is absent: implementation and source inspection cannot mark its live lifecycle/policy checks passed. Do not make the local delivery wait on an unsafe retry of the known kernel-faulting route.

The remaining operator boundary covers bounded failures, recovery/status, startup rollback, deterministic cleanup reporting, documented command operation and evidence. Final local evidence must exercise the actual wrapper path again and record all unresolved OpenShell/production boundaries. Requirements, not a preferred phase count, determine the final roadmap.

### Phase Ordering Rationale

- A truthful status/ownership path exposes the Rust prerequisite and VM/provider contract before inference or credential access.
- Gateway and discovery are prerequisites for safe GSD stage admission; neither is repaired by changing a prompt.
- Questions, routing, settlement and artifacts belong to the same interactive stage capability.
- OpenShell cannot be substituted for the supported degraded tier or described as verified without actual usable compute.
- Cross-process failure/cleanup scenarios and operational evidence must retain all prior acceptance gates, not downgrade them to compilation.

### Research Flags

Deeper source/runtime work is required for the actual resolved Anda/native build, agentOS host-function integration, authorized GSD skill/role packaging, the question-capable launch contract, pi-native routing through the restricted gateway, and OpenShell live transport/policy. Standard bounded NDJSON and ownership patterns are well understood, but the actual processes still require smoke proof.

## Confidence Assessment

| Area | Confidence | Boundary |
|------|------------|----------|
| Versioned manifests and provider/RPC contracts | HIGH | Public selected-version/package source inspected; not compiled or executed as a whole |
| Consumer-visible scope | HIGH | Supplied acceptance contract retained, no unrelated product features added |
| Proposed ownership/protocol architecture | MEDIUM | Design grounded in contracts; application behavior pending |
| Security pitfalls | HIGH for inspected source gaps | No exploit, credential access, or application failure reproduction performed |
| Local bwrap executability | Narrow observed success | Minimal namespace command only, not application/policy/inference proof |
| Full workflow, OpenShell compute, hardware TEE | UNVERIFIED | No acceptance credit from documentation or mock receipts |

### Gaps to Address

- A permitted compiler/build environment satisfying the actual Anda graph; no toolchain change is authorized.
- An authorized resolution of the incompatible GSD 1.18.0 Claude-local layout and required native skills/roles, without substitute workflows or host-global discovery.
- Explicit resolution of the plain `rpc` requirement versus built-in ask support through `rpc-ui` in selected omp.
- Typed provider integration and real VM mount/host-function behavior for agentOS 0.4.3.
- Actual restricted gateway inference and effective Sol/Luna/GLM identity within the sandbox.
- Authorized usable OpenShell compute and live transport/policy/cleanup evidence; no repeat of the recorded KVM fault.

No information gap blocks creation of this roadmap. The gaps above block relevant execution acceptance and must not be treated as automatic permission or silent scope reduction.

## Sources

### Primary versioned sources

- [agentOS library quickstart](https://rivet.dev/agentos/docs/quickstart/) and installed 0.4.3 declarations/runtime referenced in [ARCHITECTURE.md](ARCHITECTURE.md).
- [Anda engine 0.17.4 manifest](https://docs.rs/crate/anda_engine/0.17.4/source/Cargo.toml), [server 0.17.0 manifest](https://docs.rs/crate/anda_engine_server/0.17.0/source/Cargo.toml), and [schema 0.14.0 Rust minimum](https://docs.rs/crate/anda_db_schema/0.14.0/source/Cargo.toml).
- [GSD 1.18.0 Claude capability](https://github.com/open-gsd/gsd-core/blob/v1.18.0/capabilities/claude/capability.json) and [installer](https://github.com/open-gsd/gsd-core/blob/v1.18.0/bin/install.js).
- [omp 18.8.4 RPC](https://github.com/can1357/oh-my-pi/blob/v18.8.4/docs/rpc.md), [launch](https://github.com/can1357/oh-my-pi/blob/v18.8.4/packages/coding-agent/src/main.ts), [task-agent discovery](https://github.com/can1357/oh-my-pi/blob/v18.8.4/packages/coding-agent/src/task/discovery.ts), and [broker/gateway](https://github.com/can1357/oh-my-pi/blob/v18.8.4/docs/auth-broker-gateway.md).
- [OpenShell v0.1.4 release](https://github.com/NVIDIA/OpenShell/releases/tag/v0.1.4); command, streaming and policy source links are retained in [ARCHITECTURE.md](ARCHITECTURE.md) and [PITFALLS.md](PITFALLS.md).
- [AWS Nitro enclave constraints](https://docs.aws.amazon.com/enclaves/latest/user/nitro-enclave.html) and [IC-TEE](https://github.com/ldclabs/ic-tee): production topology context only.

### Project evidence

[PROJECT.md](../PROJECT.md) records directly observed runtime versions, the minimal bwrap probe, GSD runtime identity/init metadata, preserved user changes and the inherited kernel-fault stop condition. Four read-only scout agents supplied the detailed research; this synthesis is inline because named GSD agent dispatch is not exposed by the active tool interface. No application-runtime success is claimed.

---
*Research completed: 2026-10-21*
*Ready for requirements and roadmap: yes. Ready for end-to-end execution acceptance: no.*
