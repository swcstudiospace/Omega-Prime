# Phase 103 research: Ultrathink integration and cleanup

## Method and limits

This summary is based on static inspection of repository sources, not runtime proof. Re-read those source paths before execution because line references can move. External G8/G12 audit labels and `claude-ultrathink` are prompt-supplied only and not audited. PR45's presence in local history is parent-supplied history evidence only, not runtime proof or predecessor verification.

## Observed source facts

### Bridge, server, registry and eval consumer

`omega_prime/tools/ultrathink.py` defines the bridge context and CLI wrappers. Context currently has root/run/timeout and no environment/ship switch; ship writes invoke `ultrathink-ship` with their existing arguments. The existing `_error` envelope is suitable for a stable refusal. `APPROVAL_TOOLS` includes the ship writes, including merge; preserve it. The registry checks policy and approval before handler dispatch, so a bridge-local gate runs only after required approval and before runner execution. `omega_prime/mcp_server.py:980-983` passes root only; its `load_runtime(env=None)` boundary resolves process env, whereas explicit mappings are authoritative. `omega_prime/evals/runner.py:249` creates a fake-runner context with no env. Existing eval fixtures cover `session_mark`, not ship writes.

**Proposed interface:** New shared `omega_prime/integrations/ultrathink.py` owns pure `ship_enabled(env: Mapping[str, str]) -> bool` (true only for exact `1` or `true`). Append an empty-default explicit env mapping to bridge context, pass server's already-resolved mapping and per-case eval env, and gate only pr/review/merge centrally before root/runner. Stable `ship_disabled` response with a fix hint; preserve schemas, approval, argv, timeouts, status/assess and `autoMerge` behavior. These are designs, not existing features.
All interfaces in this section are proposed execution contracts, not existing features. The required interpreter is `/workspace/omes-bot/.venv/bin/python`; set `PYTHONPATH=/workspace/omes-bot-wt/p7` and include `/workspace/omes-bot/.venv/bin` on `PATH`. Use the actual worktree for all repository operations; no time caps, live configuration, external CLI invocation, or publication actions.

### Readiness, setup and plan discovery

`omega_prime/setup_check.py:146-156` currently reads `ULTRATHINK_ROOT` and checks only `bin/ultrathink-ship`; setup rows use `{ok: bool|None, detail: str}` and overall `None` is skip/nonfailure. `omega_prime/grokbot/doctor.py` has no Ultrathink check; its JSON keys/shape and PASS/WARN/FAIL behavior must remain stable. `/omega-doctor` uses a separate `lead_doctor` surface; setup/GrokBotDoctor work does not establish slash-command behavior.

`omega_prime/routines/ultrathink_turn.py:21-53` constructs the ordered state candidate list: `OMEGA_PRIME_STATE_DIR/ultrathink`, `ULTRATHINK_STATE_DIR`, `GROK_PLUGIN_DATA/ultrathink`, `${GROK_HOME:-$HOME/.grok}/plugin-data/ultrathink`, `${HERMES_HOME:-$HOME/.hermes}/ultrathink`, `${XDG_CONFIG_HOME:-$HOME/.config}/muse/ultrathink`, `$PI_CODING_AGENT_DIR/ultrathink` or `$HOME/.omp/agent/ultrathink`, `$HOME/.claude/ultrathink`. `resolve_turn_plan(state_dirs=...)` explicit list replaces environment discovery; it parses plans and returns stale immediately for first valid stale candidate.

**Proposed interface:** Expose ordered `candidate_state_dirs(env)` and share it with a read-only readiness snapshot in the shared integrations module. Readiness reports the preferred candidate directory only, not plan presence/freshness; it must not parse plan content, invoke CLIs, create directories or load config. The four proposed states are `not_configured`, `root_invalid`, `ready_ship_off`, `ready_ship_on`. Root validity requires all three named `bin` entries to be files and executable. Keep setup tuple and doctor JSON contracts; default ship-off is healthy. Keep explicit `state_dirs` replacement and stale-first-plan semantics unchanged.

### Optional native and OpenShell test behavior

`omega_prime/tests/test_prime_native.py` has nine binding tests requiring the optional extension and a status test that intentionally supports unbuilt installs. Native helper behavior can treat load errors as absence. `omega_prime/tests/test_prime_runtime_e2e.py` has four actual native E2Es. `pyproject.toml` has no native marker. Use existing `extension_path()` to skip only artifact absence; if an artifact exists but fails to load, fail. Register a native marker, mark exactly nine binding tests and four E2Es; retain the status test in Python-only lane.

`omega_prime/sandbox/runner.py` and current sandbox tests use PATH-only OpenShell production lookup. **Frozen plan 103-03 is test wiring only:** test-only `OPENSHELL_BIN` may point at an existing operator-supplied executable. When set, validate and use lookup monkeypatching to make the real probe subprocess select that exact path (including arbitrary basename); when absent retain current PATH selection. Skip only if optional binary is absent; selected present-but-broken executable fails. Do not add production resolver behavior or invented build-directory search. Remove the source-text `test_no_shell_true_in_runner_source` while preserving behavioral argv/no-fallback coverage. Live gateway tests remain untouched and are not collected for planning.

## Frozen execution interface and dependencies

| Plan | Wave | Dependencies | Scope / acceptance focus |
|---|---:|---|---|
| `103-01-PLAN.md` | 1 | none | Ship gate, explicit env propagation, eval consumer, bridge/MCP/eval tests; shared ship parser owner. |
| `103-02-PLAN.md` | 2 | 103-01 | Shared state discovery/readiness and setup/GrokBotDoctor adapters/tests; consumes 103-01 parser. |
| `103-03-PLAN.md` | 1 | none | Native optional markers and OpenShell test-only selection/probe; production runner unchanged. |
| `103-04-PLAN.md` | 3 | 103-01, 103-02, 103-03 | Setup/skill/changelog documentation, marker lanes, env/state precedence; checker referenced only when actual Phase 102 API exists. |
| `103-05-PLAN.md` | 4 | 103-01, 103-02, 103-03, 103-04 | Parent-only verification, fake-root public-path smoke, exact gates, receipt/milestone handoff; requires prior Phase 102 verification. |

The Phase 102 env checker is absent from current-HEAD evidence. Resolve after Phase 102 before execution; do not invent a module/command or reimplement it in Phase 103. Decisions provider remains untouched. Phase 103 can be planned while implementation is blocked on that prerequisite.

## Acceptance cautions and open evidence

- `ULTRATHINK_SHIP` must never be inferred from approval, tool choice, root, fake runner, or ambient process env; explicitly supplied env is the sole permission input. Gate valid ship writes before root/runner; registry approval remains upstream and independent.
- Setup/doctor must check executable file metadata only; no CLI/config/plan-content reads. A preferred discovery directory is not evidence of a plan.
- Optional skip means actual absence only. Existing broken native/OpenShell artifacts fail. Do not build native binaries or contact a gateway.
- Do not claim Phase 102 or phases 97–102 complete, Phase 103 implemented, runtime success, external audit, external CLI facts, or `77.1` passed; preserve `77.1 human_needed`.
- No tests, gates, formatters, builds, external CLI, gateway, network or live configuration were used for this research.
