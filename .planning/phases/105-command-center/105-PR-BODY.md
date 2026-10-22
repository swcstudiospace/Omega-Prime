# Draft PR handoff — Phase 105 planning only

No remote is configured for this run. This is the body for a future **draft**, stacked on unmerged Task Dashboard PR #47; no push, PR creation, merge or auto-merge is performed here.

## Plan

Planning deliverables: roadmap entry, locked context, verbatim uplift, file-disjoint W1–W5 prompts, independent plan check and receipt. The checklist below preserves the supplied second-uplift graph; unchecked implementation/export/ship steps remain for Luna or the outer driver. Supplied n1–n5 GoT results are input evidence, not a graph rerun.

### n1 — [n1] Map the current dashboard and the sandbox run path

- [ ] n1 step 1 — [n1] Step 1: Read omega_prime/dashboard (app.py, model.py, poller.py, collectors/*, static/*) on feat/task-dashboard 231495d and record the JSON snapshot and SSE shapes the new UI must consume.
- [ ] n1 step 2 — [n1] Step 2: Record PR #47's security guarantees as tests to keep: loopback-only bind, 405 for non-GET, Host check, CSP and security headers, redaction of every payload, no innerHTML with untrusted data, token never sent to the browser.
- [ ] n1 step 3 — [n1] Step 3: Map the github collector (single repo swcstudiospace/omes-bot, broker or env PAT, 60 s cache) and what CI/CD and Repositories need on top: check runs, org repo list, coding-fleet PRs.
- [ ] n1 step 4 — [n1] Step 4: Map the omp-sandbox path: Anda-signed client to TS core to openshell-podman tier; workdir /sandbox/work; today only fixture seeding exists, so a repo seed (git bundle over exec stdin) and a commit export are needed.
- [ ] n1 step 5 — [n1] Step 5: Confirm sandbox egress: chatgpt.com, auth.openai.com, api.z.ai for bun (omp) and registry.npmjs.org for node; no PyPI, so Python test tooling must be staged from the host.
### n2 — [n2] Decompose into data model, API, shell, views, design and pipeline

- [ ] n2 step 1 — [n2] Step 1: Task numbering unit: a seq/num field and next_seq counter in tasks.json, back-fill in current order from 00001, never reuse, plus a CLI helper and a read-only API field for display_id.
- [ ] n2 step 2 — [n2] Step 2: API unit: extend the github collector to the org (repos list, open PRs for omes-bot and coding-fleet, check runs per head SHA, Greptile score with reviewed commit), cached with an age badge and rate-limit handling.
- [ ] n2 step 3 — [n2] Step 3: Frontend shell unit: a Vite + React + TypeScript + Tailwind app under omega_prime/dashboard/frontend, built to omega_prime/dashboard/static/cc with hashed assets, served by the existing Python server.
- [ ] n2 step 4 — [n2] Step 4: Views unit: Overview, Tasks (sidebar list plus detail with timeline), CI/CD, Repositories, each with loading, empty, error, stale and partial states.
- [ ] n2 step 5 — [n2] Step 5: Design unit: white and purple tokens, self-hosted Greptile typefaces, Magic UI and Aceternity components adapted as local sources with reduced-motion handling.
- [ ] n2 step 6 — [n2] Step 6: Pipeline unit: sandbox seed, npm install and build, pytest and the frontend tests inside the sandbox, then export commits to the host.
### n3 — [n3] Design system: Greptile font, white/purple tokens, Magic UI + Aceternity picks

- [ ] n3 step 1 — [n3] Step 1: Inspect greptile.com's live CSS: @font-face rules declare Anybody (h1 and body default), Space Mono (most UI copy, .font-mono) and DM Sans (.font-sans), loaded through next/font.
- [ ] n3 step 2 — [n3] Step 2: All three are Google Fonts under the SIL Open Font License 1.1, so self-host them from the @fontsource packages (woff2 plus licence files) bundled by Vite.
- [ ] n3 step 3 — [n3] Step 3: Define tokens: white and near-white surfaces, purple primary (violet 600/700 family) with gradients, neutral grey text, all checked for WCAG AA contrast.
- [ ] n3 step 4 — [n3] Step 4: Pick Magic UI (MIT): Number Ticker, Blur Fade, Border Beam and Animated Shiny Text (shimmer) via 21st.dev ids 1282 and 1079 plus the magicui.design registry.
- [ ] n3 step 5 — [n3] Step 5: Pick Aceternity: Sidebar (animated, expandable) and Card Hover Effect from the ui.aceternity.com registry; skip Card Spotlight because it pulls three.js.
- [ ] n3 step 6 — [n3] Step 6: Wrap every animation in a prefers-reduced-motion check (motion's useReducedMotion plus a CSS media query) and never gate data on animation completion.
### n4 — [n4] Front-end build options under the CSP and no-runtime-Node rule

- [ ] n4 step 1 — [n4] Step 1: Option A, React + Vite static build committed to the package: no Node at runtime, hashed JS and CSS, fonts as local assets; fits script-src 'self'.
- [ ] n4 step 2 — [n4] Step 2: Option B, Preact or vanilla with hand-ported animations: smaller, but loses Magic UI and Aceternity fidelity, which the ask names explicitly.
- [ ] n4 step 3 — [n4] Step 3: Option C, a runtime Node server: rejected by the platform constraint.
- [ ] n4 step 4 — [n4] Step 4: CSP check: Vite output needs no inline scripts; motion uses inline style attributes, so style-src needs 'self' plus 'unsafe-inline' for style only, or the CSP keeps style-src 'self' if only CSS classes are used; scripts stay 'self'.
- [ ] n4 step 5 — [n4] Step 5: Packaging: built files under omega_prime/dashboard/static/cc, served by the existing static handler; the legacy page stays at /legacy as the no-JS fallback.
### n5 — [n5] Security, licence and performance risks

- [ ] n5 step 1 — [n5] Step 1: Licence: the fonts are OFL (fine to self-host with OFL.txt); Magic UI is MIT; the Aceternity licence allows use in end products but forbids redistributing its source files, so adapt the components into the app with attribution and keep no verbatim registry dump in the repo.
- [ ] n5 step 2 — [n5] Step 2: Redaction: every new field (repo names, check names, job URLs, PR titles, task notes) must go through redact_value before it reaches the snapshot.
- [ ] n5 step 3 — [n5] Step 3: Rate limits: org repo listing and check runs are N+1 calls; cache for 60-120 s, cap pages, and degrade to cached data with an age badge on 403 or 429.
- [ ] n5 step 4 — [n5] Step 4: Bundle size and animation cost: keep the bundle under about 400 KB gzip, lazy-free; animations use transform and opacity only and stop under reduced motion.
- [ ] n5 step 5 — [n5] Step 5: Sandbox risk: Python tests need pytest staged from the host; the build writes only inside the sandbox workdir and commits leave only via the export path.
### n6 — [n6] File-disjoint WORKFLOW for Sol plan + Luna build in the sandbox

- [ ] n6 step 1 — [n6] Step 1: W1 (Python): task numbering in status_file.py and a new omega_prime/dashboard/task_ids.py plus CLI `python -m omega_prime.dashboard next-id`, with tests.
- [ ] n6 step 2 — [n6] Step 2: W2 (Python): github collector extension (org repos, PR checks, Greptile commit) and model/app API fields, with redaction and rate-limit tests.
- [ ] n6 step 3 — [n6] Step 3: W3 (frontend): Vite/React/Tailwind app in omega_prime/dashboard/frontend with the shell, four views, design tokens, fonts and components; build output committed to static/cc.
- [ ] n6 step 4 — [n6] Step 4: W4 (server): serve /cc assets as the default page, keep /legacy, tighten the CSP, and keep all PR #47 security tests green.
- [ ] n6 step 5 — [n6] Step 5: W5 (verify): pytest for the dashboard, frontend unit tests and build inside the sandbox; export commits; the host pushes feat/command-center and opens a draft PR stacked on #47.
- [ ] n6 step 6 — [n6] Step 6: W6 (ship): Greptile loop up to 5 rounds, restart the dashboard on 7421 from the verified head, and capture four screenshots.

### Execution plan checklist

- [ ] 105-01 / W1 — numbering, atomic allocation, CLI and numbered snapshots
- [ ] 105-02 / W2 — read-only cached org/PR/check/score/recent-merge data
- [ ] 105-03 / W3 — four-view React app, local fonts, adapted motion, Vitest and committed build
- [ ] 105-04 / W4 — static cutover, legacy fallback, CSP/cache/MIME/security/package tests
- [ ] 105-05 / W5 — docs, attributions and final offline checks

## Trackers

- Task: [Notion](https://www.notion.so/3ffbc1a0c7ae8180a8f2d55515e8ac61)
- n1: [SPE-18154](https://linear.app/swcstudio/issue/SPE-18154/n1-map-the-current-dashboard-and-the-sandbox-run-path) · [Notion](https://www.notion.so/3ffbc1a0c7ae8157a8b3c8f9d3752316)
- n1 step 1: [SPE-18160](https://linear.app/swcstudio/issue/SPE-18160/n1-step-1-read-omega-primedashboard-apppy-modelpy-pollerpy-collectors) · [Notion](https://www.notion.so/3ffbc1a0c7ae818cb76ef2680ecd7c47)
- n1 step 2: [SPE-18161](https://linear.app/swcstudio/issue/SPE-18161/n1-step-2-record-pr-47s-security-guarantees-as-tests-to-keep-loopback) · [Notion](https://www.notion.so/3ffbc1a0c7ae810e806aef4d758e3079)
- n1 step 3: [SPE-18162](https://linear.app/swcstudio/issue/SPE-18162/n1-step-3-map-the-github-collector-single-repo-swcstudiospaceomes-bot) · [Notion](https://www.notion.so/3ffbc1a0c7ae817fb2d0e9be225e9202)
- n1 step 4: [SPE-18163](https://linear.app/swcstudio/issue/SPE-18163/n1-step-4-map-the-omp-sandbox-path-anda-signed-client-to-ts-core-to) · [Notion](https://www.notion.so/3ffbc1a0c7ae814b9193ffad8c3e0a72)
- n1 step 5: [SPE-18164](https://linear.app/swcstudio/issue/SPE-18164/n1-step-5-confirm-sandbox-egress-chatgptcom-authopenaicom-apizai-for) · [Notion](https://www.notion.so/3ffbc1a0c7ae81e5ba87c996c5ca0fba)
- n2: [SPE-18155](https://linear.app/swcstudio/issue/SPE-18155/n2-decompose-into-data-model-api-shell-views-design-and-pipeline) · [Notion](https://www.notion.so/3ffbc1a0c7ae81a1884edfa5918f683b)
- n2 step 1: [SPE-18165](https://linear.app/swcstudio/issue/SPE-18165/n2-step-1-task-numbering-unit-a-seqnum-field-and-next-seq-counter-in) · [Notion](https://www.notion.so/3ffbc1a0c7ae81acaf25edd3b6d86c58)
- n2 step 2: [SPE-18166](https://linear.app/swcstudio/issue/SPE-18166/n2-step-2-api-unit-extend-the-github-collector-to-the-org-repos-list) · [Notion](https://www.notion.so/3ffbc1a0c7ae81898751c2cd9a0babde)
- n2 step 3: [SPE-18167](https://linear.app/swcstudio/issue/SPE-18167/n2-step-3-frontend-shell-unit-a-vite-react-typescript-tailwind-app) · [Notion](https://www.notion.so/3ffbc1a0c7ae8123bf26d25bd24e211f)
- n2 step 4: [SPE-18168](https://linear.app/swcstudio/issue/SPE-18168/n2-step-4-views-unit-overview-tasks-sidebar-list-plus-detail-with) · [Notion](https://www.notion.so/3ffbc1a0c7ae818b8317d43c1fa86eed)
- n2 step 5: [SPE-18169](https://linear.app/swcstudio/issue/SPE-18169/n2-step-5-design-unit-white-and-purple-tokens-self-hosted-greptile) · [Notion](https://www.notion.so/3ffbc1a0c7ae816ca0a5e14b93796c5c)
- n2 step 6: [SPE-18170](https://linear.app/swcstudio/issue/SPE-18170/n2-step-6-pipeline-unit-sandbox-seed-npm-install-and-build-pytest-and) · [Notion](https://www.notion.so/3ffbc1a0c7ae811eaecae7275672ac4f)
- n3: [SPE-18156](https://linear.app/swcstudio/issue/SPE-18156/n3-design-system-greptile-font-whitepurple-tokens-magic-ui-aceternity) · [Notion](https://www.notion.so/3ffbc1a0c7ae811691c9f9540b3bddc3)
- n3 step 1: [SPE-18171](https://linear.app/swcstudio/issue/SPE-18171/n3-step-1-inspect-greptilecoms-live-css-font-face-rules-declare) · [Notion](https://www.notion.so/3ffbc1a0c7ae8169b2abca2457906f53)
- n3 step 2: [SPE-18172](https://linear.app/swcstudio/issue/SPE-18172/n3-step-2-all-three-are-google-fonts-under-the-sil-open-font-license) · [Notion](https://www.notion.so/3ffbc1a0c7ae81abbf32c7ac30280a77)
- n3 step 3: [SPE-18173](https://linear.app/swcstudio/issue/SPE-18173/n3-step-3-define-tokens-white-and-near-white-surfaces-purple-primary) · [Notion](https://www.notion.so/3ffbc1a0c7ae817a95c1ec5bfc304990)
- n3 step 4: [SPE-18174](https://linear.app/swcstudio/issue/SPE-18174/n3-step-4-pick-magic-ui-mit-number-ticker-blur-fade-border-beam-and) · [Notion](https://www.notion.so/3ffbc1a0c7ae81c0ac1ff7c6d8deaf1b)
- n3 step 5: [SPE-18175](https://linear.app/swcstudio/issue/SPE-18175/n3-step-5-pick-aceternity-sidebar-animated-expandable-and-card-hover) · [Notion](https://www.notion.so/3ffbc1a0c7ae81488836d01eab6db65d)
- n3 step 6: [SPE-18176](https://linear.app/swcstudio/issue/SPE-18176/n3-step-6-wrap-every-animation-in-a-prefers-reduced-motion-check) · [Notion](https://www.notion.so/3ffbc1a0c7ae81d2b8aed0920b0bf625)
- n4: [SPE-18157](https://linear.app/swcstudio/issue/SPE-18157/n4-front-end-build-options-under-the-csp-and-no-runtime-node-rule) · [Notion](https://www.notion.so/3ffbc1a0c7ae81789470eea3c8efc6f8)
- n4 step 1: [SPE-18177](https://linear.app/swcstudio/issue/SPE-18177/n4-step-1-option-a-react-vite-static-build-committed-to-the-package-no) · [Notion](https://www.notion.so/3ffbc1a0c7ae81e291f5dad0f9ead97c)
- n4 step 2: [SPE-18178](https://linear.app/swcstudio/issue/SPE-18178/n4-step-2-option-b-preact-or-vanilla-with-hand-ported-animations) · [Notion](https://www.notion.so/3ffbc1a0c7ae81e895f2c6543fcaa949)
- n4 step 3: [SPE-18179](https://linear.app/swcstudio/issue/SPE-18179/n4-step-3-option-c-a-runtime-node-server-rejected-by-the-platform) · [Notion](https://www.notion.so/3ffbc1a0c7ae8145bc87fe53e6f5d3cd)
- n4 step 4: [SPE-18180](https://linear.app/swcstudio/issue/SPE-18180/n4-step-4-csp-check-vite-output-needs-no-inline-scripts-motion-uses) · [Notion](https://www.notion.so/3ffbc1a0c7ae81dfaeaae4797362d2e5)
- n4 step 5: [SPE-18181](https://linear.app/swcstudio/issue/SPE-18181/n4-step-5-packaging-built-files-under-omega-primedashboardstaticcc) · [Notion](https://www.notion.so/3ffbc1a0c7ae81acab57d2eff7f23f5a)
- n5: [SPE-18158](https://linear.app/swcstudio/issue/SPE-18158/n5-security-licence-and-performance-risks) · [Notion](https://www.notion.so/3ffbc1a0c7ae81748395cdd299475b4d)
- n5 step 1: [SPE-18182](https://linear.app/swcstudio/issue/SPE-18182/n5-step-1-licence-the-fonts-are-ofl-fine-to-self-host-with-ofltxt) · [Notion](https://www.notion.so/3ffbc1a0c7ae8108a566c8f507cd4045)
- n5 step 2: [SPE-18183](https://linear.app/swcstudio/issue/SPE-18183/n5-step-2-redaction-every-new-field-repo-names-check-names-job-urls-pr) · [Notion](https://www.notion.so/3ffbc1a0c7ae819ba193f67cdf64189f)
- n5 step 3: [SPE-18184](https://linear.app/swcstudio/issue/SPE-18184/n5-step-3-rate-limits-org-repo-listing-and-check-runs-are-n1-calls) · [Notion](https://www.notion.so/3ffbc1a0c7ae81cdbb93c67b152c129c)
- n5 step 4: [SPE-18185](https://linear.app/swcstudio/issue/SPE-18185/n5-step-4-bundle-size-and-animation-cost-keep-the-bundle-under-about) · [Notion](https://www.notion.so/3ffbc1a0c7ae816f8e0ad5cd74e86dda)
- n5 step 5: [SPE-18186](https://linear.app/swcstudio/issue/SPE-18186/n5-step-5-sandbox-risk-python-tests-need-pytest-staged-from-the-host) · [Notion](https://www.notion.so/3ffbc1a0c7ae817b8c4dcfddf22afe15)
- n6: [SPE-18159](https://linear.app/swcstudio/issue/SPE-18159/n6-file-disjoint-workflow-for-sol-plan-luna-build-in-the-sandbox) · [Notion](https://www.notion.so/3ffbc1a0c7ae8175813fca4d0342ffba)
- n6 step 1: [SPE-18187](https://linear.app/swcstudio/issue/SPE-18187/n6-step-1-w1-python-task-numbering-in-status-filepy-and-a-new) · [Notion](https://www.notion.so/3ffbc1a0c7ae810f8c4be8cb848e216e)
- n6 step 2: [SPE-18188](https://linear.app/swcstudio/issue/SPE-18188/n6-step-2-w2-python-github-collector-extension-org-repos-pr-checks) · [Notion](https://www.notion.so/3ffbc1a0c7ae81819e64cd220493aa6f)
- n6 step 3: [SPE-18189](https://linear.app/swcstudio/issue/SPE-18189/n6-step-3-w3-frontend-vitereacttailwind-app-in-omega) · [Notion](https://www.notion.so/3ffbc1a0c7ae81c1a520e8d9b4a5d9be)
- n6 step 4: [SPE-18190](https://linear.app/swcstudio/issue/SPE-18190/n6-step-4-w4-server-serve-cc-assets-as-the-default-page-keep-legacy) · [Notion](https://www.notion.so/3ffbc1a0c7ae81c0bce3eee81ea4d746)
- n6 step 5: [SPE-18191](https://linear.app/swcstudio/issue/SPE-18191/n6-step-5-w5-verify-pytest-for-the-dashboard-frontend-unit-tests-and) · [Notion](https://www.notion.so/3ffbc1a0c7ae81c6b433c27205866c76)
- n6 step 6: [SPE-18192](https://linear.app/swcstudio/issue/SPE-18192/n6-step-6-w6-ship-greptile-loop-up-to-5-rounds-restart-the-dashboard) · [Notion](https://www.notion.so/3ffbc1a0c7ae81c2a93ff0fed67cca1b)

Tracker gap: TASK supplies a Notion URL and graphId, but no Linear TASK identifier; none invented. All ISSUE/SUBISSUE identifiers and URLs above are copied from the prompt.

## Receipt

`.receipts/bot-00-omega-prime/105-command-center-planning.json`

## Verification boundary

Planning baseline: 90 dashboard tests pass via the targeted dashboard file glob; 31 docs/public-repo tests pass. The mandated broad pytest command exits 2 on unrelated missing-dependency collection before `-k` can filter (including mcp and apscheduler). Plans retain both commands and record this baseline; no dependencies installed or unrelated tests changed. No frontend exists yet, so npm ci/test/build are execution commands, not observed planning results.

## Unverified

- rule adherence by future agents
- Product implementation and its new tests/build: planning-only stage
- Broad pytest gate: unrelated missing staged Python dependencies prevent collection
- a2/a3 live GitHub parity: outer driver; fake transports alone cannot establish it
- a6 sandbox openshell-podman/Anda attestation: outer driver
- a7 Overview/Tasks/CI-CD/Repositories real-data screenshots: outer driver
- Export/push/draft PR, Greptile loop and verified-head restart: outer driver; never merge
