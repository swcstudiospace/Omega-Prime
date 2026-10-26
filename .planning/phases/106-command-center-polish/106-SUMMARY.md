# Phase 106 — Command Center polish

Local implementation and offline verification for all five plans are recorded. Plans ran in the requested order: 01/02/04, then 03/05; the Plan 05 placeholder preceded Plan 04 imports, Plan 04 rebuilt wave-1 assets after 02, and Plan 05 rebuilt final wave-2 assets after 03. All product and documentation changes are committed locally by the end of this task; no remote, push, PR, merge, deploy, milestone audit, completion, or cleanup was run.

## Regression tests added and acceptance coverage

### Plan 01 — merge bounds, degradation, and model buckets

- `omega_prime/tests/test_dashboard_github_merges_polish.py::test_closed_pages_avoid_legacy_1_2mb_truncation` — AC1 (bounded closed-PR pagination).
- `omega_prime/tests/test_dashboard_github_merges_polish.py::test_oversize_response_reports_too_large_and_keeps_stale_merges` — AC1/AC2 (limit+1 oversize detection and stale-cache retention).
- `omega_prime/tests/test_dashboard_github_merges_polish.py::test_closed_paging_stops_at_max_merged_rows_per_target` — AC1 (merged-row cap).
- `omega_prime/tests/test_dashboard_model_buckets.py::test_done_and_failed_are_finished_only` — AC6 (terminal task projection).
- `omega_prime/tests/test_dashboard_model_buckets.py::test_unknown_exited_and_quiet_sessions_are_idle_only` — AC6 (Idle classification for Herdr/quiet sandbox records).
- `omega_prime/tests/test_dashboard_model_buckets.py::test_idle_changes_snapshot_hash_and_keeps_number_across_restart` — AC6/AC8 (snapshot identity, hash, and durable number).

### Plan 02 — backend-first branch deduplication

- `omega_prime/dashboard/frontend/src/views/CiCd.dedup.test.tsx::prefers backend branch rows even when fallback SHA is stale` — AC3 (backend row is authoritative).
- `omega_prime/dashboard/frontend/src/views/CiCd.dedup.test.tsx::adds fallback only when repository branch has no backend row` — AC3 (fallback only for an absent repository+branch row).

### Plan 03 — merged-PR Greptile evidence

- `omega_prime/tests/test_dashboard_github_greptile_polish.py::test_branch_uses_merged_pr_score_and_actual_reviewed_sha` — AC4 (merged PR association and actual reviewed SHA).
- `omega_prime/tests/test_dashboard_github_greptile_polish.py::test_direct_commit_is_not_applicable_and_lookup_failure_is_row_local` — AC4 (not-applicable and row-local unavailable states).
- `omega_prime/dashboard/frontend/src/views/CiCd.greptile.test.tsx::renders merged PR score, no PR, unavailable and unreported reviewed SHA` — AC4 (truthful card wording/state).
- `omega_prime/dashboard/frontend/src/views/CiCd.test.tsx::shows recent_merges failure with original successful section freshness` — AC1/AC4 (section freshness remains independently truthful).

### Plan 04 — routes, task detail, density, and Idle UI

- `omega_prime/dashboard/frontend/src/App.routes.test.tsx::opens and clears each bucket and restores initial hash` — AC5 (hash route state).
- `omega_prime/dashboard/frontend/src/App.routes.test.tsx::preserves bucket origin through task detail refresh Back and Forward` — AC5 (history/back-navigation continuity).
- `omega_prime/dashboard/frontend/src/components/TaskDetail.routes.test.tsx::opens numbered task detail with recorded links PR evidence wiring and events` — AC5/AC6 (detail evidence and safe recorded links).
- `omega_prime/dashboard/frontend/src/components/TaskDetail.routes.test.tsx::shows recovery only for an unknown task after snapshot data is available` — AC5 (unknown task recovery).
- `omega_prime/dashboard/frontend/src/components/TaskDetail.routes.test.tsx::resolves Idle and finished identities with their recorded evidence` — AC6 (bucket-independent task lookup).
- `omega_prime/dashboard/frontend/src/components/TaskDetail.routes.test.tsx::does not show not-found until the initial snapshot has loaded` — AC5 (loading state).
- `omega_prime/dashboard/frontend/src/views/Overview.idle.test.tsx::excludes idle sessions from Active and renders the separate group` — AC6 (separate Idle group and count).
- `omega_prime/dashboard/frontend/src/views/Tasks.density.test.tsx::toggles compact field rich rows without losing bucket controls` — AC5/AC6 (density and bucket controls).
- `omega_prime/dashboard/frontend/src/views/Tasks.density.test.tsx::keeps bucket and density controls available for an empty list` — AC5/AC6 (empty bucket state).

### Plan 05 — task-to-pane mapping and read-only terminal

- `omega_prime/tests/test_dashboard_terminal.py::test_task_mapping_roundtrips_validation_collector_and_snapshot` — AC7/AC8 (validated mapping survives the task pipeline).
- `omega_prime/tests/test_dashboard_terminal.py::test_invalid_pane_mapping_is_rejected` — AC7 (invalid mapping rejection).
- `omega_prime/tests/test_dashboard_terminal.py::test_pane_owner_fallback_is_explicit_and_validated` — AC7 (only explicit validated fallback).
- `omega_prime/tests/test_dashboard_terminal.py::test_sanitizer_redacts_controls_lines_bytes_and_tokens` — AC7 (redaction, control stripping, output bounds).
- `omega_prime/tests/test_dashboard_terminal_api.py::test_terminal_route_resolves_only_task_owned_pane_and_uses_read_argv` — AC7 (exact task authorization and read-only command).
- `omega_prime/tests/test_dashboard_terminal_api.py::test_terminal_sanitizes_redacts_caps_and_sends_only_changed_frames` — AC7 (sanitized bounded SSE frames).
- `omega_prime/tests/test_dashboard_terminal_api.py::test_terminal_disconnect_releases_capacity_and_stops_reads` — AC7 (disconnect cleanup).
- `omega_prime/tests/test_dashboard_terminal_api.py::test_terminal_cancels_capture_when_raw_asgi_viewer_disconnects` — AC7 (ASGI disconnect cancellation).
- `omega_prime/tests/test_dashboard_terminal_api.py::test_terminal_unchanged_capture_heartbeats_without_timestamp_change` — AC7 (heartbeat timestamp semantics).
- `omega_prime/tests/test_dashboard_terminal_api.py::test_terminal_state_change_does_not_advance_output_timestamp` — AC7 (status change does not fake output freshness).
- `omega_prime/tests/test_dashboard_terminal_api.py::test_terminal_reports_confirmed_closed_pane_and_releases_capacity` — AC7 (closed pane and cleanup).
- `omega_prime/tests/test_dashboard_terminal_api.py::test_terminal_stream_limit_returns_429_before_sse_starts` — AC7 (bounded concurrent admission).
- `omega_prime/tests/test_dashboard_terminal_api.py::test_terminal_has_no_input_route_or_subcommand` — AC7 (no input capability).
- `omega_prime/dashboard/frontend/src/components/TerminalPanel.test.tsx::streams plain text and cleans reconnect on task change` — AC7 (plain-text rendering, reconnect, cleanup).
- `omega_prime/dashboard/frontend/src/components/TerminalPanel.test.tsx::keeps transport running when pausing auto-scroll and exposes status` — AC7 (scroll pause does not stop transport).
- `omega_prime/dashboard/frontend/src/components/TerminalPanel.test.tsx::renders local terminal failure states and copies only the displayed buffer` — AC7 (explicit statuses and safe copy).

## Acceptance and verification results

- AC1–AC7: implementation and focused offline regressions are present. GitHub and Herdr used fake transports/runners only. No live parity or real terminal claim is made.
- AC8: final npm install, full frontend tests, production build, build-asset check, dashboard-file family, static serving tests, and docs/public-repo tests passed. The required broad Python dashboard selector did **not** pass: pytest exits 2 during collection because `mcp` and `apscheduler` are missing. The full Python suite has the same collection blocker. No dependency was installed, no tests were deselected, and no collection error was hidden.
- AC9: pending outer-driver host inspection and five screenshots. Browser visual review, 7441 checks, signed sandbox evidence and real-terminal check were not performed here.
- No baseline transplant was run; no FAIL-on-base / PASS-after paired evidence is claimed.
- Ruff and mypy could not run because their Python modules are absent. `polyglot-check` is not installed. Supplied baseline diagnostics were not reproducible in this sandbox; no claim of baseline comparison is made.

Final code/test output: full Vitest suite 17 files passed, 1 skipped; 35 tests passed, 1 skipped. Dashboard-file family 158 passed, 1 skipped. Dashboard static-serving tests 13 passed, 1 skipped. Docs/public-repo tests 31 passed. Final build output references `index-D8yHkJLM.js` and `index-Bb9yS-L5.css`; exact generated assets and limitations are recorded in Plan 05 summary and `.receipts/bot-00-omega-prime/00025-phase106-execution.json`.
