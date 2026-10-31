# Feature Research

**Domain:** Owned local CLI workflow orchestration: Rust/Anda → Node 24 core → embedded agentOS integration → external Linux sandbox → Bun omp → GSD-Core
**Researched:** 2026-10-21
**Confidence:** HIGH for the inspected versioned wire/discovery contracts; MEDIUM for the feasibility of the complete Claude-workflow-on-omp combination; application behavior remains UNVALIDATED.

## Scope and Evidence Boundary

This document defines the requested interactive CLI features and their acceptance requirements. It does not implement the app or expand the product. The binding product scope is [PROJECT.md](../PROJECT.md). Already-requested differentiators below are mandatory delivery features, not optional embellishments.

Research used the official [omp SDK documentation][omp-sdk], [RPC documentation][omp-rpc-docs], GSD's public repository and selected release, and public installed package sources. The installed omp package manifest reports **18.8.4**; its important contracts were cross-checked against [v18.8.4][omp-release]. The cached GSD package manifest reports **1.18.0**; [published npm metadata][gsd-package] identifies commit `35f5a452239b897b8d6352c973e1e4efd683370f`, which is also the selected release tag target. These are source/version observations, not proof that any sandbox session, provider inference or workflow succeeded.

No build, test, lint, formatter, install, runtime startup, authentication operation or VM launch was performed. No files were written. Protected workspaces, host authentication databases, receipts, stage state, local settings and private prompt logs were not used as sources.

## Executive Findings

1. **The required process boundary is correct.** omp's SDK is explicitly Bun-only, not a Node.js SDK. Node must control a Bun subprocess through RPC rather than importing `@oh-my-pi/pi-coding-agent`. [SDK documentation][omp-sdk]
2. **The decided installer does not supply the decided skill layout by itself.** In GSD **1.18.0**, `--claude --local` installs `.claude/commands/gsd-*.md` and `.claude/agents/`; global Claude installation is the skills-based surface. omp independently discovers `.claude/skills/*/SKILL.md` as skills and `.claude/commands/*.md` as file commands. Therefore a successful local install or discovery of `gsd-plan-phase` as a file command does **not** satisfy `/skill:gsd-plan-phase` readiness. This is an explicit prerequisite/contract blocker, not permission to change versions, use the pi installer, dispatch bare commands or author substitute workflows. [GSD Claude capability][gsd-claude] · [Selected installer][gsd-installer] · [omp Claude discovery][omp-claude]
3. **Question-capable RPC needs the correct launch variant.** In omp **18.8.4**, `--mode rpc-ui` enables the built-in tool UI and wires it to the same JSON RPC bridge. Plain `--mode rpc` can expose extension dialogs but does not enable the built-in `ask` tool in the same way. `set_ask_dialog` only opts into a richer dialog payload; it does not create that missing tool capability. `rpc-ui` does not start a TUI or require a new graphical interface. [CLI session setup][omp-main] · [RPC UI wiring][omp-mode] · [Ask tool][omp-ask]
4. **Discovery is not runnable GSD compatibility.** omp intentionally excludes direct `.claude/agents` from task-agent discovery. GSD planning requires real, separate named researcher/planner/checker agents and refuses to collapse them inline. Skill discovery alone cannot establish those agents, Claude-style `Skill()`/`Agent()`/`AskUserQuestion` behavior, argument expansion or model inheritance. [omp agent discovery][omp-agents] · [GSD planning workflow][gsd-plan-workflow]
5. **There are three different outcomes:** command admission, prompt/run termination, and artifact-backed GSD workflow success. Even `prompt_result.status="completed"` with a settled session is not a GSD success certificate. A real waiting question is a live input request, not assistant prose that happens to end with a question mark. [RPC handlers][omp-mode] · [Prompt-result implementation][omp-results] · [Session settlement][omp-settle]

## Feature Landscape

### Table Stakes — Required for a Usable CLI

| Feature | Why expected | Complexity | Required behavior |
|---|---|---|---|
| Explicit startup and readiness | The operator must know whether anything can run | HIGH | Report prerequisite checking, sandbox creation, VM boot/round trip, workspace preparation and discovery separately. Do not report ready until the selected backend, live session and both required **skill** identities are verified. |
| Planning and execution as separate operations | The requested stage determines workflow and model | MEDIUM | Planning dispatches `/skill:gsd-plan-phase`; execution dispatches `/skill:gsd-autonomous`. Reject arbitrary workflow names, unsupported operations and unrelated model overrides. [Skill dispatch][omp-mode] |
| Correlated bidirectional JSON transport | Real workflows emit events and request human input | HIGH | Keep stdin open, read stdout continuously, preserve stderr separately, correlate responses by ID and handle events during outstanding requests. Observe advertised protocol/frame limits; malformed protocol data is a classified transport failure, not silent success. [RPC protocol][omp-rpc-docs] |
| Real pending-question display and answering | Human decisions are part of GSD | HIGH | Display complete question IDs, text, options, multi-select metadata and free-text choice; answer only the matching pending request in the owning session/stage. Handle cancellation and timeout invalidation. [Question schema][omp-types] · [Validation][omp-mode] |
| Live status | Requested settings are not effective behavior | MEDIUM | Show backend/tier, isolation label, stage identity, omp session ID, requested and selected model, observed request/message model identity, active questions, streaming/compaction/queue/async state, failure reason and owned resources. [State schema][omp-types] |
| Truthful stage outcomes | A polite final response can conceal an early exit or missing artifacts | HIGH | Preserve RPC status separately from session settlement and GSD outcome. Require the intended phase artifacts and actually exercised checks before declaring workflow completion. [Prompt results][omp-results] · [GSD planning][gsd-plan-workflow] · [Autonomous routing][gsd-auto-workflow] |
| Abort and bounded shutdown | Waiting and failing sessions must remain controllable | HIGH | Expose aborting before confirmed aborted; keep reading responses/events, invalidate questions and terminate only owned children/resources when bounded cooperative cancellation fails. Report cleanup failures and remaining ownership. [Abort implementation][omp-mode] · [Session cancellation][omp-session] |
| Overlap/stale/malformed-input rejection | Concurrent mutation or misrouted answers can damage a workspace | MEDIUM | Reject overlapping stages against the same owned execution workspace; reject duplicate, cancelled, cross-session, post-terminal and malformed answers before forwarding. The transport's ability to queue prompts is not authorization for overlapping product stages. [Queue contract][omp-types] · [Question handling][omp-mode] |
| Local evidence and credential hygiene | The sandbox must not take over the host login or fabricate trust | HIGH | Broker/gateway-only provider access; redacted minimal evidence; visibly `attestation=mock` on successful, failed and degraded runs; never expose credentials or treat a source observation as inference proof. This is the [project contract](../PROJECT.md), not a feature claimed by omp. |

### Already-Requested Differentiators — Mandatory, Not Added Scope

| Feature | Value proposition | Complexity | Notes |
|---|---|---|---|
| Rust/Anda entrypoints with a real VM/external-sandbox path | One owned local control surface over the complete requested chain | HIGH | `run_gsd_stage`, `answer_question`, and `sandbox_status`; Anda does no separate inference. An omp-only host run cannot stand in for the VM/sandbox path. [Project scope](../PROJECT.md) |
| Stage-specific, inspectable model routing | Planning uses Sol; execution uses Luna | MEDIUM | Enforce `openai-codex/gpt-6.1-sol` versus `openai-codex/gpt-6-luna`; disclose only the permitted `zai/glm-5.3` fallback without changing stage identity. State/configuration observations and actual model-backed execution evidence remain different. [Model RPC][omp-mode] · [Project scope](../PROJECT.md) |
| Authorized degraded execution with honest isolation labels | Local work remains possible without unavailable OpenShell compute | HIGH | OpenShell, bwrap and agentOS-local are not security-equivalent. Carry selected tier and limits through status and final evidence; do not retry the recorded kernel-faulting VM launch. [Project observations](../PROJECT.md) |
| Genuine GSD outputs rather than slash-command-shaped prompts | The system proves its core value, not just process connectivity | HIGH | Preserve phase identity, named roles, planning/checker gates, execution artifacts and verification routing. Missing runtime compatibility is a blocker, not permission to invent replacement skill content. [GSD planning][gsd-plan-workflow] · [GSD autonomous][gsd-auto-workflow] |

### Deliberately Excluded Anti-Features

| Anti-feature | Why superficially attractive | Why excluded | Required alternative |
|---|---|---|---|
| Browser UI, dashboards, accounts, telemetry platform | Easier-looking observability/product expansion | Not requested; increases security and lifecycle scope | Operational CLI/Anda tools, streamed events and minimal local evidence only. |
| Importing the omp SDK into Node | Avoids a subprocess | Official SDK requires Bun | Node controls sandboxed Bun RPC. [SDK][omp-sdk] |
| `omp -p`, bare `/gsd-*`, or the pi GSD installer | Can produce convincing-looking output quickly | Substitutes a different execution/discovery contract | Preserve the selected Claude install and `/skill:` boundary; fail readiness until the discovered contract mismatch is explicitly resolved. [GSD layout][gsd-claude] · [omp skill dispatch][omp-mode] |
| Treating file/extension commands as skills | Makes readiness pass with existing command files | `source=file`/`extension` and `source=skill` represent different dispatch paths | Require exact `skill:gsd-plan-phase` and `skill:gsd-autonomous`, each `source=skill`. [Available commands][omp-commands] |
| Auto-answering an empty answer using the recommended option | Appears convenient for unattended operation | Silently converts absence of a human answer into consent | Require a valid explicit answer; preserve cancellation/timeout semantics. `recommended` is presentation/default metadata. [Ask validation][omp-mode] |
| Host authentication database copies or new accounts | Makes sandbox login seem easy | Violates credential ownership and refresh-token safety | Existing documented broker/gateway boundary only. [Project scope](../PROJECT.md) |
| Hidden fallback or automatic stage replay | Makes failures disappear | A missing assistant/model observation does not prove no work occurred | Preflight/select an allowed fallback safely; never automatically rerun a possibly admitted/partially executed stage. [Prompt admission][omp-mode] · [Project failure semantics](../PROJECT.md) |
| Completion based on acknowledgment, `agent_end`, final prose, process exit or a mock receipt | Cheap to implement | None verifies the GSD phase goal or executed checks | Correlated prompt result + settled state + phase-specific evidence. [Prompt implementation][omp-results] · [Settlement][omp-settle] |
| New compatibility shims, substitute workflows or collapsed planner/checker roles | Hides Claude/omp incompatibility | Changes the requested workflow and invalidates independent quality gates | Name the missing packaging/dispatch contract, keep it blocking, and resolve it explicitly without claiming out-of-box support. [Planning requirements][gsd-plan-workflow] |
| Privilege changes, container/global installs or production TEE claims | Can make local setup or security claims look stronger | Explicitly outside authorized scope | Owned local preparation; disclose degraded isolation and mock attestation. [Project scope](../PROJECT.md) |

## Source-Backed Discovery and Installer Contract

### What Exactly GSD 1.18.0 Installs

The required installer invocation is:

```sh
npx @opengsd/gsd-core@1.18.0 --claude --local
```

Run it only inside the owned sandbox workspace with Node 24 available. `--claude` plus `--local` selects runtime/location without requiring the interactive runtime/location chooser. Published package metadata requires Node `>=24.0.0` and npm `>=10.0.0`. [Package metadata][gsd-package] · [Installer][gsd-installer]

The selected Claude capability and inspected installed installer specify:

- Local target: `<cwd>/.claude`.
- Local command surface: flat `.claude/commands/gsd-<name>.md` files, including planning and autonomous entrypoints.
- Local agent surface: `.claude/agents/gsd-*.md`, plus the supporting `gsd-core` workflows/references/tools tree.
- Global Claude surface: `skills/gsd-*/SKILL.md`, produced with the Claude command-to-skill converter.
- The Claude-local install branch removes stale `skills/gsd-*` directories from prior local layouts. It is not a local skills installation branch that merely needs a Claude-version probe.
- Local file references are installation-location-specific: the installer rewrites references using the resolved local target path. Copying a cached `.claude` tree to a different sandbox path is not proven relocatable.
- Installer source declares local hook/settings output in `settings.local.json`; that private file was **not inspected**. Presence of those Claude settings does not establish that omp consumes Claude's hook/settings dialect.
- `--no-legacy-cleanup`, used by existing `src/core/gsd.ts`, skips the separate legacy-install scan; it does **not** change Claude-local command layout or disable every local migration/stale-skill removal. An isolated owned HOME/workspace remains necessary.

Sources: [Claude capability][gsd-claude] and [selected package installer][gsd-installer]. The broader [skills-migration feature description][gsd-migration] is not sufficient to override the selected release's explicit local/global capability and installer branches.

**Blocking consequence:** The pinned local installer, by itself, cannot be described as having installed the required two project-local omp skills. Do not mark that acceptance criterion satisfied based on command-file existence. A source-preserving, authorized packaging/discovery resolution is required; changing the pin, installer family, invocation surface or workflow bodies is not an implicit research decision.

### What omp Advertises

`get_available_commands` returns:

```ts
{
  id?: string;
  type: "response";
  command: "get_available_commands";
  success: true;
  data: {
    commands: Array<{
      name: string;
      aliases?: string[];
      description?: string;
      input?: { hint?: string };
      subcommands?: Array<{ name: string; description?: string; usage?: string }>;
      source: "builtin" | "skill" | "extension" | "custom" | "mcp_prompt" | "file";
    }>;
  };
}
```

The required metadata names are **`skill:gsd-plan-phase`** and **`skill:gsd-autonomous`**, with `source: "skill"`; metadata names omit the leading `/` used in prompt text. A discovered `gsd-plan-phase` with `source: "file"` is not equivalent. The current core's `startsWith("gsd-")` filter and execute-phase requirement are therefore the wrong readiness predicate. [Command builder][omp-commands] · [Wire types][omp-types]

omp's skill-command branch parses `/skill:<name>`, checks `skills.enableSkillCommands`, looks up that bare skill name in the session's loaded skills, reads its body and dispatches a user-attributed skill prompt. It is not an independent RPC command type called `skill`. Project Claude skill discovery scans `.claude/skills` separately from `.claude/commands`. [Skill dispatch][omp-mode] · [Skill loader][omp-skills] · [Claude provider][omp-claude]

`get_available_commands` does not return the skill's source path, named-agent availability, all workflow dependencies or successful execution evidence. Readiness must also establish that the loaded bodies/references come from the owned selected-version workspace, not an unrelated same-named host skill. Actual skill-message details carry provenance such as name/path/arguments; that is useful source identity evidence, not phase-success evidence. [Skill message construction][omp-skills]

### Runnable GSD Requires More Than Two Menu Entries

GSD planning names `gsd-phase-researcher`, `gsd-planner` and `gsd-plan-checker` as distinct agents. omp 18.8.4 intentionally skips direct cross-harness `.claude/agents` directories; it discovers native `.omp/agents` and selected extension/plugin agent roots instead. This must be resolved separately from skill registration. Do not count files under `.claude/agents` as proof of named dispatch. [Planning roles][gsd-plan-workflow] · [Agent discovery][omp-agents]

Two additional contracts require implementation-time proof:

- **Arguments:** GSD command bodies use `<arguments>$ARGUMENTS</arguments>`. omp's skill builder strips frontmatter and wraps the body with a skill directory and trailing `User: ...` arguments; this inspected path does not implement Claude command-template `$ARGUMENTS` substitution. **[INFERENCE]** Argument interpretation cannot be assumed equivalent just because the model sees both strings. Prove phase/flag/task identity on a disposable real workflow. [GSD entrypoint][gsd-plan-command] · [omp builder][omp-skills] · [Wrapper template][omp-skill-template]
- **Tools and permissions:** Loading skill Markdown is not installing Claude Code's `Agent`, `Skill`, or `AskUserQuestion` APIs. The inspected omp ask tool is named `ask`; its availability and RPC bridge are separate. The skill dispatch path uses the body, so Claude `allowed-tools` frontmatter is not a sandbox authorization boundary. Prove real role dispatch and live question behavior using the actual available tools, and keep filesystem/network/process restrictions in the selected execution policy. [Skill construction][omp-skills] · [Ask tool][omp-ask] · [GSD workflow][gsd-plan-workflow]

These are explicit compatibility gates. The absence of a live run during this research is not evidence that a model will or will not adapt successfully.

## Interactive RPC Feature Contract

### Runtime and Startup

Use Node only as the controller. Run the selected omp version under Bun inside the selected external sandbox. For the built-in question-tool acceptance path, use **`--mode rpc-ui`** in 18.8.4; it speaks the same JSON RPC protocol and does not launch a terminal UI. `set_ask_dialog` is still required for the batch-question schema. [SDK][omp-sdk] · [CLI][omp-main] · [Bridge][omp-mode]

Wait for `ready`, respect its advertised protocol/frame limits, then perform discovery, ask-dialog opt-in and state/model checks. Keep the stream reader active for the full session, including while prompts, dialogs and aborts are outstanding. A production transport must deliberately support the selected protocol's size/framing contract rather than treating arbitrary line splitting or JSON parse failures as harmless noise. [RPC documentation][omp-rpc-docs] · [RPC implementation][omp-mode]

### Outbound Payloads

These are **schema examples, not captured runtime exchanges**. Send each object as one compact JSON line after the corresponding prerequisite gates have passed:

```json
{"id":"commands-1","type":"get_available_commands"}
{"id":"dialog-1","type":"set_ask_dialog","enabled":true}
{"id":"state-1","type":"get_state"}
{"id":"plan-1","type":"prompt","message":"/skill:gsd-plan-phase 1"}
```

An execution-stage example is:

```json
{"id":"execute-1","type":"prompt","message":"/skill:gsd-autonomous --only 1 --interactive"}
```

The prompt message is data. User task text, answers, Unicode, newlines and shell-looking strings must not become a shell program, a new JSON frame, an arbitrary workflow selector or an unapproved path. The phase/flag examples select a bounded disposable phase; they are not permission to run against unrelated repositories. [Prompt types][omp-types] · [Skill parsing][omp-skills] · [Autonomous flags][gsd-auto-command]

| Operation | Wire request | Meaning of a successful response |
|---|---|---|
| Discovery | `{id,type:"get_available_commands"}` | Returns heterogeneous command metadata; it proves neither execution nor required skill provenance. |
| Rich ask opt-in | `{id,type:"set_ask_dialog",enabled:true}` | Returns `data.enabled`; enables rich ask payloads where the UI-capable tool path exists. |
| State | `{id,type:"get_state"}` | Snapshot of the active session, not a workflow outcome. |
| Prompt | `{id,type:"prompt",message}` | Submission/admission acknowledgment, not completion. |
| Select model | `{id,type:"set_model",provider,modelId}` | Selects a registry model or returns not-found/error; it does not prove a provider call succeeded. |
| Abort | `{id,type:"abort"}` | Cooperative session abort handler completed; application ownership/cleanup still require their own accounting. |

Sources: [RPC types][omp-types] and [handlers][omp-mode].

Important exception: a locally handled prompt can return `response.data.agentInvoked === false` and owe **no** `prompt_result` in the inspected handler. Do not leave an infinite terminal-result waiter on that path. For these GSD stages, such local handling is not a successful model-backed workflow. [Prompt handler][omp-mode]

### Live Question and Answer Schemas

The rich request has:

```ts
{
  type: "extension_ui_request";
  id: string;                    // dialog request ID
  method: "ask";
  questions: Array<{
    id: string;                  // question ID within that dialog
    question: string;
    header?: string;
    options: Array<{ label: string; description?: string; preview?: string }>;
    multi?: boolean;
    recommended?: number;       // zero-based option index
  }>;
  timeout?: number;
}
```

The answer is:

```ts
{
  type: "extension_ui_response";
  id: string;                    // original dialog ID, not a command ID
  answers: Array<{
    id: string;                  // matching question ID
    selectedOptions: string[];   // exact option labels
    customInput?: string;
  }>;
}
```

Return exactly one answer per question, in request order, with matching IDs. Unknown labels, duplicate selected labels, wrong counts/IDs and non-string custom input are rejected by omp. A single-select question must not carry both a selected option and nonempty custom text, nor multiple selected options. `multi:true` supports multiple selections; an explicit empty multi-selection can be meaningful. Free text is supported without inventing an `Other` option label in `selectedOptions`. Preserve descriptions, previews, headers and multi-select metadata when presenting the question. [Types][omp-types] · [Validation][omp-mode] · [Ask execution][omp-ask]

Other input methods use the same dialog ID:

| Method | Response payload |
|---|---|
| `select` | `{type:"extension_ui_response",id,value:string}` using a valid option |
| `input` / `editor` | `{type:"extension_ui_response",id,value:string}` |
| `confirm` | `{type:"extension_ui_response",id,confirmed:boolean}` |
| Explicit cancellation | `{type:"extension_ui_response",id,cancelled:true}` |

`timedOut:true` is reserved for a real timeout, not a substitute for declining. A server `method:"cancel"` frame identifies the cancelled dialog with **`targetId`**, not the cancel frame's own `id`. Non-input methods such as `notify` and `setStatus` are not pending questions. [UI schemas][omp-types] · [Dialog lifecycle][omp-mode]

The host must bind each dialog to **stage ID + actual omp session/process generation + dialog ID**. There is no stage/session identity inside a bare extension UI response to rescue a host that sends it down the wrong pipe. Reject invalid answers before forwarding: omp consumes the pending resolver when processing an answer and malformed input can fail the waiting tool rather than leave a friendly correction loop. Unknown/late IDs are ignored by omp's side-channel handler, so an explicit stale-answer rejection is a host product responsibility. [Control-frame and dialog handlers][omp-mode]

### Waiting Is Not Completion, and Silence Is Not Transport Failure

Enter `waiting` only for a genuinely pending supported input request. Keep command/state/abort handling alive during that wait. In the selected omp settings, `ask.timeout` defaults to **0** (disabled); when a real ask timeout is enabled, its implementation can auto-select recommended options and cancel the host presentation. Preserve the no-auto-answer interactive contract explicitly instead of inheriting a surprising timer. Stage name `plan` does not itself mean omp's separate internal plan mode is enabled. [Ask timeout setting][omp-interaction-settings] · [Ask execution][omp-ask] · [RPC timeout behavior][omp-mode]

A human may take longer than an RPC command deadline. Apply bounded transport/admission/cancellation/shutdown waits, but do not classify an intact session with a live untimed dialog as a broken transport merely because its human has not answered. Continue to report the waiting reason and permit abort.

If the assistant merely prints a numbered list/question and then emits a terminal result, there is **no live dialog to answer**. Do not fabricate a question ID or continue calling that a live tool wait. Surface the workflow as stopped/incomplete with its actual reason. GSD's `--text` mode deliberately uses ordinary text instead of `AskUserQuestion`; it cannot substitute for acceptance of live RPC question/answer behavior. `--interactive` controls whether autonomous discuss asks the operator rather than using smart-discuss defaults. [GSD planning text mode][gsd-plan-workflow] · [Autonomous guide][gsd-auto-guide]

### What a Terminal Result Proves

The selected schema is:

```ts
{
  type: "prompt_result";
  id?: string;
  agentInvoked: boolean;
  status: "completed" | "aborted" | "error";
  error?: {
    message: string;
    provider?: string;
    model?: string;
    httpStatus?: number;
    retryable: boolean;
  };
  sessionSettled: boolean;
}
```

A prompt result can be emitted when a run **yields**, while background jobs can still wake the session. If `sessionSettled:false`, keep the session alive and observe `session_settled`/`get_state.isSettled` and any subsequent work; do not close it immediately. `agent_end` belongs to a run, not a correlated product stage. The implementation derives `completed` from an assistant stop reason and even has a completed default when no assistant message is found; it does not inspect GSD files or verification checks. [Types][omp-types] · [Prompt tracker][omp-results] · [Settlement predicate][omp-settle]

| Observation | What it establishes | What it does not establish |
|---|---|---|
| Successful command response | Command accepted/handled according to that command | GSD started or finished |
| `get_state.model` | Active selected provider/model | Real inference succeeded, or every subagent used that model |
| `agentInvoked:true` | Submission reached the agent path | Valid phase, produced artifacts, successful provider request or independent checker execution |
| Assistant provider/model metadata | Model identity attached to observed assistant messages | Successful generation on its own; error/aborted messages can also carry metadata |
| `prompt_result.status="completed"` | The tracked run/prompt ended normally under RPC's criteria | Requested phase ran; no early exit, unresolved question, skipped verification or blocker |
| `sessionSettled:true` / `isSettled:true` | No live/admitted/scheduled/queued/pending wake-up work under the predicate | GSD requirements or phase verification passed |
| Exit code zero / mock receipt | Process exited normally / local record exists | Inference, isolation enforcement, hardware attestation or workflow success |
| Fresh expected phase artifacts plus actual required checks | Evidence toward the defined planning/execution outcome | Other unexercised tiers, production deployment or hardware confidentiality |

The host should preserve separate **prompt status**, **session settlement**, and **workflow outcome/reason** in status and evidence. These field concepts are a proposed product requirement, not a claim that omp supplies a GSD outcome object.

## Model, Abort and Stale-Behavior Requirements

### Model Routing

- Planning stage: `openai-codex/gpt-6.1-sol`.
- Execution stage: `openai-codex/gpt-6-luna`.
- Only permitted fallback: `zai/glm-5.3`, visibly disclosed with original stage identity and reason.

These identities come from the [project contract](../PROJECT.md), not a verified provider catalog. Use explicit model selection and compare live `get_state.model.provider`/`.id` before dispatch. Record real model-backed activity separately from requested/selected metadata, including the model attached to errors. `get_available_models` and `set_model` are useful registry/selection controls, not inference tests. [Model handlers][omp-mode]

GSD can pass role-specific model selectors to agents, and its workflow explicitly distinguishes inherited models from nonempty selectors. Top-level Sol/Luna selection alone therefore does not establish subagent routing. omp also has its own configurable model-fallback behavior. Constrain those mechanisms to the allowed project routing; do not inherit unrelated host fallback chains or Claude aliases accidentally. [Planning model handling][gsd-plan-workflow] · [omp fallback settings][omp-model-settings]

Do not use `modelsSeen.size === 0` as proof that a failed prompt had no effects. Select/disclose an allowed fallback before unsafe admission where possible; after uncertain admission, fail truthfully and require an explicit operator-controlled next action rather than replaying the stage. No automatic application-level replay/retry framework is requested.

### Abort and Ownership

Send `{id,type:"abort"}`, expose `aborting`, and keep reading the original prompt's terminal/cancellation signals. The handler invokes session abort and waits for agent idleness; session abort cancels prompt setup, retries and operations such as bash/eval as part of its implementation. This is still not proof that every owned sandbox resource has been released. [RPC abort][omp-mode] · [Session abort][omp-session]

Invalidate pending dialogs on server cancellation, confirmed stage termination, session/process replacement, disconnect and shutdown. Duplicate/late answers must get a host-visible stale error rather than disappear into omp's ignored-ID behavior. Do not report `aborted` solely because the host assigned that status before sending a command. If cancellation does not settle within the configured bound, escalate termination within owned resources and report whether cancellation/cleanup failed and what remains.

Close stdin only as part of deliberate session shutdown. omp rejects pending host callbacks and drains/disposes on EOF; that teardown path is not a successful workflow outcome. [RPC EOF handling][omp-mode]

## Runnable Phase Outcome Requirements

### Planning Acceptance

Use a disposable owned GSD project with an actual incomplete roadmap phase and requirements, not only installed command files. Submit the required planning skill with an explicit valid phase number and a concrete task.

A clean planning acceptance requires:

1. Actual required skill dispatch in the sandbox, with selected Sol routing and evidence of real model-backed activity.
2. A phase validated against the fixture roadmap and the intended task/arguments preserved.
3. Distinct required researcher/planner/checker role executions under the selected workflow where enabled/applicable; no invented named-dispatch proof or inline role substitution.
4. Fresh executable `*-PLAN.md` artifacts in the correct phase directory, with valid frontmatter, actionable tasks, dependencies, acceptance criteria and exact runnable verification commands.
5. Requirement/decision coverage gates satisfied and checker success actually observed for the clean acceptance case. An override or skipped checker must remain explicitly reported, not promoted to a clean verified plan.
6. The selected planning status/artifacts updated, with a settled session and no unresolved pending dialog.

The selected workflow expressly supports early exits, research-only/view modes, context-gathering handoffs, checkpoints, skips and overrides. Those may be valid workflow responses but are not evidence that this requested full planning outcome occurred. [Entrypoint][gsd-plan-command] · [Workflow gates and success criteria][gsd-plan-workflow]

### Execution Acceptance

Use `/skill:gsd-autonomous --only 1 --interactive` on a fresh eligible disposable phase for the bounded acceptance case. `--only` restricts it to one phase and skips milestone audit/complete/cleanup; `--interactive` exercises real discuss input instead of assuming a question will appear in default autonomous mode. An already-complete phase can exit cleanly without doing work and is not a positive execution test. [Flags and prerequisites][gsd-auto-command] · [Guide][gsd-auto-guide] · [Workflow][gsd-auto-workflow]

A clean execution acceptance requires:

1. Actual autonomous skill dispatch on Luna, with any permitted fallback disclosed and actual model-backed activity distinguished from selection.
2. Correct phase/flag/task identity and working supporting workflow/agent dispatch.
3. Observable intended task changes in the owned workspace, plan summaries and canonical phase verification artifacts; not only assistant text claiming changes were made.
4. The actual produced plan's verification commands run in the selected runtime/backend, with their real results checked. For a tiny disposable task, a suitable observable is a script whose printed marker is chosen through the live question; running that script must produce the chosen marker rather than a canned receipt.
5. Canonical verification `passed` for the clean case and consistent phase/state/roadmap progress. `human_needed`, `gaps_found`, deferred verification, skipped phase, user stop, `needs_human`, absent artifacts or an unsupported verification status must remain distinct unsuccessful/incomplete outcomes.
6. Required session settlement and owned-resource cleanup accounted for without advancing unrelated phases or publishing anything.

GSD autonomous explicitly routes on canonical verification and can stop normally with human/deferred/blocker work remaining. Its normal return is therefore not equivalent to this acceptance. [Post-execution routing and blocker handling][gsd-auto-workflow]

### No False Inference Proof

A protocol fixture, local receipt, command listing, initialized `.planning` tree, assistant model field, namespace probe or compiled program does not prove successful provider inference through Rust/core/VM/sandbox/omp. The eventual end-to-end record must identify which real model-backed workflow, backend and observable task/check were exercised. All local attestation remains mock, and unexecuted OpenShell/policy/production/TEE behavior remains unverified. [Project evidence contract](../PROJECT.md)

## Acceptance Scenarios

All scenarios below are requirements for later implementation verification; **none was executed by this research**.

| Scenario | Observable pass condition |
|---|---|
| Prerequisite/readiness failure | Missing runtime, unavailable tier, VM integration failure or missing skill produces a specific failure state; no stage is admitted as ready. |
| Installer/discovery mismatch | A workspace containing only Claude-local `commands/gsd-*.md` fails the required skill readiness gate even if `get_available_commands` advertises file commands. The chosen contract resolution is documented before positive readiness can pass. |
| Wrong provenance or command source | Same-named host/file/extension command cannot masquerade as the required selected-version project-local skill. |
| Named-role compatibility | Actual required GSD roles are discoverable/runnable through the selected omp host mechanism; files under `.claude/agents` alone do not pass. Missing roles produce an honest blocker. |
| Exact planning payload | Required `/skill:gsd-plan-phase` receives the explicit phase and supplied task as data, uses the selected model, and produces the planning outcome above. |
| Real live question | A genuine `extension_ui_request` blocks workflow progress in waiting; state/abort remain responsive; no default answer is sent; the matching explicit answer reaches the tool and affects the workflow. |
| Multi-question/free-text input | Per-question IDs/order, exact labels, multi-select and custom text are preserved; malformed count/ID/label/type combinations are rejected before forwarding. |
| Prose-only question | A normally ended assistant message asking a question does not fabricate a pending dialog or become successful workflow completion. |
| Stale answer matrix | Duplicate answer, cancelled target, wrong stage/session generation, restarted client and post-terminal answer all produce explicit stale errors and no forwarded answer. |
| Overlap and operation validation | A second conflicting stage, arbitrary skill/model, unsupported operation, path traversal and malformed input are rejected without running or mutating another stage. |
| Admission/terminal distinction | Acknowledgment and local `agentInvoked:false` paths cannot become completed GSD work or leave indefinite waiters. |
| Async settlement | `prompt_result` with `sessionSettled:false` keeps the session alive until remaining work settles or fails; later activity is not lost through early close. |
| Autonomous positive phase | A fresh single phase runs the requested workflow, produces real task changes/summaries, passes actual checks/canonical verification, and does not trigger milestone lifecycle. |
| Autonomous non-success | Already-complete/no-op, deferred human verification, gaps, skipped phase and user stop remain visibly distinct from a successful executed phase. |
| Model/fallback evidence | Requested, selected and observed identities remain distinguishable; Sol/Luna routing is enforced, permitted GLM fallback is disclosed, and uncertain failure causes no automatic stage replay. |
| Abort while running and waiting | Aborting is visible; active dialog is invalidated; cooperative abort response/terminal state or bounded owned-process escalation is observed; late answers are stale. |
| Framing and child failure | Prompt/answer newlines and shell-looking text remain data; stdout/stderr remain separate; malformed/truncated JSON, broken pipe and child exit fail correlated outstanding work rather than producing false completion. |
| Shutdown and cleanup failure | Only owned resources are stopped/deleted; normal and failed cleanup report exact remaining ownership; all local success/failure/degraded evidence says `attestation=mock`. |
| Full required path | At least one real question-bearing stage demonstrates the Rust/Anda → core → VM/external-sandbox integration → Bun omp/GSD path, actual task outcome, routing and cleanup; a host-only omp run is insufficient. |

## Feature Dependencies

```text
Owned local resources + broker/gateway boundary + selected tier
  └─ requires → real agentOS VM/external-sandbox workspace round trip
       └─ requires → Node/Bun/selected omp + exact GSD installation
            └─ requires → explicit installer/skill-layout resolution
                 ├─ required skill discovery and owned provenance
                 └─ real named-agent/tool/argument compatibility
                      └─ UI-capable RPC + ask opt-in + model selection
                           └─ correlated stage run and question lifecycle
                                └─ prompt result + session settlement
                                     └─ artifact-backed GSD outcome
                                          └─ bounded shutdown/ownership evidence

Status, stale rejection, input validation and cancellation are cross-cutting.
Artifact validation cannot compensate for an unowned or credential-leaking path.
A lower-tier success cannot validate unavailable OpenShell or hardware TEE behavior.
```

### Roadmap Dependency Notes

- Resolve the selected-version packaging/discovery contradiction **before** promising a successful stage. A command-list-only milestone would otherwise validate the wrong surface. [GSD layout][gsd-claude] · [Command sources][omp-commands]
- Prove named roles and question-capable RPC before using a GSD return as a milestone gate. The selected Claude install and plain RPC launch are not sufficient. [Agent discovery][omp-agents] · [CLI setup][omp-main]
- Implement status and failure/ownership reporting alongside startup/transport, not after positive workflow runs. Waiting, abort and teardown must be observable during the first real run.
- Verify actual plan/execution artifacts only after prompt correlation and settlement are correct; otherwise a background continuation may still change them. [Settlement][omp-settle]

## First Usable Delivery and Prioritization

The first usable delivery includes **the complete requested local contract**, not a narrowed transport demo. Sequence work to reduce uncertainty, but do not silently defer mandatory integration, questions, outcome checking or cleanup.

| Feature group | User value | Implementation cost | Priority/order |
|---|---|---|---|
| Owned backend/auth/VM/workspace prerequisites and honest status | HIGH | HIGH | P1 — foundational path |
| Exact install, skill provenance and real workflow/agent compatibility | HIGH | HIGH | P1 — readiness blocker |
| Correlated UI-capable RPC, fixed models and validated inputs | HIGH | HIGH | P1 — interactive stage foundation |
| Real questions, stale/overlap rejection, abort and bounded failure | HIGH | HIGH | P1 — required safety/functionality |
| Runnable planning/autonomous outcomes, settlement and local evidence | HIGH | HIGH | P1 — core value acceptance |
| Operational command documentation and owned cleanup | HIGH | MEDIUM | P1 — usable hand-off |

**After validation:** no additional UI/accounts/telemetry/retry platform is proposed. OpenShell behavior that cannot be exercised with authorized compute stays explicitly unvalidated rather than being represented as covered by degraded-tier success. Production confidential infrastructure remains outside local application acceptance. [Project scope](../PROJECT.md)

## Existing Source Gaps Relevant to Features

These are static observations, not reproduced runtime failures.

| Location | Observation | Required product correction |
|---|---|---|
| `src/core/gsd.ts` | Installs and caches Claude-local command files; comments describe them as `/gsd-*`; copies cached `.claude` trees | Distinguish commands from skills, resolve the pinned installer mismatch and establish installed-path/reference validity without host config copying. |
| `src/core/core.ts:#probeOmp` | Filters bare `gsd-*` names and requires execute-phase instead of autonomous | Require exact `skill:` metadata/source and required autonomous identity; verify supporting runtime compatibility separately. |
| `src/core/core.ts:#spawnOmp` | Uses plain `--mode rpc` | For the built-in live-question path use the version's UI-capable RPC wiring, with no new TUI product surface. |
| `src/core/core.ts:runStage` | Builds bare `/${gsdCommand}`, allows model override and does not reject workspace-overlapping stages | Enforce the two stage/skill/model identities, preserve task data and reject conflicting overlap. |
| `src/core/core.ts:#drive` | Treats completed prompt_result as completed stage and closes immediately | Separate workflow outcome from RPC status; honor pending async settlement and phase evidence. |
| `src/core/core.ts:#drive` | Replays with fallback when the result failed and no model metadata was collected | Remove the zero-models-means-no-effects assumption; no uncertain automatic replay. |
| `src/core/omp-rpc.ts:prompt` | Always waits for prompt_result after acknowledgment | Handle local `data.agentInvoked:false` explicitly; it is not model-backed GSD success. |
| `src/core/omp-rpc.ts:answer` | Empty input chooses recommended/default options; missing per-question input can inherit a shared answer | Require explicit method-appropriate validated answers; preserve full question schema and no-consent-by-default semantics. |
| `src/core/omp-rpc.ts:#onData` | Malformed JSON becomes ignored noise | Classify protocol corruption and settle/fail affected work safely; do not erase evidence of a broken stream. |
| Abort/status handling | Abort status is assigned before observed completion; errors are swallowed; live state is not exposed as the complete requested lifecycle | Distinguish aborting/aborted/failure, expose actual session state and account for outstanding ownership. |

Local source references: [GSD bootstrap](../../src/core/gsd.ts), [RPC client](../../src/core/omp-rpc.ts), [core](../../src/core/core.ts), [routing](../../src/core/config.ts).

## Key Blockers and Confidence

| Item | Finding | Confidence / exact unresolved contract |
|---|---|---|
| Claude-local installer versus required skills | Selected release installs commands locally and skills globally | HIGH source confidence. A permitted, source-preserving project-local skill packaging/discovery resolution is missing; do not silently substitute a different installer or command surface. |
| Named GSD agents in omp | Direct `.claude/agents` intentionally excluded | HIGH source confidence. The authorized actual role-discovery/dispatch mechanism for these selected Claude workflow assets remains unresolved. |
| Claude workflow dialect on omp | Skill body wrapping is not Claude tool installation or proven `$ARGUMENTS` expansion | HIGH for inspected source behavior; MEDIUM/UNVALIDATED for full compatibility. Requires real phase/role/question execution, not menu visibility. |
| Question-capable launch | `rpc-ui` enables tool UI; plain rpc does not provide equivalent built-in ask capability | HIGH source confidence. Live sandbox acceptance remains unexecuted. |
| Wire schemas and settlement | Exact command/dialog types and yielded-versus-settled behavior found | HIGH source confidence. Host implementation still needs the stated behavior and real verification. |
| Planned model availability/auth and fallback | Sol/Luna/GLM identities are required, not tested availability | UNVALIDATED runtime prerequisite. Prove selected-model reachability through the approved broker/gateway without accessing token storage. |
| Complete wrapper/VM/backend workflow | Existing files and local observations do not establish it | UNVALIDATED. Requires the defined disposable end-to-end stage and actual observable outcome. |
| OpenShell/hardware claims | Recorded local VM fault and absent hardware proof are already supplied constraints | Do not retry for confirmation; no production or hardware-security acceptance follows from this research. |

## Sources

### Product and Local Source

- [Supplied project contract](../PROJECT.md)
- [Current GSD bootstrap](../../src/core/gsd.ts)
- [Current RPC client](../../src/core/omp-rpc.ts)
- [Current core lifecycle/stage logic](../../src/core/core.ts)
- [Version/model pins](../../src/core/config.ts)

### Official and Selected-Version Sources

[omp-sdk]: https://omp.sh/docs/sdk
[omp-rpc-docs]: https://omp.sh/docs/rpc
[omp-release]: https://github.com/can1357/oh-my-pi/tree/v18.8.4
[omp-types]: https://github.com/can1357/oh-my-pi/blob/v18.8.4/packages/coding-agent/src/modes/rpc/rpc-types.ts
[omp-mode]: https://github.com/can1357/oh-my-pi/blob/v18.8.4/packages/coding-agent/src/modes/rpc/rpc-mode.ts
[omp-commands]: https://github.com/can1357/oh-my-pi/blob/v18.8.4/packages/coding-agent/src/slash-commands/available-commands.ts
[omp-claude]: https://github.com/can1357/oh-my-pi/blob/v18.8.4/packages/coding-agent/src/discovery/claude.ts
[omp-skills]: https://github.com/can1357/oh-my-pi/blob/v18.8.4/packages/coding-agent/src/extensibility/skills.ts
[omp-main]: https://github.com/can1357/oh-my-pi/blob/v18.8.4/packages/coding-agent/src/main.ts
[omp-ask]: https://github.com/can1357/oh-my-pi/blob/v18.8.4/packages/coding-agent/src/tools/ask.ts
[omp-agents]: https://github.com/can1357/oh-my-pi/blob/v18.8.4/packages/coding-agent/src/task/discovery.ts
[omp-results]: https://github.com/can1357/oh-my-pi/blob/v18.8.4/packages/coding-agent/src/modes/rpc/rpc-prompt-results.ts
[omp-settle]: https://github.com/can1357/oh-my-pi/blob/v18.8.4/packages/coding-agent/src/modes/rpc/rpc-session-settle.ts
[omp-session]: https://github.com/can1357/oh-my-pi/blob/v18.8.4/packages/coding-agent/src/session/agent-session.ts
[omp-model-settings]: https://github.com/can1357/oh-my-pi/blob/v18.8.4/packages/coding-agent/src/session/settings.ts
[omp-interaction-settings]: https://github.com/can1357/oh-my-pi/blob/v18.8.4/packages/coding-agent/src/modes/settings.ts
[omp-skill-template]: https://github.com/can1357/oh-my-pi/blob/v18.8.4/packages/coding-agent/src/prompts/skills/user-invocation.md
[gsd-package]: https://registry.npmjs.org/@opengsd/gsd-core/1.18.0
[gsd-claude]: https://github.com/open-gsd/gsd-core/blob/v1.18.0/capabilities/claude/capability.json
[gsd-installer]: https://github.com/open-gsd/gsd-core/blob/v1.18.0/bin/install.js
[gsd-migration]: https://github.com/open-gsd/gsd-core/blob/v1.18.0/docs/features/claude-code-skills-migration.md
[gsd-plan-command]: https://github.com/open-gsd/gsd-core/blob/v1.18.0/commands/gsd/plan-phase.md
[gsd-auto-command]: https://github.com/open-gsd/gsd-core/blob/v1.18.0/commands/gsd/autonomous.md
[gsd-plan-workflow]: https://github.com/open-gsd/gsd-core/blob/v1.18.0/gsd-core/workflows/plan-phase.md
[gsd-auto-workflow]: https://github.com/open-gsd/gsd-core/blob/v1.18.0/gsd-core/workflows/autonomous.md
[gsd-auto-guide]: https://github.com/open-gsd/gsd-core/blob/v1.18.0/docs/how-to/run-phases-autonomously.md

- [omp SDK][omp-sdk] and [RPC overview][omp-rpc-docs] — official public starting points. High-level examples are not used to erase the more precise selected-release prompt/settlement contracts.
- [omp 18.8.4 types][omp-types], [handlers][omp-mode], [prompt tracker][omp-results] and [settlement][omp-settle] — exact interactive payload/lifecycle contracts.
- [omp command provenance][omp-commands], [Claude discovery][omp-claude], [skills][omp-skills] and [task agents][omp-agents] — discovery surfaces are not interchangeable.
- [GSD published 1.18.0 metadata][gsd-package], [Claude capability][gsd-claude] and [installer][gsd-installer] — version, runtime prerequisites and local/global installation distinction.
- [GSD planning workflow][gsd-plan-workflow], [autonomous workflow][gsd-auto-workflow] and [autonomous guide][gsd-auto-guide] — role separation, phase artifacts, verification, human/deferred stops and bounded single-phase behavior.

---
*Feature research for Ove's local omp sandbox CLI workflow. Source review only; no application acceptance is claimed.*
