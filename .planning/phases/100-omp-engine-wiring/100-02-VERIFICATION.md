# Plan 100-02 verification — skills attachment

## Installed contract

- Installed `omp` is 18.12.3. The runtime source at `src/main.ts:1710-1714` maps the `--skills` comma-separated value to the active session's `includeSkills` setting. `src/extensibility/skills.ts:349-352` applies each pattern with `Bun.Glob(pattern).match(name)` to the final skill name. The session filter is a name glob; a filesystem directory is not a name glob.
- The provider-free `omp skill list --json` command exercises installed skill discovery and reports `filePath` for each loaded manifest. The command itself lists discovery results without applying the session's `--skills` filter, so its output is not used as a filter-behavior assertion. The session-argv test separately asserts that `--skills` is exactly `*`.
- GSD's configured custom skill directory remains native; this plan adds only catalog-derived Omega links and copies no skill files.

## Provider-free real-loader proof

Command:

```text
PYTHONPATH=/workspace/omes-bot-wt/p4 /workspace/omes-bot/.venv/bin/python -m pytest omega_prime/tests/test_engine_skills_real.py -q
```

Exit code: 0 (`1 passed`). The test uses installed `omp skill list --json` against a disposable project view and confirms:

- Omega `omega-commands` and `python`, configured `gsd-autonomous`, and a user-planted skill resolve to readable canonical manifests.
- The user skill's relative `references/guide.md` resource resolves beside its canonical manifest.
- Removing the project `.omp/skills` view removes the Omega and user-planted skills while native GSD resolution remains.

`omega_prime/tests/test_engine.py::test_fake_omp_session_discovers_configured_mcp_tools` separately proves the session receives `--skills "*"` and exposes an Omega MCP tool in its session tool list. The actual MCP registry test asserts `omega_command` and `clarify` are registered by the configured server.

## Fixture lifecycle proof

Commands:

```text
PYTHONPATH=/workspace/omes-bot-wt/p4 /workspace/omes-bot/.venv/bin/python -m pytest omega_prime/tests/test_engine.py -q -k 'fake_omp_session_discovers_configured_mcp_tools or loops_prefer_the_fake_engine or successful_engine_turn_spends_one_caller_iteration or rpc_ui_'
PYTHONPATH=/workspace/omes-bot-wt/p4 /workspace/omes-bot/.venv/bin/python -m pytest omega_prime/tests/test_engine_clarify_driver.py omega_prime/tests/test_engine_skills.py omega_prime/tests/test_engine_clarify_ipc.py -q
```

Exit codes: `0` (11 passed) and `0` (29 passed). These fixture assertions cover lease sharing, pre-existing path backoff, link identity, cleanup, MCP discovery, the session's name-glob argument, and RPC-UI operator answer/abort behavior. The main Phase 100 receipt records the final integrated suite and remaining limits.

## Scope note

The plan's proposed absolute-directory negative control cannot be asserted through `omp skill list`: a real installed invocation with that flag still enumerates discovered skills because this command does not create a session. The source-level session filter above and fake RPC-session argv test are the applicable evidence; no claim is made that `skill list` applies the runtime filter. No model/provider session or global configuration write was used.
