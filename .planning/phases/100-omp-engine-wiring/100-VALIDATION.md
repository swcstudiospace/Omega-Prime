# Phase 100 validation model

This validation model defines what execution must prove and what cannot be
claimed today. It is planning guidance, not a result. The parent session's
observed four-gate baseline is recorded in §6; no test or gate ran while this
document was written.

## 1. Evidence model

- A **fake-omp test** (extended `_FAKE` in `omega_prime/tests/test_engine.py`)
  proves the repository-side contract only: what the driver writes, sends,
  records, restores, and how it reacts to scripted child behavior. It is not
  installed-loader evidence.
- Installed-behavior claims require permitted direct evidence gathered by
  plan 100-00. Until then they are unverified and execution is blocked from
  claiming acceptance that depends on them.
- `attached` is proven only by session-visible tool discovery, never JSON
  existence, a `ready` event, synthetic PONG, or argv echo.
- Gate outcomes are read from exact commands in §5; baseline failures are
  matched by exact node id against freshly observed execution output and §6.

## 2. Success-criteria coverage (ROADMAP Phase 100)

| # | Criterion (paraphrase) | Owning plan(s) | Required evidence at execution |
| --- | --- | --- | --- |
| 1 | Session tool-list contains Omega Prime tools | 100-01 (fixtures from 100-00) | Extended fake resolves the effective `--cwd` independently of process cwd, reads only the intended project MCP location, performs real MCP initialize + tools/list via the already-used SDK, and exposes discovered names through the agreed session-state response; assert Omega-specific `omega_command` plus a representative read tool. Negative: JSON only under `jobs/<id>/profile` discovers no Omega tools. Plus `test_mcp_server.py:203-218`-style own-client roundtrip through the *configured* command with work checkout outside install root. |
| 2 | Real GSD and Omega skills load; `--skills` is a glob, not a path | 100-02 (sources/evidence from 100-00) | Actual provider-free resolution proves Omega, user-seeded, and real GSD skill resolution with canonical source paths; missing sources or proof interface BLOCK execution and criterion 2 remains unsatisfied. |
| 3 | Fake rpc-ui ask answered once, stale rejected, cancellation responsive | 100-03 (protocol/service), 100-04 (driver/control) | Fake blocks completion until production reply entrypoint delivers human answer; exactly one `extension_ui_response` with original external request id; duplicate/stale/cross-job answers rejected with zero response frames; cancel while pending reaches child, invalidates request, later reply stale; validation failure remains pending; no separate bridge deadline or timeout cap is added. The existing `_TURN_TIMEOUT` remains unchanged, so a sufficiently prolonged ask may end at that outer timeout until Phase 97 merges. |
| 4 | Work-tree `.omp/mcp.json` restored; user config and shared login untouched | 100-01 | Byte/mode/inode/type/symlink-target snapshot before/after across pre-existing-config matrix (§3); run-owned artifacts removed only when identity permits; child must be proven reaped before releasing ownership; user replacement preserved and cleanup conflict reported. |
| 5 | Mode docs + green/baseline-only gates (+ prompt check if prompts changed) | 100-05 | Docs state `/skill:` needs rpc/rpc-ui and ask needs rpc-ui; exact four gates run with outcomes; prompt gate only if prompts changed; catalog checked after 04 regeneration. |

## 3. Error-case matrix (must be exercised, not assumed)

**MCP acquisition/backoff (plan 01):** pre-existing regular JSON (unrelated
servers, unknown keys); non-JSON/binary file; directory at `mcp.json`; leaf
symlink; dangling symlink; `.omp` ancestor symlink; occupied lease from a
concurrent job (barrier-held); read-only project; permission denial. Each
yields exactly one `attach_failed`, one engine process and prompt, a normal
assistant response, preserved engine ok/started semantics, no Python fallback,
no second model call, and no mutation of the pre-existing path (no-follow
checks; no read of secret-bearing contents; sentinel outside the tree proves
no traversal write).

**MCP launch/discovery failure (plan 01):** server command import failure,
MCP startup failure, discovery absence after JSON publish — all report
`attach_failed` (nonfatal, session continues) and never `attached` from
artifact existence; positive ordering assertion: `attached` cannot precede
tool discovery.

**Cleanup/ownership (plan 01):** success; Popen failure; setup/ready
failure; RPC/prompt-started failure; cancellation/BaseException; process-close
failure. Run-owned entry removed only when identity still matches; user
replacement (regular file/symlink/extra `.omp` child) preserved and cleanup
conflict reported; original failure never masked by cleanup failure. Child
must be proven reaped before ownership release, otherwise preserve owner and
report cleanup failure. Crash recovery is ownership-driven, never wall-clock
age or reused PID; live child blocks reclaim and unknown journal backs off.
Acquisition, publication, registration, recovery, and release use
directory-descriptor-relative no-follow/no-replace operations; staged inode
identity is durable before publish.

**Skills (plan 02):** old absolute-path `--skills` value (negative control)
fails resolution; removing a required root fails resolution; duplicate names
across shipped/GSD/user roots resolve deterministically to the documented
precedence; user sources and pre-existing global config remain byte-identical;
no per-job `--profile`, no `agent.db` copy, default/custom `PI_CODING_AGENT_DIR`
unchanged; redteam-pack skills not swept in by glob.

**UI request lifecycle (plans 03/04):** malformed/missing/empty/wrong-type
request id; unknown method; missing prompt/options/schema — explicit rejection,
no autoanswer, reader keeps running. Invalid answers (schema-invalid ask,
unoffered select option, truthy-string/int confirm) keep the request pending
with actionable rejection. Reservation to sending is atomic, delivery is one
write, answered follows successful delivery, and delivery failure is terminal
undeliverable (never immortal pending). Duplicate incoming request delivers
once; two racing valid answers produce one write and one stale rejection.
Identity is the full job/session-generation/turn-generation/request-id tuple;
generation is freshly unique across process restarts. Session-targeted abort
requires the observed session generation. EOF while pending, broken stdin,
and answer-vs-abort race have honest deterministic outcomes without deadlock.
Control remains readable while blocked and does not queue behind `ToolGate`.
Control storage must be private/no-follow and proven outside every resolved
writable bind; absent/unsafe/unsandboxed isolation stops interaction rather
than degrading to answers. CLI confirmation accepts explicit true or false.
Confirm answers do not alter global approval logs or authorize connector
sends. Production context is constructed at composition root, not only tests.
`max` is accepted; `auto` is never added. Preserve shared login,
started-failure no-replay and the existing Phase 97 `_TURN_TIMEOUT` and related
arguments; add no separate session/turn cap. A pending ask may end at that
outer timeout until Phase 97 merges.

## 4. Task-to-test mapping

| Plan | Tasks | Test/evidence targets |
| --- | --- | --- |
| 100-00 | installed loader/discovery contract; rpc-ui fixtures; Phase 97/99 discrepancy; GSD availability; baseline | Evidence notes consumed by all plans; record installed-loader/protocol evidence and real GSD source/resolver facts or explicit execution blockers. |
| 100-01 | lease/backoff bridge; discovery status; outer cleanup lifecycle | `omega_prime/tests/test_engine.py`; `omega_prime/tests/test_mcp_server.py` configured-command own-client roundtrip. |
| 100-02 | discovery configuration; name globs; GSD proof | `omega_prime/tests/test_engine.py`; real provider-free resolver proof for Omega, user, and GSD is required; missing proof blocks acceptance. |
| 100-03 | protocol types/validation/`max`; UI service; lifecycle | `omega_prime/engine/protocol.py`, `omega_prime/engine/hitl.py`, `omega_prime/tests/test_hitl.py`; no `test_engine.py` ownership. |
| 100-04 | production context, dispatcher/driver/caller, control, operator answer/cancel/status | `omega_prime/tests/test_engine.py` integrated regression set; status/watch and CLI stale-session/turn, explicit true/false, authorization and isolation tests; clarify consumer updates. |
| 100-05 | docs; conditional catalog/prompt checks; full proof; receipt | Docs review; central §5 gate run after all edits; §2 smoke as a set; receipt lists exercised and unverified facts without waiving required proof. |

Test-helper reuse (from report 1, verified locations at planning time):
`_FAKE` :47-124, `_install_fake` :135-140, `_env` :143-168, `_events`
:164-169, `_omp_commands` :670-681, `_logged_argv` :892-899;
`test_mcp_session.py` SDK fixture server :39-98 and `_HANG_SERVER` :104-115
for ownership/termination patterns.

## 5. Exact four gates (central execution verifier, plan 05)

Environment: `/workspace/omes-bot/.venv/bin/python`, venv `bin` on `PATH`,
`PYTHONPATH=/workspace/omes-bot-wt/p4`. Run from this worktree. Never read or
print secret/token values. Plan 00 records the pre-change baseline; after all
implementation edits land, the main agent runs this integrated sweep once:

1. `ruff check omega_prime/`
2. `ruff format --check omega_prime/`
3. `mypy omega_prime/`
4. `pytest omega_prime/tests -q`

The prompt assembly check is conditional on prompt changes. The catalog is
regenerated by plan 04 when its tool contract changes and checked by plan 05;
neither conditional action replaces or duplicates the integrated four-gate
sweep.

## 6. Observed planning-session baseline (do not rerun to confirm)

Observed by the parent session in this worktree on 2026-10-21 before Phase 100
application changes (planning-document edits do not change application code):
`ruff check` exit 0; `ruff format --check` exit 0, 589 files; `mypy` exit 0,
462 files; `pytest omega_prime/tests -q` exit 1 (232.67s):

- **27 failed, 4777 passed, 11 skipped, 15 warnings.**
- All 27 failures concern the empty/uninitialized `prime-agent` submodule in
  this environment. Exact node ids:

```
omega_prime/tests/test_grokbot_oneclick.py::test_oneclick_dry_run_sse
omega_prime/tests/test_prime_kernel_boundary.py::test_read_only_skill_list_still_returns_the_skills
omega_prime/tests/test_prime_kernel_boundary.py::test_read_only_crates_still_reports_the_workspace
omega_prime/tests/test_prime_kernel_cells.py::test_namespace_persists_across_cells
omega_prime/tests/test_prime_kernel_cells.py::test_cell_output_is_returned_and_never_written_to_the_process_streams
omega_prime/tests/test_prime_kernel_cells.py::test_cell_exit_requests_are_cell_errors_and_the_kernel_keeps_working
omega_prime/tests/test_prime_kernel_cells.py::test_cell_spawn_uses_in_process_host
omega_prime/tests/test_prime_kernel_cells.py::test_cell_child_answer_visible_only_through_collect
omega_prime/tests/test_prime_kernel_cells.py::test_factory_help_while_disabled
omega_prime/tests/test_prime_kernel_cells.py::test_validate_spec_rejects_empty
omega_prime/tests/test_prime_kernel_cells.py::test_enable_factory_keeps_help
omega_prime/tests/test_prime_kernel_cells.py::test_run_bash_echo
omega_prime/tests/test_prime_kernel_host.py::test_prime_kernel_host_round_trip
omega_prime/tests/test_prime_kernel_skills.py::test_list_skills_marks_edit_importable_and_factory_markdown_only
omega_prime/tests/test_prime_kernel_skills.py::test_load_skill_edit_replaces_a_unique_string
omega_prime/tests/test_prime_kernel_skills.py::test_list_skills_does_not_import_attach_image
omega_prime/tests/test_prime_native.py::test_native_status_accepts_unbuilt_extension
omega_prime/tests/test_prime_native.py::test_goal_bindings_follow_pa_core
omega_prime/tests/test_prime_native.py::test_autonomous_run_uses_pa_core_limits
omega_prime/tests/test_prime_native.py::test_protocol_parse_and_encode_use_the_rust_frames
omega_prime/tests/test_prime_native.py::test_heartbeat_schedule_and_deferral
omega_prime/tests/test_prime_native.py::test_refinement_history_round_trip
omega_prime/tests/test_prime_native.py::test_goal_apply_uses_pa_core_transitions
omega_prime/tests/test_prime_native.py::test_slash_parsers_match_pa_core
omega_prime/tests/test_prime_native.py::test_native_tool_validation_rejects_missing_required_argument
omega_prime/tests/test_prime_native.py::test_kernel_tools_call_the_rust_commands
omega_prime/tests/test_setup_check.py::test_checks_pass_on_repo_root
```

Handling rules: this is an **observed planning-session baseline** about the
environment (uninitialized `prime-agent`), not evidence of source regressions
and **not a permanent allowlist** — future runs must re-observe and match
failures by exact node id at their own time. The parent did not initialize the
submodule or repair this baseline; execution must not either, unless separately
authorized. Any failure outside this list at execution time is routed to the
responsible 100-01..100-04 owner only if phase-caused, then centrally
reverified; unrelated failures remain reported and cannot be claimed as passing.

## 7. No existing full-protocol/loader evidence

As of planning (2026-10-21), the repository contains **no** full
omp-protocol or omp-loader evidence:

- No implementation or fixture of `extension_ui_request` /
  `extension_ui_response` anywhere in the repo (exact and alternate-name
  searches; only ROADMAP requirement text matches). `protocol.py:20-47` has
  no UI command/event and excludes `max`.
- No code reads or proves the installed omp MCP config loader (`.omp/mcp.json`
  or agent-root precedence); `write_mcp_json` only writes profile-local files
  the installed loader is not shown to read, given `--cwd <work>`.
- No GSD `SKILL.md` or GSD discovery contract exists in the repo.
- `_FAKE` proves argv and event plumbing only; it loads no MCP config, no
  skills, and answers every command generically. Existing green tests
  therefore prove none of the five success criteria.

Everything in §2-§4 is consequently a future requirement with no current
evidence; plans 100-00..100-05 own closing exactly these gaps, and their
frontmatter `must_haves` are the truths execution must establish.

## 8. Verification limits

- Research workers did not run tests, gates, probes, live sessions, or installed
  commands. The parent session ran `omp --help` and observed the exact four
  gates/baseline in §6; planning workers only observed installed package
  filenames, not loader/protocol contents.
- Fake-based tests stay labeled repo-contract-only; required installed claims
  need 100-00 evidence.
- Actual GSD/Omega resolution and the production scripted delivery/reply/cancel
  proof are acceptance requirements: missing evidence blocks completion and is
  not waived through `unverified`. Live operator delivery to Ove, real
  bubblewrap execution limits, and post-SIGKILL cleanup may remain unverified
  only when not required by the acceptance proof. The 100-05 receipt lists
  unverified facts, including rule adherence by future agents.
