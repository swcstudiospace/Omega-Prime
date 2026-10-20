---
phase: 105-command-center
plan: "04"
subsystem: dashboard-server
tags: [starlette, static-assets, CSP, SSE, setuptools]
requires:
  - phase: 105-03
    provides: built Command Center entry, hashed JS/CSS, fonts, and license notices
provides:
  - built Command Center at / and /cc/ with explicit /legacy recovery and confined assets
  - self-only CSP, route-level MIME/cache policy, and HTTP security regressions
  - API/SSE regression proving shared redacted six-key snapshot data
  - expanded package-data rules for nested generated frontend assets
affects: [105-command-center, dashboard-packaging, local-dashboard-security]
tech-stack:
  added: []
  patterns:
    - explicit allowlisted static routes with realpath containment and no SPA fallback
    - isolated raw-ASGI SSE checks for named snapshot events
key-files:
  created:
    - omega_prime/tests/test_dashboard_command_center_static.py
  modified:
    - omega_prime/dashboard/app.py
    - pyproject.toml
    - omega_prime/tests/test_dashboard_app.py
    - omega_prime/tests/test_dashboard_app_fixes.py
    - omega_prime/tests/test_dashboard_live_fixes.py
    - omega_prime/tests/test_dashboard_integration.py
key-decisions:
  - "The built Command Center is the default page; /legacy preserves the existing no-JavaScript dashboard."
  - "Only generated JS/CSS/WOFF2 and text notices are exposed beneath /cc; no broad static mount or SPA catchall is used."
  - "Package-data collection is tested with setuptools build_py when that builder exists; no package was installed when setuptools was absent."
patterns-established:
  - "HTML revalidates; only content-hashed JS/CSS/fonts receive immutable caching; license text remains non-immutable."
  - "The existing redacted poller snapshot is published unchanged through JSON and named SSE."
requirements-completed: [a1, a4, a5]
coverage:
  - id: D1
    description: "Default and legacy routes serve confined static assets with required security headers, MIME, and cache policy."
    requirement: a5
    verification:
      - kind: integration
        ref: "python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_dashboard_command_center_static.py omega_prime/tests/test_dashboard_app.py omega_prime/tests/test_dashboard_live_fixes.py"
        status: pass
    human_judgment: false
  - id: D2
    description: "Nested generated assets and notices are included in the built Python package."
    requirement: a4
    verification:
      - kind: other
        ref: "test_package_data_build_py_collects_nested_dashboard_assets"
        status: unknown
    human_judgment: true
    rationale: "The environment has no setuptools package builder, so real package-data collection could not execute without a prohibited install."
  - id: D3
    description: "The JSON API and named snapshot SSE expose the same recursively redacted nested GitHub payload while retaining six top-level snapshot keys."
    requirement: a5
    verification:
      - kind: integration
        ref: "omega_prime/tests/test_dashboard_integration.py::test_snapshot_api_and_named_sse_share_redacted_nested_github_data"
        status: pass
    human_judgment: false
  - id: D4
    description: "Durable task display identity reaches the local API and SSE after process restart."
    requirement: a1
    verification:
      - kind: e2e
        ref: "isolated Python runtime smoke on 127.0.0.1:51103; see Evidence and limitations"
        status: pass
    human_judgment: true
    rationale: "Browser task selection and visual behavior were not verified because managed Chromium could not be installed in this environment."
actuals:
  tokens: 6409
  tasks: 3
  commits: 3
commits: 3
plan_head_before: d3fbf60a0cbf839d32f1e503bec6e75509e984ab
plan_head_after: 81ceea88a9de8f00bf2ff96f5ea93d022a4ab163
duration: not-measured
completed: 2026-10-20
status: complete
---

# Phase 105 Plan 04: Command Center Server Integration Summary

**Serves the W3-built Command Center by default with confined, self-only static delivery; preserves the legacy dashboard and verifies that redacted JSON and SSE share one snapshot.**

## Performance

- **Duration:** Not measured; a plan start timestamp was not captured.
- **Started:** Not captured.
- **Completed:** 2026-10-20T23:05:16Z.
- **Tasks:** 3 / 3.
- **Files modified:** 7 planned implementation/test files (plus this summary and the requested receipt).

## Accomplishments

- Added `/`, `/cc/`, self-only `/cc` redirect, `/legacy`, and allowlisted legacy `/static/app.js` and `/static/app.css` routes. Missing built entry returns explicit `503` with a `/legacy` recovery link and `no-store`; unknown paths stay real `404`s.
- Restrained `/cc` assets to resolved descendants in the built tree, permitting only JS/CSS/WOFF2 and license text. Tests cover raw/encoded traversal, symlink escape, hidden files, source maps, wrong namespaces, MIME, cache, methods, exact Host handling including duplicate Host headers, and all expected security headers.
- Added package-data inclusion patterns for the built entry, direct nested asset files, and notices while retaining legacy static data.
- Added an actual raw-ASGI named-SSE integration assertion against the JSON payload, including a nested secret-bearing GitHub fixture and unknown nested-field filtering. Existing six-key snapshot and prior SSE/disconnect behavior are retained.

## Task Commits

1. **Task 1: Serve built/default and legacy pages under guarded static policy** — `e357071` (`feat`).
2. **Task 2: Include all generated assets/fonts/notices in the Python package** — `6e36b55` (`chore`).
3. **Task 3: Prove shared redacted API/SSE and end-to-end local operation** — `81ceea8` (`test`).

No metadata/state/roadmap commit was made; this execution was limited to the requested task commits and the requested summary/receipt artifacts.

## Verification

| Command or smoke | Result |
|---|---|
| `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_dashboard_command_center_static.py omega_prime/tests/test_dashboard_app.py omega_prime/tests/test_dashboard_live_fixes.py` | Exit 0; 21 passed, 1 Starlette/httpx deprecation warning. An earlier run exposed two fixture assertions that were corrected (the built entry's `/legacy` link and IPv6 test host configuration). |
| `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_dashboard_app_fixes.py omega_prime/tests/test_dashboard_command_center_static.py` | Exit 0; 12 passed, 1 skipped. Real package collection test skipped because `setuptools` is unavailable. |
| `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_dashboard_integration.py omega_prime/tests/test_dashboard_app.py omega_prime/tests/test_dashboard_app_fixes.py omega_prime/tests/test_dashboard_live_fixes.py omega_prime/tests/test_dashboard_command_center_static.py` | Exit 0; 34 passed, 1 skipped, 1 Starlette/httpx deprecation warning. An earlier run exposed an incomplete PR fixture (missing `repo` and `number`); the fixture was corrected before the passing run. |
| `python3 -c 'import setuptools; ...'` | Exit 1: `ModuleNotFoundError: No module named 'setuptools'`. No package installation was attempted. |
| Isolated CLI/API/SSE process smoke, temporary `HOME`, temporary task bridge, fleet default disabled, `GITHUB_PAT_DESK_LEAD` unset, loopback port `51103` | Completed. Separate `next-id` processes each returned `00002`; bridge bytes and `mtime_ns` were unchanged. This is the correct next unallocated sequence after the seeded task's pending backfill, not an assignment. `assign-id --id tasks:smoke-task --title 'W4 Smoke Task'` returned and persisted `00001 - W4 Smoke Task`. The live API exposed that identity; a named `snapshot` SSE carried the same task display ID; restarting the process with the same temporary HOME preserved it. `/` and `/legacy` both returned successfully. API and SSE snapshots were observed on different poll intervals, so their generated timestamps differed; exact same-snapshot JSON/SSE equality is covered by the deterministic integration test above. The temporary process was stopped. |

The first smoke assertion expected `next-id` to print `00001`; the actual `00002` was explained by the seeded task's pending backfill. The observed document remained byte- and mtime-identical, and the subsequent real assignment correctly persisted sequence 1. No code change was made for this expectation correction.

## Evidence and limitations

- The browser automation module was present, but opening the local UI failed because it could not install managed Chromium (`Unable to connect`). No browser selection without reload, screenshot, reduced-motion rendering, no-JavaScript visual check, or outage-to-polling visual recovery is claimed.
- Setuptools was absent, so test-level real `build_py` package collection and serving from the collected package could not run. The source-tree HTTP route and security checks did run. This is an unresolved packaging-evidence prerequisite; do not treat the skipped test as proof of archive contents.
- No live GitHub requests, live parity, signed sandbox/attestation, or real-data screenshots were run or claimed.
- No source changes were made to W3-generated assets or W1/W2 contracts.

## Deviations from Plan

No implementation scope deviations. The plan's package collection and browser portions were attempted but remain unverified for the exact tooling limitations above; no dependencies were installed. The dedicated local CLI/API/SSE smoke completed after adjusting the expected preview sequence to account for seeded pending backfill.

## Issues Encountered

- The configured Python environment lacks `setuptools`; the real package-data test skipped, and no install was attempted.
- Managed browser startup could not install Chromium because the environment could not connect to the download endpoint.
- The initial Task 1 and Task 3 acceptance runs found test-fixture assumptions; both were corrected and the final task-specific acceptance commands passed.

## Next Phase Readiness

The server integration and offline security/API regressions are complete. The outer integration owner must run the required aggregate dashboard commands after W5 and may need an environment with existing setuptools/browser tooling to close the package archive and browser UI evidence gaps.

## Self-Check: PASSED

- Summary and receipt files exist.
- Task commits `e357071`, `6e36b55`, and `81ceea8` are ancestors of HEAD.

---
*Phase: 105-command-center*
*Completed: 2026-10-20*
