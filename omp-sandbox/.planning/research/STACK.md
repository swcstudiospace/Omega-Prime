# Stack Research

**Project:** Ove's Local omp Sandbox  
**Domain:** Owned local Rust/TypeScript orchestration of an embedded agentOS VM, an external Linux sandbox, and interactive omp/GSD workflows  
**Researched:** 2026-10-21  
**Confidence:** HIGH for inspected published manifests and concrete incompatibilities; MEDIUM for end-to-end feasibility pending the gates below  
**Research mode:** Version-constrained feasibility research, not application implementation

## Executive Decision

Keep the stack already decided in [PROJECT.md](../PROJECT.md): Rust embedding Anda, a Node 24 TypeScript core using Rivet's embedded library, an external Linux sandbox running Bun/omp RPC, and exactly project-local GSD-Core 1.18.0 Claude skills. Use the OpenShell 0.1.4 release CLI, with a separately disclosed bwrap degraded tier when authorized OpenShell compute is unavailable. Do not substitute hosted agentOS, a Node import of the Bun-only omp SDK, a different GSD installer, direct Anda inference, or an unapproved toolchain upgrade.

**The immediate hard prerequisite is Rust compatibility, not application coding:** rustc 1.85.1 does not satisfy the selected Anda graph's mandatory published manifests. `anda_engine` 0.17.4 and `anda_core` 0.17.1 require `anda_db_schema ^0.14`; published `anda_db_schema` 0.14.0 declares `rust-version = "1.95"`. `anda_engine` also requires `anda_db ^0.14` with `full`, and the inspected 0.14 releases declare the same floor. Edition-2024 support in Rust 1.85 does not overcome that MSRV requirement. Rust **at least 1.95 is a necessary lower bound, not a proved sufficient compiler for the whole graph**. [A1][A2][A4][A5][A6][R1][R2]

A second, independent warning is already published: the selected engine's docs.rs build failed on a much newer nightly in `anda_cognitive_nexus` 0.14.4. Do not promise that merely approving a newer Rust toolchain will make the entire integration build. [A7]

## Evidence Boundaries

- **OBSERVED — local source:** Read directly from this repository's public project/manifests/configuration/bootstrap files. This establishes file contents, not execution success.
- **REPORTED — local runtime:** Safe observations supplied by [PROJECT.md](../PROJECT.md), inherited without rerunning them. No versions or probes were re-executed during this research.
- **PUBLISHED — selected release:** Versioned registry metadata, normalized crates.io package source on docs.rs, npm-distributed files, or OpenShell's `v0.1.4` tag.
- **PENDING:** A required contract, artifact, or runtime behavior not established by the preceding evidence.
- **[INFERENCE]:** An explicit deduction rather than an exercised result.

This document was prepared by a read-only STACK scout. No named GSD researcher dispatch was claimed. No package installation, Cargo metadata resolution, build, lint, formatter, test, runtime startup, OpenShell retry, toolchain change, or repository write was performed. No credential database, authentication material, private prompt log, stage artifact, receipt, or protected neighboring workspace was used as a source.

## Recommended Stack

### Core Technologies and Version Status

| Technology | Selected version | Purpose and rationale | Observed/source/pending distinction | Confidence |
|---|---|---|---|---|
| Node.js | **24.21.0** | Runs the TypeScript core and project-local GSD tooling. Retain the exact decided patch release rather than chasing current Node. | REPORTED: `/home/box/tools/node24/bin/node` and PATH `node` report `v24.21.0` in PROJECT. OBSERVED: bootstrap pins `24.21.0`; root `engines.node` is only `>=24`, not an exact runtime lock. PUBLISHED: the exact release exists. Sandbox runtime remains PENDING. [N1] | HIGH for release/pins; runtime observation inherited |
| `@rivet-dev/agentos-core` | **0.4.3** | Embedded VM library owned by the Node core, not hosted VM management. | OBSERVED: exact package and lockfile pin. PUBLISHED: embedded ownership, ESM exports, runtime dependencies and matching source. VM boot/round trip remains PENDING. [J1][J3][J5] | HIGH |
| `@rivet-dev/agentos-sandbox` | **0.4.3** | Adapts an external sandbox backend into the VM integration. Attach through `AgentOs.create({ sandbox: { provider } })`. | OBSERVED: exact package/lock pin. PUBLISHED: depends on core **exactly 0.4.3**, re-exports the core contracts, and exports `sandboxAgentProvider`. A complete OpenShell-backed implementation remains PENDING. [J2][J4][J6][J7] | HIGH for package contract |
| Rust / Cargo | **1.85.1 currently reported; approved compatible toolchain unresolved** | Owns the Node child and embeds the real Anda engine/server. No silent upgrade. | REPORTED: both tools are 1.85.1. OBSERVED: local wrapper advertises `rust-version = "1.85"`. PUBLISHED: the required graph has a mandatory **1.95** floor. Current toolchain is incompatible with that graph. [A1][A2][A4][A5][A6] | HIGH for blocker |
| `anda_engine` | **0.17.4** | Actual Rust engine embedding and tool registration. It must not perform separate model inference. | PUBLISHED: exact engine package and normalized manifest inspected. OBSERVED: not present in the local Cargo dependencies or lockfile. Engine integration/build remains PENDING and blocked by current Rust. [A1][A8] | HIGH for version/manifest |
| `anda_engine_server` | **0.17.0**, matching the selected engine's compatible 0.17 line | Exposes the three requested tools using documented server registration/authentication/authorization. | PUBLISHED: real released server version; requires `anda_engine ^0.17` and `anda_core ^0.17`. Exact engine 0.17.4/core 0.17.1 satisfy those declared ranges. Not locally integrated. Do **not** invent a server 0.17.4. [A3][A9] | HIGH for manifest compatibility; runtime pending |
| `anda_core` | **0.17.1** | The matching engine tool/context types, where directly required by the implementation. | PUBLISHED: engine requires `^0.17.1`; server accepts `^0.17`. Exact 0.17.1 is published, unyanked in inspected registry metadata, and appears in the engine's published lock snapshot. Not locally integrated. [A1][A2][A8][A10] | HIGH |
| Bun | **1.4.2** | Executes omp outside the agentOS VM, preserving the Node/Bun process boundary. | REPORTED: local Bun 1.4.2. OBSERVED: bootstrap pins 1.4.2. PUBLISHED: exact package exists; omp 18.8.4 declares Bun `>=1.3.14`, which 1.4.2 satisfies. Sandbox execution remains PENDING. [J11][J12] | HIGH for declared compatibility |
| omp / `@oh-my-pi/pi-coding-agent` | **18.8.4 repository/bootstrap target** | Interactive newline-JSON RPC process under Bun; owns all workflow inference. | OBSERVED: config and bootstrap target 18.8.4. REPORTED: executable resolves to `/home/box/.local/bin/omp`; PROJECT does not establish its installed package version. PUBLISHED: 18.8.4 exposes the `omp` bin and Bun engine requirement. Exact installed version and sandbox RPC remain PENDING. [J11] | HIGH for target/package; installed version pending |
| `@opengsd/gsd-core` | **1.18.0** | Exact Claude/project-local skill installation and workflow content. | REPORTED: project-local runtime identity is 1.18.0. OBSERVED: config/bootstrap pin 1.18.0. PUBLISHED: requires Node `>=24.0.0` and npm `>=10.0.0`; bin maps `gsd-core` to the installer. Sandbox installation/discovery remains PENDING. [J13] | HIGH |
| OpenShell | **stable 0.1.4 release CLI** | Intended execution backend, addressed through a custom provider without adding its token-gated TS SDK. | OBSERVED: config/bootstrap target. REPORTED: a user-local release installation exists in PROGRESS but is outside inspected PATH; actual application transport is unverified. PUBLISHED: release and tag-specific build/runtime prerequisites inspected. Local compute is unavailable under the supplied stop condition. [O1][O2][O3][O4] | HIGH for release/prerequisites; live behavior pending |
| bubblewrap / `bwrap` | **0.12.0 reported** | Explicitly degraded owned local Linux execution tier, not OpenShell equivalence. | REPORTED: minimal `--unshare-all` probe exited 0 without host-home mounts. This is not proof of full application filesystem/network/process policy or model access. See PROJECT. | HIGH for inherited probe scope; application pending |
| Model routing | **`openai-codex/gpt-6.1-sol` planning; `openai-codex/gpt-6-luna` execution; disclosed `zai/glm-5.3` fallback only** | Preserve the requested stage identity and keep provider credentials behind auth-gateway/auth-broker. | OBSERVED: config contains these strings. They are routing requirements, **not verified service availability or effective session settings**. Live broker reachability/auth/model selection is PENDING. See PROJECT. | HIGH for decision; availability unverified |

### Supporting Libraries Already in the Selected Stack

These are existing development dependencies or dependencies already pulled by the selected packages, not recommendations to add unrelated products.

| Library/artifact | Observed version or published constraint | Role | Compatibility consequence |
|---|---|---|---|
| TypeScript | **5.9.3** exact local dev dependency | Static checks for the existing Node ESM/NodeNext core | Retain the existing pin. No typecheck was run. The compiler package is not proof of execution. |
| `@types/node` | **22.20.5** exact local dev dependency | Current Node API declarations | This is a typing-version mismatch with Node 24, not an observed runtime failure. Review against actual used Node APIs during implementation; do not silently choose an invented replacement version. |
| `@rivet-dev/agentos-runtime-core` and `@rivet-dev/agentos-runtime-sidecar` | **0.4.3** exact dependencies/lock entries | Embedded runtime and native execution sidecar | Keep them aligned with core. Sidecar declares Node `>=20`; Linux x64 package explicitly selects Linux/x64/**glibc**. Do not assume a musl base is supported by this artifact. [J1][J8][J9] |
| `better-sqlite3` | core constraint **`^12.8.0`**; local lock **12.11.1** | Native SQLite dependency of the selected core | Published 12.11.1 accepts Node 24.x, but its install script is `prebuild-install || node-gyp rebuild --release`. ABI/prebuild availability and actual native loading remain unproved. [J1][J10] |
| `sandbox-agent` and matching native CLI | sandbox constraint **`^0.4.2`**; local lock **0.4.2** | Existing HTTP/process/filesystem integration helper | Bootstrap instead requests **0.5.3**. That is outside the selected package's declared `^0.4.2` range; mixed client/server protocol compatibility has not been shown. Align deliberately with the selected graph or establish the exact mixed-version contract—do not call it compatible by name alone. [J2][J14][J15] |
| `serde_json` | local Cargo constraint **`1`**; lock **1.0.154** | Existing wrapper/core JSON data boundary | This is the local wrapper's only direct Rust dependency. Its presence says nothing about compiling or embedding Anda. See local Cargo manifests/lockfile. |

## Published Rivet 0.4.3 Requirements and Contracts

### Node compatibility is narrower than a blanket “Rivet requires Node 24” claim

The published **core and sandbox 0.4.3 manifests do not declare an `engines` field**. Their `_nodeVersion: 24.22.0` is publisher-environment metadata, not a minimum supported runtime. The relevant inspected declared requirements are sidecar Node `>=20`, local-lock `better-sqlite3` 12.11.1 including `24.x`, the application's own Node `>=24`, and GSD-Core 1.18.0 Node `>=24.0.0` / npm `>=10.0.0`. Therefore Node **24.21.0 passes the inspected version constraints**, without establishing native-module loading or VM startup. Do not upgrade to 24.22.0 solely because npm metadata records the publisher using it. Actual npm selection remains a prerequisite observation to capture under the same Node PATH during implementation. [J1][J2][J8][J10][J13]

The core pins both runtime packages to **0.4.3**; sandbox pins core to **0.4.3**. The local lockfile matches these pins and the registry integrity strings for the two direct Rivet packages. This is a coherent declared JS package set, not a successful installation/boot attestation. [J1][J2]

### Distinguish the two provider interfaces

1. **Core-facing `AgentOsSandboxProvider`:** `start(): Promise<AgentOsSandboxClient>`. Its client supplies `runProcess`, `createProcess`, process listing/stopping/killing/logs/input, and optional `dispose`. The VM accepts `sandbox: { provider }`. Provider form wires client disposal into VM disposal. [J4][J5]
2. **SandboxAgent backend `SandboxProvider`:** a different interface with `name`, `create`, `destroy`, and URL/fetch/server-availability hooks. `sandboxAgentProvider(backend, options)` adapts this backend into the core-facing provider and maps disposal to `destroySandbox`. Do not implement one interface and cast it to the other. [J6][J7][J16]
3. **Runtime filesystem prerequisite missing from the small core client interface:** `createSandboxFs` reads a serializable `client.baseUrl`, optional token/default headers, and configures native mount plugin `sandbox_agent`. It throws when no serializable base URL exists. A process-only object that structurally satisfies the `.d.ts` client interface is therefore insufficient for the required `/mnt/sandbox` filesystem mount. [J5]

**Roadmap implication:** the custom OpenShell CLI provider must still supply the selected release's actual SandboxAgent-compatible filesystem/process endpoint and its reachability/lifetime, or otherwise resolve that exact published integration contract without replacing the selected packages. CLI create/exec/upload/download/delete methods alone are not proof that the VM's native sandbox mount can consume the provider. A JS-only `getFetch` path must not be assumed to satisfy the native mount's serialized `baseUrl` requirement. [J5][J7][J16]

The selected core defaults the VM mount to `/mnt/sandbox`, enables sandbox host functions, and rejects removed legacy `sandbox.mount`, `sandbox.hostFunctions`, `sandbox.path`, and `sandbox.basePath` options. Use the selected release's `mountPath` and `sandboxRoot` contracts rather than examples from older/current-main interfaces. [J4][J5]

## Actual Rust Prerequisites

### Matching crates do not share one patch number

| Crate | Published version used for research | Edition / explicit `rust-version` | Important normal dependency constraints | Result |
|---|---|---|---|---|
| `anda_engine` | **0.17.4** | Edition **2024**; no explicit `rust-version` in inspected normalized manifest | `anda_core ^0.17.1`; `anda_db ^0.14` with `full`; `anda_db_schema ^0.14`; `anda_db_tfs ^0.14` with `full`; `anda_cognitive_nexus ^0.14`; Tokio `^1.53.3` with `full`; Reqwest `^0.13`; RMCP `^3.5` | Top-level lack of MSRV does **not** mean Rust 1.85 compatibility. Required dependencies carry a higher floor. [A1] |
| `anda_core` | **0.17.1** | Edition **2024**; no explicit `rust-version` in inspected normalized manifest | `anda_db_schema ^0.14`, `object_store ^0.14`, `reqwest ^0.13`, among others | Same mandatory schema blocker even for direct core use. [A2] |
| `anda_engine_server` | **0.17.0** | Edition **2024**; no explicit `rust-version` in inspected normalized manifest | Engine `^0.17`, core `^0.17`, Axum `^0.8`, Tokio `^1.53.3`, `ic_auth_types ^0.10.5`, verifier `^0.10` with `envelope`, `tower-http ^0.7` | Declared ranges admit exact engine 0.17.4/core 0.17.1; actual compile/server behavior not proved. [A3][A9] |
| `anda_db_schema` | **0.14.0** | Edition **2024**, **`rust-version = "1.95"`** | Required directly by both engine and core | **Hard current-toolchain manifest blocker.** Confirmed in package source and sparse registry metadata. [A4][A5] |
| `anda_db` | **0.14.2** in engine's published lock snapshot | Edition **2024**, **`rust-version = "1.95"`** | Required by engine; includes CRoaring and zstd native dependencies | Independent **hard current-toolchain manifest blocker**. Sparse index also declares 1.95 for inspected 0.14.0 and 0.14.1, so selecting another inspected 0.14 patch does not evade it. [A6][A8][A11] |

The `0.17.4` engine package's published lock snapshot contains core **0.17.1**, schema **0.14.0**, database **0.14.2**, and cognitive nexus **0.14.4**. This is useful reproducible upstream evidence, but **a dependency crate's published lockfile does not become this application's resolved Cargo.lock**. Server integration and feature unification can produce a different application graph. [A8]

### Verdict on rustc 1.85.1

**No: it is not a plausible supported compiler for the selected, unmodified graph.**

The decisive chain is:

```text
anda_engine =0.17.4
  -> anda_core ^0.17.1 -> anda_db_schema ^0.14
  -> anda_db_schema ^0.14
  -> anda_db ^0.14 [full]

published anda_db_schema 0.14.0: rust-version = 1.95
published anda_db 0.14.2:       rust-version = 1.95
current reported rustc:        1.85.1
```

These are **normal, non-optional** requirements in the inspected engine/core manifests. Disabling the engine's default features cannot remove them. Rust 1.85 stabilized edition 2024, so rejecting it merely for the edition would be incorrect; the independently published dependency MSRV is the actual blocker. Cargo documents unsupported `rust-version` as a build error. Do not use `--ignore-rust-version`, patch away upstream declarations, downgrade Anda, or silently upgrade Rust as a substitute for the required consent and compatibility gate. [A1][A2][A4][A6][R1][R2]

The local wrapper's edition 2021 and `rust-version = "1.85"` describe only the current thin wrapper. Its `anda = []` feature and Cargo.lock contain no Anda engine/server graph. A check of that current wrapper—even if successful elsewhere—would not validate the requested integration. No Cargo metadata or compilation check was run for this research.

### Native build prerequisites are also real

The selected engine is not a small pure-Rust tool-only dependency merely because this application forbids direct inference. Its published lock snapshot contains native cryptography, bitmap, and compression libraries. [A1][A8]

| Published snapshot dependency | Source-grounded prerequisite | What remains pending |
|---|---|---|
| `croaring-sys` **5.2.2**, through `croaring` **2.8.0** | Build script uses `cc::Build`, compiles bundled `CRoaring/roaring.c`, and requests **C11**. An available compatible native C compiler/toolchain is required for that source path. [B1] | Actual compiler/linker availability and target compatibility were not checked. |
| `zstd-sys` **2.1.2+zstd.1.5.7** | Default non-CMake path builds bundled C sources through `cc`; system-library/pkg-config and CMake branches depend on features/environment. Do not declare CMake or libclang universally mandatory from the package name alone. [B2][B3] | Application-resolved features, selected build path, and availability of corresponding native tools. |
| `aws-lc-sys` **0.46.0** | Builds bundled AWS-LC unless an explicitly configured system-library path is used. Package includes build tooling dependencies, and its README identifies native build prerequisites and pregenerated bindings for `x86_64_unknown_linux_gnu`. Go and Perl are **not absolutely necessary** according to this selected package README. [B4][B5] | Exact native compiler/build-path requirements for the eventual resolved application graph. Do not pre-install global tools or claim NASM/libclang/Perl are unconditional Linux prerequisites. |

These are **observed upstream snapshot versions**, not new application-level dependency recommendations. Pin the actual application lock graph when real Anda integration becomes authorized; then inspect its maximum MSRV and native requirements. At least Rust 1.95 and a suitable native toolchain are necessary here, but this research does not establish the full graph's minimum working compiler or a successful build. [A8][B1][B2][B4]

### Published build failure is a separate gate

The requested `https://docs.rs/anda_engine/0.17.4/anda_engine/` does not provide a successful rendered API build; docs.rs reports the 0.17.4 documentation build failed. The published log for build **4922320** uses `rustc 1.101.0-nightly` and fails with **E0275** while checking `anda_cognitive_nexus` **0.14.4**, in `EffectiveAuthority::resolve_at_depth`. [A7]

That is an externally observed build failure in a particular nightly/docs.rs/all-features environment, **not a reproduced local stable build failure or proof that every possible permitted resolution fails**. It does mean “upgrade to 1.95 and the selected graph is guaranteed to build” is unsupported. Inspect the exact permitted application resolution and upstream release evidence during the Rust feasibility gate; do not silently introduce a dependency patch, alternative engine release, or inference product. [A7][A8]

## OpenShell 0.1.4 Compatibility

- **Use the stable release binary/CLI, not a local source build.** The `v0.1.4` workspace declares edition 2024 and **Rust 1.94**. The currently reported Rust 1.85.1 does not satisfy that source-build declaration either. This is separate from Anda's **1.95** dependency floor. A prebuilt CLI avoids building OpenShell locally; it does **not** solve the Anda wrapper's build requirement. [O1][O2]
- **The installer is not a harmless binary lookup.** Tag-specific installation documentation says the default script installs the CLI/policy prover/local gateway and starts the gateway; on Linux it normally installs Debian/RPM packages and a user service. Do not run the quick-install snippet under this research or treat it as authorized host mutation. Preserve the recorded user-local release installation and explicit executable configuration. [O3]
- **Compute is a separate prerequisite.** Release docs list Docker Engine 28+, Podman 5.x/cgroups v2/user socket, or MicroVM host virtualization; the runtime page additionally documents Kubernetes and that VM is never auto-detected. The CLI alone is not a compute provider. PROJECT reports no container tools on PATH and records the kernel-faulting local MicroVM attempt as a stop condition. Never retry it to establish proof. [O3][O4]
- **Linux release packages require glibc 2.28+.** Do not infer the exact host/owned-sandbox libc merely from x64/Linux or from the absence of containers. [O3]
- **Do not add `@nvidia/openshell-sdk`.** The selected tag's TypeScript SDK docs say Node 20.3+ and GitHub Packages with a token possessing `read:packages`. It is a real option for separately authorized environments, but outside the decided CLI/no-new-credentials boundary. No SDK package version is invented here. [O5]
- **Interactive transport remains pending.** PROGRESS records help-level claims about stdin/output/exit behavior, but no successful sandbox application run. Prove non-TTY interactive newline-JSON stdin/stdout, separate stderr, exit status, cancellation, transfers, and cleanup only on authorized working compute. Neither the CLI's existence nor an upload/download implementation establishes the omp transport. See PROJECT and [O1][O4].

## Local Manifest and Bootstrap Drift

| Local observation | Implication for implementation |
|---|---|
| Root `package.json` pins the two Rivet packages exactly, but `engines.node` says `>=24`. | Preserve 0.4.3 and explicitly select `/home/box/tools/node24/bin/node` / 24.21.0 for owned children. A package engine range is not runtime selection. |
| The Rust crate depends only on `serde_json`; `anda` and `tee` are empty feature seams. | They are not accepted substitutes for real engine/server registration or hardware attestation. Actual Anda acceptance is blocked until its prerequisites are resolved. |
| Root Cargo workspace uses resolver 2 and has no resolved Anda dependencies. | Do not infer that edition-2024-aware resolver defaults or an existing Cargo.lock have selected a Rust-1.85-compatible Anda graph. |
| Host lockfile resolves `sandbox-agent`/CLI 0.4.2; bootstrap installs 0.5.3. | Resolve helper/client/server alignment before the VM external-mount gate. `^0.4.2` excludes 0.5.3; this proves a version-set mismatch, **not by itself a demonstrated wire-protocol failure**. [J2][J14][J15] |
| `OMP_BIN` defaults to `$HOME/.bun/bin/omp`, while PROJECT observes `/home/box/.local/bin/omp`. | Verify and disclose the selected executable rather than treating the default as a working path. Do not copy host login state to make another installation work. |
| `BUN_EXE` contains an installation-specific pnpm path, although PROJECT reports Bun 1.4.2. | Resolve executable identity for owned startup and sandbox bootstrap; the version string is not proof that this default path exists. |
| Bootstrap pins Node 24.21.0, Bun 1.4.2, omp 18.8.4, and GSD 1.18.0 but is explicitly marked unexercised. | Retain the target versions; prove installation and discovery inside the owned workspace rather than promoting the script's contents to runtime evidence. |
| Bootstrap invokes the selected GSD installer with an additional `--no-legacy-cleanup` and redirects stdin. | The required acceptance boundary remains exact package 1.18.0 with `--claude --local`; do not assume bootstrap extra flags/noninteractive behavior prove correct installation. Inspect the selected installer before carrying extra flags forward. |
| Current Node type declarations are 22.x while runtime is 24.x. | Treat as a review item, not an invented incompatibility or permission to upgrade dependencies during research. |

Local evidence: [`package.json`](../../package.json), [`package-lock.json`](../../package-lock.json), [`Cargo.toml`](../../Cargo.toml), [`Cargo.lock`](../../Cargo.lock), [`crates/omp-sandbox-anda/Cargo.toml`](../../crates/omp-sandbox-anda/Cargo.toml), [`src/core/config.ts`](../../src/core/config.ts), [`sandbox/bootstrap.sh`](../../sandbox/bootstrap.sh), and [PROJECT.md](../PROJECT.md). The existing user change to `src/core/vm.ts` remains protected; it was not modified or needed as proof here.

## Development Tools

| Tool | Purpose | Boundary |
|---|---|---|
| Existing Node 24.21.0 / npm | Core startup and project-local package execution | Capture effective npm version against GSD's `>=10` requirement; do not replace host runtime/configuration. [J13] |
| Existing TypeScript 5.9.3 | Existing `check` script | Preserve the pin; run the repository check only during authorized implementation verification. |
| Cargo/Rust | Wrapper/engine/server build | Current 1.85.1 cannot satisfy the selected graph. Obtain explicit toolchain consent before any upgrade; choose and disclose the exact approved compiler only after graph review. |
| OpenShell release CLI and bwrap | Intended and degraded backend boundaries | No global install, privilege changes, container installation, KVM changes, or retry of the recorded VM kernel fault. |

Do not add a web framework, queue, database service, orchestrator, telemetry layer, retry framework, alternate model SDK, or separate inference runtime. Embedded package dependencies already present in Anda/Rivet are not reasons to expose new products or credentials.

## Installation and Pinning Contract

**Documentation only; none of these installation steps was executed in this research.**

- Retain direct JS dependency pins `@rivet-dev/agentos-core: "0.4.3"` and `@rivet-dev/agentos-sandbox: "0.4.3"`, with the existing application lockfile. Do not copy `npm install ...@latest` examples from unversioned docs. [J1][J2]
- Use the existing Node **24.21.0** explicitly. In the owned external sandbox, prepare Bun **1.4.2** and the repository's omp **18.8.4** target; verify their effective executable versions there. Bun 1.4.2 satisfies omp's declared minimum, not its end-to-end acceptance. [N1][J11][J12]
- The mandated project-local GSD path, run from the owned sandbox workspace, is:

```sh
npx @opengsd/gsd-core@1.18.0 --claude --local
```

  Node `>=24.0.0` and npm `>=10.0.0` are its published prerequisites. Verify the resulting Claude skills through omp command discovery; installer exit status alone is insufficient. [J13]
- Real Rust integration should select **`anda_engine = "=0.17.4"`**, the matching published **`anda_engine_server = "=0.17.0"`**, and **`anda_core = "=0.17.1"`** where directly used. These are the requested engine/server/core components, not extra products. Do not put them behind empty acceptance-bypassing features. Regenerate and retain the real application lock graph only in an authorized implementation task. [A1][A2][A3]
- No Rust toolchain installation command is prescribed as pre-approved. The **necessary floor is 1.95**, with the actual working compiler/full resolution still pending. Never silently replace that uncertainty with “latest stable works.” [A4][A6][A7]
- Use OpenShell **v0.1.4** release artifacts and the selected absolute CLI path. Do not execute the upstream installer, source-build OpenShell, or add the token-gated SDK to satisfy a local preflight. [O1][O2][O3][O5]
- Restrict all owned artifacts, sandbox HOME, and GSD files to the owned workspace. Existing omp provider logins remain behind the required auth-broker/auth-gateway; never mount/copy the host login database. See PROJECT.

## Alternatives Considered / What NOT to Use

| Decided choice | Alternative not selected | Why not for this project |
|---|---|---|
| Embedded `@rivet-dev/agentos-core` 0.4.3 | Hosted `@rivet-dev/agentos` | Hosted durable identity/HTTP ownership is a different product boundary; this project requires an owned embedded VM plus external sandbox. Hosted may suit a different long-lived shared service, not this contract. [J3] |
| Node core controlling Bun omp RPC | Import omp's SDK into Node, or use `omp -p` | Published omp package targets Bun; project requires interactive correlated RPC and live question handling, not one-shot prompt execution. [J11]; PROJECT |
| Exact GSD 1.18.0 `--claude --local` | pi GSD installer, bare `/gsd-*`, substituted workflow skills | Those do not implement the decided Claude-skill discovery/dispatch boundary. Require `gsd-plan-phase` and `gsd-autonomous`. PROJECT; selected installer package [J13] |
| OpenShell 0.1.4 CLI | OpenShell source build on Rust 1.85.1 | Release workspace declares Rust 1.94; the local compiler is below it, and source build is explicitly out of scope. [O2] |
| OpenShell 0.1.4 CLI | OpenShell TypeScript SDK | Selected docs require GitHub Packages authentication and `read:packages`; new credentials/product dependency is not authorized. It could be considered only under a different explicit contract. [O5] |
| Authorized bwrap degraded tier | Install Docker/Podman or alter KVM/groups/privileges to make OpenShell pass | These mutate the host outside permission; the recorded MicroVM kernel fault must not be retried. PROJECT; OpenShell compute prerequisites [O3][O4] |
| Actual Anda 0.17.4 integration | Thin serde-only wrapper, empty `anda` feature, earlier Anda release, or `--ignore-rust-version` | None establishes the named engine/server acceptance; bypassing the manifest does not prove compiler support. [A1][A4][R2]; local Cargo manifest |
| omp model access through auth-gateway/auth-broker | Direct Anda inference or copied provider login DB | Changes inference/credential ownership and violates the supplied security boundary. PROJECT |
| Explicit `attestation=mock` local receipts | Hardware/production dTEE, SGX/GPU TEE, Nitro or confidential-compute success claims | Required hardware/infrastructure proof is absent. `dTEE` is the project's architecture label, not a validated local property. PROJECT |

## Stack Patterns by Authorized Tier

**If authorized working OpenShell compute exists:**
- Use the exact 0.1.4 CLI provider and selected Rivet 0.4.3 contracts.
- Prove real lifecycle/transfers, SandboxAgent endpoint/native VM mount, and interactive omp transport separately before readiness.
- Preserve owned-resource identity; do not delete or stop pre-existing resources.

**If OpenShell compute is unavailable on this host:**
- Disclose the unavailable intended backend and select the authorized bwrap degraded tier.
- The successful minimal namespace probe is only a prerequisite observation. Prove application policy, mounts, process/network restrictions, broker reachability, and cleanup without mounting host homes or credentials.
- agentOS-local may be an explicitly selected host-execution fallback, but is not equivalent isolation and must never be labeled OpenShell.

**In every tier:**
- Still boot the actual embedded VM and establish the requested external-sandbox file/command round trip.
- Run omp under Bun outside the VM and GSD in the owned workspace.
- Keep Sol/Luna stage routing and disclose only the permitted GLM fallback; verify effective live settings.
- Expose Anda tools without separate inference, report lifecycle/failure states, and label every local receipt `attestation=mock`.

These tier decisions are supplied requirements, not runtime findings. See [PROJECT.md](../PROJECT.md). OpenShell's separation of gateway/compute and runtime requirements is source-verified in [O3][O4].

## Roadmap Gates and Key Blockers

| Gate | Must be established before the dependent phase can claim readiness | Current status |
|---|---|---|
| **Rust dependency feasibility and consent** | Explicit permission for an exact compatible toolchain; exact engine/server/core application resolution; maximum MSRV/native-tool review; real build of the selected integration | **BLOCKED on current 1.85.1:** mandatory 1.95 manifests. Newer Rust alone is not guaranteed sufficient because of the published cognitive-nexus build failure. [A4][A6][A7] |
| **Rivet native runtime and actual provider contract** | Node 24.21.0, matching 0.4.3 native/runtime artifacts, actual VM boot/disposal; complete provider with reachable serializable SandboxAgent endpoint; `/mnt/sandbox` and host-function file/command round trip | **PENDING.** Manifest consistency is established; native loading/runtime acceptance is not. [J1][J4][J5][J8][J9][J10] |
| **Helper-version alignment and sandbox preparation** | Resolve host `sandbox-agent` 0.4.2 versus bootstrap 0.5.3; prepare/verify exact Node/Bun/omp/GSD in the owned sandbox | **PENDING.** Version-set mismatch is observed, protocol incompatibility not yet demonstrated. [J2][J14][J15] |
| **Execution backend availability and isolation** | Authorized available OpenShell compute or explicitly degraded verified bwrap; real policy/lifecycle/transfers and framed interactive stdin/stdout/stderr/exit | **OpenShell local MicroVM STOPPED; never retry.** bwrap application behavior pending beyond the reported minimal probe. PROJECT; [O3][O4] |
| **GSD/auth/model readiness** | Exact project-local Claude skill discovery, `get_available_commands` containing plan-phase/autonomous, broker/gateway endpoint restrictions, effective Sol/Luna/fallback state | **PENDING.** Node version constraints and GSD package identity are not live discovery/auth proof. [J11][J13]; PROJECT |
| **Full wrapper/server end-to-end acceptance** | Real `run_gsd_stage`, `answer_question`, `sandbox_status` registration/auth/authz; a disposable real stage through Rust/core/VM/sandbox/omp; question correlation and stale rejection, abort/failure/shutdown/owned cleanup; mock receipts on success and failure | **PENDING and dependent on earlier gates.** A serde-only wrapper, package metadata, compilation, or mocked execution cannot satisfy it. PROJECT; matching server manifest [A3] |

**Roadmap ordering:** surface the Rust hard blocker at prerequisite/feasibility time, not at final packaging. In parallel, source-grounded Node/provider design and isolated owned-workspace preparation can proceed without asserting full integration is complete. Keep unavailable OpenShell compute and Rust compiler consent as independent blockers: bwrap does not solve Anda's compiler requirement, and a newer compiler does not make local KVM safe. No phase may turn unexecuted OpenShell checks or absent hardware attestation into validated requirements.

## Confidence and Unresolved Contracts

| Finding | Confidence | Remaining uncertainty |
|---|---|---|
| Local direct Rivet pins match published 0.4.3 package dependencies/integrities | HIGH | Installed files/native binaries and actual VM behavior were not checked. |
| Node 24.21.0 satisfies inspected published version constraints | HIGH | Effective npm version, native addon load, sidecar launch, and owned-sandbox runtime still pending. |
| Matching server/core are 0.17.0 / 0.17.1 | HIGH | Compatible manifest ranges are not server API/build proof. |
| rustc 1.85.1 is below required graph MSRV | HIGH | Exact maximum/working Rust version for the full application graph remains unresolved; 1.95 is only a necessary floor. |
| Native C compilation is present in the upstream graph | HIGH | Actual application feature resolution/tool availability and target-specific requirements remain pending. |
| Selected engine docs.rs build failed in cognitive nexus | HIGH for that published build | Whether the approved stable application build reproduces it is unknown; no local reproduction was attempted. |
| Core native sandbox mount needs serializable `baseUrl` beyond the small process-client interface | HIGH | Exact OpenShell-backed endpoint exposure/ownership/transport design remains an implementation research gate. |
| Host helper 0.4.2 and bootstrap helper 0.5.3 differ | HIGH | Mixed-version wire compatibility is unresolved, not assumed broken. |
| Full application/runtime/isolation/model acceptance | UNVERIFIED | No application build or runtime proof was generated here. |

The crates.io REST API requests returned HTTP 403 during research. The required hard-blocker evidence was recovered from **published normalized package manifests on docs.rs**, independently cross-checked against reachable sparse registry metadata for schema/database and matching core/server. The large engine sparse-index response was incomplete for the requested release and was **not** used to infer selected-release metadata. No conclusion relies on current Anda or Rivet `main`; versioned package source takes precedence. The current Rivet quickstart was used only as orientation and was checked against 0.4.3-distributed files. OpenShell source conclusions use `v0.1.4`, not its moving main branch.

## Sources

### Local source of scope and observations

- [PROJECT.md](../PROJECT.md) — decisions, permissions, safe runtime observations, acceptance and stop conditions.
- [package.json](../../package.json), [package-lock.json](../../package-lock.json) — local direct and resolved JavaScript versions.
- [Cargo.toml](../../Cargo.toml), [Cargo.lock](../../Cargo.lock), [wrapper Cargo.toml](../../crates/omp-sandbox-anda/Cargo.toml) — current serde-only Rust wrapper, not actual Anda integration.
- [config.ts](../../src/core/config.ts), [bootstrap.sh](../../sandbox/bootstrap.sh), [tsconfig.json](../../tsconfig.json) — targets, executable defaults, unexercised sandbox bootstrap, existing TypeScript convention.
- [PROGRESS.md](../../PROGRESS.md) — only safe prerequisite/OpenShell historical observations; no private stage logs inspected.

### Rivet / JavaScript release sources

[J1]: https://registry.npmjs.org/@rivet-dev/agentos-core/0.4.3
[J2]: https://registry.npmjs.org/@rivet-dev/agentos-sandbox/0.4.3
[J3]: https://rivet.dev/agentos/docs/quickstart/
[J4]: https://unpkg.com/@rivet-dev/agentos-core@0.4.3/dist/sandbox.d.ts
[J5]: https://unpkg.com/@rivet-dev/agentos-core@0.4.3/dist/sandbox.js
[J6]: https://unpkg.com/@rivet-dev/agentos-sandbox@0.4.3/dist/provider.d.ts
[J7]: https://unpkg.com/@rivet-dev/agentos-sandbox@0.4.3/dist/provider.js
[J8]: https://registry.npmjs.org/@rivet-dev/agentos-runtime-sidecar/0.4.3
[J9]: https://registry.npmjs.org/@rivet-dev/agentos-runtime-sidecar-linux-x64-gnu/0.4.3
[J10]: https://registry.npmjs.org/better-sqlite3/12.11.1
[J11]: https://registry.npmjs.org/@oh-my-pi/pi-coding-agent/18.8.4
[J12]: https://registry.npmjs.org/bun/1.4.2
[J13]: https://registry.npmjs.org/@opengsd/gsd-core/1.18.0
[J14]: https://registry.npmjs.org/sandbox-agent/0.4.2
[J15]: https://registry.npmjs.org/sandbox-agent/0.5.3
[J16]: https://unpkg.com/sandbox-agent@0.4.2/dist/types-DdcvY5CI.d.ts
[N1]: https://nodejs.org/en/blog/release/v24.21.0

- [Rivet core 0.4.3 registry metadata][J1] — published direct runtime/native dependency requirements; absent direct engines declaration.
- [Rivet sandbox 0.4.3 registry metadata][J2] — exact core pairing and SandboxAgent constraint.
- [Rivet library quickstart][J3] — embedded/hosted distinction; unversioned, not authoritative over selected package source.
- [Core sandbox declarations][J4] and [implementation][J5] — provider lifecycle, native mount serialization, removed options, host functions.
- [Sandbox provider declarations][J6] and [adapter implementation][J7] — distinct backend-to-core provider boundary.
- [Sidecar metadata][J8], [Linux x64 glibc artifact metadata][J9], [SQLite metadata][J10] — Node/native platform constraints.
- [omp 18.8.4 metadata][J11], [Bun 1.4.2 metadata][J12], [GSD 1.18.0 metadata][J13] — exact selected package engines and entry points.
- [SandboxAgent 0.4.2][J14], [0.5.3][J15], [0.4.2 backend interface][J16] — helper-version drift and exact provider interface.
- [Node 24.21.0 release][N1] — exact published runtime release.

### Anda / Rust / native prerequisites

[A1]: https://docs.rs/crate/anda_engine/0.17.4/source/Cargo.toml
[A2]: https://docs.rs/crate/anda_core/0.17.1/source/Cargo.toml
[A3]: https://docs.rs/crate/anda_engine_server/0.17.0/source/Cargo.toml
[A4]: https://docs.rs/crate/anda_db_schema/0.14.0/source/Cargo.toml
[A5]: https://index.crates.io/an/da/anda_db_schema
[A6]: https://docs.rs/crate/anda_db/0.14.2/source/Cargo.toml
[A7]: https://docs.rs/crate/anda_engine/0.17.4/builds/4922320
[A8]: https://docs.rs/crate/anda_engine/0.17.4/source/Cargo.lock
[A9]: https://index.crates.io/an/da/anda_engine_server
[A10]: https://index.crates.io/an/da/anda_core
[A11]: https://index.crates.io/an/da/anda_db
[B1]: https://docs.rs/crate/croaring-sys/5.2.2/source/build.rs
[B2]: https://docs.rs/crate/zstd-sys/2.1.2%2Bzstd.1.5.7/source/Cargo.toml
[B3]: https://docs.rs/crate/zstd-sys/2.1.2%2Bzstd.1.5.7/source/build.rs
[B4]: https://docs.rs/crate/aws-lc-sys/0.46.0/source/Cargo.toml
[B5]: https://docs.rs/crate/aws-lc-sys/0.46.0/source/README.md
[R1]: https://blog.rust-lang.org/2025/02/20/Rust-1.85.0/
[R2]: https://doc.rust-lang.org/cargo/reference/rust-version.html

- [Engine manifest][A1], [core manifest][A2], [server manifest][A3] — selected-release editions, absence of explicit top-level MSRV, actual dependency constraints.
- [Schema manifest][A4] and [registry metadata][A5] — independent confirmation of mandatory Rust 1.95 floor.
- [Database manifest][A6] and [registry metadata][A11] — second floor and mandatory native dependencies.
- [Selected engine documentation build log][A7] — exact external E0275 failure; not local build evidence.
- [Published engine lock snapshot][A8] — exact observed upstream dependency versions, not an application-resolution proof.
- [Server registry metadata][A9], [core registry metadata][A10] — matching real releases and compatible declared ranges.
- [CRoaring build source][B1], [zstd manifest][B2]/[build source][B3], [AWS-LC manifest][B4]/[README][B5] — actual native build boundaries and conditional prerequisites.
- [Rust 1.85 release][R1] — edition-2024 support; [Cargo rust-version reference][R2] — meaning of the independent MSRV field.

### OpenShell selected release

[O1]: https://github.com/NVIDIA/OpenShell/releases/tag/v0.1.4
[O2]: https://github.com/NVIDIA/OpenShell/blob/v0.1.4/Cargo.toml
[O3]: https://github.com/NVIDIA/OpenShell/blob/v0.1.4/docs/about/installation.mdx
[O4]: https://github.com/NVIDIA/OpenShell/blob/v0.1.4/docs/how-it-works/sandboxes/runtimes.mdx
[O5]: https://github.com/NVIDIA/OpenShell/blob/v0.1.4/docs/sdk/typescript.mdx

- [Release v0.1.4][O1] — selected stable release, pointing to commit `5a7728c304b5528e954e9ae900cf21f84e1a12b1`.
- [Tag-specific Cargo workspace][O2] — edition 2024 / Rust 1.94 source-build requirement.
- [Tag-specific installation documentation][O3] — runtime prerequisites, Linux glibc 2.28+, installer/service side effects.
- [Tag-specific runtime documentation][O4] — gateway/compute separation and driver selection.
- [Tag-specific TypeScript SDK documentation][O5] — GitHub Packages token requirement; not selected.

---
*Stack research for the decided local omp sandbox integration. Declared compatibility is not compile proof; compile proof is not application, isolation, model-routing, or hardware-attestation proof.*
