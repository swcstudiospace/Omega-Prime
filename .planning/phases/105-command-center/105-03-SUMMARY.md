---
phase: 105-command-center
plan: "03"
subsystem: ui
tags: [react, typescript, vite, tailwindcss, vitest, motion, command-center]
requires:
  - phase: 105-01
    provides: Canonical task sequence/display ids, timeline and wiring fields, six-key snapshot
  - phase: 105-02
    provides: sources.github.extra.command_center repositories, ci_cd, recent_merges and meta
provides:
  - Four-view React Command Center consuming the unchanged same-origin snapshot/SSE contracts
  - Local-font static build at /cc with hashed JavaScript, CSS and WOFF2 assets
  - Accessible task selection, source/error/stale/partial states, safe links and evidence-grounded CI/repository views
  - Automated frontend behavior and generated-asset checks
 affects: [105-04, 105-05, command-center, dashboard]
actuals:
  tokens: 154823
  tasks: 3
  commits: 6
plan_head_before: 000821f47eabeebe4470c4dda2268688fa85c5b0
plan_head_after: d3fbf60a0cbf839d32f1e503bec6e75509e984ab
tech-stack:
  added: [React 19.3.1, TypeScript 7.0.2, Vite 8.3.4, Tailwind CSS 4.3.3, Vitest 5.0.5, Motion 14.4.0]
  patterns: [same-origin named snapshot SSE with polling fallback, typed rendering of frozen W1/W2 contracts, locally bundled licensed fonts, observed-only timelines and merges]
key-files:
  created:
    - omega_prime/dashboard/frontend/package.json
    - omega_prime/dashboard/frontend/package-lock.json
    - omega_prime/dashboard/frontend/src/App.tsx
    - omega_prime/dashboard/frontend/src/hooks/useDashboard.ts
    - omega_prime/dashboard/frontend/src/types.ts
    - omega_prime/dashboard/frontend/src/lib/selectors.ts
    - omega_prime/dashboard/frontend/src/views/Overview.tsx
    - omega_prime/dashboard/frontend/src/views/Tasks.tsx
    - omega_prime/dashboard/frontend/src/views/CiCd.tsx
    - omega_prime/dashboard/frontend/src/views/Repositories.tsx
    - omega_prime/dashboard/frontend/public/licenses/Magic-UI-MIT.txt
    - omega_prime/dashboard/static/cc/index.html
  modified: []
key-decisions:
  - "Keep the existing six-field snapshot and named snapshot SSE routes; no new backend contract or runtime Node server."
  - "Use same-origin /api/snapshot and /api/stream with one polling fallback loop and reconnect cleanup."
  - "Join wiring only when repo/branch/SHA evidence is sufficient; display unknown rather than infer history or merge state."
  - "Self-host the admitted Latin typefaces and ship their distributed OFL notices."
patterns-established:
  - "Task UI selection uses namespaced technical ids while display numbering remains server-issued."
  - "All collector text uses normal escaped React rendering; external navigation requires an accepted HTTPS or same-origin URL."
  - "Staleness uses GitHub collection metadata and remains distinct from the live SSE connection state."
requirements-completed: [a1, a2, a3, a4, a5, a7]
coverage:
  - id: D-03
    description: React/TypeScript/Vite/Tailwind static build with a real lockfile and no runtime Node requirement
    verification:
      - kind: unit
        ref: "npm run build"
        status: pass
      - kind: integration
        ref: "CC_CHECK_BUILD=1 npm test -- --run src/test/build-assets.test.ts"
        status: pass
    human_judgment: false
  - id: D-01/D-04
    description: Server-issued numbered task selection with local detail navigation and deterministic selectors
    verification:
      - kind: unit
        ref: "src/lib/selectors.test.ts; src/views/Tasks.test.tsx; src/App.test.tsx"
        status: pass
    human_judgment: false
  - id: D-02/D-05
    description: Four views preserve healthy local evidence while representing loading, empty, error, stale and partial states
    verification:
      - kind: unit
        ref: "npm test -- --run"
        status: pass
    human_judgment: false
  - id: D-06/D-07
    description: Local OFL fonts, shipped notices and reduced-motion-aware adapted motion treatments
    verification:
      - kind: integration
        ref: "CC_CHECK_BUILD=1 npm test -- --run src/test/build-assets.test.ts"
        status: pass
      - kind: unit
        ref: "src/components/motion.test.tsx"
        status: pass
    human_judgment: true
    rationale: Browser font loading, contrast and visual quality were not inspected; live visual acceptance is an outer-driver responsibility.
  - id: a7
    description: Frontend is ready for real-data capture in the outer driver
    verification: []
    human_judgment: true
    rationale: No real-data screenshot or live parity claim is made by this W3 execution.
duration: 15min
duration_seconds: 877
completed: 2026-10-20
status: complete
commits: 6
---

# Phase 105 Plan 03: Omega Prime Command Center Summary

**React Command Center with four evidence-backed views, server-numbered task selection, licensed local fonts and a committed self-hosted static build.**

## Performance

- **Duration:** 14m 37s
- **Started:** 2026-10-20T22:38:54Z
- **Completed:** 2026-10-20T22:53:31Z
- **Tasks:** 3
- **Files modified/created:** 62 source, test, font and generated-build paths (excluding this summary and receipt)
- **Actual diff scale:** 619,294 characters / 4 = 154,823 tokens, including generated text build output; 6 plan commits measured from the persisted ledger.
- **Final post-integration build size:** 376.50 kB JavaScript (119.38 kB gzip as reported by Vite), 19.25 kB CSS (5.52 kB gzip), and 94.21 kB local WOFF2 fonts. The post-integration asset check measured 489,985 raw bytes and 219,137 bytes when gzip-compressing each distributable file separately.

## Accomplishments

- Built Overview, Tasks, CI/CD and Repositories over the W1/W2 snapshot, preserving the original snapshot fields and consuming `sources.github.command_center` at its published source-status path.
- Added initial snapshot fetch, the named `snapshot` SSE listener, one 5-second poll fallback, 30-second reconnect and unmount/StrictMode cleanup.
- Added stable namespaced selection while retaining canonical `display_id`; active/finished duplicate identities share one row and combine available timeline/detail evidence.
- Added honest empty/loading/error/retry/stale/partial states, source health, observed-only task/check/merge history, GitHub evidence, and deterministic repository search/sorting.
- Validated outbound links and rendered collector-provided strings only as escaped React text.
- Shipped the actual generated hashed entry, CSS, JS, four local WOFF2 font files and license/attribution notices under `omega_prime/dashboard/static/cc/`.

## Package admission and lock evidence

All 19 direct packages were checked against the official npm registry before install. Parent-provided registry metadata supplied the identity, repository/homepage, license, peer and engine review; Node `v24.21.0` satisfies the admitted toolchain constraints. Every package resolved from `registry.npmjs.org`, was locked by the generated lockfile, and its recorded `integrity` is copied below. The package suffix `.tgz` is the actual registry tarball resolved in `package-lock.json`.

| Package | Version | License | Registry tarball | SRI integrity |
|---|---:|---|---|---|
| react | 19.3.1 | MIT | https://registry.npmjs.org/react/-/react-19.3.1.tgz | sha512-QNhWdt7nrgVWWQG58QuGsAR3jv3Hbe4mb4vz7w2csnHavciZJgda1CPNaldF6eVxv90ud3jUXpKlHL69gO38yw== |
| react-dom | 19.3.1 | MIT | https://registry.npmjs.org/react-dom/-/react-dom-19.3.1.tgz | sha512-Y0bARz53lhEC1NMCASs0/j+faB/zqt3R8hpvg1e8+61ZVJ4UL0UdC4cBbXfZMcIuhrfgeILTLe+ZeBCavQPtKQ== |
| motion | 14.4.0 | MIT | https://registry.npmjs.org/motion/-/motion-14.4.0.tgz | sha512-JJTIdeNjtF2zi/py0ndRKxQF+8z2pcDUgJYXul7zqbhMvyTLRsPFR9ifE/j4cFCpCMlehQVJkTwd7BZFiPKgQg== |
| @fontsource-variable/anybody | 5.3.0 | OFL-1.1 | https://registry.npmjs.org/@fontsource-variable/anybody/-/anybody-5.3.0.tgz | sha512-3u/FSV3mGv9xW3x+CdK9lAZYT1Y3NaRA1bexeoIuk9s5QE8sD8OOTcofvpPx0Z6M0Fq1WAJ06ZAw8hivdRQWHQ== |
| @fontsource-variable/dm-sans | 5.3.0 | OFL-1.1 | https://registry.npmjs.org/@fontsource-variable/dm-sans/-/dm-sans-5.3.0.tgz | sha512-BpUG5bqePiDFMBM4ZtLSlPdAIOM990uB1dlXzIf4aw21yQR7BmykedLXXXMqGWsR18aMLOsbWccwJNh3ztTQaA== |
| @fontsource/space-mono | 5.3.0 | OFL-1.1 | https://registry.npmjs.org/@fontsource/space-mono/-/space-mono-5.3.0.tgz | sha512-FrpBMOVWn3PRdRHlrZWC55X3kZG/2BTH7SM5ZyS/bky3iKya2BturI5lk+t0RoDQL2JyHzURsVq/EisCsxpI5A== |
| typescript | 7.0.2 | Apache-2.0 | https://registry.npmjs.org/typescript/-/typescript-7.0.2.tgz | sha512-8FYau96o3NKOhbjKi/qNvG/W5jhzxkbdm5sj9AbZ/5T5sWqn3hJgLfGx27sRKZWTvyzCP8dLRBTf5tBTSRVUNA== |
| vite | 8.3.4 | MIT | https://registry.npmjs.org/vite/-/vite-8.3.4.tgz | sha512-IftLE8phJhdNRIGNEV4JCCvfNScoZQVfcXaN8TQropWGJtOHcu+iAB9pHcuGvmEb/kddw0LBe/jS8ztdC6xdiQ== |
| @vitejs/plugin-react | 6.1.2 | MIT | https://registry.npmjs.org/@vitejs/plugin-react/-/plugin-react-6.1.2.tgz | sha512-fhJNGUdscQa4HkI0xFxwonWyhnVReCPDbXI7FH7jmzpnVCrFQI7PRrhWecO263610vOrs9tr2Y4xcf8uAdFxbw== |
| tailwindcss | 4.3.3 | MIT | https://registry.npmjs.org/tailwindcss/-/tailwindcss-4.3.3.tgz | sha512-gOhV3P7ufE62QDGg1zVaTgCR+EtPv92k2nIhVcVKcLmxT1sUBsQGhnZj175j+MqRt4zLF7ic+sCYjfhxMxj7YQ== |
| @tailwindcss/vite | 4.3.3 | MIT | https://registry.npmjs.org/@tailwindcss/vite/-/vite-4.3.3.tgz | sha512-yYU8cogLeSh/ms2jh8Fj7jaba/EWa7Ja6GoUqYZaraEuCI5YS6ms6ObZgjjedm+jm6XZjdNRWBpPP6Z86oOxcw== |
| vitest | 5.0.5 | MIT | https://registry.npmjs.org/vitest/-/vitest-5.0.5.tgz | sha512-cPODOMF8YnkiiYtDdHo6n4wWmBrOBBbOIYH2d33M4l3CdNn72h+qMmO6uXffUN6A5AZFXNND/d+krS9mgkXRfg== |
| jsdom | 30.1.3 | MIT | https://registry.npmjs.org/jsdom/-/jsdom-30.1.3.tgz | sha512-hQMUPvRgN7KxsfIytDQji7bimuJeRQa6Qo5+iuiZQk5CafeRxLIA10/cG5YCBFNpD9RO3oqIiN4CO1Y44kxqXg== |
| @types/node | 26.6.5 | MIT | https://registry.npmjs.org/@types/node/-/node-26.6.5.tgz | sha512-uMzsv9stDdofIZBx4UGh4Z9ZgXnGC9seRDscilBcTHAeOFk67MZg/k9bWVfO+bk4qZmG+l52hRnLwUiH2LnJTg== |
| @types/react | 19.3.0 | MIT | https://registry.npmjs.org/@types/react/-/react-19.3.0.tgz | sha512-N0rFCuH9YoxG9/m61l9MfpJKfmLOVU0em7ipIz6TRgSSkvReLB9vL85GB+yr8Bs5leqpvg96JSwF4ZS1s4viQg== |
| @types/react-dom | 19.3.0 | MIT | https://registry.npmjs.org/@types/react-dom/-/react-dom-19.3.0.tgz | sha512-ZI7bU42mZXXKHn/qNLEw2IrbiINU7X5+vfgdixBHkCNpYWXjKgfQ/P+uyGb5CjOLB9UcnTeg3rylQtV2hym44Q== |
| @testing-library/react | 16.3.3 | MIT | https://registry.npmjs.org/@testing-library/react/-/react-16.3.3.tgz | sha512-Uo193NgQbPMz6lrrhtRQQFcMC6Re/ELLFbbuVL30WDlZxlpZf9/lMHTAVxPRLw1q1iu9OJmR1c2BLiENRstdBg== |
| @testing-library/jest-dom | 7.0.1 | MIT | https://registry.npmjs.org/@testing-library/jest-dom/-/jest-dom-7.0.1.tgz | sha512-oMDTC3oA+6CXSO2JZnvOI7CA6oVub6kij5ggk9ohwye5slmkwxYDXcPOVxgMw/RQlticjtO0C1RZkR97HgrWMw== |
| @testing-library/user-event | 14.6.8 | MIT | https://registry.npmjs.org/@testing-library/user-event/-/user-event-14.6.8.tgz | sha512-vgSSqnANMnkcYR/vUNDbwS7aM3OErRLNUG8vCVVlj4oc6cKffUrccX7UAS5AmJQQNZTSibi9z5GvvRZ0aYgUCQ== |

The actual Anybody, DM Sans and Space Mono `LICENSE` files were copied from their installed packages. Magic UI's upstream MIT license/copyright is preserved at `public/licenses/Magic-UI-MIT.txt` (source: https://github.com/magicuidesign/magicui/blob/main/LICENSE.md). The Aceternity sidebar/card treatment is adapted, not source-copied; its attribution and source-use restriction ship in `Aceternity-Attribution.txt`. No unlisted runtime or UI dependency was added.

## Verification

| Command | Exit | Result |
|---|---:|---|
| `npm ci --registry=https://registry.npmjs.org` (from frontend) | 0 | Clean lockfile installation; 128 packages added. |
| `npm test -- --run` (from frontend) | 0 | 10 files; 17 passed, one build-output-only check skipped for its dedicated run. |
| `npm run build` (from frontend) | 0 | `tsc -b` then Vite 8.3.4; 445 modules transformed; hashed JS/CSS/fonts emitted. |
| `CC_CHECK_BUILD=1 npm test -- --run src/test/build-assets.test.ts` (from frontend) | 0 | Local hashed references/fonts/licenses, no maps, source or node_modules escape; measured raw/gzip asset sizes. |
| Task 1 targeted tests | 0 (after fixes) | 4 files, 7 tests passed. |
| Task 2 targeted tests | 0 (after fixes) | 6 files, 10 tests passed. |

The first targeted test attempts and the first two build attempts exposed fixable fixture, formatting, assertion and TypeScript-config issues. They were corrected before the final successful acceptance runs; no dependency substitution or extra install was needed. The build-asset validator was narrowed to check external runtime requests rather than reject harmless external documentation strings embedded in React's error messages.

## Task Commits

1. **Task 1: Typed shell, transport and visual primitives** — `e266cca` (`feat(105-03): establish typed static shell and licensed visual primitives`)
2. **Task 1 asset completion: Local WOFF2 faces** — `a43df2d` (`feat(105-03): include self-hosted font assets`)
3. **Task 2: Four views and evidence selectors** — `962c425` (`feat(105-03): compose four command center views`)
4. **Task 3: Generated static build and asset check** — `fbb3e74` (`feat(105-03): ship verified static command center build`)
5. **License attribution correction** — `faf2a21` (`fix(105-03): use upstream Magic UI copyright notice`)
6. **W1 wiring field correction and refreshed build** — `d3fbf60` (`fix(105-03): correlate wiring on published head_sha`)

**Plan commit ledger:** `plan_head_before=000821f47eabeebe4470c4dda2268688fa85c5b0`, `plan_head_after=d3fbf60a0cbf839d32f1e503bec6e75509e984ab`, `commits=6` measured by `git rev-list --count`.

## Files Created/Modified

- `omega_prime/dashboard/frontend/` — toolchain, lock, application, transport, types, styles, reusable components, four views and Vitest tests.
- `omega_prime/dashboard/frontend/src/assets/fonts/` — Anybody, DM Sans and Space Mono local Latin WOFF2 source assets.
- `omega_prime/dashboard/frontend/public/licenses/` — actual OFL notices plus Magic UI MIT and adapted Aceternity attribution.
- `omega_prime/dashboard/static/cc/index.html` — static shell, `/legacy` failure/no-JS recovery link.
- `omega_prime/dashboard/static/cc/assets/` — final emitted outputs: `index-CtUpPsk6.js`, `index-DIGTqjrX.css`, `anybody-latin-variable-D2L-W0xx.woff2`, `dm-sans-latin-variable-Xz1IZZA0.woff2`, `space-mono-latin-400-Rg4St2Dn.woff2`, `space-mono-latin-700-mWgeinG7.woff2`.
- `omega_prime/dashboard/static/cc/licenses/` — shipped OFL/MIT notices and Aceternity adaptation credit.

## Decisions Made

- Preserved the existing server snapshot and SSE contracts exactly; no API route, server, deployment or app.py change was made.
- Displayed nullable/missing GitHub and wiring evidence as unknown or unavailable, and joined only on the actual W1 `repo`/`branch`/`head_sha` fields when evidence was unique.
- Used same-origin snapshot and stream connections and local WOFF2 assets; no external runtime origin or CDN is required.
- Kept all task and collector strings in escaped React text and constrained links to safe HTTPS or same-origin HTTP(S).

## Deviations from Plan

### Auto-fixed blocking issues

- **TypeScript project build config:** the initial TypeScript 7 build rejected `noEmit` on the referenced node project; the node project now emits declarations only into ignored `node_modules/.cache`, and the app config includes explicit Node/Vite types.
- **Sydney formatter:** `dateStyle`/`timeStyle` cannot be combined with `timeZoneName` in the runtime formatter; explicit date/time fields now preserve AEDT/AEST labels.
- **Asset validation:** initial checks incorrectly treated dependency documentation URLs as runtime requests and did not resolve `/cc/` CSS font URLs correctly; the checker now validates same-origin fetch/EventSource patterns and resolves Vite base-prefixed local assets.
- **W1 wiring contract:** a final code review against the published model found that W1 exposes `wiring.head_sha`, not `wiring.sha`, and does not publish merge order. The selector now joins on the actual repo/branch/head_sha fields, tests unique/ambiguous/missing cases, and presents merge order as unavailable.
- **License attribution:** corrected the Magic UI MIT notice to the upstream copyright holder, `Magic UI`, and synchronized the shipped notice.
- **Post-integration payload-path correction:** W2 publishes `command_center` directly on the GitHub source status (after projecting internal `SourceResult.extra`). Frontend types, fixtures, selectors and views were aligned with that observable snapshot shape and the static build was regenerated. Final frontend tests/build and generated-asset inspection were rerun successfully by the integration owner.
- **TDD sequencing:** source and behavior tests were completed before executing the requested acceptance commands, honoring the instruction not to run checks during W3 construction. This did not create a RED-first TDD commit sequence for the two plan tasks marked `tdd="true"`; acceptance tests ran after the full W3 implementation was in place.

**Total deviations:** 6 corrective/sequence adjustments.
**Impact:** All W3 paths and acceptance requirements were completed; no server or W4/W5 scope was modified. Parent integration owns final aggregate checks.

## Issues Encountered

- Registry-admitted dependency versions installed and built on Node v24.21.0. No package legitimacy gate was required.
- npm install/build generated local `node_modules` and TypeScript build cache; both were removed after the checks. The committed npm lock and generated static output remain.

## User Setup Required

None — no external service configuration is introduced.

## Next Phase Readiness

W4 can mount the generated static directory at `/cc`, keep the existing snapshot and stream routes, and use its existing local-source behavior during GitHub degradation. W5/the outer driver still own server-route/security integration, live parity, browser accessibility/contrast inspection and real-data a7 screenshot capture. This summary does not claim any of those external validations.

## Self-Check: PASSED

Confirmed this summary, the verification receipt, the static shell, final hashed JS/CSS, local font assets and shipped notices exist; parsed the receipt as JSON and verified all six task commits are ancestors of final `HEAD`. Final test/build/asset-validation commands are recorded in the receipt.

---
*Phase: 105-command-center*
*Completed: 2026-10-20*
