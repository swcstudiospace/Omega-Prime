# Phase 98 — Research and execution boundary

**Status:** Source research only. Runtime changes and phase acceptance checks have not been performed. Findings below describe the inspected checkout at `44b28df`; phase 97 is not assumed merged.

## Current implementation evidence

- `omega_prime/mcp_server.py` (`default_registry`) builds one local `MemoryStore` for growth and lead. Growth is registered before the Substrate client is constructed. Lead receives `_DeskSubstrate`, an events/graphs adapter, not a raw memory client. No production construction of `HindsightService` or `HindsightBridge` was found in the research searches.
- Actual local add/replace/remove tool is the nested memory handler in `omega_prime/tools/growth.py`; `omega_prime/memory/provider.py` / `manager.py` route it to `MemoryStore`. `target=user`, replace and remove are local operations and must remain so; do not publish USER.md or seed onboarding memory unintentionally. A local entry is limited by the total `MemoryStore` memory budget of 2200 characters (user budget 1375), including wrapper/metadata/separators; the delimiter `§` is forbidden. Local duplicate detection is exact entry equality.
- `omega_prime/tools/lead.py`: `LeadContext` holds local memory and substrate adapter but no source coordinator, selector, Hindsight service or repo field. `LeadClient.memory_recall` searches local bank-tagged memory only, returns `{banks, results:[{bank, entries}]}`, has per-bank rather than aggregate limits and no repo boundary. `memory_retain` requires `receipt_path` or `source`, rejects secret-shaped content/metadata, then stores a tagged evidence string locally. Lead retain is approval-gated; recall is read-only. Brief caches for 300 seconds and retain does not yet invalidate it. Doctor is `LeadClient.doctor`; it currently treats object presence as green, which is not remote source health.
- `/omega-recall` routes through lead recall, but `/omega-retain` currently invokes builtin local memory directly. `omega_prime/commands/__init__.py` command wrapper checks top-level error but would not necessarily report `{ok:false}` as failure. Both need clean-cutover coverage. The readiness consumers `omega_prime/grokbot/readiness_local.py` and `readiness_workflows.py` parse `results[*].entries`; migrate every consumer if using a structured hit list, rather than preserving two sources of truth.
- Existing local `omega_prime/memory/hindsight.py` is a bank-tag adapter over `MemoryStore`, not the remote API. Its bank validation is not sufficient for interpolation into a remote URL. Keep it for legacy local banks and no-leak isolation.
- `omega_prime/memory/hindsight_service.py` is the optional HTTP client. It requires an explicit base URL to construct; credentials are obtained through broker aliases `HINDSIGHT_API_KEY` / `HINDSIGHT_API_TOKEN`. Current URL checks allow HTTP and userinfo; error typing is not structured enough for reliable unauthorized/unreachable classification, malformed recall envelopes may map to `[]`, and its redaction failure path can expose original error text. Harden URL validation, response parsing, broker integration and redaction before composition. Construct only when Hindsight is configured so an unused/broken secret file does not disable local/Substrate behavior.
- `omega_prime/memory/hindsight_bridge.py` has only test references in this checkout. It is service-first; successful remote recall hides local results; errors can switch to local success without reporting remote failure; successful remote write is not local-first. **Decision:** remove it and migrate bridge-only tests into coordinator coverage. Keep `HindsightService` tests and expand them; this is not a reason to remove service coverage.
- Current substrate code is not phase-98-ready: checked-in `SubstrateClient.memory_search` converts `SubstrateError` to `[]`; it cannot distinguish empty from failure. `memory_write` lacks the required `expected_version` and `idempotency_key` parameters. Existing status dictionaries include accepted/conflict/quarantined/denied; only accepted proves publication. Phase 97 must supply structured search/error/status contracts and exact write semantics. Do not guess its signatures, treat `None` as a wildcard, parse exception text for authorization, or claim transport/idempotency behavior unsupported by an authoritative contract.
- `omega_prime/mcp_server.py` currently constructs Substrate with a loopback default (`http://127.0.0.1:7410`); object existence or that default is not proof of explicit configuration. Resolve selected default from the phase-97 authoritative endpoint resolver, never a loopback probe. Configured-but-unavailable explicit selection stays selected and reports failure. Do not move transport fixes into phase 98.
- Scope and repository isolation do not exist across all current memory paths. Local store is install/state scoped, not repo scoped; shared Hindsight bank cannot be presumed repo isolated. Validate trusted scope/repo before IO; filter every source, do not make legacy records with no repo provenance automatically repo-scoped, and do not rely on caller-selected bank names as authorization. Hindsight keys only from environment or mode-checked secret file through existing broker; reject non-HTTPS, URL userinfo/query/fragment and unsafe bank path segments before credential lookup/network. Never place keys in settings and redact all returned errors/content.

## Phase-97 prerequisite gate

Rebase/re-read phase-98 plans only after phase 97 is present. Confirm, from its actual artifacts and tests:

1. Authoritative configured/effective Substrate URL resolution, distinguishing explicit endpoint from constructor loopback default and preserving saved binding blocked/skipped states.
2. Search result contract that separates valid empty from unreachable/unauthorized/malformed replies and parses the real server response shape.
3. Stable write signature and typed result/status surface, including exact `expected_version`, idempotency key, repo and scope semantics.
4. Which remote statuses represent accepted publication and how retry behavior preserves one logical operation.
5. Hindsight request/auth error classification and phase-97 settings integration boundaries, if changed by that work.

Any unresolved prerequisite blocks runtime execution rather than authorizing phase 98 to add another transport/parser/settings path. In this planning session the prerequisite is unmet: the current checkout is at 44b28df, phase 97 is absent, and the inspected substrate signatures/errors are still the old ones.

## Chosen contract and plan ownership

The frozen interfaces, normalization, source ordering, exact state enum, result structures, selector policy, retained legacy behaviors, exact gate commands, and the four file ownership/dependency waves are in `CONTEXT.md` and referenced by all four plans. Briefly: APIs are `resolve_memory_backends(...)`, `recall_memory(...)`, and `retain_memory(...)`; recall emits one structured `results` hit list with merged provenance; canonical hashing is SHA-256 of UTF-8 after outer strip and CRLF/CR-to-LF; deterministic order is local, substrate, Hindsight; no unlike-score comparison. Retain is local-first, always publishes to configured Substrate regardless of selector, and writes Hindsight only when selected. No new retries/background jobs or fake fallback. Composition happens only after complete coordinator operations exist; wave 2 files are disjoint; final command/doctor wiring is serial wave 3.

The four plan names linked by ROADMAP are `98-01-PLAN.md` (resolver and Hindsight service security), `98-02-PLAN.md` (recall and lead/readiness migration), `98-03-PLAN.md` (retain and growth add route), and `98-04-PLAN.md` (composition, commands, doctor, docs, integration). Their implementation tasks must remain unchecked until performed. This research is not an execution report.

## Baseline evidence, observed before planning edits

Source context records baseline at `44b28df`: `ruff check omega_prime/` exit 0; `ruff format --check omega_prime/` exit 0 (589 files); `mypy omega_prime/` exit 0 (462 source files); `pytest omega_prime/tests -q` exit 1 with 27 failed, 4777 passed, 11 skipped, 15 warnings in 244.72s. These observed outcomes are recorded, not rerun. Failures are pre-existing missing/uninitialized prime-agent checkout/runtime, missing skills/Cargo files, or setup/preflight expectation. Names from `artifact://11`:

- `omega_prime/tests/test_grokbot_oneclick.py::test_oneclick_dry_run_sse` — empty/uninitialized `prime-agent` preflight.
- `omega_prime/tests/test_prime_kernel_boundary.py::test_read_only_skill_list_still_returns_the_skills` — missing `skills` result key.
- `omega_prime/tests/test_prime_kernel_boundary.py::test_read_only_crates_still_reports_the_workspace` — missing `loaded` result key.
- `omega_prime/tests/test_prime_kernel_cells.py::test_namespace_persists_across_cells` — missing `rlm` runtime.
- `omega_prime/tests/test_prime_kernel_cells.py::test_cell_output_is_returned_and_never_written_to_the_process_streams` — missing `rlm` runtime.
- `omega_prime/tests/test_prime_kernel_cells.py::test_cell_exit_requests_are_cell_errors_and_the_kernel_keeps_working` — missing `rlm` runtime.
- `omega_prime/tests/test_prime_kernel_cells.py::test_cell_spawn_uses_in_process_host` — missing `rlm` runtime.
- `omega_prime/tests/test_prime_kernel_cells.py::test_cell_child_answer_visible_only_through_collect` — missing `rlm` runtime.
- `omega_prime/tests/test_prime_kernel_cells.py::test_factory_help_while_disabled` — missing `rlm` runtime.
- `omega_prime/tests/test_prime_kernel_cells.py::test_validate_spec_rejects_empty` — missing `rlm` runtime.
- `omega_prime/tests/test_prime_kernel_cells.py::test_enable_factory_keeps_help` — missing `rlm` runtime.
- `omega_prime/tests/test_prime_kernel_cells.py::test_run_bash_echo` — missing `rlm` runtime.
- `omega_prime/tests/test_prime_kernel_host.py::test_prime_kernel_host_round_trip` — missing `prime-agent/Cargo.toml`.
- `omega_prime/tests/test_prime_kernel_skills.py::test_list_skills_marks_edit_importable_and_factory_markdown_only` — missing `prime-agent/skills`.
- `omega_prime/tests/test_prime_kernel_skills.py::test_load_skill_edit_replaces_a_unique_string` — `edit` skill absent under missing `prime-agent/skills`.
- `omega_prime/tests/test_prime_kernel_skills.py::test_list_skills_does_not_import_attach_image` — missing `prime-agent/skills`.
- `omega_prime/tests/test_prime_native.py::test_native_status_accepts_unbuilt_extension` — missing `prime-agent/Cargo.toml`.
- `omega_prime/tests/test_prime_native.py::test_goal_bindings_follow_pa_core` — missing `prime-agent/Cargo.toml`.
- `omega_prime/tests/test_prime_native.py::test_autonomous_run_uses_pa_core_limits` — missing `prime-agent/Cargo.toml`.
- `omega_prime/tests/test_prime_native.py::test_protocol_parse_and_encode_use_the_rust_frames` — missing `prime-agent/Cargo.toml`.
- `omega_prime/tests/test_prime_native.py::test_heartbeat_schedule_and_deferral` — missing `prime-agent/Cargo.toml`.
- `omega_prime/tests/test_prime_native.py::test_refinement_history_round_trip` — missing `prime-agent/Cargo.toml`.
- `omega_prime/tests/test_prime_native.py::test_goal_apply_uses_pa_core_transitions` — missing `prime-agent/Cargo.toml`.
- `omega_prime/tests/test_prime_native.py::test_slash_parsers_match_pa_core` — missing `prime-agent/Cargo.toml`.
- `omega_prime/tests/test_prime_native.py::test_native_tool_validation_rejects_missing_required_argument` — missing `prime-agent/Cargo.toml`.
- `omega_prime/tests/test_prime_native.py::test_kernel_tools_call_the_rust_commands` — missing `prime-agent/Cargo.toml`.
- `omega_prime/tests/test_setup_check.py::test_checks_pass_on_repo_root` — setup/preflight expectation failure.

No baseline failure has been repaired or reclassified as green by this planning work. Phase implementation should preserve the baseline list and distinguish newly introduced failures from it.

## Planning constraints and unverified

- No current phase-98/97 planning artifacts were found in this checkout before this planning work. GSD skill is unavailable here: `.claude/.agents` and phase-98 GSD plan-phase files are absent. Repository planning conventions were used; no GSD execution is implied.
- No environment or secret values were inspected and no network/service calls or installs were made. The recorded baseline gates were run before planning edits in this session; no post-edit gates are claimed. Implementation authors must run wave-focused checks at the planned boundaries and the main owner runs full gates after integration.
- No tracker IDs provided; no Notion URLs provided. `G2` is an audit gap label, not a tracker identifier.
- Phase-97 API/server idempotency and optimistic-version semantics remain unverified; these are execution blockers. Hindsight server-side `document_id` idempotency is unverified. Future live URL/restart/VPS verification is operator-owned; phase 77.1 human UAT remains untouched.
- A planning receipt is required at `.receipts/bot-00-omega-prime/phase-98-planning.json` before any completion claim. User explicitly prohibited a PR; no PR is part of this session.
- `rule adherence by future agents` remains unverified.
