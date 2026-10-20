# Phase 100 research digest: omp engine wiring

Planning-session digest of three research-worker reports and parent-observed
installed-omp evidence. The research workers did not run tests, gates,
installed commands, live sessions, or network operations. The parent session
ran `omp --help` and the exact four gates; this digest reports those supplied
observations separately below. Sources: `agent://phase100-research-1` (MCP),
`agent://phase100-research-2` (skills), `agent://phase100-research-3`
(UI/HITL), plus spot re-reads of cited engine files during planning. A
planning worker observed installed package filenames only, not loader or
protocol contents. Line numbers were as read on 2026-10-21; re-read before
execution.

## 1. Verified repository symbols (read in this worktree)

| Location | Symbol | Verified fact |
| --- | --- | --- |
| `omega_prime/engine/mcp_bridge.py:11-26` | `_SKILLS`, `skills_dir()`, `install_root()` | Skills root is the fixed `<install>/omega_prime/skills` directory; its docstring claims `--skills` loads it. |
| `omega_prime/engine/mcp_bridge.py:29-59` | `write_mcp_json()` | Writes `mcp.json` and `.mcp.json` into a supplied profile dir via unconditional `Path.write_text`; payload is `mcpServers["omega-prime"]` with `sys.executable -m omega_prime.mcp_server --root <install> --work-root <checkout>`; no credentials, no `CURSOR_PLUGIN_ROOT`. Serialization only — not a loader/attachment proof. |
| `omega_prime/engine/omp_driver.py:362-399,421-428` | `_run()` | Creates `jobs/<id>/profile`, writes MCP JSON there, passes `--cwd <work>` while the child's process cwd is the profile; passes `skills_dir()` (an absolute directory) to `--skills`. |
| `omega_prime/engine/omp_driver.py:47-49` | `_READY_TIMEOUT=15.0`, `_COMMAND_TIMEOUT=15.0`, `_TURN_TIMEOUT=120.0` | Engine source still contains timeout arguments today (see §5). |
| `omega_prime/engine/omp_driver.py:139-230` | `OmpSession` | Tracks proc/events/stdout queue only; `send()` generates a fresh command id; no pending-request map, no UI handling, no job/session/turn generation. |
| `omega_prime/engine/omp_driver.py:201-236,594-601` | `close()`, `_next()`, `session_abort()` | `close` closes stdin → wait → kill; `_next` returns `None` for both queue timeout and child EOF; `session_abort` only sends abort and awaits its RPC ack. |
| `omega_prime/engine/omp_driver.py:522-550` | `_accept_prompt` | Uses one absolute deadline for the prompt wait. |
| `omega_prime/engine/protocol.py:20-28` | `COMMANDS` | `prompt, steer, abort, set_model, set_thinking_level, set_event_filter, get_session_stats` — no `extension_ui_response`, no discovery/`get_state` command. |
| `omega_prime/engine/protocol.py:30-43` | `STREAM_EVENTS`, `EVENT_FILTER` | Ten stream events plus `response`; no UI request event, no attachment events. |
| `omega_prime/engine/protocol.py:45-47` | `THINKING_LEVELS`, `MODES` | Levels `off, minimal, low, medium, high, xhigh` — `max` and `auto` absent today; `MODES` already accepts `rpc` and `rpc-ui`. |
| `omega_prime/engine/protocol.py:80-91` | `loads()` | Accepts any object frame without request-schema validation; malformed lines become notices. |
| `omega_prime/engine/jobs.py:45-75,128-184,186-217` | `append_event`, `write_json`, `child_env()` | Redacted persistence plus a narrow allowlist env; `OMEGA_PRIME_OMP_HOME` becomes `PI_CODING_AGENT_DIR=<override>/agent`; arbitrary incoming `PI_CODING_AGENT_DIR`/`XDG_CONFIG_HOME` are not passed through. |
| `omega_prime/engine/attach.py:118-172` | `route_turn()` | Can fall back to Python for pre-prompt engine failures in `auto`, refuses to replay once started — so `attach_failed` must be nonfatal attachment state, not `EngineResult(ok=False)`. |
| `omega_prime/tools/clarify.py:1-28` | `ClarifyLog`, `clarify()` | Module docstring: "This port does not open a UI. It appends the question it was given and returns that question." `clarify()` appends and returns `{"question": question}` — an echo, not HITL. |
| `omega_prime/tools/coding.py:39-53,90-96,251-257` | registration | Registers the one-arg echo wrapper with a schema/description promising the echo. |
| `omega_prime/tools/dispatch.py:100-125` | `omega_jobs_status`/`omega_jobs_cancel` | Target the remote coding fleet via `FleetClient`; no local omp session answer/abort surface. |
| `omega_prime/engine/cli.py:13-29`, `omega_prime/engine/watch.py:20-42` | `watch` | CLI exposes watch only (wrapper caps `--timeout` at 2s default; the API supports `timeout=None`); unknown events print only their type name. |
| `omega_prime/mcp_server.py:1234-1255` | `ToolGate` | Serializes tool calls per registry and holds the slot until the worker ends — an answer/cancel surface implemented as an ordinary gated tool would deadlock behind a blocked clarify/engine call. |
| `omega_prime/tests/test_engine.py:47-124` | `_FAKE` | Fake omp logs argv, emits `ready` unconditionally, streams a full prompt, answers every other command generically; loads no MCP config and no skills. |
| `omega_prime/tests/test_engine.py:135-168` | `_install_fake`, `_env` | Hermetic fake-binary install and fake env (unsandboxed default). |
| `omega_prime/tests/test_engine.py:229-290` | `test_fake_omp_session_writes_events_mcp_and_commands` | Currently treats an argv value ending in `omega_prime/skills` as success — a directory-echo assertion, not skill discovery. |
| `omega_prime/tests/test_engine.py:902-1001` | login/sandbox tests | Assert no `--profile`, no copied `agent.db`, writable shared-home bind, custom `PI_CODING_AGENT_DIR` on all child invocations. |
| `omega_prime/commands/generated.py:110-112` | `checked_in_skills(root)` | Existing checked-in skill catalog entry point returning folder names. |
| `omega_prime/skills/<name>/SKILL.md` (28 manifests) | frontmatter names | `android, code-review, contract-first-changes, debugging, deno-typescript, desk-bootstrap, desk-doctor, desk-gateway, getting-started, gotxcot-uplift, greptile-merge-gate, hindsight-memory, ios, lead-pack, omega-commands, python, ragflow-docs, railway-tailscale, remote-dev-machine, rust, security-secrets-handling, security-supply-chain, terraform-k8s, tool-packs, trackplan-dispatch, ultrathink, vercel, verification-receipts`. An `omega-*` glob selects 1 of 28. A further 14 `SKILL.md` manifests live under `omega_prime/redteam/pack/skills` (separate root; not engine skills). |
| `docs/tool-catalog.md:3-4` | generated header | `DO NOT EDIT — generated by python -m omega_prime.tooling.catalog`, rebuild `--out docs/tool-catalog.md`. |
| `.planning/seeds/SEED-009-gsd-core-bridge.md:18,30-38` | seed | GSD-Core absent from the product; bridge-not-vendor decision; historical install breadcrumbs labeled source-only, not runtime proof. |

## 2. Parent-observed installed omp evidence (help only; internals unverified)

The parent session ran `omp --help` for v18.8.4. It showed:

- `--cwd` selects the directory to start in and overrides launch cwd.
  The help does not establish MCP discovery roots or config precedence;
  those require the permitted execution-time evidence in 100-00.
- `--skills` is a comma-separated skill-name glob filter, not a directory
  path. Repository code passes a directory today (§1 `_run`).
- Thinking levels listed: `off, minimal, low, medium, high, xhigh, max, auto`.
  `max` is required; `auto` is out of scope.

The parent also observed the exact four gates and baseline documented in
`100-VALIDATION.md` §6. Research workers did not run the installed binary or
these gates. No installed-loader or protocol contents were read; reconfirm
required behavior through 100-00 evidence before execution claims.

## 3. External source not read (vs. what is merely missing)

Two different evidence gaps must not be conflated:

- **Installed omp internals — external, not read.** The installed omp
  loader/config precedence (whether/where `.omp/mcp.json` is read, project vs
  agent-root precedence, merge behavior), the discovery/`get_state` response
  schema, and the exact `extension_ui_request`/`extension_ui_response` wire
  envelope were not observed. Exact-name repository search found **no**
  implementation of `extension_ui_request`/`extension_ui_response` anywhere in
  this repo; alternate-name searches found only the Phase 100 roadmap text.
  Plans must treat these as prerequisites to establish (100-00), never invent
  config keys, wire fields, or method names. A fake proves the repository-side
  contract only and must be labeled as such.
- **Real GSD skills — genuinely absent from the repository.** Repo-wide
  manifest discovery found no GSD `SKILL.md`, no GSD skill implementation, and
  no installed loader contract for one. SEED-009 records GSD-Core as absent
  with a bridge-not-vendor decision; its historical install breadcrumbs are
  not runtime proof. This does **not** assert GSD is absent from the
  operator's machine — the operator's machine was not inspected. Missing GSD
  sources are an explicit execution prerequisite/stop condition, never a fake
  placeholder skill and never a hardcoded historical path.

## 4. What "clarify" is today

`omega_prime/tools/clarify.py` is an in-memory question recorder and echo: it
appends the question to a `ClarifyLog` and returns `{"question": question}`;
its own docstring says it does not open a UI. Consumers
(`test_tools.py:300-315`, `grokbot/readiness_local.py`, `test_grokbot_e2e.py`,
`grokbot/emulator.py`) rely on the echo. Routing rpc-ui asks to "clarify"
without adding a real operator delivery/reply/cancel surface would therefore
satisfy nothing: the ask would be recorded and echoed, never answered. No
existing surface (Telegram tools, fleet status/cancel, approvals API, watch)
binds a human reply to a live omp UI request; the approvals API and
streamable-principal infrastructure are reusable *patterns*, not an existing
Ove transport. UI confirmation must not become connector-tool approval.

## 5. Timeout mismatch (recorded discrepancy)

The uplift states time caps were removed in wiring phase 1 (GSD Phase 97, this
milestone's registration does not verify it). Current engine source still
contains timeout arguments: `_READY_TIMEOUT = 15.0`, `_COMMAND_TIMEOUT = 15.0`,
`_TURN_TIMEOUT = 120.0` (`omega_prime/engine/omp_driver.py:47-49`), and
`_accept_prompt` waits under one absolute deadline (`omp_driver.py:522-550`).
Phase 100 must not expand into cap removal nor add new caps; at the same time
a 120-second turn deadline must never terminate an uncapped human wait. The
UI plans (03/04) must treat "compatible uncapped wait contract" as a
prerequisite from Phase 97 and record the discrepancy outcome; plan 100-00
verifies prerequisite history before implementation.

## 6. Baseline gates observed by the planning session

Observed once by the parent in this worktree (do not rerun to confirm; see
100-VALIDATION.md): `ruff check omega_prime/` exit 0; `ruff format --check
omega_prime/` exit 0 (589 files); `mypy omega_prime/` exit 0 (462 files);
`pytest omega_prime/tests -q` exit 1 — 27 failed, 4777 passed, 11 skipped,
15 warnings, 232.67s, all 27 concerning an empty/uninitialized `prime-agent`
(exact node ids in 100-VALIDATION.md). Planning changed no application or test
files before these commands. These are observations about the environment at
planning time, not evidence about future implementation behavior.

## 7. Per-stream headline findings

- **MCP (report 1):** today's flow writes MCP JSON into the job profile but
  launches with `--cwd <work>`; the mismatch means attachment is unproven.
  Recommended: work-tree-local exclusive-ownership lease at
  `<effective --cwd>/.omp/mcp.json`, safe backoff (no merge, no overwrite) on
  any pre-existing config including symlinks, `attached` only from
  session-visible tool discovery, `attach_failed` nonfatal to the engine turn,
  crash-safe next-acquisition recovery, global `~/.omp/agent/mcp.json`
  untouched, no secrets in generated config.
- **Skills (report 2):** `--skills` must become name globs; sources must be
  established separately through omp's actual discovery mechanism (unknown —
  prerequisite). Derive Omega names from the checked-in catalog (28 names; no
  `omega-` prefix family); preserve user skill sources; GSD exposure only if
  real manifests are found; fake argv assertions prove nothing.
- **UI (report 3):** needs one event-dispatch owner for all child stdout, UI
  replies keyed by the original external request id with a
  job/session/turn/request identity tuple, answer-once with atomic
  stale/duplicate rejection, cancellation usable during pending human waits
  (never behind the serialized `ToolGate`), redacted `ui_pending` /
  `ui_answered` / `ui_rejected_stale` persistence, and a real operator
  delivery/reply/cancel surface with an honest authorization boundary
  (local operator process boundary or authenticated principal + job
  ownership; possession of an id is not authorization).

## 8. Explicitly unverified (carried into plan stop conditions)

- Installed omp `.omp/mcp.json` search/merge/precedence behavior.
- Installed discovery/`get_state` response schema and namespaced tool names.
- Exact `extension_ui_request`/`extension_ui_response` envelope, method
  vocabulary, id correlation, and cancellation frames.
- Real GSD skill manifests/names and a supported discoverable location.
- Runtime `omega_prime.mcp_server` importability from an unrelated work
  checkout in every supported install mode.
- Real bubblewrap execution and child env inheritance (wrapper tests log argv
  only); post-SIGKILL cleanup (guarantee is next-safe-acquisition).
- Live operator delivery to Ove (any scripted smoke is not a live round trip).
- Rule adherence by future agents.
