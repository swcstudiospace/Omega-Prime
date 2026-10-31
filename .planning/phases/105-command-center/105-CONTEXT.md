# Phase 105: Omega Prime Command Center — Context

## Scope and authority

Planning only, on existing `feat/command-center`, stacked on Phase 104 / unmerged PR #47. The verbatim `105-UPLIFT.xml` is authoritative; user instructions narrow this run to plans and local commits. Do not re-run the uplift graph or create tracker rows. No product code, remote operations, deployment, sandbox-core changes or runtime restart in this session. All planning paths, `.planning/STATE.md` and `.receipts/bot-00-omega-prime/105-command-center-planning.json` resolve through `omega_prime/ownership.yaml` to bot-00-omega-prime (last match wins).

<decisions>
### Locked decisions
- **D-01:** Persist positive integer `seq` per task and top-level `next_seq` in tasks.json. Backfill current order from 1, keep finished ids, never reuse; display `f"{seq:05d} - {title}"`. A small helper plus `python -m omega_prime.dashboard` CLI supports both showing the next available id without consuming it and durably assigning one to a task under a locked atomic read-modify-write. Snapshot exposes seq and display_id. Preserve source identity and existing callers.
- **D-02:** Read-only GitHub data: swcstudiospace repository metadata (name, visibility, default branch, pushed_at, open PR count, language, latest default-branch CI conclusion); open omes-bot and coding-fleet PRs with checks (name, conclusion, duration, details_url), Greptile score and reviewed commit. Cache 60–120 seconds, fetched_at and age, capped pagination, cached degradation for offline/403/429, partial source health. Token remains broker/env-only; redact_value covers every new field. Fake transports only in tests.
- **D-03:** React + TypeScript + Vite + Tailwind in omega_prime/dashboard/frontend. npm scripts test=vitest and build=typecheck then Vite. Registry registry.npmjs.org; discover compatible versions rather than invent pins. Commit package-lock.json and hashed build output under omega_prime/dashboard/static/cc. No Node runtime.
- **D-04:** Four views: Overview (active/waiting-on-Ove/PR-gate/finished counts, recent merges, source health, live feed), Tasks (search/filter/state badges, numbered sidebar, selection without reload; owner/source/state/model, started/last activity AEDT, progress, links, timeline), CI/CD (checks, failures/link-outs, scores/reviewed commits, wiring merge-order state), Repositories (searchable/sortable metadata and GitHub links). Use existing app.py JSON/SSE endpoints, never invent routes. SSE failure falls back to polling.
- **D-05:** Every view supports loading shimmer, empty, error/retry, stale age badge and partial data. Local sections continue during GitHub failure. Render text safely; no innerHTML/dangerouslySetInnerHTML with data. Missing collector facts must be identified as unavailable, not fabricated.
- **D-06:** White/near-white default with purple primary/gradients, neutral text, WCAG AA. Self-host @fontsource-variable/anybody (headings/counts), @fontsource/space-mono (ids/code/badges), @fontsource-variable/dm-sans (body). Preserve OFL 1.1 licences in shipped assets. Typeface inspection is supplied evidence from README-REFS.md, not a fresh inspection by this session.
- **D-07:** Use motion package. Magic UI MIT Number Ticker, Blur Fade, Border Beam, Animated Shiny Text; adapted Aceternity animated Sidebar and Card Hover Effect. Preserve MIT notices; no verbatim Aceternity registry dumps or source-library copies committed. Reference sources are /sandbox/home/refs/components/src/*.tsx and the supplied registry/21st.dev files. Exclude three.js/Card Spotlight. Disable all effects via useReducedMotion and CSS prefers-reduced-motion, display data immediately. Aim below about 400 KB gzip and avoid lazy loading.
- **D-08:** Serve built app at default page; /cc asset namespace and /legacy old page/no-JS fallback. Correct MIME and immutable hashed-asset caching; HTML revalidates. CSP default-src self, script-src self, font-src self, connect-src self; style-src self plus unsafe-inline only for motion inline styles, documented. No external origins. Keep loopback-only bind (reject 0.0.0.0 and ::), Host validation, non-GET=405, redaction and every existing security header/test.
- **D-09:** File-disjoint W1 Python ids; W2 Python GitHub; W3 frontend/build; W4 server; W5 docs and final checks. Assign model.py once, app.py once and each existing test file once. Read existing source before finalising ownership/interfaces. Explicit user wave ordering overrides default tracer-first decomposition; numbering is the first complete persisted-data-to-snapshot slice.
- **D-10:** Acceptance commands run offline except npm dependency downloads from the reachable registry: python3 -m pytest -q -p no:cacheprovider omega_prime/tests/ -k dashboard; npm ci --registry=https://registry.npmjs.org, npm test -- --run, npm run build from omega_prime/dashboard/frontend. No Python package installation/network. Every automated command has a failure signal. This session runs existing Python checks and plan validators, not a nonexistent frontend build.
- **D-11:** a6 signed openshell-podman/Anda receipt and a7 four real-data screenshots are produced by the outer driver, not blockers on planning. Outer driver also verifies live a2/a3 parity, exports/pushes, opens draft PR stacked on #47, runs Greptile up to five rounds, and restarts on verified head. No merge or auto-merge. Record these obligations without claiming offline fixtures prove live parity.
</decisions>

## Acceptance mapping

| Criterion | Planned owner | Evidence boundary |
|---|---|---|
| a1 | 105-01 and 105-03 | restart/concurrent allocation and UI selection/id tests; runtime smoke during execution |
| a2 | 105-02 and 105-03 | fake GitHub check/score tests; live match outer driver |
| a3 | 105-02 and 105-03 | fake org metadata/search/sort tests; live match outer driver |
| a4 | 105-03 and 105-05 | local fonts/licences, motion/reduced-motion tests, build; visual proof outer driver |
| a5 | 105-01 through 105-04 | redaction, route/security regressions, self-only CSP |
| a6 | 105-05 handoff | tests/build records in repo; signed tier/access attestation outer driver |
| a7 | 105-05 handoff | four screenshots with real data outer driver |

## Execution boundaries

Read the current code rather than assuming the uplift's older description (stdlib versus Starlette in particular). Preserve existing API routes and legacy consumers. Plans must name proposed new contracts explicitly as additions, not existing facts. Read model/poller metadata before deciding how recent merges, timeline and wiring merge order are derived; where unavailable, present an honest state instead of synthesising history. Runtime tasks.json is user data outside the repository, not a committed fixture path.

## Deferred ideas

Additional Sandbox/Fleet/Linear views, remote auth/public hosting, mobile UI, CI reruns/merges/comments, and collector changes unrelated to the required views are out of scope. Sandbox seeding/export infrastructure in c5 belongs to the outer driver; current sandbox is already provisioned.

## Frozen file-disjoint integration boundary

W1 exclusively owns `model.py`, `poller.py`, `__main__.py`, `status_file.py`, `collectors/tasks_json.py` and new `task_ids.py`. It implements the optional numbering-path constructor argument before the CLI calls it, so W1 remains independently runnable. W1 adds the bounded `command_center` key to the GitHub source-extra allowlist at the existing metadata publication boundary; it is absent until the collector supplies it. W2 exclusively owns `collectors/github.py` and any new pure GitHub helper and emits `sources.github.command_center = {repositories, ci_cd, recent_merges, meta}` through that boundary. W2 does not edit model, poller, CLI or app. W3 consumes this additive shape and the original legacy arrays through the unchanged snapshot/SSE routes. W4 owns app/static-serving/package-data integration and server-facing tests. A timeout at the poller boundary must retain previous cached GitHub data while health becomes degraded; W1 owns that boundary behavior.

Number every task-like sidebar identity, including collector-derived tasks, through an explicitly allowlisted durable identity registry in tasks.json; keep technical IDs for joins. A task appearing in active and finished shares its sequence. Backfill original bridge order before sorted normalization, retain removed identities and finished numbers, and use a sidecar lock shared by writer, allocator and CLI across atomic replacement. The five-digit format is minimum width, not a new 99999-task limit. Timeline entries are actual observations only. W2 supplies bounded real merged-PR metadata for Overview; closed or runner-done is not evidence of a merge. Wiring associations use collected branch/SHA/stage facts and label unknown prerequisites rather than invent a merge graph.

## Observed planning checks

The requested broad `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/ -k dashboard` exits 2 during unrelated test collection because staged dependencies do not include packages such as mcp and apscheduler. Preserve this gate and record the blocker; do not install or alter unrelated code. The offline targeted command `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_dashboard*.py` passes 90 tests (one Starlette deprecation warning). `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_docs.py omega_prime/tests/test_public_repo.py` passes 31 tests. These establish the planning baseline, not implementation acceptance.

## Tracker provenance

Identifiers and URLs remain in the verbatim uplift and the run's commit trailers / PR handoff, not general guidance. No new tracker rows. n1–n5 GoT results are supplied completed lead evidence; this run maps current repo facts and translates n6's workflow into executable plans without re-running the graph.
