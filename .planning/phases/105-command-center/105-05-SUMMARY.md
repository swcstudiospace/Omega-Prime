---
phase: 105-command-center
plan: "05"
subsystem: docs-and-verification
requires:
  - phase: 105-03
    provides: Self-hosted React frontend and verified build output
  - phase: 105-04
    provides: Guarded routes, package configuration, JSON/SSE security tests
provides:
  - Updated Command Center operator, security, schema, identity, build and attribution guide
  - Unreleased changelog entry
  - Final offline acceptance outcomes and explicit outer-driver handoff
affects: [105-command-center, dashboard]
completed: 2026-10-20
status: implementation-complete-with-recorded-verification-limitations
---

# Phase 105 Plan 05: Command Center Documentation and Final Evidence

Updated `docs/dashboard.md` in place and added a Phase 105 entry under the existing `## Unreleased` changelog section. The guide now covers the loopback/read-only policy, routes and recovery behavior, the six-field snapshot, published `sources.github.command_center` path, durable task numbering, view semantics, bounded GitHub cache, self-hosted static build, fonts/licenses, adapted components and evidence limits.

The final frontend contract review found that `DashboardPoller` publishes the W2 `command_center` object directly on `sources.github`, while the initial frontend expected a nonexistent `extra.command_center` wrapper. Corrected the frontend types, selectors, view accessors and fixtures to match actual published data, regenerated the static build, and reran the frontend suite/build and asset inspection. Updated the W3 execution summary with the corrected final asset name and measured output.

## Verification

| Command / smoke | Exit | Result |
|---|---:|---|
| `(npm ci --registry=https://registry.npmjs.org && npm test -- --run && npm run build)` from `omega_prime/dashboard/frontend` | 0 | 128 packages installed; Vitest: 10 files passed, 1 build-only file skipped; 17 passed, 1 skipped; TypeScript project build and Vite 8.3.4 build passed, 445 modules transformed. Final JS `index-CtUpPsk6.js` (376.50 kB; Vite gzip 119.38 kB), CSS 19.25 kB (5.52 kB gzip), four WOFF2 fonts. |
| `CC_CHECK_BUILD=1 npm test -- --run src/test/build-assets.test.ts` from frontend | 0 | Generated entry/assets/fonts/notices and external/runtime asset constraints validated; 489,985 distributable raw bytes and 219,137 bytes from individually gzip-compressed files. |
| `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/ -k dashboard` | 2 | Required command attempted; collection failed before tests because unrelated project modules cannot import absent `mcp` and `apscheduler`. No package was installed and the failure was not suppressed. |
| `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_dashboard*.py` | 0 | 133 passed, 1 skipped, 1 existing Starlette/httpx deprecation warning. A pre-existing 1 ms timeout fixture was made deterministic (50 ms timeout, 100 ms simulated delay) after the initial run exposed a scheduling race. |
| `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_docs.py omega_prime/tests/test_public_repo.py` | 0 | 31 passed after documentation changes. |
| W4 route acceptance | 0 | 21 passed in static/app/live-fix tests. |
| W4 packaging and static-asset acceptance | 0 | 12 passed, 1 skipped; real `setuptools build_py` collection skipped because setuptools is unavailable. No Python package installation was permitted. |
| W4 API/SSE integration acceptance | 0 | 34 passed, 1 skipped; package-data case skipped for the same missing setuptools prerequisite. |
| W4 isolated local CLI/API/SSE smoke | 0 | Temporary HOME and loopback port 51103; repeated `next-id` previews preserved task-file bytes/mtime, `assign-id` persisted sequence 1, root/legacy/API/named SSE returned the assigned identity, and a process restart retained it. No production port or live GitHub transport used. |

The W4 setuptools test's skip is not evidence that a built wheel contains the static assets. The configured package-data rules include the output paths, but actual package collection/serving remains unverified because the environment lacks setuptools and W4 forbids Python package installation.

## Acceptance and handoff

- **a1:** Stable server-issued numbering and local no-reload selection are covered by W1 persistence/frontend tests and the W4 local process/API/SSE smoke. Browser task-selection inspection itself was not available.
- **a2:** Fake-transport collector/render behavior is local evidence only. Authenticated live PR heads, check results, Greptile score and reviewed-commit parity remain unverified for the outer driver.
- **a3:** Offline repository projection, cache, sorting and null-state behavior are covered. Live `swcstudiospace` inventory/count/default-branch parity remains unverified.
- **a4:** Self-hosted Anybody, DM Sans and Space Mono WOFF2 plus actual OFL notices and Magic UI MIT / Aceternity adaptation attribution are included. Visual contrast, loaded-font rendering and at least three visible animations/reduced-motion behavior were not browser-verified because managed Chromium could not be installed.
- **a5:** W4 route/security/API/SSE tests passed. The broad dashboard selector did not reach execution due missing unrelated Python dependencies; no claim is made that it passed.
- **a6:** The local npm/build/test outputs are recorded; the signed openshell-podman tier and Anda-access receipt must be generated by the outer driver and remain unverified here.
- **a7:** No real-data screenshots were taken. The outer driver must capture Overview, Tasks, CI/CD and Repositories on the verified head.

No live GitHub requests, server restart on port 7421, export, push, PR creation, Greptile loop, merge or auto-merge occurred. No milestone audit, complete or cleanup ran. Phase 97's deferred upstream MCP contract work is unchanged.

## Commits

- W3 implementation and regenerated published-path frontend fix: `cc0b6fe` (the W3 summary and receipt are committed with this correction).
- W4 test fixture timing stabilization: `ed45552`.
- W5 guide and changelog: `3d1fadf`.

Phase-level roadmap/state/summary/receipt changes are committed separately by the integration owner after this evidence record.
