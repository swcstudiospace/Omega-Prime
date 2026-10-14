# Driving the bot

How to run the Grok Bot with Omega Prime's Prime capability families on, what
the bot gets from each one, and what it does not. [Setup](setup.md) covers
install and secrets; this page starts after it.

## What you are driving

| Piece | What it is | Who runs it |
| --- | --- | --- |
| The bot | Your Grok Bot seat loaded with the Omega Prime template (`omega_prime/grokbot/templates/OMEGA_PRIME.md`). The conversation loop belongs to the seat, not to this checkout — how a seat takes a prompt file is a Grok Bot setting this repository neither controls nor tests. | who operates the seat |
| The tool host | `python -m omega_prime.mcp_server`: one process serving the tool registry over MCP stdio, behind the roster, the seat policy, and approvals. | You, on a machine you control |
| The effective prompt | The assembled system prompt. The bot calls a tool only when its prompt lists it. | You, with the assembler |

`OmegaPrimeAgent`, the Python conversation loop ([Agent loop](agent-loop.md)),
is a library for embedders. No command in this repository starts it, so Prime's
loop-level behavior (goal continuation, autonomous limits and gates, heartbeat
firing) does not run behind a Grok Bot. What the bot gets is the tools,
including `rlm` and `messaging` when those flags are on.

Two files are both called a roster: `omega_prime/grokbot/rosters/default.json`
feeds the assembler (bot id, branch, version), and
`omega_prime/contracts/tool-rosters/omega-prime.yaml` lists the tools the host
may serve.

## What each family gives the bot

Every Prime family is off by default. A default host serves the roster
intersection `setup_check` reports (117 today) and no Prime tool.
`delegate_task` is served; without a provider env it returns
`not_configured: provider`.

| Family and flag | Tools | Behind a Grok Bot |
| --- | --- | --- |
| Harness `prime.harness.enabled` | `harness_upsert`, `harness_get`, `harness_list`, `harness_delete`, `harness_refine`, `harness_rollback` | Works. Stores supplemental entries (kinds `prompt`, `memory`, `skill`, `subagent`, `factory`). `harness_refine` applies only evidence-backed proposals and snapshots first; `harness_rollback` restores the last snapshot. Nothing injects entries into the prompt: the bot reads them with `harness_list` or `harness_get`. Gated: upsert, delete, refine, rollback. |
| Goals `prime.goals.enabled` | `goal_set`, `goal_pause`, `goal_resume`, `goal_clear`, `goal_status` | Stores one goal (objective, steps, token budget). Nothing accrues usage or continues turns, so budget and stale checks never trigger. Gated: all but `goal_status`. |
| Heartbeat `prime.heartbeat.enabled` | `heartbeat_set`, `heartbeat_list`, `heartbeat_clear` | Persists jobs in `cron/jobs.json`. Jobs never fire: firing needs a process that bound a named live session. Gated: set, clear. |
| Autonomous `prime.autonomous.enabled` | `autonomous_start`, `autonomous_status`, `autonomous_stop` | Records a run's limits in the host process. Nothing counts turns or runs the quality gate, and the record is lost on restart. Gated: start, stop. |
| Kernel `prime.kernel.enabled` | `prime_cell`, `prime_bash`, `prime_skill_list`, `prime_crates`, `prime_goal`, `prime_autonomous`, `prime_factory_run`, `prime_factory_status`, `prime_factory_stop`, `prime_factory_resume`, `prime_factory_graph` | Works: real execution (below). Gated: `prime_cell`, `prime_bash`, `prime_goal`, `prime_autonomous`, `prime_factory_run`, `prime_factory_stop`, `prime_factory_resume`. |
| RLM `prime.rlm.enabled` | `rlm_spawn`, `rlm_collect`, `rlm_list_subagents`, `rlm_delete_subagent`, `rlm_create_session`, `rlm_progress_note`, `rlm_rename` | Served on the desk parent. Without a child-model provider, `rlm_spawn` and `rlm_create_session` return `not_configured: provider`. Gated: spawn, create, delete, rename. |
| Messaging `prime.messaging.enabled` | `agent_message_send`, `agent_observe` | Served on the seat session `bot-00-omega-prime`. Gated: send. |

Kernel tools:

- `prime_cell` runs Python in one persistent namespace. It returns `ok`,
  `value` (the repr of a trailing expression), `error`, and the `stdout` and
  `stderr` the cell printed while it ran. Top-level `await` works, and `exit()`
  or `sys.exit()` is a cell error, not the end of the host. A valid
  `await rlm.spawn(prompt, name=...)` from a cell fails with
  `rlm run requires an explicit model selector`: the host configures no model
  and no child runner.
- `prime_bash` runs a command through Prime's `rlm.bash` and returns
  `exit_code` and `stdout` (stderr is merged into it).
- `prime_crates` reports the nine linked Rust crates (`pa-telemetry`,
  `pa-types`, `pa-ai`, `pa-models`, `pa-agent`, `pa-core`, `pa-daemon`,
  `pa-tui`, `pa-cli`) and whether the native extension loaded.
- `prime_goal` and `prime_autonomous` run Prime's own `/goal` and `/autonomous`
  commands through `pa-core`. They keep their own state under `prime-kernel/`
  and do not see the `goal_*` or `autonomous_*` records: `prime_goal` with
  `status` answers `No active goal.` right after `goal_set`.
- The factory tools call the pinned `rlm.factory` and need a spec id: a stored
  harness `factory` entry or a built-in machine (`builder`, `pr-manager`,
  `review-sweep`). A `builder` run starts, then pauses with `spawn admission
  failed: repl runtime is not serving`, so treat the factory as unproven.
  `prime_factory_graph` answers `the factory is disabled` until
  `prime_factory_run` has enabled it.

## Turn families on

Save this as `omega-prime.json` in the directory you pass as `--root`:

```json
{
  "prime": {
    "harness": { "enabled": true },
    "goals": { "enabled": true },
    "heartbeat": { "enabled": true },
    "autonomous": { "enabled": true },
    "kernel": { "enabled": true },
    "rlm": { "enabled": true },
    "messaging": { "enabled": true }
  }
}
```

Or set `OMEGA_PRIME_PRIME_<FAMILY>_ENABLED=1` in the host's environment
(`true`, `yes`, and `on` also enable; any other value, including an empty one,
turns the family off and overrides the file). The environment wins over the
file. The host reads both at startup, so restart it after any change. `rlm`
and `messaging` register when their flags are on. Without a child-model
provider, `rlm_spawn` and `rlm_create_session` return
`not_configured: provider`. Messaging uses the seat session
`bot-00-omega-prime`.

The kernel family needs the pinned `prime-agent/` checkout for every tool (see
[Build from source](../README.md#build-from-source-both-toolchains)). Three
tools also need the native extension: `prime_crates` reports `loaded: false`
without it, and `prime_goal` and `prime_autonomous` fail with
`ImportError: omega_prime_prime extension is not built`. Build it from the
repository root:

```bash
cargo build --locked --manifest-path native/omega-prime-prime/Cargo.toml \
  --target-dir prime-agent/target
```

## Start the tool host

```bash
.venv/bin/python -m omega_prime.mcp_server --root . \
  --approve goal_set:alice --approve goal_clear:alice
```

| Flag | Meaning |
| --- | --- |
| `--root DIR` | Repository checkout holding `omega_prime/` and `omega-prime.json` (default: the current directory). |
| `--work-root DIR` | Directory the desk tools act on (default: `OMEGA_PRIME_WORK_ROOT`, else `--root`). |
| `--home DIR` | The bot's home: the working directory of the `execute_code` and `mcp_call` tools (default: `~`). |
| `--no-roster` | Serve every registered tool instead of the roster's default set. |
| `--approve TOOL:APPROVER` | Pre-approve one gated tool. Repeatable. A malformed value, or an approver equal to the bot's own id, exits 2. |

Desk state (including the `intake.json` work-order queue) is stored under
`OMEGA_PRIME_STATE_DIR`. Export the secrets from [Setup](setup.md#4-secrets-go-in-the-private-environment-never-in-examples) in the host's environment,
then register the command with your seat. How a Grok Bot seat takes a custom
MCP server is a Grok Bot setting this repository neither controls nor tests —
confirm it with the checks below.

### Approvals

The host has no approval prompt. A gated tool answers
`{"error": "approval required", "tool": "goal_set"}` unless the host was
started with `--approve` for it. An approval covers every later call to that
tool until the process exits; restart to revoke. The approver name is
recorded, not authenticated, and the host does not check the tool name: a typo
approves nothing.

`prime_cell` and `prime_bash` run code as the host's user, and the default host
already serves ungated tools that run code and write files (`run_terminal`,
`execute_code`, `mcp_call`, `write_file`, `patch_file`). Sandbox the host
whether or not any Prime flag is on ([Tool host](tool-host.md#openshell)).

### State on disk

The Prime families write under `--root`, and the repository ignores these paths:

| Path | Holds |
| --- | --- |
| `goals/` | The `goal_*` record. |
| `harness-local/`, `harness-global/` | Harness entries. `global_: true` writes the cross-session store. |
| `cron/jobs.json` | Heartbeat jobs. |
| `prime-kernel/`, `prime-agent-dir/` | Kernel state, including `prime_goal`'s record. `prime-agent-dir/` appears with the first `prime_factory_run`. |

An autonomous run is held in the host process and is not written to disk.

### Intake queue

The intake queue (`intake.json`, resolved from the state directory) is the
authoritative work-order list the desk tools and cron passes share. Every
public operation reloads the file first, and mutations run as serialized
reload–mutate–atomic-replace transactions under the instance thread lock
plus an exclusive `flock` on the stable `intake.json.lock` sidecar, so
cooperating POSIX writers never lose each other's committed orders, claims,
or field updates. A corrupt file raises instead of reading as empty.
Stale-lease completion is not fenced: `ack` is an ordered merge with no
expected-claim condition, and the queue is not globally transactional with
any other store. A warm host sees orders an independent producer appended
without needing a rebuild. The readiness disposable export excludes the
live queue, schedules, and session state — [Local
validation](local-validation.md#isolation) re-seeds its own fixture queue.

## Build the effective prompt

The bot calls a tool only when its prompt lists it, and the shipped prompt
(`omega_prime/prompts-assembled/OMEGA_PRIME.xml`) names no Prime tool. Build one
that does, with the same families the host serves:

```bash
.venv/bin/python -m omega_prime.assemble \
  --enable-family harness --enable-family goals --enable-family heartbeat \
  --enable-family autonomous --enable-family kernel \
  --enable-family rlm --enable-family messaging \
  --output build/OMEGA_PRIME.effective.xml
```

`--output` is required with `--enable-family`, and the shipped file is never
written. Add `--check` to compare an existing file instead of writing it: it
exits 1 when the file is stale or missing, and the fix is to run the same
command without `--check`. The shipped prompt and the default host share the
roster intersection `setup_check` reports (117 today). Prime families stay
off by default. `delegate_task` is served; without a provider env it returns
`not_configured: provider`. Harness, goals, heartbeat, autonomous, and
kernel add 28 tools. `rlm` adds seven and `messaging` adds two. The command
above turns all seven on (154 tools). The host serves each family only when
its flag is on.

The template tells the bot to read `omega_prime/prompts-assembled/OMEGA_PRIME.xml` on first
run. Give your bot the effective file instead. This repository builds the file;
how Grok takes it is a Grok Bot setting this repository neither controls nor
tests, so confirm it with the checks below.

## Check it

1. Count what the host serves. With all seven Prime families on you should
   see 154; with the five self-contained families (harness, goals, heartbeat,
   autonomous, kernel) you should see 145; with none, 117. Prime families
   stay off by default. Cron, learning, and durable tools are always served.

   ```bash
   .venv/bin/python -m omega_prime.setup_check --root .
   ```

   Look for `[ok] registry: registry serves 117 roster tools` on a default
   host. With every `OMEGA_PRIME_PRIME_<FAMILY>_ENABLED=1`, the served set
   equals the 154 roster names outside the default-off Red Team family;
   `OMEGA_PRIME_REDTEAM_ENABLED=1` adds its eleven tempest_* tools for the
   full 165.

2. Call a tool over real MCP stdio. `prime_crates` is read-only, so it needs no
   approval:

   ```bash
   .venv/bin/python - <<'EOF'
   import json, sys
   from omega_prime.tools.mcp_session import mcp_session_call

   out = mcp_session_call(
       [sys.executable, "-m", "omega_prime.mcp_server", "--root", "."],
       "prime_crates", {}, cwd=".", timeout=60,
   )
   print(json.dumps(json.loads(out["result"]["content"][0]["text"]), indent=2))
   EOF
   ```

   Expect `"loaded": true`, `"error": null`, and the nine crate names — once
   the native extension is built per the `cargo build` command above. Without
   it, `prime_crates` reports `loaded: false`; that is the honest
   unbuilt-extension state, not a failure of the host.

3. In the bot, send the smoke prompt from [Setup](setup.md#7-smoke-prompt-needs-a-bot-seat). The
   tool list should now include the Prime tools in your effective prompt. Then
   ask it to call `prime_crates`; it should return the payload from step 2.
   The prompt does not mark which tools are gated; the
   [tool catalog](tool-catalog.md) does. Seat-side behavior is preparation to
   confirm, not a repository-run proof — the repository proof is steps 1–2.

## Asking for things

Plain requests work. The bot picks the tool; the right column is what to
expect.

| You say | Likely call | Notes |
| --- | --- | --- |
| "Remember: run the linter before every PR." | `harness_upsert`, kind `memory` | Gated. Read back with `harness_list`. |
| "Set a goal: ship the migration. Steps: schema, backfill, cutover." | `goal_set`, later `goal_status` | Gated. Stored only; the bot must read it back. |
| "Try `import json; json.dumps({'a': 1})` in the Prime REPL." | `prime_cell` | Gated. Variables persist between cells. |
| "Which Prime crates are linked in?" | `prime_crates` | Free. |
| "Run the tests through Prime's bash." | `prime_bash` | Gated. Runs as the host's user. |
| `/omega-help` | `omega_command` with `/omega-help` | Free. Lists every curated and generated command. |
| `/omega-code-review` | `omega_command` with `/omega-code-review` | Free. Returns the code-review skill's instructions; the bot then follows them. |
| `/omega-onboard-routine` | `omega_command` with `/omega-onboard-routine` | Runs the onboard routine's workflow, the same doctor, roster, memory-seed and connector steps as `/omega-onboard`. Gated tools inside it stay gated. |
| `/omega-python` | `omega_command` with `/omega-python` | Runs ruff and compileall. Not the same as native `/python` or `/omega-python-skill`. |

### Slash commands

Send `/omega-<name>` as the whole message, with any arguments after a space
(`/omega-code-review src/app.py`). The `omega_command` tool description and
the `omega-commands` skill tell the Bot to pass that exact text to the
`omega_command` tool of the `omega-prime` server. A sentence that only
mentions a command is ordinary prose, not a command. `/omega-help` lists
every name: the 27 curated commands, one `/omega-<skill>` per checked-in
skill and one `/omega-<routine>` per routine (61 today). A generated name
that matches a curated command or another generated name takes a `-skill`
or `-routine` suffix: `/omega-python-skill`, `/omega-onboard-routine`,
`/omega-connectors-routine`, `/omega-ultrathink-skill` and
`/omega-ultrathink-routine`. A host that surfaces skills natively may also
show `/python`: native `/python` shows the python skill's instructions and
runs nothing, `/omega-python` runs ruff and compileall through
`omega_command`, and `/omega-python-skill` loads the python skill's
instructions through `omega_command`. Two skills now match their folder
names: `secrets-handling` is `security-secrets-handling` and `supply-chain`
is `security-supply-chain`. A copy saved under an old name in a private Bot
skill library may persist until you remove it (UNPROVEN, user UAT row 4).
Whether a live Bot follows this contract, and whether `/omega-*` entries
appear natively in its `/` menu, is user UAT row 4.

The user UAT rows are listed in
[Grok Bot native host](grok-bot-native.md#user-uat); attaching the server to
a Bot is section 1 of
[grokbot/SETUP.md](../omega_prime/grokbot/SETUP.md).

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| A Prime tool is missing from the host's tool list | The family's flag is off in the host process, `--root` points where your `omega-prime.json` is not, or the host was not restarted after the change. | Run `setup_check --root <the same root>`, then restart the host. |
| `unknown_command` from `omega_command` | The name is not in the catalog, or a generated name lost its `-skill`/`-routine` suffix. | Send `/omega-help` and use a listed name. |
| `approval required` | The tool is gated and the host was not started with `--approve` for it. | Add `--approve TOOL:YOU` and restart. |
| `policy forbids <tool>` | The name is not on the roster, or the seat policy (`omega_prime/contracts/policies/omega-prime.json`) forbids it. | Only rostered tools are served. |
| `Unknown tool: <name>` | The tool is on the roster but the host did not register it: its Prime family is off, including `rlm` and `messaging`, or the host was not restarted after the flag change. | Turn the family on and restart the host. `delegate_task` is served and returns `not_configured: provider` without a provider env. |
| `unknown_field`, `bad_type`, `bad_value`, or `unsupported_schema_version`, for example `unknown_field: goal_set does not accept: scope` | The call was rejected before any effect; nothing was written. A gated tool answers `approval required` before its arguments are checked. | Fix the arguments. Each tool's parameters are in the [tool catalog](tool-catalog.md). |
| `prime_crates` shows `loaded: false`, or `ImportError: omega_prime_prime extension is not built` | The native extension is missing. `prime_cell`, `prime_bash`, and the rest of the kernel family still work. | Build it with the `cargo build` command above. |
| `the factory is disabled; run /factory on to enable it` | The factory is off until `prime_factory_run` enables it. | Approve and call `prime_factory_run` with a valid spec id. |
| A heartbeat job never fires | The host has no loop bound to a named session. | Expected here. Jobs fire only under an embedded `OmegaPrimeAgent(..., session_name=...)`; see [Agent loop](agent-loop.md). |
| The host serves Prime tools but the bot never calls them | Its prompt does not list them. | Build and load the effective prompt. |

More on the typed errors and handler rules: [Migration to v10](migration.md).
Per-tool approval and parameters: [Tool catalog](tool-catalog.md).
