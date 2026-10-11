# Connector contracts

The connector layer (`omega_prime/prime/`) is the typed, versioned boundary
between the tool surface (registry dispatch → JSON) and the ported Prime
capability modules (`agent/rlm.py`, `learning/harness.py`, `agent/goals.py`,
`agent/autonomous.py`, `agent/messaging.py`), the heartbeat runtime
(`cron/heartbeat_runtime.py`) and the in-process kernel tools
(`tools/prime_runtime.py`). There are seven adapters; every registered Prime
tool family decodes through a versioned typed request. These adapters are not
an FFI bridge. The crate link and the in-process `rlm` import are a separate
gate (`prime.kernel.enabled`); see
[ADR 0001](adr/0001-prime-merge-architecture.md) and
[ADR 0002](adr/0002-prime-runtime-integration.md).

The default product is in-process Python in one process with no sidecar;
the crate link and native kernel import are opt-in behind
`prime.kernel.enabled` and need the bridge build. Optional separately
started services (a Node red-team backend, external stores) stay outside
this boundary — see [Architecture](architecture.md).

The typed **request** boundary applies to every registered tool of the seven
families and to the declared keys of the kernel RLM bridge. Loop-internal
capability calls (per-turn goal accrual, the continuation prompt, the
completion report, and the autonomous driver consult) use the live goal store
and driver directly; their responses are validated through typed views
(`GoalStatusView`, `VerdictView`, `DriverStatusView`). This is the design: those
internal calls have no typed request. The kernel host wire builds its typed
requests only from the declared keys (`name`, `model`, `thinking`, and, for
create-session, `cwd`, which must be null) and ignores other SDK-sent kwargs.

## Adapters

| Module | Wraps | Request/response types |
| --- | --- | --- |
| `prime/rlm.py` | `RlmHost` / `NoRlmHost` | Spawn, collect, list, create-session, delete, rename, and progress-note requests; typed child/session views |
| `prime/harness.py` | `HarnessState` | Upsert, list, delete, refine, snapshot, and rollback requests; entry/refinement views |
| `prime/goals.py` | `PrimeGoalStore` | Set, accrue, status, pause, resume, and clear requests; `GoalStatusView` |
| `prime/autonomous.py` | `AutonomousDriver` | Start, status, and stop requests; `VerdictView` and driver-status views |
| `prime/messaging.py` | `SessionRegistry` | Send and observe requests; `MessageView` |
| `prime/heartbeat.py` | `HeartbeatRuntime` | Set, clear, and list requests; `HeartbeatConnector` |
| `prime/kernel.py` | the in-process kernel tools in `tools/prime_runtime.py` | Cell, factory-run, run (status/stop/resume), factory-graph, bash, skill-list, crates, `/goal` command, and `/autonomous` command requests; `KernelConnector` |

## Rules every adapter enforces

1. **Versioned schemas.** Each adapter module carries `SCHEMA_VERSION = 1`.
   A payload may declare `schema_version`, which must be an integer >= 1
   (`bad_type` for a non-integer, `bad_value` below 1). `from_dict` rejects a
   version newer than the adapter's own with
   `PrimeError("unsupported_schema_version", ...)` — the forward-compat
   guard is loud, never silently last-good.
2. **Strict decoding.** A tool handler accepts only its declared parameters
   plus `schema_version`. Any other key — including session-bound or alias
   fields such as `sender`, `session`, `scope`, `stale_after_turns`,
   `mark_read`, and `cwd` — is rejected with `unknown_field` and is never
   merged into the decoded payload (`reject_extra` in `prime/types.py`).
   Omitted required fields reach the typed decoder (`bad_type`; a closed
   vocabulary such as the harness `kind` gives `bad_value`). Booleans never
   pass as integers and vice versa (`bad_type`); closed vocabularies (status
   sets, harness kinds/scopes, stop reasons) are enforced on both encode and
   decode (`bad_value`).
3. **Structured failures.** Boundary-decoding failures (`unknown_field`,
   `bad_type`, `bad_value`, `unsupported_schema_version`, and the messaging
   cause codes `unknown_sender`, `unknown_recipient`, `unknown_session`) use
   `PrimeError(code, reason)`. `ToolRegistry.dispatch` converts one into the
   `{error, code, reason}` envelope and audits it with verdict `error` and
   reason `<code>: <reason>`; policy and approval refusals are audited
   `denied`. The consuming loop's error tool message retains the code prefix
   and reason. A missing recipient is `unknown_recipient`, never a silent drop.
   Failures the capability raises after decoding (a duplicate child name, an
   unknown child id, a store `ValueError` such as a whitespace-only `goal_set` objective)
   keep the pinned Prime/source error text as a bare
   `{"error": "<Type>: <text>"}` envelope with no `code`, audited as `error`
   with that text as the reason. Results the capability returns rather than
   raises are unchanged and audited as allowed: `heartbeat_clear` on an unknown
   id returns `{"error": "no heartbeat job: '<id>'"}`, and a `harness_get` miss
   returns `{"entry": null}`.
4. **Round-trip fidelity.** `from_dict(x.to_dict()) == x` for every request
   type; tool-surface JSON decodes back into typed views losslessly
   (pinned by `tests/test_prime_contracts.py`; the heartbeat and kernel
   requests are pinned by `tests/test_prime_heartbeat_boundary.py` and
   `tests/test_prime_kernel_boundary.py`).
5. **Authorized decoding.** Registered tools run in the order policy →
   approval → decode, so strict decoding and capability effects never precede
   authorization. Explicit schema v1 is accepted; future versions and
   undeclared keys reach a typed `PrimeError` rather than a generic
   handler-signature error. For the heartbeat and kernel adapters a rejected
   request never reaches the heartbeat runtime, never imports the pinned `rlm`
   runtime, never builds a kernel, and never touches the filesystem.
6. **Bounded recovery.** Raising children settle as error results; a bounded
   collect returns an unsettled running snapshot on timeout. Malformed request
   and producer-response failures reach the actual consuming model, and valid
   subsequent turns recover. Goal/autonomous control-hook failures emit
   `prime_degraded` and veto implicit continuation. Provider errors and
   refusals retain their meanings. Heartbeat errors use the owning binding's sink.

Loop budget arithmetic derives known usage per call; it does not rewrite the
producer's public/native payload to fill missing totals. Driver stop metadata
retains `detail` and gate diagnostics. `autonomous_completed` on an incomplete
boundary is the existing library stop code, not proof of successful work.

Tool-call arguments in the conversation loop must be a JSON object (a decoded
`dict` or a string that parses to one). `None` and the empty string mean no
arguments; any other payload (truncated JSON, a list, a number, `null`,
whitespace-only text) produces an `error:` tool row and the call is not
executed, while the other calls of the same round still run. The refusal stop
reasons `approval_refused` and `policy_refused` describe the most recent tool
round of the public run only, so a refusal the model recovered from in a later
round does not make the turn terminal.

## Per-family tool contracts

- **Messaging.** `agent_message_send` takes `recipient` and `body`;
  `agent_observe` takes no argument. The sender and the observed session are
  the session name bound when the family is registered, never a model-supplied
  field: `sender`, `session`, and `mark_read` are `unknown_field`, so a forged
  sender and a cross-session inbox read or consume are impossible. Connector
  failures are classified by cause: `bad_value` (empty body),
  `unknown_sender`, `unknown_recipient`, and `unknown_session`.
- **Goals.** `goal_set` takes `objective`, `token_budget`, and `steps`, and
  replaces the whole goal (objective, steps, and the Prime sidecar) through
  `PrimeGoalStore.replace_goal`. The objective, budget, and every step are
  validated before anything is written; a blank step is `bad_value`. A
  rejected request writes nothing, previous steps never carry into the new
  goal, the Prime state restarts as `active` with zero tokens used, and the
  result is the status after the new steps are applied. Each document is
  written once; a crash between the goal file and the sidecar write is not
  covered. `stale_after_turns` is a field of the connector's `SetGoalRequest`,
  not a tool argument: the tool rejects it as `unknown_field` and the default
  staleness window applies.
- **Harness.** `global_` (a boolean; any other type is `bad_type`) is the only
  scope input. A `scope` key is `unknown_field`.
- **Autonomous.** `max_minutes` must be a finite positive number or null; NaN
  and +/-Infinity are `bad_value`.
- **Heartbeat.** `interval_seconds` must be a finite number > 0 and `due_at`
  a finite number, else `bad_type` or `bad_value`; the scheduler's calendar
  limits also surface as `bad_value`.

## RLM persistence and channels

Children receive their own prompt, not the parent's conversation. Each child
has a real atomic session document containing its prompt, final answer, identity,
owner, and terminal/error status. Collect does not report `done` before the
terminal document is saved. Failed storage surfaces an error, not completion.

The parent contract is explicit and nothing is defaulted: `session_dir`
(non-empty `str` or `PathLike`), `session_name` (`str` or `None`),
`delegate_depth` (`int` >= 0), `max_depth` (`int` >= 0), and `max_children`
(`int` >= 1). `require_parent` checks it when an `RlmHost` is constructed and
when `register_rlm_tools` is given a parent (a `None` parent registers the
tools without constructing a host), so a misconfigured parent fails at
construction or registration, not at the first model call, and never falls
back to the process working directory. `OmegaPrimeAgent(..., session_dir=...)`
supplies the directory; without it the agent cannot be an RLM parent.

`Agent.max_children` defaults to 1, and a settled child keeps counting toward
the limit until `rlm_delete_subagent` removes it, so a default
`OmegaPrimeAgent` parent holds one child at a time.

Admission is atomic: name uniqueness, the child limit (`max_children`), the
depth bound (`delegate_depth >= max_depth` refuses a spawn), and registration
are one critical section, and a storage failure on the first durable write
admits nothing. `rlm_create_session` admits the child first and writes the
session-store roster row afterward, so a refused create leaves no roster
record and a roster-write failure reaps the just-admitted child. The tool has
no `cwd`: a supplied `cwd` is `unknown_field` at the tool surface, and the
typed `CreateSessionRequest` accepts `cwd=None` (the pinned SDK sends it) and
rejects any other value with `bad_value`, because children share the host
process directory.

A fresh host recovers its own durable records at construction without
rerunning children; records owned by another named parent are never picked up.
Recovery rewrites only interrupted (still `running`) records, turning them
into explicit errors, and survives a failing write: the record stays
registered in memory as an error that names the failed recovery write.
Settled records are not rewritten. The supported assumption is one live host
per (`session_dir`, owner `session_name`): records are keyed by the owner name
only, so a second live host for the same pair would recover, and mark
interrupted, the first host's running children.

Rename persists lifecycle metadata and delete persists a tombstone, including
concurrent child settlement. A tombstone keeps only identity, owner, and
status — no prompt, answer, or error text — and exists to defeat resurrection.
Each document write is atomic; parent self-rename is not a multi-file
transaction.

List and delete return metadata only. Child answer text is exposed through
collect, and only for settled children: `answer_preview` (a 200-character
preview; the full answer lives only in the session file) and `error` are
reported when the child has settled, never next to a `running` status. Collect
and list read the in-memory registry under a lock that no file write holds, so
they never block behind a durable write (a bounded collect still waits, within
its timeout, for an already finished child's terminal document). Kernel progress identity comes from the
executing worker context: root or explicit-ID impersonation is rejected,
accepted notes update the owning child's metadata, and immediate repeats are
throttled. The registered progress-note tool keeps its separate
explicit-child selector contract. `rlm_collect` `timeout_ms` has no upper
bound and does not observe the interrupt flag.

The default host has no child runner; it reports that missing configuration
explicitly. Scripted or provider-backed child execution requires an explicit
runner. Kernel spawn/create also require a caller-selected model or an embedding
host's configured model: the bridge never invents a scripted selector or model
availability. This is not a native session engine or daemon.

## Parity evidence

Prime behavior fixtures cite upstream source for status vocabularies, no-host
errors, progress bounds, timeouts, goals, and harness validation. They are
source-derived contracts, not a passing Rust/Python differential oracle. The
retained upstream Rust diagnostic has known nonzero local results; its CI
configuration must not be mistaken for an observed cloud pass.

`tests/parity/pre_v10_loop_transcript.json` instead contains actual transcripts
captured by executing immutable pre-v10 commit `79ff51af41e2469b906a501814d1899a13be9679`.
The comparator (`tests/test_prime_regression.py`) replays its six consumer cases
through `Agent` / `run_conversation` with a test-local dispatcher and compares
messages, offered tools, approval decisions, effects, errors, stops, and
model-call accounting without normalization. It does not exercise the
registered public agent's error rows; those are covered by
`tests/test_prime_failure_injection.py` and the boundary tests. The unmodified
historical suite separately recorded 376 passes and two obsolete
source/inventory-pin failures; it is not claimed fully green.

## Further reading

Execution and security boundaries live in [Architecture](architecture.md);
contribution seams and the extension contract live in
[Build aesthetics](build-aesthetics.md); the hand-run workflow lives in
[Local validation](local-validation.md). The Greptile-synthesized history is
a separately labelled untrusted archive — see [Greptile](greptile.md) —
and is never quoted here as current interface truth.
