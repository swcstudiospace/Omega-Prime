# Phase 97: Substrate direct connect — Research

**Status:** Planning evidence only; no implementation or live verification.

## Source map and execution preflight

Current local source facts and line references are recorded in `97-CONTEXT.md:71-87`. Focused research confirmed:

- Runtime substrate construction is `omega_prime/mcp_server.py:887-897`; standalone substrate tools call `omega_prime/substrate/client.py`, while lead graph tools route through `_DeskSubstrate.call_tool` at `mcp_server.py:361-399`. The lead adapter currently expects JSON-object results, so text-returning release/complete behavior must be covered end-to-end.
- `omega_prime/providers/http.py:57-64,94-142,214-302` exposes ordinary JSON operations; it discards response metadata at its JSON decoding boundary. MCP request/response handling belongs in the explicit transport seam rather than changing unrelated provider HTTP consumers.
- Existing tests/conventions: `omega_prime/tests/test_transports.py` socket-pair HTTP fixtures; `test_substrate.py` scripted client fakes and socket fixtures; `test_substrate_session.py` tool dispatch/approval fakes; `test_lead.py` lead graph wrappers; `test_mcp_server.py` outer MCP serialization/redaction; `test_engine.py` fake NDJSON executable; `test_lanes.py` fake fleet/executor; settings/doctor conventions are in `test_setup_check.py`, `test_grokbot_runtime.py`, `test_grokbot_io.py`, `test_grokbot_doctor.py`, and `test_omega_commands.py`. Future test names in plans are proposals, not existing tests.
- The client `SubstrateSession.session_id`, graph `session_id`, MCP `Mcp-Session-Id`, and graph `lease_id` are separate values and must not be conflated. Preserve the runtime's ready-binding precedence and blocking states. Generic `SUBSTRATE_TOKEN` resolution precedes `SUBSTRATE_TOKEN_GROK_BOT`; per-name secret paths remain available; anonymous mode is allowed with a warning, never a secret value.
- Settings file is fixed at `~/.omega-prime/settings.json`, overridden only by `OMEGA_PRIME_SETTINGS_FILE`. Exact allowlist: `SUBSTRATE_URL`, `HINDSIGHT_URL`, `ULTRATHINK_ROOT`, `ULTRATHINK_SHIP`, `OMEGA_PRIME_STATE_DIR`; all values strings. No credential `*_FILE` keys in settings (NAME_FILE remains process-env/secret resolution only). Reject the entire malformed document without partial overlay. Unknown nonsecret keys warn/ignore; secret-shaped keys are refused by name only; never inspect or echo their values. Process env overrides settings. `load_runtime(env={})` must bypass settings and ambient configuration lookup. Main must start degraded on missing/rejected/malformed settings and retain sanitized loaded/rejection provenance through stdio/SSE, runtime, actual lead doctor, and setup diagnostics.
- For settings safety, check lexical aliases and resolved targets, including `.git` file worktrees, tracked paths, all worktrees, and group/world-writable permissions before reading content. Fail closed if safety cannot be established; account for symlink/race concerns. Do not mistake the existing per-agent `settings.json` reader or `binding_path()` repository-only guard for this complete policy.
- No session/turn-duration deadline or environment timeout override is allowed. Request/socket liveness polling is operational detection, not a turn deadline. Preserve unrelated ordinary bounded transport/socket/probe waits. Lane duration becomes uncapped (`LaneLimits.max_minutes: int | None = None`); `_with_local_caps` preserves `None`; configured duration classification is guarded as specified; local `spec.json` stores explicit JSON `null`, while fleet serialization remains unchanged. Preserve file, memory, concurrency, and other resource limits. Cancellation must be reachable from the existing interrupt source, and no post-start Python fallback is introduced.

### Upstream evidence and uncertainty

Read-only public GitHub checks on 2026-10-21 returned 404 for:

- <https://api.github.com/repos/swcstudiospace/agent-substrate/git/trees/main?recursive=1>
- <https://github.com/swcstudiospace/agent-substrate>
- <https://raw.githubusercontent.com/swcstudiospace/agent-substrate/main/packages/mcp-server/src/mcp.ts>

This does **not** establish whether the repository is private, moved, or otherwise unavailable. Current server revision, MCP mount/path, tool schemas, session mode, protocol version, and response types are unverified. Audit G1 cites substrate `main` at `05d8f45` and PRs #79/#80/#84 for the newer argument expectations; historical local spike captures contain older, contradictory schemas. Neither is an authoritative current contract. Execution preflight MUST obtain safe read-only authoritative current schema evidence or operator-supplied verified endpoint evidence before freezing server-specific fixture/path assertions. Never guess a grok-bot MCP path or invent a mount. The public GitHub requests returned 404, and the authenticated `gh api repos/swcstudiospace/agent-substrate --jq '{default_branch:.default_branch,pushed_at:.pushed_at}'` metadata query exited 4 because `GH_TOKEN` was unavailable; no token value was read. Until successful preflight, server-specific production changes and declaring Phase 97 complete are blocked; generic transport parsing and uncapped engine/lane work may proceed independently.

## Parent-reported baseline (not rerun here)

For copyable commands, export the environment:

```bash
export PATH=/workspace/omes-bot/.venv/bin:$PATH
export PYTHONPATH=/workspace/omes-bot-wt/p1
```

Python executable: `/workspace/omes-bot/.venv/bin/python`.

```bash
/workspace/omes-bot/.venv/bin/ruff check omega_prime/
/workspace/omes-bot/.venv/bin/ruff format --check omega_prime/
/workspace/omes-bot/.venv/bin/mypy omega_prime/
/workspace/omes-bot/.venv/bin/pytest omega_prime/tests -q
bash omega_prime/scripts/assemble-prompts.sh --check  # only if prompts change
```

Recorded exits/results: `ruff check` exit 0; `ruff format --check` exit 0 (589 files); `mypy` exit 0 (462 files); `pytest` exit 1 (27 failed, 4777 passed, 11 skipped, 15 warnings, 236.78s). Prompt assembly was not indicated as run and no prompt changes are planned. These are parent-reported baseline only, not results of this planning worker. The final required regression gates remain applicable; compare failures with the exact named baseline below, require zero new failures and passing new targeted tests, and report expected skips/results without requiring the pre-implementation pass count to remain unchanged.

The 27 named pytest failures recorded in `artifact://26:-85` are:

1. `omega_prime/tests/test_grokbot_oneclick.py::test_oneclick_dry_run_sse` — assert 3 == 0.
2. `omega_prime/tests/test_prime_kernel_boundary.py::test_read_only_skill_list_still_returns_the_skills` — KeyError `skills`.
3. `omega_prime/tests/test_prime_kernel_boundary.py::test_read_only_crates_still_reports_the_workspace` — KeyError `loaded`.
4. `omega_prime/tests/test_prime_kernel_cells.py::test_namespace_persists_across_cells` — `ModuleNotFoundError: rlm`.
5. `omega_prime/tests/test_prime_kernel_cells.py::test_cell_output_is_returned_and_never_written_to_the_process_streams` — `ModuleNotFoundError: rlm`.
6. `omega_prime/tests/test_prime_kernel_cells.py::test_cell_exit_requests_are_cell_errors_and_the_kernel_keeps_working` — `ModuleNotFoundError: rlm`.
7. `omega_prime/tests/test_prime_kernel_cells.py::test_cell_spawn_uses_in_process_host` — `ModuleNotFoundError: rlm`.
8. `omega_prime/tests/test_prime_kernel_cells.py::test_cell_child_answer_visible_only_through_collect` — `ModuleNotFoundError: rlm`.
9. `omega_prime/tests/test_prime_kernel_cells.py::test_factory_help_while_disabled` — `ModuleNotFoundError: rlm`.
10. `omega_prime/tests/test_prime_kernel_cells.py::test_validate_spec_rejects_empty` — `ModuleNotFoundError: rlm`.
11. `omega_prime/tests/test_prime_kernel_cells.py::test_enable_factory_keeps_help` — `ModuleNotFoundError: rlm`.
12. `omega_prime/tests/test_prime_kernel_cells.py::test_run_bash_echo` — `ModuleNotFoundError: rlm`.
13. `omega_prime/tests/test_prime_kernel_host.py::test_prime_kernel_host_round_trip` — missing `prime-agent/Cargo.toml`.
14. `omega_prime/tests/test_prime_kernel_skills.py::test_list_skills_marks_edit_importable_and_factory_markdown_only` — missing `prime-agent/skills`.
15. `omega_prime/tests/test_prime_kernel_skills.py::test_load_skill_edit_replaces_a_unique_string` — skill `edit` missing under `prime-agent/skills`.
16. `omega_prime/tests/test_prime_kernel_skills.py::test_list_skills_does_not_import_attach_image` — missing `prime-agent/skills`.
17. `omega_prime/tests/test_prime_native.py::test_native_status_accepts_unbuilt_extension` — missing `prime-agent/Cargo.toml`.
18. `omega_prime/tests/test_prime_native.py::test_goal_bindings_follow_pa_core` — missing `prime-agent/Cargo.toml`.
19. `omega_prime/tests/test_prime_native.py::test_autonomous_run_uses_pa_core_limits` — missing `prime-agent/Cargo.toml`.
20. `omega_prime/tests/test_prime_native.py::test_protocol_parse_and_encode_use_the_rust_frames` — missing `prime-agent/Cargo.toml`.
21. `omega_prime/tests/test_prime_native.py::test_heartbeat_schedule_and_deferral` — missing `prime-agent/Cargo.toml`.
22. `omega_prime/tests/test_prime_native.py::test_refinement_history_round_trip` — missing `prime-agent/Cargo.toml`.
23. `omega_prime/tests/test_prime_native.py::test_goal_apply_uses_pa_core_transitions` — missing `prime-agent/Cargo.toml`.
24. `omega_prime/tests/test_prime_native.py::test_slash_parsers_match_pa_core` — missing `prime-agent/Cargo.toml`.
25. `omega_prime/tests/test_prime_native.py::test_native_tool_validation_rejects_missing_required_argument` — missing `prime-agent/Cargo.toml`.
26. `omega_prime/tests/test_prime_native.py::test_kernel_tools_call_the_rust_commands` — missing `prime-agent/Cargo.toml`.
27. `omega_prime/tests/test_setup_check.py::test_checks_pass_on_repo_root` — assert False is True.

Failure classes: absent/uninitialized `prime-agent` submodule, missing `rlm`, dependent oneclick/setup preflight failures, and two prime-kernel boundary payload mismatches. No baseline failure is assigned to this wiring change or repaired here.

## Planning ownership and sequence

Five exclusive future implementation plans (all concrete source, test, and documentation targets are owned by `bot-00-omega-prime` under the last-match `**` rule). Each plan's `files_modified` is the authoritative exhaustive path list; use it rather than duplicating or broadening ownership here.

| Plan | Scope | Wave | Depends on |
|---|---|---:|---|
| `97-01` | uncapped engine and lane behavior | 1 | — |
| `97-02` | MCP streamable HTTP transport | 1 | — |
| `97-03` | exact substrate arguments, leases, and memory contract | 2 | `97-02` |
| `97-04` | explicit configuration, guarded settings, safe URL/auth, and diagnostics | 3 | `97-03` |
| `97-05` | documentation and integration handoff | 4 | `97-01`, `97-04` |

The `97-02` implementation targets are `omega_prime/providers/http.py`, `omega_prime/substrate/client.py`, `omega_prime/tests/test_transports.py`, `omega_prime/tests/test_substrate.py`, and `omega_prime/tests/test_substrate_session.py`. Plan 97-01 includes `omega_prime/agent/delegate.py`. Plan 97-03 includes `omega_prime/mcp_server.py`, `omega_prime/tests/test_mcp_server.py`, and `omega_prime/tests/test_substrate_session.py`; do not add an imagined `test_substrate_tools.py`. Plan 97-04 includes `omega_prime/grokbot/remote.py`, `omega_prime/tests/test_grokbot_remote.py`, `omega_prime/tests/test_omega_commands.py`, and `omega_prime/tests/test_installer_bindings.py`. Any proposed test paths are future plan ownership, not claims that those tests already exist.

The `mcp_server.py` and `substrate/client.py` overlaps across implementation plans are deliberately serialized by waves. No same-wave shared implementation files. Plan 97-05 owns docs; catalog output is generated only if a changed tool-schema description affects it, then the parent runs its conditional check. The parent owns implementation integration and verification. Phase 97 cannot be declared complete unless authoritative upstream schema/path/protocol fixture evidence is obtained; without it, server-specific production changes remain blocked while generic transport and caps work may proceed.
