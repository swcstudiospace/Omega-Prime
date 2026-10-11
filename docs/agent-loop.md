# The agent loop

Omega Prime runs the Hermes conversation loop with the Omp harness behavior
ports. `OmegaPrimeAgent` (`omega_prime/agent/runtime.py`) composes that same
loop (`omega_prime/agent/conversation_loop.py::run_conversation`) with a
`ToolRegistry` (`omega_prime/tools/registry.py`) and a model adapter; it does
not create a second agent or loop.

```mermaid
flowchart TB
    SYS["_install_system_prompt (once)"] --> HOOKS["_run_extension_hooks, then legacy before_model"]
    HOOKS -- stop verdict --> STOPP["Stop with verdict reason; no model call"]
    HOOKS -- continue --> CALL["model.complete via registered schemas"]
    CALL --> ROUND{"tool calls?"}
    ROUND -- calls --> DISP["turn_tool_round → ToolRegistry.dispatch"]
    DISP --> ROWS["Policy/approval checked; append handler result or refusal rows"]
    ROWS --> NEXT{"Tool-round verdict and output budget"}
    NEXT -- continue --> HOOKS
    NEXT -- break --> END["finalize_turn; emit/journal final rows; finish journal"]
    ROUND -- no calls --> TEXT["finish_text_response; emit/journal response rows"]
    TEXT --> PRIME{"Prime hooks present?"}
    PRIME -- no --> END
    PRIME -- yes --> GOAL["_prime_text_boundary: accounting and precedence gates"]
    GOAL -- continuation rows --> HOOKS
    GOAL -- stop --> END
    STOPP --> END
```

Interception order is fixed: `ExtensionHooks` callbacks first (registration
order, first `{"stop": True}` verdict wins, later hooks do not run), then the
single legacy `Agent.before_model` hook. A refusal (`approval_refused`,
`policy_refused`) describes only the most recent tool round — a refusal the
model recovered from in a later round does not make the turn terminal. See
[architecture](architecture.md#the-loop) for the matching state view and
[build-aesthetics](build-aesthetics.md#extension-contract) for the
registration contract.

## Native Prime provider

Prepare the pinned checkout as described in the [source-build instructions](../README.md#build-from-source-both-toolchains), then build the extension from the repository root:

```bash
cargo build --locked --manifest-path native/omega-prime-prime/Cargo.toml \
  --target-dir prime-agent/target
```

The public composition accepts a full camelCase Prime model descriptor, not
just a provider name. Descriptors come from
`omega_prime.prime_kernel.native.load_extension().ai.resolve_models()`.
Choose the descriptor and credentials appropriate to your provider:

```python
from pathlib import Path

from omega_prime.agent import OmegaPrimeAgent
from omega_prime.mcp_server import default_registry


def make_prime_agent(descriptor: dict, api_key: str) -> OmegaPrimeAgent:
    registry = default_registry(Path.cwd(), Path.home())
    return OmegaPrimeAgent.from_prime(
        descriptor,
        registry=registry,
        api_key=api_key,
        provider_options={"timeoutMs": 60_000},
        system_message="Use only the offered registry tools. Respect approvals.",
    )
```

Call `agent.run(user_message)` for a turn; `agent.messages` retains its session
history. An optional `roster` restricts offered tools. Model calls receive the
registered descriptions and parameter schemas; tool execution goes through
`ToolRegistry.dispatch`, including policy and approval refusals.

Tool-call arguments must be a JSON object. A decoded `dict` is used as is;
`None` and the empty string mean no arguments. Any other payload (truncated
JSON, a list, a number, `null`, whitespace-only text) is not run as a call:
its tool row is an `error:` row naming the problem, and the other calls of the
same round still execute.

`PrimeProviderModel` calls native `pa_ai::complete_simple`, which drains the
real provider stream. It converts text, embedded images, thinking, calls, and
results; it does not fetch remote image URLs. Plain callable tool maps are
rejected rather than assigned invented schemas. `prime_message` on assistant
history rows preserves the raw Prime wire message, including opaque signatures
and response IDs. JSON serialization retains that metadata; editing visible
content without matching metadata fails replay explicitly.

Pass a `CredentialBroker` instead of an explicit key when host approval and
per-call credential resolution are required. There is no ambient-key or
Python-provider fallback in this adapter. The Python adapter requires an API
key; managed AWS/Vertex credential modes are not integrated through that adapter.
Missing native bindings, provider errors, and aborted completions raise
`ProviderError`. Usage includes cache counters; failed calls clear stale usage.

`timeoutMs` covers completion and queued Codex session waiting. The bridge
releases the GIL, checks Python interrupts, and permits at most one second for
provider cancellation settlement. An unsettled cancellation is an explicit
failure, not success; it also prevents unsafe Codex session reuse.

Native provider selection is explicit and independent of `prime.kernel.enabled`.
The pinned checkout is not patched. The localhost integration suite exercises
the real Rust HTTP parser, disk-reading tool rounds, durable metadata, approval
refusal, timeout, and unsupported options. It does not establish live-service
authentication for every native provider.

## Optional Prime tool families

Every Prime tool family is default off. Enabling a family adds registrations
through the existing connectors; it does not automatically graft Prime's
native agent loop or session engine into the conversation loop.

- **Goals** (`prime.goals.enabled`): persisted objectives and ordered steps
  steer logical-turn continuation in the existing loop. Known provider usage,
  including tool-call rounds and successful calls preceding a provider error,
  accrues once. Fresh pause/completion/budget/stale state stops continuation.
  Clearing ongoing goal work cancels that run's implicit continuation;
  never-set goal absence does not veto independent autonomy. `goal_set`
  replaces the whole goal (objective, steps, and Prime state) after validating
  everything up front; a rejected request writes nothing. Tools: `goal_set`,
  `goal_pause`, `goal_resume`, `goal_clear`, `goal_status`.
- **Heartbeats** (`prime.heartbeat.enabled`): one registry-owned in-process
  APScheduler runtime routes persisted due prompts to `session_name` bindings.
  A beat waits behind its owner's foreground lease, preserving the transcript;
  other named sessions can progress independently. Actual callback response
  strings are persisted. Tools: `heartbeat_set`, `heartbeat_list`, `heartbeat_clear`.
- **Autonomous mode** (`prime.autonomous.enabled`): after `autonomous_start`,
  the live driver counts logical turns and known tokens and enforces turn,
  token, and elapsed-time limits plus real argv quality gates. Gates run only
  for eligible completed responses, never paused/terminal work. Stop metadata
  retains producer diagnostics; a passed gate proves only its own command,
  not goal completion. Tools: `autonomous_start`, `autonomous_status`,
  `autonomous_stop`.
- **Agent messaging** (`prime.messaging.enabled`): in-process named-session
  messaging. The default host registers `agent_message_send` and
  `agent_observe` on session `bot-00-omega-prime` when the flag is on. An
  embedder may pass its own session registry to `register_messaging_tools`.
- **RLM recursion** (`prime.rlm.enabled`): the seven `rlm_*` tools. The default
  host registers them on the desk parent when the flag is on. Without a
  child-model provider, `rlm_spawn` and `rlm_create_session` return
  `not_configured: provider`. `OmegaPrimeAgent(..., session_dir=...)` supplies
  the durable directory an embedded agent offers as an RLM parent; without it
  that agent cannot be a parent, and no working-directory default is used. See
  [connectors.md](connectors.md#rlm-persistence-and-channels) for the parent
  contract and the tool catalog for the tools.
- **Continual harness** (`prime.harness.enabled`): evidence-backed supplemental
  harness refinement with snapshots and rollback; not a base-prompt rewrite.
- **Kernel** (`prime.kernel.enabled`): pinned Python runtime tools and native
  crate helpers. Linked-crate probes are diagnostics, not application integration.

Implicit continuation stays within one transcript, journal run, exclusive lease,
caller budget, and cumulative `max_iterations` cap; it never refills the budget.
Refusal, incomplete response, goal stop, driver stop, and outer bounds retain
their actual stop reasons rather than claiming task completion. The refusal
stop reasons `approval_refused` and `policy_refused` describe only the public
run's most recent tool round: a refusal the model recovered from in a later
round does not make the turn terminal.

Normal provider finish reasons `stop`, `end_turn`, and `stop_sequence` are
eligible completed text boundaries; unknown/truncated reasons remain incomplete.
An enabled but idle goal/autonomous family does not relabel a normal answer just
because it uses the caller's last iteration. If a later continuation is interrupted
before producing a reply, `final_response` retains the last accepted answer.

Budget deltas are derived per paid model call: component-only usage and explicit
totals can coexist without dropping the component-only call. Public `usage` and
native wire metadata keep their raw key-wise shape, so a partial aggregate total
need not equal the derived goal/driver charge. No tokens are invented for unknown
usage.

Autonomous stops are sticky across public runs; an active goal does not restart
a stopped driver. Explicit `autonomous_start` creates a new run. At incomplete
boundaries the existing driver reports `reason: autonomous_completed`; inspect
`detail`, `turn_exit_reason`, and `completed: false` for the actual cap, refusal,
goal stop, or provider failure. That reason alone is not a success receipt.

Control-hook failures use `guarded_hook` and emit redacted `prime_degraded`
evidence. The normal response remains available, but either degraded control
family prevents further implicit calls in that public run. Ordinary tool
refusals and provider/control-flow exceptions are not relabeled as hook failures.
Native `pa-agent`, `pa-core::SessionEngine`, TUI, and daemon supervision do not
own this composition.

Use `OmegaPrimeAgent(..., session_name="alpha")` with the same enabled registry
to attach a live heartbeat owner. Context-manager exit or `close()` conditionally
detaches that exact owner; replacement and other bindings survive. Last-owner
shutdown waits for callbacks and stops owned scheduling. Cleanup failures
propagate with retryable handles. A blocking shutdown requested from its own
callback/sink, or a start during a shutdown drain, raises `RuntimeError` instead
of deadlocking or reporting success. Explicit whole-runtime `close()` drops all
bindings. No bookkeeping lock is held across a model callback or error sink.

Malformed heartbeat rows are diagnosed and disarmed rather than re-armed at the
same deadline. Schedule arguments must be finite, calendar-representable, and
advance the clock; invalid arguments fail before writing the jobs file. Foreign
rows and injected-backend entries are not pruned. Whole-file corruption or an
owned heartbeat missing its identity remains an explicit error.

Overdue beats catch up once after a scheduler stall. Owned scheduler pools can
restart; an injected scheduler is shut down by runtime cleanup and cannot be
reused through that runtime afterward. Supply a fresh backend/runtime rather
than treating a dead executor pool as live.

Harness refinement snapshots entries before applying approved changes. Rollback
restores those saved entries while retaining the audit trail; it never rewrites
the fixed base prompt. Older snapshots retain the bytes originally captured.

## Configuration

`omega-prime.json` at the repo root, or
`OMEGA_PRIME_PRIME_<FAMILY>_ENABLED=1` per family (environment beats file):

```json
{
  "prime": {
    "rlm": { "enabled": false },
    "harness": { "enabled": false },
    "goals": { "enabled": false },
    "heartbeat": { "enabled": false },
    "autonomous": { "enabled": false, "max_turns": 12, "max_tokens": 200000,
                    "max_minutes": 30, "gate": ["pytest", "-q"] },
    "messaging": { "enabled": false },
    "kernel": { "enabled": false }
  }
}
```

A disabled family is not registered in the live tool surface. The prompt
template, the tool roster, and the policy contract are the allowed superset;
the assembler drops the tool entries of every disabled family, so the default
assembled artifact names no Prime tools. The live offered schemas remain
authoritative. Writes require the existing registry approval checks.

The flag is consumed per family by the site that registers the family. The
default MCP registry (`omega_prime.mcp_server.default_registry`) registers
`harness`, `goals`, `heartbeat`, `autonomous`, `kernel`, `rlm`, and
`messaging` when their flags are on. `rlm` uses the desk parent (session
directory under the desk store). `messaging` uses the seat session
`bot-00-omega-prime`. An embedder can still call `register_rlm_tools` or
`register_messaging_tools` with its own parent or session. `delegate_task`
is served. Without a provider env it returns `not_configured: provider`.
Prime families stay off by default.

`heartbeat_set` only persists a job (`cron/jobs.json` under the root). A job
fires only in a process that bound the named live session (an
`OmegaPrimeAgent(..., session_name=...)` on the same registry) and started the
heartbeat runtime; the MCP default registry publishes the heartbeat tools
without a consuming loop, so jobs scheduled through it do not fire there.

