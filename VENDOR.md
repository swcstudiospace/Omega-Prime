# Vendored behavior

Hermes and Omp remain behavior ports: their ignored checkouts are not imported
at runtime. Prime is also a read-only pin, but as a tracked submodule, and optional kernel tools
import its Python `rlm` runtime in-process, and the product's PyO3 bridge links
its Rust libraries. The checkout is not patched or copied into Omega's source
tree. Ported modules retain their upstream MIT notices.

## Hermes Agent

- Remote: https://github.com/NousResearch/hermes-agent.git
- Commit: `1a4508e2aff2db5f50409893a2115be777bd5643`
- Modules adapted: `agent/conversation_loop.py`, `agent/turn_iteration_prep.py`, `agent/turn_tool_round.py`, `agent/turn_final_response.py`, `agent/turn_finalizer.py`, `agent/prompt_builder.py`, `agent/iteration_budget.py`, `agent/interrupt_control.py`, `agent/turn_facade_lease.py`, `agent/agent_runtime_helpers.py` (`apply_pending_steer_to_tool_results`), `agent/turn_preflight.py`, and `agent/conversation_compression.py` from commit `1a4508e2aff2db5f50409893a2115be777bd5643`.
- Tool modules adapted: `tools/registry.py`, `tools/file_operations.py`, `tools/patch_parser.py`, `tools/file_operations_search.py`, `tools/terminal_tool.py` (local argv backend only), `tools/todo_tool.py`, `tools/clarify_tool.py`, `tools/web_tools.py`, and `tools/vision_tools.py` from commit `1a4508e2aff2db5f50409893a2115be777bd5643`.
- Growth modules adapted: `tools/skill_manager_tool.py`, `tools/skills_tool.py`, `agent/skill_utils.py`, `tools/memory_tool.py`, `tools/memory_tool_store.py`, `agent/memory_provider.py`, `agent/memory_manager.py`, `tools/session_search_tool.py`, and `agent/curator.py` from commit `1a4508e2aff2db5f50409893a2115be777bd5643`.
- Delegation and cron tick adapted: `tools/delegate_tool.py` and `cron/scheduler_tick.py` from commit `1a4508e2aff2db5f50409893a2115be777bd5643`.
- Platform modules adapted: `tools/code_execution_tool.py`, `tools/mcp_tool.py`, `tools/browser_tool.py`, `tools/approval.py`, and plugin `register` (`hermes_cli/plugins.py` `register_tool`) from commit `1a4508e2aff2db5f50409893a2115be777bd5643`.
- License: MIT
- Copyright: Copyright (c) 2025 Nous Research
- What is adapted: the agent runtime. Conversation loop and turn phases, session, budget, interrupt, prompt builder, compression, tool registry and toolsets, skills and curator, memory and session search, delegation, cron, and plugin registration for tools, memory, and model providers.
- What is not adapted: TUI, desktop, website, UI locales, nix and docker packaging, messaging gateways, Feishu, Yuanbao, Home Assistant, Spotify, and the kanban UI. A later phase may pull a piece in only if that phase cannot pass its own tests without it.

## oh-my-pi (Omp)

- Remote: https://github.com/can1357/oh-my-pi.git
- Commit: `0e2411c0df59fce8c56dc03a5f1c9afffcb0d746`
- Harness modules adapted: `packages/agent/src/agent-loop.ts` (turn events, `beforeModelCall`), `packages/agent/src/live-steering.ts`, `packages/agent/src/pause.ts`, `packages/agent/src/output-budget.ts` (`fitOutputTokensToContextWindow`), `packages/agent/src/compaction/pruning.ts`, and `packages/agent/src/speculative-execution.ts` from commit `0e2411c0df59fce8c56dc03a5f1c9afffcb0d746`.
- Edit modules adapted: `packages/coding-agent/src/edit/schemas.ts` (patch op), `packages/coding-agent/src/edit/settings.ts` (`edit.fuzzyMatch`), `packages/coding-agent/src/edit/normalize.ts` (line endings), and the repair-after-failure shape of `packages/coding-agent/src/edit/auto-repair.ts` (deterministic passes, no model call) from commit `0e2411c0df59fce8c56dc03a5f1c9afffcb0d746`.
- IDE modules adapted: `packages/coding-agent/src/jsonrpc/message-framing.ts`, `packages/coding-agent/src/lsp/client.ts` (initialize, didOpen, publishDiagnostics, shutdown), and `packages/coding-agent/src/dap/session.ts` plus `client.ts` (launch, breakpoints, stop, threads, stackTrace) from commit `0e2411c0df59fce8c56dc03a5f1c9afffcb0d746`.
- Mode and learning modules adapted: `packages/coding-agent/src/session/agent-storage.ts` (versioned documents), `packages/coding-agent/src/plan-mode/plan-protection.ts` (plan refusal), `packages/coding-agent/src/extensibility/hooks/runner.ts` (first stop wins), `packages/coding-agent/src/autolearn/controller.ts` (capture after a substantive turn), `packages/coding-agent/src/goals` (objective plus steps), `packages/coding-agent/src/advisor/advise-tool.ts` (advisory blocks), `packages/coding-agent/src/exec/exec.ts` (argv jobs), and `packages/coding-agent/src/security/preflight.ts` (check before action) from commit `0e2411c0df59fce8c56dc03a5f1c9afffcb0d746`.
- Memory modules adapted: `packages/coding-agent/src/memory-backend/tool-names.ts` (retain/recall verbs), `packages/coding-agent/src/hindsight/client.ts` (bank retain/recall), and `packages/mnemopi/src/core/memory.ts` (remember/recall/get/forget) from commit `0e2411c0df59fce8c56dc03a5f1c9afffcb0d746`.
- Provider modules adapted: `providers/base.py` (declarative profile) and `packages/ai/src/providers` (`xai-base-url.ts`, `openai-completions.ts`, `anthropic-client.ts`, `google.ts`, `ollama.ts`, `mock.ts`) from commits `1a4508e2aff2db5f50409893a2115be777bd5643` and `0e2411c0df59fce8c56dc03a5f1c9afffcb0d746`.
- License: MIT
- Copyright: Copyright Mario Zechner 2025, Can Bölük 2025-2026, Stencil Labs 2026
- What is adapted: `packages/agent` behavior, the coding-agent tool and edit pipeline, LSP, DAP, sessions, tasks, MCP, capabilities, extensions, modes and plan mode, memories, hindsight, mnemopi, autolearn, goals, advisor, exec job control, agent-side security, and the `packages/ai` provider surface. The port is Python inside the same Omega Prime agent. TypeScript tests are the spec.
- What is not adapted: `packages/tui`, collab web, stats site, CLI gallery and install chrome, Rust crates, and bazel or nix packaging. Same exception as Hermes: pull a piece in only when a phase's tests cannot pass without it.

## Prime Agent

- Remote: https://github.com/PrimeIntellect-ai/prime-agent.git
- Commit: `72f790babc3befe23b1ff12d3f0049be3977e335` (2026-10-20). Moved from `967eb13fd488507af5f590e9c6ea8b2672f1fc05` (2026-10-07) on 2026-10-22 to pick up upstream's `imported_session_compacts` test fix (#3678). This is the earliest upstream commit that contains it. The workspace version moves from 0.9.8 to 0.10.0, and the toolchain stays 1.98.1. The `@ 967eb13f` provenance notes in ported modules and parity fixtures still name the commit each port was read from.
- Rust toolchain: `1.98.1`, recorded in `omega_prime/contracts/prime-agent.pin.json`.
- License: MIT
- Copyright: PrimeIntellect
- Role: read-only behavior-port source, CI parity oracle, and library source for the optional in-process runtime. The Rust workspace has 9 library crates: `pa-telemetry`, `pa-types`, `pa-ai`, `pa-models`, `pa-agent`, `pa-core`, `pa-daemon`, `pa-tui`, `pa-cli`. Since `72f790b` it also has `pa-perf`, upstream's corpus and bench harness. That crate is `publish = false` and never ships, so `workspace_members()` and the native bridge leave it out. The kernel-side Python runtime lives in `prime-agent-runtime/src/rlm/`. `native/omega-prime-prime` links those Rust libraries without starting their TUI or daemon supervisor.
- What is adapted (v10): the Python capability contracts for RLM recursion, continual harness, persistent goals, heartbeats, autonomous budgets/gates and agent messaging. The existing Omega conversation loop owns goal/autonomous continuation and named heartbeat re-entry.
- Optional native runtime: `prime.kernel.enabled` imports pinned Python cells/factory/bash/skills; `OmegaPrimeAgent.from_prime` calls actual Rust providers independently of that flag. Linked-crate probes are diagnostics, not a full native application merge. A live child runner is still required for RLM child execution.
- What is not integrated: native `pa-agent` or `pa-core::SessionEngine` ownership of the loop, the TUI, CLI binary, daemon supervisor/process model, telemetry sinks, managed AWS/Vertex authentication, and upstream installers.
- The Phase 53 “622 passed” number is not the full workspace. It is the sum of `test result: ok` lines in an unskipped fail-fast run (exit 101) that stopped in `pa-cli` `acp_mode_e2e` (42 passed, 3 failed in that binary). `pa-core`, `pa-daemon`, `pa-models`, `pa-telemetry`, `pa-tui`, and `pa-types` had not started.
- At `967eb13`, local `cargo test --workspace --locked` matched the CI oracle only when all four of these held:
  - `prime-agent` is absent from `PATH`. `pa-cli` differential tests treat `which prime-agent` as a TypeScript oracle unless `PA_TS_BINARY` is set, and they skip when that lookup misses. A published 0.9.8 binary is the wrong oracle: it fails `differential_corpus_matches_ts_binary` on the package-update hint (`update --force` versus `update`). It also runs `ts_daemon_differential_cli_output`. That test expects `send` to print `Sent to ...`. Without a model credential both CLIs exit 1 with the same `No API key found for the selected model` text, and paths point at the installed release under `~/.local/share/prime-agent/releases/`. Identical stderr is still a failure. Measured locally on 2026-10-08 with `/root/.local/bin/prime-agent` → 0.9.8 (`83fb0912…`): workspace `cargo test` exited 101 on that one test.
  - Provider credentials are unset, including `GH_TOKEN`, `GITHUB_TOKEN`, and `OPENROUTER_API_KEY`. With those set, startup resolution selects `github-copilot/claude-fable-5` and the catalog lists `github-copilot` and `openrouter` beside `prime-inference`. Three `pa-daemon` tests then fail: `the_startup_chain_refuses_models_outside_the_allowlist`, `switch_model_never_poisons_the_selection_with_a_refused_candidate`, and `model_catalog_and_available_models_match_the_ts_shapes`. CI does not export them.
  - `futimens` updates ctime. `cache_rejects_replacement_and_same_length_in_place_rewrite` restores mtime after a same-length rewrite and expects the scan generation (`len`, inode, mtime, ctime) to change. On Linux 6.8.0-142 / ext4 here, `futimens` leaves ctime unchanged, and a rewrite inside one timestamp tick keeps the generation identical, so the cached name stays `bravo` while the file reads `delta`. The test is not in the pin skip set. The parity gate is `ubuntu-latest`, where `futimens` bumps ctime. Re-ran 2026-10-08 with `--exact` filter `session_store::info_tests::cache_rejects_replacement_and_same_length_in_place_rewrite` (exit 101, 0.01s). Panic at `session_store_info_tests.rs:195` inside `assert_fold_matches`: `read_session_info` returned `name: Some("bravo")` and `legacy_read_session_info` returned `name: Some("delta")`. Result line: 0 passed, 1 failed, 965 filtered. The 965 are the other lib tests excluded by `--exact`, and this is the same lib failure already counted below, not a second one.
  - `imported_session_compacts` counts `type: message` rows in the first directory entry whose name starts with `grown-import`. The import writes `grown-import.jsonl` and the window sidecar `grown-import.window-cache.json` (`path.with_extension("window-cache.json")`). On this ext4 mount, `readdir` returns the sidecar first; that order is stable across creation order. The sidecar has no message rows, so the count is 0 against 3600 after a successful import. Reproduced in isolation on 2026-10-08 in 1.70s. `/tmp` is this root ext4, not a tmpfs. The test is not in the pin skip set. At `967eb13` the same order also hit `ubuntu-latest`: from 2026-10-21, `rust-parity` failed on it in 7 of the 11 runs that reached it, always 0 against 3600. The sidecar is written synchronously during the import, so this is directory order, not a persist or flush race. Upstream `72f790bab` (#3678) matches only `.jsonl`, and the current pin includes it.
- A path-clean skip-set run on 2026-10-08 (`prime-agent` absent from `PATH`, provider credentials unset) was interrupted in `pa-daemon` `stop_escalation_e2e` (`a_well_behaved_worker_keeps_the_clean_stop` had passed; the second test had not finished). Finished targets: 3249 passed, 2 failed, 19 ignored, 3 filtered (the ACP skips). The failures are the ctime test and `imported_session_compacts`. 3249 is not a workspace total.
- The five `pa-daemon` targets the interrupted run had not finished were re-run the same day, same environment, `--no-fail-fast`. `stop_escalation_e2e` 2 passed, including `a_hung_worker_is_killed_within_the_escalation_window_and_its_lease_frees` in 62.38s. `thinking_level_e2e` 4 passed, `thinking_level_roster_e2e` 2 passed, `worker_orphan_exit_e2e` 2 passed, `worker_stderr_e2e` 2 passed. `import_compaction_e2e` failed again in 1.70s (0 vs 3600). That closes the `pa-daemon` integration list; the only failure in it is `imported_session_compacts`.
- The four crates that had not started were run the same day, same environment: `cargo test --locked --no-fail-fast -p pa-models -p pa-telemetry -p pa-tui -p pa-types` (exit 0, 370s). Totals: `pa-models` 73 passed across 5 targets, `pa-telemetry` 68 passed across 3 targets, `pa-tui` 1537 passed across 49 targets, `pa-types` 150 passed across 5 targets. Together 1828 passed, 0 failed, 0 ignored, across 62 result lines. Doc-tests in all four crates ran 0 tests; those four lines are among the 62 and add 0 to the 1828. 1828 is not a workspace total: `pa-ai`, `pa-agent`, `pa-core`, `pa-cli`, and `pa-daemon` are not in it. The same log's ctime filter was `session_store_info_tests::cache_rejects_replacement_and_same_length_in_place_rewrite`. That module is `session_store::info_tests` (`#[path = "session_store_info_tests.rs"]`), so the filter matched nothing: 0 passed, 966 filtered, exit 0. It did not execute the ctime test.
- Doc-tests are not inside the 3249. Cargo prints them after every selected lib, bin, and integration target, and the interrupted log has no `Doc-tests` line. The same day, same hermetic environment: `cargo test --locked --doc --no-fail-fast -p pa-ai -p pa-agent -p pa-core -p pa-cli -p pa-daemon` (exit 0). Each of those five crates ran 0 doc-tests.
- Local no-fail-fast total for the default `cargo test --workspace --locked` targets, with the three ACP skips filtered, is **5089 passed, 2 failed, 19 ignored, 3 filtered**. Arithmetic: 3249 (finished targets before the interrupt) + 12 (the five `pa-daemon` integration binaries that had not emitted a result line) + 1828 (the four crates, including their empty doc-tests). The five crates' doc-tests add 0. The two failures are the ctime test and `imported_session_compacts`. The 965-filtered and 966-filtered lib invocations are not in this total. Examples and benches are outside the default set CI runs. CI itself is fail-fast and does not print this sum; on `ubuntu-latest` the two ext4 hazards above are not the expected result.

### Linked crates

The sources are read from `prime-agent/crates/<name>/`, the tracked submodule checkout.
`native/omega-prime-prime/` is the only Rust Omega Prime authors: a PyO3 library
that links all nine crates. Its built output is
`prime-agent/target/debug/libomega_prime_prime.so`. A running tool host reports
the link through `prime_crates`. "Probe only" means the sole caller is
`crates.probe()`, which `prime_crates` exposes.

| Crate | Upstream role | In Omega Prime today |
| --- | --- | --- |
| `pa-core` | Session engine: tools, skills, prompts, kernel, subagents, settings | Used: `prime_goal` and `prime_autonomous` run its `/goal` and `/autonomous` parsing and state. `SessionEngine` is not used. |
| `pa-ai` | Provider APIs, model registry, streaming | Used: `ai.complete` behind `PrimeProviderModel`, selected explicitly with `OmegaPrimeAgent.from_prime`. |
| `pa-models` | Live model catalog | Bound as `ai.resolve_models`, which callers use to pick a descriptor for `from_prime`. Otherwise probe only. |
| `pa-types` | Shared domain and wire types | Indirect: goal state and frame types inside the bridge. |
| `pa-agent` | Agent loop | Probe only (argument validation). Its loop is not used. |
| `pa-telemetry` | Event schema, queues, sinks | Probe only. |
| `pa-daemon` | Session supervisor, workers, wire protocol | Probe only (frame encoding, durable-create command). There is no process model. |
| `pa-tui` | Terminal UI | Probe only (session-search scoring). |
| `pa-cli` | The `prime-agent` binary | Probe only (mode names, missing-subsystem text). |

## T3MP3ST red team pack

- Remote: https://github.com/swcstudiospace/purple-team.git
- Commit: `1a1a4b012c2cf3a46d1899c9968ba440eb8bfda8` (branch `omega-pin/f6d2fc2-audit-20261024`: upstream **v1.4** `f6d2fc2` plus two lockfile-only bumps. `6679b82` (tag `omega-pin-f6d2fc2-zod467`) moved zod 4.2.1 → 4.6.7 for GHSA-mrhx-c7xc-m3w5. `1a1a4b0` moved body-parser 1.20.8 → 1.20.9 and 2.3.0 → 2.4.0 (GHSA-27r3-jxmh-p828), undici 8.11.2 → 8.12.3 (GHSA-4qv8-2p9j-2685, GHSA-f72v-6w45-q472, GHSA-xjj6-f33q-929c) and fast-uri 3.1.8 → 3.1.9. Not on upstream main; the branch keeps it reachable.)
- Root `redteam/` submodule: tracked gitlink at the same pin, alongside the preserved `prime-agent/` gitlink. An ordinary `git clone` leaves it empty; populate with `git clone --recurse-submodules` or `git submodule update --init redteam`.
- License: MIT
- Copyright: 2026 SWC Studio Space
- What is vendored: the declarative Grok Bot pack — the seat templates, the roster, and the 14 saved skills — copied verbatim under `omega_prime/redteam/pack/`, plus a registry tool family (`omega_prime/tools/redteam.py`) that mirrors the upstream MCP gateway proxy surface against the upstream loopback HTTP API. The Python package, the adapter, and the vendored pack are ordinary parent content.
- What is not adapted: the TypeScript runtime (agent loop, arsenal execution, server), the War Room UI, and the `bench/` benchmark corpora. The backend is separately versioned upstream source (its own `t3mp3st` package contract), reached through the `redteam/` gitlink and started separately — never ported, never auto-launched. The corpora carry upstream and per-challenge licenses (upstream `THIRD-PARTY.md`) and are excluded from the vendored pack; nothing here implies benchmark content becomes Omega-licensed (AGPL-3.0-only). A later phase may pull a piece in only if that phase cannot pass its own tests without it.
- Authorized use only: the pack and its tools exist for engagements against systems you own or have explicit written permission to test; nothing here authorizes testing anyone else's systems.

## Merge rule

One Python process. One `OmegaPrimeAgent`. Where the upstreams implement the same concern, one Omega Prime implementation has to satisfy all invariants. Temporal is not the merge. It can become a durability adapter after cron and delegation exist.
