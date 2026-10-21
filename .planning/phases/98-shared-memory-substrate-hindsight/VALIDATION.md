# Phase 98 — Validation and acceptance trace

**Status: implementation executed and verified on the phase-98 parallel branch.** The explicit operator parallel-mode decision supersedes the planning-time phase-97 execution gate; phase 98 was built against this branch's current substrate API and must be rebased/re-read after phase 97 before ordered integration. Phase 97 still owns authoritative endpoint resolution and source-aware `memory_search` failures; this branch cannot distinguish a Substrate search outage when the current client converts it to an empty list. The parent recorded the pre-change baseline below. Current implementation results are in the execution record later in this document and in `SUMMARY.md`.

## Acceptance matrix

| ID | Required behavior | Required named case(s) | Planned test location |
|---|---|---|---|
| MEM-98-01 | `local`, `substrate`, `hindsight` accepted; invalid selector/config rejected safely; default substrate only when explicit resolved URL exists, else local; explicit unavailable backend fails, never silently falls back; selector local is not recall opt-out | `test_selector_all_values`; `test_invalid_selector_fails_validation`; `test_default_requires_explicit_resolved_substrate_url`; `test_explicit_missing_backend_does_not_fallback`; `test_local_selector_still_recalls_shared_sources` | `test_memory_backends.py`, `test_memory_integration.py` |
| MEM-98-02 | Independently recall local, configured substrate, configured Hindsight; missing local does not suppress remote; scope/repo validated before IO and isolated in every backend; local legacy banks remain readable; content-hash dedup preserves provenance/scores, deterministic ordering, global limit | `test_recall_each_source_state`; `test_failed_source_does_not_hide_other_hits`; `test_remote_recall_without_local_store`; `test_scope_repo_rejected_before_io`; `test_repo_isolation_all_backends`; `test_legacy_local_banks_unscoped`; `test_hash_dedup_merges_sources`; `test_order_and_global_limit`; `test_scores_are_source_local`; `test_state_before_dedup_and_limit` | new `test_memory_recall.py`; readiness tests; `test_memory_integration.py` |
| MEM-98-03 | Validate all inputs before IO; local save first; no remote after local refusal; substrate attempted when configured for any selector; only `accepted` succeeds; all nonaccepted outcomes and transport errors preserve local_saved but return failure; no rollback; local budgets respected | `test_validation_precedes_any_io`; `test_local_first_order`; `test_full_store_stops_remote_writes`; `test_substrate_accepted_only_success`; `test_conflict_quarantined_denied_unknown_are_failures`; `test_transport_failure_reports_local_saved`; `test_each_required_destination_timeout_is_independent`; `test_no_rollback`; `test_local_budget_and_metadata_overhead`; `test_codec_exact_envelope_roundtrip`; `test_codec_rejects_malformed_versioned_record`; `test_graph_task_metadata_roundtrip` | new `test_memory_retain.py`, codec tests in `test_memory_backends.py` |
| MEM-98-04 | Configured Hindsight always recalled; Hindsight writes only for selected hindsight; configured substrate writes under every selector; typed Hindsight failures reported; remove incompatible service-first bridge | `test_hindsight_recalled_for_each_selector`; `test_hindsight_write_only_when_selected`; `test_configured_substrate_written_for_each_selector`; `test_hindsight_refusal_and_failure_visible`; service bridge fallback tests removed/migrated | `test_memory_recall.py`, `test_memory_retain.py`, `test_hindsight_service.py` |
| MEM-98-05 | Per-source states exactly answered/empty/not_configured/unreachable/unauthorized; healthy valid empty distinct from malformed/network/auth failure; diagnostics separate and sanitized; source state pre-dedup; retain distinguishes selection/attempt from states without inventing a sixth state | `test_valid_empty_is_empty`; `test_malformed_envelope_not_empty`; `test_transport_failure_unreachable`; `test_structured_401_403_unauthorized`; `test_exception_text_does_not_infer_auth`; `test_unselected_unconfigured_not_configured`; `test_all_three_statuses_always_present` | backend, recall, Hindsight and doctor tests |
| MEM-98-06 | One structured result envelope; source/backend names; both commands, direct lead retain/recall, growth add, doctor and brief cache consistent; commands fail on remote write failure; approvals/evidence retained | `test_lead_recall_envelope`; `test_lead_retain_evidence_and_grant`; `test_growth_add_structured_partial_failure`; `test_omega_recall_returns_lead_result`; `test_omega_retain_uses_approval_gated_lead_result`; `test_command_propagates_ok_false`; `test_secret_rejection_before_dispatch`; `test_doctor_lists_three_sources`; `test_doctor_check_remains_read_only`; `test_retain_invalidates_brief_cache` | `test_lead.py`, `test_growth.py`, `test_omega_commands.py`, `test_desk_wiring.py`, integration tests |
| MEM-98-07 | Hindsight HTTPS-only, safe origin/bank path, no URL credentials/query/fragment; environment/0600 broker secrets only; no settings keys; no credential resolution if URL unset; redirect policy safe; errors/outputs redact even when broker redaction fails | `test_https_required_before_auth_or_io`; `test_reject_userinfo_query_fragment`; `test_reject_unsafe_bank_segment`; `test_redirect_does_not_forward_credentials`; `test_env_secret_and_mode_checked_file`; `test_unset_url_skips_secret_lookup_and_network`; `test_redaction_failure_never_returns_raw_error`; `test_key_never_in_settings_or_results` | `test_hindsight_service.py`, backend/wiring tests, `test_substrate_probes.py` |
| MEM-98-08 | Existing synchronous services remain synchronous; async handlers keep blocking fanout off-loop; one coordinator, one store, raw substrate client (not `_DeskSubstrate`); user-profile/replace/remove/onboarding stay local | `test_user_target_never_published`; `test_replace_remove_are_local`; `test_onboarding_stays_local`; fake-backed production behavior verifies shared effects through registered user paths | growth, integration tests |
| MEM-98-09 | Fake-only loopback/scripted transport exercises real registry, growth, lead, both slash commands and doctor; includes independent outages, secret/approval gates and response-loss retry identity only if server contract proves it | `test_default_registry_fake_backends`; `test_real_growth_add_path`; `test_real_lead_retain_and_recall`; `test_real_omega_retain_and_recall`; `test_real_doctor_reports_backend_states`; `test_real_retain_reload_recall_same_repo`; `test_reloaded_local_memory_rejects_other_repo`; `test_reloaded_local_memory_keeps_unscoped_legacy_readable`; `test_timeout_one_recall_source_keeps_healthy_hits`; `test_timeout_required_retain_destination_still_attempts_others`; `test_commit_then_drop_response_reuses_identity` only if phase-97 contract guarantees dedup; no live service request | new `test_memory_integration.py`, `test_omega_commands.py` |
| MEM-98-10 | Docs match runtime, exact full gates run once by main agent, prompt assembly only if prompts change, receipt contains factual verification and `rule adherence by future agents` under `unverified` | documentation review against `PLAN.md`; command log/receipt; conditional assembly result | docs assigned to 98-04; parent execution receipt |

### Cross-backend tests that MUST assert exact detail

- Canonicalization: `strip outer whitespace`, CRLF/CR→LF, no case fold or arbitrary bracket stripping; `test_hash_normalization_exact_rules` in `test_memory_recall.py`.
- No arbitrary scores: source score values remain attached only to their provenance; `test_scores_are_source_local`.
- A source that returns a duplicate still reports its original `answered` state; truncation does not alter state; covered by `test_state_before_dedup_and_limit`.
- Hindsight configured but selector local/substrate is queried, never written. For retain, configured substrate is written regardless of selector; Hindsight writes only when selected. A configured but intentionally unselected Hindsight destination is distinguished by `selected_for_write:false` and `attempted:false`, not mislabeled `not_configured`; attempted operation destinations use only the five defined states. Unset URL means no broker/key lookup/network request.
 - Stable operation UUID is generated once per logical operation and bound in a `MemoryBackends` in-memory ledger before local mutation. Caller replay must use the same ID in a separate invocation; fingerprint is canonical typed JSON for the exact versioned record (content plus all scope/repo/kind/bank/evidence/tags/graph/task fields), expected_version, selected backend, and normalized effective destination identities without credentials. Same fingerprint reuses the key; any field mismatch refuses before local/network IO. Unknown caller IDs (including after process/backend recreation, restart, or eviction) refuse before IO; never auto-adopt or mint a replacement. Keep at most the 1024 most-recent completed bindings in LRU order, never evict in-flight bindings. No TTL, session/time cap, journal, retry loop, queue, or background worker. Verify every fingerprint dimension and unknown/restart/eviction refusal. Exact-once is never claimed without upstream server support; Hindsight `document_id`, hashes, and JSON-RPC IDs are not proof.
- `memory_retain` retains its existing secret/evidence/grant checks. Growth add keeps existing host write policy. `/omega-retain` cannot bypass either by dispatching builtin `memory`. Target `user`, replace/remove, and onboarding paths remain local and do not write substrate/Hindsight.
- Results use a single `results:[{content,content_hash,sources:[...]}]` representation. All consumers in `omega_prime/grokbot/readiness_local.py` and `readiness_workflows.py` are migrated; do not add duplicate `entries` or `hits` aliases.

## Fake-backed production smoke recipe (future execution)

1. Build `default_registry` with a temporary local memory store and injected loopback fake SubstrateClient and fake Hindsight HTTP transport. Use synthetic local test URLs/credentials only; no environment value, live configuration, or service inspection. Configure all selector modes in isolated cases.
2. Dispatch registered growth `memory(action="add", target="memory")`, then registered `lead_memory_retain` with required grant/evidence, then `lead_memory_recall`; assert local-first order, backend participation, hash/provenance, repo isolation, and source states. Retain to disk, then recall through a separately constructed/reloaded MemoryStore without recreating registry; verify same-repo hit/hash, cross-repo exclusion, and legacy unscoped record behavior.
3. Execute `/omega-retain` and `/omega-recall` through `execute_command` / actual command dispatch, not a mocked handler; assert approval denial is honored, retain calls lead coordinator, refused remote publication returns command failure with `local_saved:true`, and recall distinguishes empty from remote error.
4. Dispatch `lead_doctor(action="check")` and `/omega-doctor` against fakes for answered, empty, not-configured, unreachable, and unauthorized cases; assert all backend names/states and no writes. Confirm a constructed but unreachable client is not green.
5. Independently script ordinary per-backend HTTP request timeouts: recall timeout is `unreachable` while healthy-source hits survive; each required retain timeout produces `local_saved:true`, `ok:false`, sanitized diagnostics, and does not suppress attempts to other required destinations. Do not add session/turn/execution-budget time caps, retries, or background work.
6. Script an upstream fake that commits then drops the first response. Assert caller/transport replay retains exactly the operation key only if phase-97 server contract guarantees dedup. Never make this against a live service. If that contract is absent, record the prerequisite blocker instead.
7. Verify that no unset Hindsight URL resolves secrets or sends traffic, no selected non-Hindsight destination writes Hindsight, and no USER.md/profile or local replace/remove/onboarding operation publishes remotely.

## Exact full phase gates (main agent only, once after all implementation)

Use the authorized environment (`PATH=/workspace/omes-bot/.venv/bin:$PATH`, `PYTHONPATH=/workspace/omes-bot-wt/p2`) for Python tools. Planning authors ran no tests or checks; the parent recorded the separate pre-change baseline below. Execution sequence: orchestrator runs focused wave-1 tests after 98-01 and before wave 2; focused union tests after both wave-2 plans land and before wave 3; exact full gates once after wave 3. Subagents do not run tests, gates, or formatters. Commands:
```sh
ruff check omega_prime/
ruff format --check omega_prime/
mypy omega_prime/
pytest omega_prime/tests -q
# Run only if prompt files changed:
bash omega_prime/scripts/assemble-prompts.sh --check
```

Record each actual exit/result. Do not turn known baseline-only failures into phase failures or call the full suite green when those failures remain; also do not attribute a new failure to baseline unless its baseline status is evidenced. No selective test set substitutes for the full `pytest omega_prime/tests -q` acceptance gate. Planning-document artifact consistency checks are separate and are not a runtime verification substitute.

## Existing observed baseline at 44b28df — recorded by parent, not re-run

Before planning edits, recorded results were: `ruff check` exit 0; `ruff format --check` exit 0 (589 files); `mypy` exit 0 (462 source files); full pytest exit 1 with **27 failed, 4777 passed, 11 skipped, 15 warnings in 244.72s**. These are pre-change baseline observations, not phase-98 implementation verification. The planning authors did not run gates. Observed baseline failures, all unrelated to this phase's memory runtime scope, were:

1. `omega_prime/tests/test_grokbot_oneclick.py::test_oneclick_dry_run_sse` — preflight rejects empty/uninitialized prime-agent submodule.
2. `omega_prime/tests/test_prime_kernel_boundary.py::test_read_only_skill_list_still_returns_the_skills` — result lacks expected `skills`.
3. `omega_prime/tests/test_prime_kernel_boundary.py::test_read_only_crates_still_reports_the_workspace` — result lacks expected `loaded`.
4. `omega_prime/tests/test_prime_kernel_cells.py::test_namespace_persists_across_cells` — missing `rlm` runtime module.
5. `omega_prime/tests/test_prime_kernel_cells.py::test_cell_output_is_returned_and_never_written_to_the_process_streams` — missing `rlm`.
6. `omega_prime/tests/test_prime_kernel_cells.py::test_cell_exit_requests_are_cell_errors_and_the_kernel_keeps_working` — missing `rlm`.
7. `omega_prime/tests/test_prime_kernel_cells.py::test_cell_spawn_uses_in_process_host` — missing `rlm`.
8. `omega_prime/tests/test_prime_kernel_cells.py::test_cell_child_answer_visible_only_through_collect` — missing `rlm`.
9. `omega_prime/tests/test_prime_kernel_cells.py::test_factory_help_while_disabled` — missing `rlm`.
10. `omega_prime/tests/test_prime_kernel_cells.py::test_validate_spec_rejects_empty` — missing `rlm`.
11. `omega_prime/tests/test_prime_kernel_cells.py::test_enable_factory_keeps_help` — missing `rlm`.
12. `omega_prime/tests/test_prime_kernel_cells.py::test_run_bash_echo` — missing `rlm`.
13. `omega_prime/tests/test_prime_kernel_host.py::test_prime_kernel_host_round_trip` — absent `prime-agent/Cargo.toml`.
14. `omega_prime/tests/test_prime_kernel_skills.py::test_list_skills_marks_edit_importable_and_factory_markdown_only` — absent `prime-agent/skills`.
15. `omega_prime/tests/test_prime_kernel_skills.py::test_load_skill_edit_replaces_a_unique_string` — missing `edit` skill under absent submodule.
16. `omega_prime/tests/test_prime_kernel_skills.py::test_list_skills_does_not_import_attach_image` — absent `prime-agent/skills`.
17. `omega_prime/tests/test_prime_native.py::test_native_status_accepts_unbuilt_extension` — absent `prime-agent/Cargo.toml`.
18. `omega_prime/tests/test_prime_native.py::test_goal_bindings_follow_pa_core` — absent `prime-agent/Cargo.toml`.
19. `omega_prime/tests/test_prime_native.py::test_autonomous_run_uses_pa_core_limits` — absent `prime-agent/Cargo.toml`.
20. `omega_prime/tests/test_prime_native.py::test_protocol_parse_and_encode_use_the_rust_frames` — absent `prime-agent/Cargo.toml`.
21. `omega_prime/tests/test_prime_native.py::test_heartbeat_schedule_and_deferral` — absent `prime-agent/Cargo.toml`.
22. `omega_prime/tests/test_prime_native.py::test_refinement_history_round_trip` — absent `prime-agent/Cargo.toml`.
23. `omega_prime/tests/test_prime_native.py::test_goal_apply_uses_pa_core_transitions` — absent `prime-agent/Cargo.toml`.
24. `omega_prime/tests/test_prime_native.py::test_slash_parsers_match_pa_core` — absent `prime-agent/Cargo.toml`.
25. `omega_prime/tests/test_prime_native.py::test_native_tool_validation_rejects_missing_required_argument` — absent `prime-agent/Cargo.toml`.
26. `omega_prime/tests/test_prime_native.py::test_kernel_tools_call_the_rust_commands` — absent `prime-agent/Cargo.toml`.
27. `omega_prime/tests/test_setup_check.py::test_checks_pass_on_repo_root` — setup/preflight expectation mismatch.

Do not initialize submodules or repair these tests as part of phase 98. The baseline log's preflight mentioned an empty prime-agent directory and unregistered tool-roster entries; no live service check was performed here. These are baseline observations, not phase-98 acceptance results.

## Planning-session final verification

The orchestrator reran the exact source gates after planning authoring:

| Command | Exit | Observed result |
|---|---|---|
| `ruff check omega_prime/` | 0 | All checks passed |
| `ruff format --check omega_prime/` | 0 | 589 files already formatted |
| `mypy omega_prime/` | 0 | No issues in 462 source files |
| `pytest omega_prime/tests -q` | 1 | 4777 passed, 11 skipped, the same 27 named baseline failures; 218.50 seconds |

Planning artifact smoke passed: roadmap/state consistency, executable plan metadata, dependency waves, disjoint wave-2 ownership, requirement coverage, baseline enumeration, links, codec/retry/timeout contracts, and planning-only tracked changes. No phase-98 runtime implementation or acceptance behavior was exercised. Prompt assembly was not applicable: no prompts changed. Command evidence is in `.receipts/bot-00-omega-prime/phase-98-planning.json`.

## Phase 98 implementation execution

Executed on `fix/wiring-p2-shared-memory` against the available base, without waiting for phase 97, as directed by the operator. This is branch-local implementation evidence, not proof of ordered integration.

| Command | Exit | Result |
|---|---:|---|
| `ruff check omega_prime/` | 0 | All checks passed |
| `ruff format --check omega_prime/` | 0 | 595 files already formatted |
| `mypy omega_prime/` | 0 | No issues in 468 source files |
| `pytest omega_prime/tests -q` | 1 | 27 failed, 4847 passed, 11 skipped, 15 warnings in 228.59s; same 27 named baseline failures listed above |
| `python -m omega_prime.tooling.plugin --check` | 0 | 66 generated files up to date |
| `python -m omega_prime.tooling.catalog --check` | 0 | Generated tool catalog up to date |
| `pytest omega_prime/tests/test_grokbot_readiness.py::test_cli_full_local_profile -q` | 0 | 1 passed after migrating the readiness retain scenario to the approved source syntax |

The full-suite failures exactly match the 27 pre-change baseline cases enumerated in this file. The phase's memory/runtime tests passed in the full run. Prompt files were unchanged, so prompt-format and assembled-prompt checks were not applicable. No live service was contacted.

Integration limitation: this base's `SubstrateClient.memory_search` catches transport errors and returns `[]`; the phase-97 source-aware contract is needed to distinguish `empty` from `unreachable`. Do not reimplement that transport behavior here. After the ordered rebase, wire the final resolver/error shape and rerun the full gates. Operator-owned `HINDSIGHT_URL` configuration and connector restart remain unperformed.

## Operator-owned checks and receipt

Live URL validation, VPS/restart operation, and phase77.1 human UAT remain operator-owned, untouched, and **unverified/nonblocking for code-plan completion**. Never use those checks as implementation prerequisites or claim they passed. The parent completion workflow must write `.receipts/<bot-id>/<task-id>.json` using the verification-receipts skill, list `rule adherence by future agents` under `unverified`, and record exactly which gates were exercised; this validation document is not that receipt.
