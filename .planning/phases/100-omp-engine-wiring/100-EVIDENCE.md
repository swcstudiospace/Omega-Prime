# Phase 100 execution evidence

Execution evidence for plans 100-01 through 100-05. Installed omp was inspected at execution time; plans' 18.8.4-specific assumptions are not treated as current without evidence.

## Installed omp identity

- `omp --version` returned `omp/18.12.3`; `omp --help` returned `omp v18.12.3`.
- Binary resolves to the installed global `@oh-my-pi/pi-coding-agent` CLI entrypoint. The source tree was available.
- `--skills` is documented as a comma-separated glob filter over skill names; it is not a directory option. `--thinking` accepts `off, minimal, low, medium, high, xhigh, max, auto`; phase implementation adds only `max` and preserves the major-18 compatibility check.

## MCP discovery precedence and schema

- `src/discovery/builtin.ts` constructs the native project candidates as `<cwd>/.omp/mcp.json` then `<cwd>/.omp/.mcp.json`, followed by the active user agent directory's `mcp.json` and `.mcp.json` (builtin.ts:234-242). `<cwd>` is the loader's project root; `src/mcp/loader.ts` documents that root as its `cwd` argument.
- `src/mcp/types.ts` defines `MCPStdioServerConfig`: `command` required; `args`, `env`, and `cwd` optional; `type` optional and defaults to stdio (types.ts:105-120); top-level config has `mcpServers` (types.ts:150-158). `src/mcp/config.ts` merges discovered provider results into name-keyed config output (config.ts:147-165). Project discovery is enabled by default (config.ts:103-105).
- The RPC `get_state` success data includes `dumpTools` with the session's active tool names (rpc-mode.ts:2025-2069; rpc-types.ts:151-197). MCP tool names are produced as `mcp__<server>_<tool>` by `src/mcp/tool-bridge.ts:417-429`; the configured server key `omega-prime` therefore has an identifiable namespace for session-visible discovery.
- The running session exposes tool discovery through `get_state.dumpTools`; status must be set only from that returned list after setup, not from the file. Same-name precedence still depends on loader provider ordering and is not inferred here.

## Skills loader and inventory

- `src/discovery/builtin.ts` walks project ancestors for `.omp/skills/` (builtin.ts:309-329). `src/extensibility/skills.ts` supports additive `skills.customDirectories` and scans those roots (skills.ts:289-305, 470-481). Skill listing uses the real installed loader (`src/cli/skill-list.ts:27-89`) and reports each resolved manifest path.
- The installed operator config exposes a `skills.customDirectories` entry for the installed GSD-Core skills directory; its exact path is intentionally omitted here. `omp skill list --json` in this checkout returned real `gsd-*` skill manifests from that directory, including `gsd-autonomous`, without provider calls.
- A provider-free run of `omp skill list <checkout> --json` against a temporary project `.omp/skills` view containing symlinks returned `omega-commands` and `python` from their canonical repository skill manifests, alongside the real configured `gsd-autonomous` manifest. The temporary project view was removed after the probe.
- Installed 18.12.3 source `src/main.ts:1710-1714` maps `--skills` to the session `includeSkills` setting; `src/extensibility/skills.ts:349-352` applies each glob to the final skill name. The `skill list` command reports discovered manifests but does not apply that session filter; the fake session test asserts the actual launch argument is `*`.

## rpc-ui request/response fixtures

Observed in the installed source `src/modes/rpc/rpc-types.ts:693-729,874-884` and `src/modes/rpc/rpc-mode.ts:377-394,1215-1272`:

- Request base: `{type: "extension_ui_request", id: string, method: string}`. Confirm adds `title`, `message`; select adds `title`, `options: string[]`; input adds `title` and optional placeholder; ask adds an `answers`-request shape of `questions: [{id, question, options: [{label, description?, preview?}], multi?, recommended?}]`. UI requests include additional methods; only ask/select/confirm are accepted for the live clarify route.
- Response correlation reuses the original `id`. Select/input response uses `{type: "extension_ui_response", id, value: string}`; confirm uses `confirmed: boolean`; ask uses `answers: [{id, selectedOptions: string[], customInput?}]`; cancellation is `{cancelled: true}`. The host's response is delivered via the pending map and does not wait for a response acknowledgment.
- The request writer emits an extension UI request and stores the id in a pending-response map before returning to await it (rpc-mode.ts:1260-1272). Extension UI requests are written directly to the RPC output path; `set_event_filter` filters session events, not UI frames (rpc-mode.ts:1409-1411, 2196-2209). `ask` is emitted only after the host opts in with `set_ask_dialog` (rpc-types.ts:721-728).

## Prerequisites and execution scope

- Operator explicitly directed parallel wiring: Phase 100 executes against the current code without waiting for Phase 97-99 verification/merge gates. Phase 97 owns timeout changes; this phase leaves `omp_driver._TURN_TIMEOUT` and related constants unchanged.
- Phase 99's behavior is not inferred from roadmap status. This evidence records the operator's explicit decision to proceed under parallel phase execution, not a claim that Phase 99 was merged or reverified here.
- Live clarify to Ove is the selected default. No policy-file answers are implemented.

## Final gates

Run on 2026-10-20 against the Phase 100 final source:

- `ruff check omega_prime/`: exit 0.
- `ruff format --check omega_prime/`: exit 0; 599 files already formatted.
- `mypy omega_prime/`: exit 0; 472 source files.
- `pytest omega_prime/tests -q`: exit 1; 27 failures, 4867 passed, 11 skipped. The 27 failures exactly match the Phase 100 validation baseline caused by the empty `prime-agent` submodule. No Phase 100 failure remained; names and root causes are in `100-SUMMARY.md`.
- RPC-UI/fake-engine focused tests: 11 passed; driver, skills, and IPC tests: 29 passed; real installed skill loader test: 1 passed; local readiness profile CLI: 1 passed; HITL tests: 27 passed.
- `python -m omega_prime.tooling.catalog --check`: exit 0, generated tool catalog current.
- `test ! -e .omp/mcp.json && test ! -e .omp/.mcp.json`: exit 0; no project MCP config was left behind.
- Prompt assembly check not run: no prompt sources changed.

The suite's one-click dry-run failure still reports the uninitialized `prime-agent` submodule; RPC-UI control health is a warning for the unconfigured local operator directory, not a test failure. No live Ove round trip, provider/model session, connector or deployment was exercised.
## Settled decisions and remaining limits

- `max` is accepted by installed omp 18.12.3 help. Do not add `auto` as a repository-accepted level.
- Project MCP uses the observed `.omp/mcp.json` candidate, with fail-closed ownership and cleanup. Do not mutate global MCP config or copy the omp login store.
- Real GSD and Omega skill resolution is observed through the installed `omp skill list --json` loader: a temporary project symlink view resolved Omega `omega-commands` and `python`, while the configured GSD custom directory resolved `gsd-autonomous`. Permanent tests still need to verify the `--skills` wildcard and the implementation's attachment lifecycle.
- No live Ove round trip or provider/model session was performed. No facts about successful connector deployment or production behavior are claimed.
