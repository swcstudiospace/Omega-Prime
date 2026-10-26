# Phase 106 Plan 05 Summary

Completed the validated task-to-pane mapping and read-only terminal SSE feature. The prerequisite placeholder component was committed as `863ee71` before Plan 04 imported it; this plan replaced it with the final `TerminalPanel` and performed the sole wave-2 generated-asset build after Plan 03 integration.

## Implemented contract

- Optional `herdr_pane` mapping is validated by the bridge writer/reader and retained through normalized task records and snapshots. Pane resolution requires the exact current task identity; a valid explicit `owner.kind: herdr-pane` mapping is the only fallback. The browser supplies no pane identifier.
- `GET /api/tasks/{task_id}/terminal` emits named `terminal` SSE events. The only permitted subprocess argv is `herdr pane read <validated-pane> --source recent --lines 200 --format text`; execution uses injected runners in tests, never a live Herdr command.
- Capture uses a 1,000,000-byte bound and 2-second timeout, strips terminal control sequences and redacts before output clipping, caps output to 200 lines and 65,536 UTF-8 bytes, and admits at most four active streams. Reads stop on disconnect; subprocess/task cancellation and stream capacity are released. Unchanged output is a heartbeat and does not advance the output timestamp.
- No input endpoint or input subcommand exists. Existing loopback-only binding, method-before-Host 405 handling, Host validation, CSP/security headers, and self-only origins remain in the application. Terminal payloads contain sanitized text/status/timestamp, not the pane id, argv, raw stderr, or GitHub token.
- The frontend uses one task-scoped `EventSource`, renders plain React text only, reconnects without replacing the last safe buffer, cleans up on task change/unmount, and exposes pause-auto-scroll/copy-visible-buffer behavior and explicit local failure states.

## Regression tests and acceptance coverage

Python mapping and sanitizer tests:

- `omega_prime/tests/test_dashboard_terminal.py::test_task_mapping_roundtrips_validation_collector_and_snapshot` — AC7/AC8 (mapping survives write/read, collector, and snapshot).
- `omega_prime/tests/test_dashboard_terminal.py::test_invalid_pane_mapping_is_rejected` — AC7 (reject malformed/injected mapping).
- `omega_prime/tests/test_dashboard_terminal.py::test_pane_owner_fallback_is_explicit_and_validated` — AC7 (no arbitrary owner fallback).
- `omega_prime/tests/test_dashboard_terminal.py::test_sanitizer_redacts_controls_lines_bytes_and_tokens` — AC7 (sanitization and output bounds).

Terminal API tests:

- `omega_prime/tests/test_dashboard_terminal_api.py::test_terminal_route_resolves_only_task_owned_pane_and_uses_read_argv` — AC7 (exact task authorization and read-only argv).
- `omega_prime/tests/test_dashboard_terminal_api.py::test_terminal_sanitizes_redacts_caps_and_sends_only_changed_frames` — AC7 (wire frames contain safe changed output only).
- `omega_prime/tests/test_dashboard_terminal_api.py::test_terminal_disconnect_releases_capacity_and_stops_reads` — AC7 (disconnect cleanup).
- `omega_prime/tests/test_dashboard_terminal_api.py::test_terminal_cancels_capture_when_raw_asgi_viewer_disconnects` — AC7 (raw ASGI disconnect cancellation).
- `omega_prime/tests/test_dashboard_terminal_api.py::test_terminal_unchanged_capture_heartbeats_without_timestamp_change` — AC7 (heartbeat semantics).
- `omega_prime/tests/test_dashboard_terminal_api.py::test_terminal_state_change_does_not_advance_output_timestamp` — AC7 (only output changes timestamp).
- `omega_prime/tests/test_dashboard_terminal_api.py::test_terminal_reports_confirmed_closed_pane_and_releases_capacity` — AC7 (closed-pane state and cleanup).
- `omega_prime/tests/test_dashboard_terminal_api.py::test_terminal_stream_limit_returns_429_before_sse_starts` — AC7 (bounded admission).
- `omega_prime/tests/test_dashboard_terminal_api.py::test_terminal_has_no_input_route_or_subcommand` — AC7 (no terminal input capability).

Frontend tests:

- `omega_prime/dashboard/frontend/src/components/TerminalPanel.test.tsx::streams plain text and cleans reconnect on task change` — AC7 (text-only rendering, reconnect, lifecycle cleanup).
- `omega_prime/dashboard/frontend/src/components/TerminalPanel.test.tsx::keeps transport running when pausing auto-scroll and exposes status` — AC7 (pause affects scroll only).
- `omega_prime/dashboard/frontend/src/components/TerminalPanel.test.tsx::renders local terminal failure states and copies only the displayed buffer` — AC7 (safe copy and explicit failure states).

## Verification and final generated assets

- `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_dashboard_terminal.py omega_prime/tests/test_dashboard_terminal_api.py` — exit 0; 13 passed.
- `npm test -- --run src/components/TerminalPanel.test.tsx src/components/TaskDetail.routes.test.tsx` — exit 0; 2 files / 7 tests passed.
- Final `npm ci --registry=https://registry.npmjs.org` — exit 0; no dependency or lockfile change was introduced.
- Final `npm test -- --run` — exit 0; 17 test files passed, 1 skipped; 35 tests passed, 1 skipped.
- Final `npm run build` — exit 0; actual output references `assets/index-D8yHkJLM.js` and `assets/index-Bb9yS-L5.css`; locally hosted fonts and license notices remain present.
- `CC_CHECK_BUILD=1 npm test -- --run src/test/build-assets.test.ts` — exit 0; 1 passed.
- `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_docs.py omega_prime/tests/test_public_repo.py` — exit 0; 31 passed.
- Dashboard family `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_dashboard*.py` — exit 0; 158 passed, 1 skipped.

The required broad `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/ -k dashboard` and full Python suite both exit 2 during collection because `mcp` and `apscheduler` are absent. No Python packages were installed and collection was not suppressed. `ruff`, `mypy`, and `polyglot-check` are unavailable; supplied baseline files were not reproducible here. No baseline transplant was run, so no FAIL-on-base claim is made. Live Herdr reads, host 7441 inspection/screenshots, signed sandbox evidence, and actual terminal behavior remain unverified outer-driver gates.
