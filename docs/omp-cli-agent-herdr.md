# omp CLI agent in a Herdr pane inside omp-sandbox (spec)

- **Task:** 00045 - omp CLI agent in Herdr + sandbox-control tools · **Date:** 2026-10-23 (AEDT)
- **Draft PR:** feat/00043-omp-cli-herdr → main (stacked on #75; see "Sequencing")
- **Status:** design + partial build. §8 says what is built and what is spec-only.

## 1. What changes and why

Today `omp-sandbox/src/core/core.ts` spawns, inside the sandbox,
`omp --mode rpc-ui --no-title --no-lsp --model <m> --thinking <t> --approval-mode yolo`
over a piped `openshell sandbox exec --no-tty` channel. It then drives GSD stages through `OmpRpcClient` (`src/core/omp-rpc.ts`) using newline-delimited JSON: `prompt`, `set_ask_dialog`, `extension_ui_request`/`extension_ui_response`, `abort`, `get_state` and `get_available_commands`. Nobody can watch or type into that session, and it is a marshaling harness rather than the agent loop omp users get.

**Target:** the real interactive `omp` TUI runs inside the sandbox, and its terminal is a **Herdr pane** that the Command Center already shows and lets you type into. Omega Prime drives it the way a human would (typed slash commands, keys), plus one thin, file-based channel for unattended ask answers (§4.2).

## 2. Where Herdr runs: host pane, sandbox process

| Option | Verdict |
|---|---|
| **A. Herdr on the box. The pane's process is `openshell sandbox exec --tty … -- omp`** | **Chosen.** The Command Center's `herdr` collector and `terminal_ws` already attach to the box's Herdr server, so this needs no new server, socket or image change. OpenShell gives the remote PTY. Herdr owns the local PTY and its scrollback/resume |
| B. A Herdr server inside the sandbox image | Rejected. It needs a second Herdr server per sandbox, plus a socket bridged out. OpenShell never authorizes loopback destinations, so that means another relay. It also needs an image rebuild, and the dashboard could not see the pane without new plumbing |

The pane command is built by `omega_prime/sandbox_control/herdr_pane.py::agent_pane_argv` (built and tested):

```
openshell sandbox exec -n <sbx> --tty --no-login-shell --timeout 0 --workdir /sandbox/work \
  --env HOME=/sandbox/home --env TERM=xterm-256color --env LANG=C.UTF-8 --env OMP_SANDBOX=1 --env PATH=... \
  -- sh -c 'echo $$ > /tmp/.ompsbx-<tag>.sid; [ -f /sandbox/home/.omp-sandbox.env ] && . /sandbox/home/.omp-sandbox.env; exec omp "$@"' \
  sh --model <m> --thinking <t> [--approval-mode yolo] [--continue]
```

- `--tty` replaces `--no-tty`, and `TERM=xterm-256color` replaces `TERM=dumb`. There is **no `--mode`**, so omp starts its interactive TUI, where `hasUI` is true and the `ask` tool registers. This is the same condition that forced `rpc-ui` in PROGRESS 00:45.
- With `--tty`, OpenShell starts the command as a **session leader that owns the PTY**. `sh` records `$$` (= the session id) in a sid file and `exec`s omp, so omp keeps the controlling terminal: resize (SIGWINCH) works, and `down`/abort kill the whole remote tree with `pkill -KILL -s $(cat sidfile)`, as `OpenShellPodmanTier.#killSession` does. **No `setsid`** on this path: checked live, `setsid -w` left omp with TTY `?` (detached from the PTY). The `--no-tty` RPC path keeps `setsid`.
- Secret-shaped env names (`TOKEN|KEY|SECRET|PASSWORD`) are **refused** on argv. Secrets live only in the 0600 env file staged over exec stdin.

### Launch sequence (per task)
1. `herdr workspace create --cwd <host task dir> --label <task-label> --no-focus`. This returns `root_pane.pane_id` and `workspace.workspace_id`.
2. `herdr pane wait-output <pane> --regex '[$#>%] ?$'`, the same shell-prompt wait as `scripts/omp-herdr-session.sh open`.
3. `herdr pane run <pane> -- <agent_pane_argv…>`.
4. `herdr pane report-agent <pane> --source omega-prime --agent omp --state working -- <resume argv>`. Herdr's `agent start --kind omp` detects omp by executable, but the pane's foreground process is `openshell`, so Omega Prime *reports* the agent lifecycle and gives Herdr a resume command (the same argv with `--continue`).
5. Record `herdr_pane {pane_id, workspace}` on the task row via the locked helpers. The Command Center then shows the live terminal.

### Operator observations (2026-10-23 AEDT, OpenShell 0.1.4, Podman driver, omp 18.8.4)
These were interactive checks on the box. Their full commands and exit codes were not recorded, so they are **observations, not completion claims**: re-run them before relying on them. The receipted evidence for this PR is the unit-test run in §8.
- `sandbox exec --tty` gives the remote process a real PTY (`/dev/pts/N`, 80x24 default, `TERM` forwarded), and exec exit codes pass through (`exit 7` → 7).
- `herdr pane run <pane> "<shlex-joined agent_pane_argv>"` in a fresh Herdr workspace rendered the **interactive omp TUI** (first-run provider setup, then the main prompt `π > GLM-4.6 > /sandbox`). omp ran as `Ssl+` on `pts/N` (foreground, with a controlling TTY).
- `send-keys C-c` left setup; `send-keys C-c C-c` exited omp back to the host shell, with no omp left in the sandbox. `stop_session_argv` (`pkill -s` on the sid) also killed it.
- The tools ran end-to-end against a probe sandbox: `sandbox_list`/`status` (Ready, DependenciesReady), `sandbox_exec` (stdout `omp/18.8.4`, exit 3 returned), `sandbox_exec` on a non-owned name refused, and `sandbox_down` deleted with no leftovers. The podman-events source over the same window emitted created → started → removed and **no false death** for the teardown's 137 kills.
- Not verified yet (needs broker env + credentials in the sandbox, i.e. the TS tier): a real GSD stage typed into the pane, ask widgets, and session-JSONL status parsing.

## 3. Auth: the broker relay still feeds it
Nothing changes in the trust model:
- `OpenShellPodmanTier.start()` still stages `/sandbox/home/.omp-sandbox.env` (0600, over exec stdin) with `OMP_AUTH_BROKER_URL=http://127.0.0.1:18990`. It also still runs the sandbox-local relay (`SANDBOX_RELAY_JS`) over one authenticated exec stdio channel to the host `omp auth-broker`.
- The pane command sources that env file before `exec omp`, so the interactive omp gets short-lived broker tokens exactly as the RPC omp did. No bearer ever reaches argv, Herdr's env (`--env` on `workspace create` carries no secrets) or logs.
- **Lifetime rule:** the relay and `forward service` processes belong to the host core (`server.ts`/the Anda-fronted core). The core must outlive every pane it launched. `down` closes panes first (step 6), then the relay, then deletes the sandbox.

## 4. Stages, ask dialogs and abort without RPC marshaling

### 4.1 GSD stages
| Today (rpc-ui) | Interactive pane |
|---|---|
| `{type:"prompt", message:"/<gsd-cmd> <MAGIC>\n\n<task>"}` | `herdr pane send-text <pane> "/<gsd-cmd> <MAGIC>"` + `send-keys Enter`. The task body goes in through a file (`/sandbox/home/tasks/<stage>.md`, uploaded over the gateway) referenced by the prompt, so multi-line text never depends on TUI paste handling |
| `prompt_result.status` | Herdr agent state from `report-agent` plus the omp **session JSONL** inside the sandbox, read via `sandbox_exec` (`tail` of the newest session file). This is authoritative for completed/failed and for the models seen |
| `get_available_commands` probe | `omp --version` + listing the project `.omp` GSD commands via exec (no session needed) |
| Fallback model on early failure | Same policy. If the session file shows no assistant turn, re-launch the pane with the fallback `--model` |

### 4.2 Ask dialogs: one thin channel is unavoidable for unattended answers
In the TUI, `ask`/`select`/`confirm` render as interactive widgets.
- **Attended:** Ove or the box agent answers in the Command Center terminal (that pane). No channel is needed.
- **Unattended:** pressing arrow keys blindly is fragile because widget layouts change between omp releases. So the one remaining control channel is an **omp extension, `ask-bridge`**, installed project-local in the sandbox next to GSD-Core. When an ask opens, it writes `/sandbox/home/.omp-ask/<id>.json` (`{id, method, questions, options, recommended}`). It then waits for `<id>.answer.json`, which Omega Prime writes via `sandbox_exec` (stdin, 0600). The widget then resolves exactly as an `extension_ui_response` would, so stale/unknown answer semantics carry over from `OmpRpcClient.answer` (StaleAnswerError). The TUI still shows the question and the answer.
- **Not built.** It needs the omp extension API surface to be verified on 18.8.4. Until then, unattended runs can keep `--harness rpc` (see §7).

### 4.3 Abort and stop
| Intent | Mechanism |
|---|---|
| Interrupt the current turn | `herdr pane send-keys <pane> Escape` (omp's interrupt). The agent is reported `idle` |
| Stop the agent | `send-keys C-c C-c`, then the session-group kill via the sid file if it is still alive after a grace period |
| Down | Close the panes → kill sids → stop the relay/forward → `openshell sandbox delete` → leftover check (containers/volumes/list), as in `OpenShellPodmanTier.dispose` |
The "a late frame overwrote aborted" bug (PROGRESS 01:27) cannot recur: abort state is owned by Omega Prime, not inferred from frames.

## 5. Sandbox control tools in the Omega Prime plugin (built)
New family "Sandbox" in the `user-omega-prime` MCP server (`omega_prime/tools/sandbox_control.py`), registered in `mcp_server.build_*`, the tool roster, the seat policy and the generated tool catalog:

| Tool | Approval | What it does |
|---|---|---|
| `sandbox_create` | **required** | Fail-closed preflight (OpenShell binary runnable, gateway `Connected`, policy file present), then `openshell sandbox create --name ompsbx-<slug>-<hex> --from localhost/omp-sandbox:0.1 --policy <policy-podman.yaml> --detach --no-tty --no-auto-providers`, wait for `Ready`, and evaluate the L14 readiness gate. Never starts the gateway, the Podman socket or an image build (those stay with the omp-sandbox tier and the operator) |
| `sandbox_exec` | **required** | argv only (no shell string), cwd confined to `/sandbox`, secret-shaped env refused, bounded and redacted output tails, exit code returned; emits `exited` when the exec exit code ≠ 0 is a sandbox fault (L15) |
| `sandbox_down` | **required** | `openshell sandbox delete` + leftover check; emits `removed` |
| `sandbox_status` | read-only | One sandbox: phase, containers, readiness reasons |
| `sandbox_list` | read-only | All OpenShell sandboxes (owned ones are flagged by the `ompsbx-` prefix) |

Mutations only touch sandboxes this family owns (`ompsbx-` prefix). `osp-*` sandboxes from the TS core are visible to status/list but never exec'd or deleted.

## 6. Lifecycle events (built)
- `omega_prime/sdlc_events/sandbox.py`: `SandboxLifecycle` (created, started, ready, not_ready, unhealthy, oom, exited, removed), `SandboxEvent` (CloudEvents 1.0 `to_cloudevent()`, subject `op.sbx.<name>.<lifecycle>`), `evaluate_sandbox_ready` (L14) and `on_sandbox_event`.
- **L14 gate:** ready only if the container is Ready, the broker relay is ready (when a broker is configured), **tools are connected** and **skills are connected**. With no probe configured, tools/skills are *not* connected, so `not_ready` + blocked with `tools_not_connected (#61)`. It fails closed until the #61 decision supplies the probe.
- **L15:** `exited` with code ≠ 0 → blocked on the owning task.
- **Rows:** each sandbox gets its own row `gb:sbx-<name>` (working → done on `removed`). Blocking lifecycles (not_ready, unhealthy, oom, exit≠0) also mark the **owning** task blocked. An owner row is never marked done by a sandbox event.
- **L13 source:** `omega_prime/sdlc_events/sources/podman_events.py` parses `podman events --format json --filter type=container`. The format was verified on this box: `Status` create/init/start/attach/died/cleanup/remove, `ContainerExitCode` on died/remove, and workload names `openshell-<ws>--<sandbox>-<uuid>` with supervisor `openshell-supervisor-<uuid>`. It maps workload events to lifecycle and supervisor deaths to `unhealthy`, and dedupes with `podman:<ID>:<Status>:<timeNano>` delivery ids.
- **Teardown vs crash:** a death is held for a 10 s grace. A removal within the grace of *event time* drops only deaths with a delete's signal code (137/143); exit 1 or an unknown code is still emitted before `removed`. A removal is paired before anything expires on its line. Two timers release a held death and are kept apart: stream time (the newest event time on any line, including ignored events, so replays are deterministic) and, for a live pipe with `clock=time.time`, the wall time since the death *arrived*. Events that reach the reader late (a queued backlog) therefore still pair. `with_heartbeats(stdout)` flushes held deaths on a quiet stream.
- **Ownership records:** per-sandbox `flock` and a unique temp file per write, so a background RLM child and a host call can both run `sandbox_down` safely. A failed delete clears the teardown mark (later deaths are real again), and a mark expires after 10 minutes regardless. The sandbox uuid stays on the record until cleanup passes, so a retry still finds `openshell-supervisor-<uuid>` leftovers.
- **Failure results:** a failed Podman leftover probe is `leftover_check_failed`, never an empty result. Create errors carry the allocated `name`. A `report_exit` exec whose exit is unknown (timeout, missing CLI) emits a blocking `exited` with `exit_unknown:<code>`. The runner keeps only the last 64 KiB of each stream while the command runs. A clipped control reply (any `openshell` call other than `exec`) is `control_output_truncated`, and a clipped Podman leftover listing is `leftover_check_failed`, so `down` never reports `removed` from a partial listing. Leftover listings are filtered server-side by sandbox name and supervisor uuid to stay small. `OMP_SANDBOX_OPENSHELL` may be a bare command name, resolved on `PATH`.
- **Sinks:** the CloudEvents JSONL outbox (`$OMEGA_PRIME_STATE_DIR/events/outbox.jsonl`, 0600, for the future NATS bridge) + `on_sandbox_event` → task store. The live tasks.json store uses the dashboard's locked helpers when the dashboard package is importable. Otherwise it degrades to outbox-only and every tool result says so (`task_store: unavailable`); it never writes tasks.json by hand. Rows it creates carry `owner {kind: sandbox-control, ref: <task id>}`, which the dashboard validator requires. The family reads the resolved env from `default_registry` (so `OMEGA_PRIME_STATE_DIR` from `settings.json` applies), not `os.environ`.

## 7. Changes in omp-sandbox (TS), spec-only in this PR
1. `src/core/interactive.ts`: `InteractiveSession {launch(stage), sendPrompt, interrupt, stop, state(), answer()}` on top of the Herdr CLI + `agent_pane_argv` semantics. It reads the session JSONL for status and models.
2. `core.ts`: `#spawnOmp` → `InteractiveSession` behind `OMP_SANDBOX_HARNESS=interactive|rpc` (default `rpc` until e2e parity, then flip). `answer()` routes to ask-bridge; `abort()` → interrupt + stop.
3. `providers/openshell-podman.ts`: `spawnTty(argv)` mirroring `spawnStdio` with `--tty` and `TERM=xterm-256color`.
4. The `ask-bridge` omp extension, installed by `ensureGsd` project-local.
5. `tests/e2e.py`: run the existing plan/execute/ask/stale/abort/down matrix in both harness modes.

**Sequencing:** omp-sandbox enters Omega Prime via the draft stack #54 → #55 → #61 → #65 → #67. The TS changes land on top of that stack after Ove's #61 call; this PR is Python-only and does not depend on the stack.

## 8. Built vs spec-only (this PR)
| Item | State |
|---|---|
| `sandbox_create/exec/down/status/list` tools + registry/roster/policy/catalog | **Built + unit tests** (fake runner); live smoke observed, unreceipted (§2) |
| `on_sandbox_event`, `evaluate_sandbox_ready` (L14), L15 mapping, CloudEvents envelope | **Built + unit tests** |
| podman events parser/source (L13) | **Built + unit tests** on real captured event lines |
| `agent_pane_argv`, `stop_session_argv`, `HerdrAgentPane` (launch/report/prompt/interrupt/stop) | **Built + unit tests**; TUI launch and stop observed live, unreceipted (§2). Not yet exposed as an MCP tool |
| Interactive TS harness, ask-bridge extension, e2e parity | **Spec only** (§7) |
| Live podman-events daemon wiring into the dashboard lifespan | **Spec only.** The dashboard lives in the herdr-ws tree; it's a one-line lifespan hook once the dashboard lands on main |

**Receipt (00073 review round):** `python -m pytest -q -p no:cacheprovider omega_prime/tests/sandbox_control omega_prime/tests/sdlc_events` → `86 passed`, exit 0. `ruff check omega_prime/` → exit 0. `mypy` on the changed modules → exit 0.

## 9. Needs Ove's yes
- **Nothing for this PR** beyond the normal merge (Ming merges). No deploy, no Railway, no VPS, no installs: `herdr`, `openshell` 0.1.4 and rootless Podman are already on the box.
- **Later (TS phase):** baking `ask-bridge` into `localhost/omp-sandbox:0.1` means a **local image rebuild** (box-local, not a deploy). Flipping the default harness from `rpc` to `interactive` changes how every sandbox run behaves, so it's his call after e2e parity.
- **Approval mode:** keeping `--approval-mode yolo` *inside* the sandbox (the sandbox is the boundary, as today) vs omp's default approvals in the interactive TUI, which would stop unattended runs on every tool call. The suggested default is yolo inside the sandbox only.
- **#61:** what "tools/skills connected" means (connect host MCP + skills / refuse ready / document the gap) defines the L14 probe. Until then L14 fails closed.
