# Phase 106: Command Center polish — Research

**Researched:** 2026-10-21
**Domain:** Existing Python/Starlette dashboard, bounded GitHub collection, React hash navigation, read-only Herdr SSE
**Confidence:** HIGH for opened repository definitions; MEDIUM for externally cited protocol guidance; live behavior not exercised.

<user_constraints>
## User Constraints (from CONTEXT.md)

The following locked material is copied verbatim. [VERIFIED: .planning/phases/106-command-center-polish/106-CONTEXT.md:9-37,43-45]

DATA_Q7M4K2VX_START
### Locked decisions

1. Recent merges: use smaller closed-PR pages, detect bodies over the cap by a bounded limit+1 read and a distinct `too_large` reason, and stop at MAX_MERGES. Never unbounded reads, silent truncation, hidden banners or swallowed errors. Preserve cached merges as stale on genuine failure, deadline/budget/cache, unauthenticated behavior and GET/no-redirect/token guarantees.
2. CI/CD: backend `ci_cd` is authoritative. One branch card per repository+branch even with mixed stale/current SHA values; synthetic `latest_main_ci` only where that repository+branch has no backend branch row. Stable unique keys; preserve PR rows.
3. c1: lookup the merged PR producing the validated branch-head SHA using GET `/repos/{owner}/{repo}/commits/{sha}/pulls`, inside existing bounds/cache. Attach score, reviewed_sha and source `merged_pr` with validated PR number. Empty lookup means `not_applicable` / “No PR review for this commit”. Lookup failure is row-local `unavailable`, not section-wide partial. PR body-marker scores receive reviewed_sha where derivable; otherwise “reviewed SHA not reported”. Never the four-unknown string.
4. c2: hash routes `#/overview`, `#/tasks`, `#/tasks?bucket=waiting|working|pr-gate|finished`, `#/tasks/<id>`, `#/cicd`, `#/repositories`. Refresh/Back/Forward restore. Unknown bucket -> All plus notice; unknown task -> “Task not found”. Summary cards/sidebar toggle buckets; clicking active bucket or All clears. Detail Back preserves bucket context.
5. Buckets use snapshot waiting_on_ove, non-terminal active, pr_gate and finished (deduplicated by id). Task detail includes numbered title, status, timeline, linked PR CI/Greptile joined from ci_cd, Notion/Linear links, agent/owner, model/plan, wiring branch/worktree, events and terminal mount. Compact field-rich rows include id/title/state/age/owner/PR+CI/score; offer compact/comfortable toggle.
6. c3: optional `herdr_pane` validated in status_file with `^w\d+:p\d+$`, surfaced through tasks_json/snapshot. Owner fallback only when owner explicitly identifies a Herdr pane. Browser sends task id only; server validates/looks up task and resolves pane. No automatic bridge population or writes to real task files.
7. c4: GET `/api/tasks/{task_id}/terminal` SSE behind existing loopback/Host/GET/CSP guards. Exact argv `["herdr","pane","read",pane,"--source","recent","--lines","200","--format","text"]`; short timeout, about 1/s only while connected, changed frames plus heartbeats and capped concurrent streams. Strip ANSI/control sequences, cap last 200 lines and 64 KB, apply redact_value plus Bearer, ghp_/github_pat_, sk-, JWT-like and KEY=value secret patterns before emission. Plain-text rendering, no innerHTML. Live/no session attached/pane closed/Herdr unavailable/reconnecting; pause/resume auto-scroll, copy, last update. No input endpoint, POST, WebSocket, send-text/send-keys/run or shell path.
8. c5: original 12:54 scope belongs within these five plans, not a new phase. Preserve uplift graph n1–n8 ordering and tracker mapping; do not rerun uplift or create tracker rows.

### Approved model-bucket addendum (18:39 AEDT)

- Plan 106-01: tasks_json `done`/`failed` go only to finished with result `ok`/`failed`, never active. Non-terminal behavior unchanged.
- Plan 106-01: Herdr unknown/non-live-agent panes, panes whose matched omp session has stop_reason, and quiet sandbox_e2e runs go to new `Snapshot.idle_sessions: list[Task]`, included in to_dict/content_hash/API payload, not active. Live working/idle/blocked agent panes remain active; blocked waiting item remains. Working sandbox_e2e remains active. Audit poller/app/task_ids and every active consumer; allocate ids consistently across idle tasks.
- Plan 106-04: types/selectors/Overview/sidebar/Tasks render “Idle sessions”, excluded from Active/Working count/bucket. Collapsed default is allowed.
- Regression gates: done+failed finished-only; unknown/stopped/quiet idle-only while working remains active; frontend idle rendering and Active count exclusion. Every new regression must fail on ad3942b and pass after implementation.

### Execution shape and ownership

Exactly five PLAN.md files, exactly two waves: wave 1 = 01/02/04; wave 2 = 03/05. File-disjoint means within a wave: the contract explicitly requires cross-wave edits to collector and model files. Ownership resolves to bot-00-omega-prime under omega_prime/ownership.yaml, last match wins. Plans must list exact files and explicitly separate source ownership from prerequisite/integration ordering. 04 alone rebuilds/commits static/cc in wave 1 after 02 is integrated; 05 alone rebuilds/commits it in wave 2 after 03 is integrated. No extra execution plan. Explicitly coordinate the 05-owned TerminalPanel placeholder import before 04 builds, without permitting 04 to own that file or leave a broken import.

### Evidence and verification

Read every reference in `/sandbox/home/refs`: README-REFS.md, ADDENDUM-model-buckets.md, uplift, baseline ruff/mypy, scrubbed Herdr snapshot shape and pane-read help. Herdr is absent in sandbox; fake runner proves offline behavior; real live terminal/screenshots remain host evidence. Existing lint/type failures are recorded inputs, not newly reproduced failures or permission to introduce more.

Each plan must name exact regression test nodes/titles for the outer driver to transplant and run against ad3942b. A missing module/import is not sufficient evidence of the behavioral regression: adapt tests to existing public entrypoints or document a minimal baseline harness. All new regressions must FAIL on ad3942b and PASS after change; do not claim this during planning. Python command: `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/ -k dashboard`. Frontend npm ci/test/build run in omega_prime/dashboard/frontend with registry.npmjs.org explicitly selected. No GitHub/Herdr network dependency in offline tests. Verify full suites, configured lint/types, polyglot-check --changed at execution; record pre-existing limits honestly.

Uplift acceptance criteria are indexed AC1–AC9 in source order. AC1/2 merges and clean banners; AC3 dedup; AC4 Greptile; AC5 buckets; AC6 detail/density; AC7 terminal; AC8 fail-before/pass-after and suites; AC9 five screenshots and local commits. Live GitHub and five screenshots (Overview, bucket, detail with terminal, CI/CD, Repositories) belong to isolated host 7441 verification, not an offline claim. No merge, auto-merge, force-push, deploy, token helper or secret disclosure.

### Deferred / out of scope

Interactive terminal control, bridge population of herdr_pane, live task-file writes, moving the 7421 service, unrelated redesign/data sources, changing GitHub/Greptile/CI settings, push/merge/deploy. `rule adherence by future agents` remains unverified in every receipt.
DATA_Q7M4K2VX_END

### Claude's Discretion

No separately named discretion section exists in the opened context. Internal helper decomposition, test names and bounded implementation knobs below are recommendations, not replacement product decisions. The parent explicitly approved the narrow 05 prerequisite exception described below during this research.
</user_constraints>

<phase_requirements>
## Phase Requirements

Acceptance labels are the context's AC1–AC9 indexing, not invented REQUIREMENTS.md IDs. [VERIFIED: .planning/phases/106-command-center-polish/106-CONTEXT.md:37-37]

| ID | Approved behavior | Research support / owner |
|---|---|---|
| AC1 | Clean live GitHub error/banner state | Honest oversize, smaller pages, failure retention; 01 and host gate |
| AC2 | Both repositories' actual recent merges | Per-target bounded enumeration before global cap; 01 |
| AC3 | One authoritative default-branch card | Repo+branch stale-aware dedup; 02 |
| AC4 | Branch score / no PR / unavailable wording | Associated merged PR, true SHA provenance, row-local errors; 03 |
| AC5 | Deep-linkable bucket views with clear behavior | Hash adapter, shared selectors, history tests; 04 |
| AC6 | Complete numbered task detail and denser lists | Published link/wiring evidence, finished metadata preservation, density; 01/04 |
| AC7 | Redacted read-only mapped terminal | Validated bridge, task-resolved guarded SSE, sanitizer/lifecycle; 05 |
| AC8 | Behavioral fail-before/pass-after and full suites | Named public-entrypoint regressions, real terminal expected200/base404 missing-feature contract, actual implementation polish-only security coverage and execution commands |
| AC9 | Five real screenshots and local commits | Isolated host gate, generated-output ownership, parent delivery |
| Approved addendum | Finished-only terminal tasks; separate idle sessions | Full model/allocator/API/frontend consumer audit; 01/04 |
</phase_requirements>

## Summary

Use the existing stack and fix the existing seams; do not add packages, a router dependency, another server, a terminal emulator or a replacement SSE implementation. The server is **Starlette**, not the older uplift's stdlib description: `from starlette.applications import Starlette`, `class _SSEResponse(Response)`, and `def create_app(...)` are the actual implementation. [VERIFIED: omega_prime/dashboard/app.py:15-45,204-210] Phase 105 already explicitly corrected this discrepancy: “Use the actual Starlette snapshot/SSE service, not the uplift's outdated stdlib description.” [VERIFIED: .planning/phases/105-command-center/105-02-PLAN.md:45-48]

The highest-risk planning errors are coordination errors: importing a nonexistent terminal component in wave 1; two builders deleting each other's output; shrinking closed pagination below the requested merge capacity; dropping finished-task detail when removing it from active; and forgetting the allocator when introducing idle rows. Concrete fixes and custody are below. The approved addendum supersedes Phase 105's old six-key snapshot invariant; preserve legacy arrays but deliberately add the idle array and migrate exact-shape assertions. Existing assertion: `assert set(first) == {"generated_at", "sources", "active", "pr_gate", "waiting_on_ove", "finished"}`. [VERIFIED: omega_prime/tests/test_dashboard_snapshot_contract.py:28-42]

**Primary recommendation:** preserve exactly 01/02/04 → 03/05, freeze the minimal interfaces before execution, and serialize only the explicit terminal prerequisite and end-of-wave build barriers. No tests, builds, linters, package installs or product edits were performed in this research.

## Architectural Responsibility Map

| Capability | Primary tier | Secondary tier | Planning rationale |
|---|---|---|---|
| HTTP response bounds, merged-PR discovery | API/backend collector | Existing cache | Keep authenticated network work server-side; use injected transport. |
| Active/finished/idle classification | Backend normalization | Durable numbering | Publish one authoritative group; client selectors must not repair bad model data. |
| Bucket routes, density, task detail | Browser/client | Existing snapshot | Fragment-only routing, no server SPA fallback. |
| Pane authorization/mapping, sanitization and stream limit | API/backend | Validated bridge and current snapshot | Browser supplies task identity, never arbitrary pane identity. |
| Terminal display/reconnect/autoscroll | Browser/client | Existing loopback SSE | Plain text and lifecycle cleanup, no input surface. |
| Generated assets | Static/build | Existing Python asset service | One writer at each wave barrier. |

These are prescriptive ownership recommendations grounded in the opened collector, normalization, app and frontend boundaries, not new service boundaries. [VERIFIED: omega_prime/dashboard/poller.py:177-232; omega_prime/dashboard/app.py:300-331; omega_prime/dashboard/frontend/src/hooks/useDashboard.ts:26-85]

## Project Constraints (from CLAUDE.md)

Initial file discovery returned `CLAUDE.md` as missing; no CLAUDE directives were loaded or invented. The actionable project constraints instead come from the opened agent rules: “Resolve every path you will create or edit ... before the first edit”, “Stay on a feature branch”, and “Do not print or commit secrets”. [VERIFIED: AGENTS.md:150-162] Ownership's literal broad rule is `pattern: "**"` / `owner: bot-00-omega-prime`; later Python and TypeScript matches have the same owner. Planning output and this research receipt therefore belong to that owner. [VERIFIED: omega_prime/ownership.yaml:15-17,29-32,44-47]

Follow the supplied graph rather than create tracker rows. Planning documentation is not automatically committed: configuration contains `"commit_docs": false`, while validation and security are enabled with `"nyquist_validation": true` and `"security_enforcement": true`. [VERIFIED: .planning/config.json:1-15] The parent owns planning commits and final delivery; this research makes no push, PR or product completion claim.

## Standard Stack

These are **existing declarations/installed observations**, not registry-current recommendations or new package admissions.

| Layer | Existing version evidence | Use |
|---|---|---|
| Python | `requires-python = ">=3.11"`; observed interpreter 3.12.3 | Existing backend; no dependency change. [VERIFIED: pyproject.toml:8-8; runtime metadata command in Sources] |
| Starlette / Uvicorn | Installed metadata 1.7.0 / 0.54.0 | Existing app and launcher, not a stdlib rewrite. [VERIFIED: runtime metadata command in Sources; omega_prime/dashboard/app.py:15-18; omega_prime/dashboard/__main__.py:169-173] |
| React / React DOM | `"react": "19.3.1"`, `"react-dom": "19.3.1"` | Existing UI. [VERIFIED: omega_prime/dashboard/frontend/package.json:14-16] |
| TypeScript / Vite | `"typescript": "7.0.2"`, `"vite": "8.3.4"` | Existing build. [VERIFIED: omega_prime/dashboard/frontend/package.json:29-31] |
| Vitest / jsdom | `"vitest": "5.0.5"`, `"jsdom": "30.1.3"` | Existing DOM/unit framework. [VERIFIED: omega_prime/dashboard/frontend/package.json:27-31] |
| Testing Library | `"@testing-library/react": "16.3.3"`, `"@testing-library/user-event": "14.6.8"` | User-facing DOM regression tests. [VERIFIED: omega_prime/dashboard/frontend/package.json:21-22] |
| pytest | Installed metadata 9.1.1; declared `"pytest>=8.0"` | Fake transports, runners and ASGI tests. [VERIFIED: pyproject.toml:38-42; runtime metadata command in Sources] |
| Styling/motion | `"tailwindcss": "4.3.3"`, `"motion": "14.4.0"` | Retain existing conventions, no redesign. [VERIFIED: omega_prime/dashboard/frontend/package.json:14-14,28-28] |

**Installation:** none required by the feature design. Executor may restore the already locked npm tree using `npm ci --offline --registry=https://registry.npmjs.org` only when cache is populated; a cache miss must be recorded, not worked around with invented pins or a different registry. The existing scripts are `"test": "vitest"` and `"build": "tsc -b && vite build"`. [VERIFIED: omega_prime/dashboard/frontend/package.json:6-8]

## Package Legitimacy Audit

No new external package is recommended or installed by this phase research. A new package audit is therefore not applicable. Existing package names above are verified as repository declarations, **not** tagged `[VERIFIED: npm registry]`; current registry versions, publication dates and package-legitimacy verdicts were not queried. Retain the existing lock and prohibit adding a router/SSE/terminal dependency just for these changes.

## Architecture Patterns

### System architecture diagram

```text
GitHub API -- bounded GET / cap+1 --> collector -- per-section cache --> poller
local collectors + validated bridge -------------------------------> normalizer
                                                                       |
                                                       number active + idle + waits + finished
                                                                       |
                                                     redacted snapshot / content hash
                                                                       |
                                         GET snapshot + named snapshot SSE
                                                                       v
hash route --> bucket selector --> compact list --> task detail --> terminal slot
                                                        |
                                               EventSource(task ID only)
                                                        v
loopback/Host/GET guard --> task lookup --> validated pane mapping --> stream cap
                                                        |
                                              argv-only Herdr pane read
                                                        |
                                    strip controls --> redact --> cap --> changed SSE
                                                        |
                                               escaped <pre> text
```

The lower terminal path is proposed implementation, not an existing endpoint. Existing SSE uses `yield "retry: 3000\n\n"`, named `event: snapshot`, and `yield ": heartbeat\n\n"`; reuse the response mechanism, not a new buffering transport. [VERIFIED: omega_prime/dashboard/app.py:308-331]

### Per-wave disjoint ownership and shared-file sequencing

All paths in this table are edit targets/proposals, not claims that new files already exist. Resolve them against ownership before execution. New test filenames are explicitly proposed.

| Plan | Wave | Sole source/test custody inside its wave | Cross-wave handoff |
|---|---|---|---|
| 106-01 | 1 | `omega_prime/dashboard/collectors/github.py`, `github_data.py`, `model.py`, `task_ids.py`, `app.py` (cold idle shape only); existing `omega_prime/tests/test_dashboard_model.py`, `test_dashboard_task_ids.py`, `test_dashboard_snapshot_contract.py`, `test_dashboard_app.py`, `test_dashboard_integration.py`; proposed `test_dashboard_github_merges_polish.py`, `test_dashboard_model_buckets.py` | 03 later extends GitHub files; 05 later extends model/app after 01. 03 never edits model. No status_file/herdr mapping work in 01. |
| 106-02 | 1 | `omega_prime/dashboard/frontend/src/views/CiCd.tsx`, its `CiCd.test.tsx`; proposed `views/CiCd.dedup.test.tsx` | 03 extends same card/view after 02. 02 edits no global types/selectors/fixtures/styles/generated output. |
| 106-04 | 1 | `frontend/src/App.tsx`, `types.ts`, `lib/selectors.ts`, `lib/links.ts` if route/link migration requires it, new hash-router module, `views/Overview.tsx`, `views/Tasks.tsx`, `components/CommandSidebar.tsx`, `SummaryCards.tsx`, `TaskDetail.tsx`, `styles.css`; corresponding existing frontend tests and new routing/detail tests; `frontend/src/test/fixtures.ts`; `omega_prime/tests/test_dashboard_app_fixes.py`, `test_dashboard_command_center_static.py` for generated-asset regression custody | Freeze types for 03/05 now; no 03 edit to global selectors/types. 05 later extends task types for pane mapping and may add its own CSS/module, but does not rewrite routing/detail. Sole wave-1 generated-output owner after 02 commit. |
| 106-03 | 2 | GitHub collector/helpers inherited from 01; existing GitHub command-center/cache tests; proposed `test_dashboard_github_greptile_polish.py`; CiCd files inherited from 02; proposed `views/CiCd.greptile.test.tsx` | Wait for 01+02; consume completed 04 types/selectors read-only. Publish source changes before 05 final build. No model/app/status_file/global styles or shared-fixture edits. |
| 106-05 | 2, plus explicit prerequisite exception | `omega_prime/dashboard/terminal.py` (new), `app.py`, `model.py`, `status_file.py`, `collectors/tasks_json.py` if passthrough needs adjustment, own new Python terminal tests; `frontend/src/components/TerminalPanel.tsx`, its test and dedicated CSS, optional new terminal hook/tests; `types.ts` pane fields after 04; `docs/dashboard.md`, `CHANGELOG.md` | Only05 authors TerminalPanel prerequisite below; all real terminal/backend work stays wave2. Sole wave-2 generated-output owner after 03 commit. Does not edit 03's collector/CiCd/tests or 04's route/detail source. |

**Build ownership is separate from source readiness.** Existing build config says `outDir: fileURLToPath(new URL('../static/cc', import.meta.url))` and `emptyOutDir: true`; simultaneous builds can replace the same directory. [VERIFIED: omega_prime/dashboard/frontend/vite.config.ts:12-17] Only04 runs/commits the wave-1 build after02 source integration. Only05 runs/commits wave-2 build after03 source integration. No builder commits guessed hashes. 01/02/04 remain source-parallel; 03/05 remain source-parallel after their dependencies, except these barriers. All execution checks run only at authorized settled integration points, never against a sibling's partial changes.

**Static test trap:** `assert client.get("/cc/assets/index-DIGTqjrX.css", headers=host).status_code == 200` hardcodes an old asset. [VERIFIED: omega_prime/tests/test_dashboard_app_fixes.py:80-84] Assign that module to04; extract the actual linked stylesheet from served/generated HTML and request it, never pin the next hash. 01 owns integration.py's existing method-guard test; it may use an arbitrary disallowed-method path unchanged because rejection precedes asset lookup. Literal methods are `("POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")`. [VERIFIED: omega_prime/dashboard/app.py:148-163; omega_prime/tests/test_dashboard_integration.py:248-250]

### TerminalPanel prerequisite: explicit, narrow scheduling exception

**Chosen contract, approved by parent:** within existing 106-05 task 1, the **05 owner alone** authors the minimal component artifact before04's final integration/build. It exports the named `TerminalPanel({taskId}: {taskId: string})`; interim visible text is **“Terminal streaming not connected”**. It does not claim a task lacks a session, simulate live output, or open a connection before the server exists. The04 owner imports/mounts it from TaskDetail using the resolved technical task ID and never edits that file. Its path must be absent from04 `files_modified`. The05 owner replaces the interim implementation with real SSE during wave2; the final deliverable must contain no placeholder.

This is **not** a sixth plan or third wave. It is an explicitly documented prerequisite-artifact exception to05's wave2 scheduling, and the sole pre-wave product action when execution is eventually authorized. This research creates no such product artifact. Track prerequisite completion separately from05 implementation completion; do not claim05 done when only the artifact exists.

**Tradeoff:** strictly forbidding any05 artifact before wave2 makes a real04 import impossible. The viable alternative is an import-free `ReactNode` terminal-slot prop owned by04, with05 later mounting via the shell, but that requires a later shared-shell edit and does not satisfy the approved literal04 placeholder-import wording. Do not silently use a test-only module mock, missing import, ignored file, `null` component, or04-authored file and pretend ownership remained disjoint.

### Backend fix patterns

1. **Honest response limit.** Current request performs `response.read(1_000_000)` while `get` tests `len(body) > 1_000_000`; the latter cannot detect an oversize real response already truncated by the former. `fail` maps ValueError to `"error"`. [VERIFIED: omega_prime/dashboard/collectors/github.py:52-61,125-134,174-181] Read limit+1, use a dedicated oversize exception/reason before generic ValueError classification, and enforce the same size policy on injected transports. Check size before JSON parsing, including every endpoint, and preserve failed-repository stale-cache merging.
2. **Separate closed pagination policy.** Current bounds are `MAX_MERGES = 100`, `MAX_PAGES = 3`, `PAGE_SIZE = 100`. [VERIFIED: omega_prime/dashboard/github_data.py:11-14] Reducing all pages to30 would permit only90 candidates in three pages. Recommendation: closed-only page size30, closed-only ceiling10 pages (same300 candidate envelope), early stop at100 *merged* rows per target. Keep other endpoint pagination unchanged. Query page length/slicing must use the selected page size rather than the old global100. A page with closed-unmerged PRs must not consume the merged-row target. Visit both target repositories before global merge sort/cap; otherwise the first target filling100 can suppress the second target entirely. Reaching display capacity is not a failed fetch; exhausting scan bounds before enough qualifying rows must remain honest truncation. This recommendation is a bounded implementation choice, not measured evidence that every30-row response fits.
3. **Preserve previous subsection data.** Existing code replaces failed-repository new merges with old merges and retains original per-section time: `cc["recent_merges"] = ... old_merges ...`, `sections[section]["fetched_at"] = old_section.get("fetched_at")`. [VERIFIED: omega_prime/dashboard/collectors/github.py:337-350] New oversize handling must flow through that path, not return an empty successful list.
4. **Branch enrichment is row-local.** `_greptile(... on_truncate, on_error)` currently calls supplied callbacks; the open-PR caller uses callbacks that mark the whole CI section partial. [VERIFIED: omega_prime/dashboard/collectors/github.py:253-257,362-384] For branch enrichment supply row-local callbacks, including failed/truncated review/comment discovery, and do not reuse `mark_ci_failure` there. Preserve existing open-PR error semantics and tests.
5. **Keep review provenance honest.** Body marker returns `"reviewed_sha": None`, `"source": "body"`, `"matches_head": None`; review matching requires `item.get("commit_id") == sha`. [VERIFIED: omega_prime/dashboard/collectors/github.py:362-397] For a linked merged PR pass its validated PR-head SHA into extraction, not the merge-commit SHA, then preserve its actual reviewed SHA. A merge commit and PR head are not interchangeable evidence. Attach the validated PR number with proposed optional `pr_number`;04 reserves that field in CiRow before03. Preserve original extraction provenance in an optional evidence-source field if needed. Empty associations mean not-applicable; failed/malformed/truncated or ambiguous associations are not proof of direct commit. Do not fabricate a reviewed SHA from PR.head when a body marker did not report it.
6. **Validate before URL composition.** Existing reference rule is `_SAFE_REF = re.compile(r"^[A-Za-z0-9_-]{1,80}$")`; number validation is `isinstance(number, int) and not isinstance(number, bool) and 0 < number < 2**31`; `_safe_url` requires `parsed.scheme == "https" and parsed.hostname == "github.com"`. [VERIFIED: omega_prime/dashboard/collectors/github.py:36-37,252-255,405-410] Reuse those rules, the existing budget/deadline and redaction. Existing bounds are `budget = [1000]`, `deadline = time.monotonic() + 12.0`, `cache_ttl: float = 90.0`. [VERIFIED: omega_prime/dashboard/collectors/github.py:70-75,104-108]

### Model/idle consumer audit — mandatory before01 edit

| Consumer | Existing source evidence | Required treatment |
|---|---|---|
| Snapshot serialization/hash | `"active": [x.to_dict() for x in self.active]`; hash serializes `self.to_dict()` [VERIFIED: omega_prime/dashboard/model.py:178-200] | Append idle field after existing four dataclass fields; serialize it and test that idle changes affect hash. Preserve old positional constructor order. |
| Normalizer return | `return Snapshot(active, prs, waiting_list, finished)` [VERIFIED: omega_prime/dashboard/model.py:716-719] | Prefer keywords after adding idle; do not insert idle as second positional argument. |
| Herdr classification | `state = status if status in {"idle", "working", "blocked"} else "unknown"`; unconditional `active.append(...)`; owner `Owner("herdr-pane", pid, f"Herdr pane {pid}")` [VERIFIED: omega_prime/dashboard/model.py:274-310] | Route unknown/exited/no-live-agent panes to idle; preserve their identity/detail, session claiming and blocked waiting behavior. Stop-reason evidence outranks apparent working status. Do not require a local OMP process for every non-OMP live agent. |
| Sandbox E2E | `"working" if age * 60 < quiet_minutes else "quiet"` goes through `active.append` [VERIFIED: omega_prime/dashboard/model.py:432-447] | Only quiet sandbox_e2e moves idle; working remains active. Do not broaden to all quiet omega jobs or unmatched OMP sessions. |
| Bridge terminal states | Mapping includes `"done": "done"`, `"failed": "failed"`; unconditional active append followed only by `if state == "done"` finished creation [VERIFIED: omega_prime/dashboard/model.py:617-661] | Terminal states append only finished with approved result and continue; retain nonterminal behavior. Preserve available owner/links/detail/model/timing metadata in the finished projection so detail page does not regress when the richer active duplicate disappears. |
| Numbering | `rows = [*snapshot.active, *snapshot.waiting_on_ove, *snapshot.finished]` [VERIFIED: omega_prime/dashboard/task_ids.py:114-133] | Include idle tasks in the same deterministic identity allocation and history observation. Test active→idle→active retains seq; no new persistent idle array is required in bridge JSON. |
| Poller publication | `normalized = snapshot.to_dict()`; `**normalized` enters payload/hash [VERIFIED: omega_prime/dashboard/poller.py:199-228] | Existing pass-through should carry idle automatically. Test publication/hash; no extra source-status allowlist for idle. Keep poller source unchanged unless implementation proves a need. |
| Cold API shape | `_empty_snapshot()` has `"active": []`, `"pr_gate": []`, `"waiting_on_ove": []`, `"finished": []` [VERIFIED: omega_prime/dashboard/app.py:193-201] |01 adds idle here,05 extends terminal route later. Migrate exact shape assertions deliberately. |
| Task selector / observed activity / sidebar / Tasks | `taskSelection` starts with active+finished, then waiting; `observedActivity` calls that selector [VERIFIED: omega_prime/dashboard/frontend/src/lib/selectors.ts:3-35] |04 adds idle to all-task identity/detail/event selection, but displays it in a separate group and excludes it from Working. Never hide idle from task routes. |
| Overview counts | Active excludes only `['done', 'complete', 'finished']` [VERIFIED: omega_prime/dashboard/frontend/src/views/Overview.tsx:11-12] |04 shares one nonterminal selector (including failed as terminal) with bucket counts; idle never comes from active. Preserve finished-id dedup. |
| CI wiring correlation | `localTasks = [...(snapshot.active ?? []), ...(snapshot.finished ?? [])]` [VERIFIED: omega_prime/dashboard/frontend/src/views/CiCd.tsx:17-18] |02 deliberately leaves idle correlation untouched to avoid04 selector ownership overlap;03 adds idle into correlation after04 types land. Idle evidence may still corroborate wiring, but never increases Active. |
| Legacy UI | `safeArray(snapshot && snapshot.active)` and render dispatch only `active`, `PR gate`, `waiting`, `finished` [VERIFIED: omega_prime/dashboard/static/app.js:80-89,204-210] | Deliberately skip idle in legacy recovery page; keep its arrays compatible. User-requested Idle group is implemented across the React overview/sidebar/tasks, not an unrelated legacy redesign. |

**Non-live-agent evidence caveat:** collector `_collect_agents` emits `"agent_status"`, `"session_path"` and deduplicates by pane id; the host snapshot additionally has `"agent"`, `"agent_session"`, and `"pane_id": "w1:p1"`. [VERIFIED: omega_prime/dashboard/collectors/herdr.py:44-65; /sandbox/home/refs/herdr-api-snapshot-shape.json:61-85] Define an explicit live-agent predicate from present status plus matched-session stop evidence. Do not equate “not matched to OMP” with “no live agent”, and do not silently classify a known working non-OMP pane idle. If retaining an additional live-agent marker is necessary, assign its collector/test change to01;05 does not concurrently reinterpret classification. [ASSUMED: A1 — precise host semantics of missing agent metadata need fake cases plus host confirmation.]

**Detail data audit:** `_wiring` currently returns only `"phase"`, `"branch"`, `"head_sha"`, `"stage"`, `"repo"`; the source wiring record already supplies `worktree`, used for process matching and a phase-log link. [VERIFIED: omega_prime/dashboard/model.py:57-64,392-428] Assign preserving worktree in normalized wiring to01 and its frontend optional type to04. Current links mismatch is real: bridge accepts `"label"`/`"path_or_url"` while frontend declares `{ label: string; url: string }` and renders `safeHref(link.url)`. [VERIFIED: omega_prime/dashboard/status_file.py:63-75; omega_prime/dashboard/frontend/src/types.ts:1-8; omega_prime/dashboard/frontend/src/components/TaskDetail.tsx:12-13]04 must migrate to the actual published shape across consumers/tests; do not invent server URLs or hide Notion/Linear links. Existing omega-job detail incorporates `item.get("plan")`, but Task has only model as a structured field; display available plan evidence honestly rather than fabricate a plan field/value. [VERIFIED: omega_prime/dashboard/model.py:76-92,499-515]

### Frontend patterns

- Put hash parsing/formatting and bucket selection in small pure functions; initialize from current fragment, subscribe to hashchange, clean up on unmount, and reset location between tests. Encode technical task IDs as one URL segment and decode once with malformed-encoding handling. Existing fixture identity is `id: 'worker/1'`; existing model identity is `f"herdr:{pid}"`. A raw slash therefore cannot simply be used as an unescaped route delimiter. [VERIFIED: omega_prime/dashboard/frontend/src/test/fixtures.ts:5-8; omega_prime/dashboard/model.py:291-297] Fragment semantics are documented by MDN. [CITED: https://developer.mozilla.org/en-US/docs/Web/API/Window/hashchange_event]
- Keep bucket origin in the detail route query/history contract so refresh and Back retain it; do not rely only on React memory. Keep all/unknown-bucket notices and controls outside the empty-content wrapper. PR-gate rows are PrRow, waiting rows are WaitingItem and finished rows are FinishedItem, not uniform Tasks. [VERIFIED: omega_prime/dashboard/frontend/src/types.ts:4-12] Make the route selection a discriminated UI adapter that resolves those identities without fabricating a backend Task. Clicking a related wait should resolve related_task_id when present; otherwise show the wait's own recorded detail.
- Dedup branch rows by repository+branch for suppression, retain authoritative head SHA in canonical row identity. The locked context intentionally tightens the uplift's repo+branch+SHA wording for stale/current mixtures. Backend rows win even when fallback SHA differs; preserve distinct branches and PRs. Dedup repeated backend branch entries deterministically and retain unique React keys. Existing concatenation is `const rows = [...branchRows, ...detailedRows]`, key `row.id`. [VERIFIED: omega_prime/dashboard/frontend/src/views/CiCd.tsx:10-26]
-04 must reserve optional Greptile `pr_number` in its owned types now;03 later only changes GitHub/CiCd. Detail and list scores should consume that shape without depending on private CiCard implementation.05 only adds optional pane mapping types after04. Do not have03 and05 both edit model or global types in wave2.

### Terminal implementation pattern

Reuse `_SSEResponse` for cancellation/headers and add a small testable terminal service with injected argv runner, clock/sleeper and concurrency limiter. The existing response races disconnect against `anext(self.events)` and cancels pending work. [VERIFIED: omega_prime/dashboard/app.py:64-124] Ensure generator `finally` releases limiter reservations on disconnect, runner timeout, exhausted iterator and transport error; explicitly close the source iterator if necessary rather than assume cancellation closes every async generator.

Pane mapping must survive `status_file._validated` → `make_tasks_json_collector` → `normalize_tasks` → `Task.to_dict` (and preserved finished detail if applicable). Validation currently allowlists task fields, so adding only a TypeScript field or only passing raw extras will lose it. [VERIFIED: omega_prime/dashboard/status_file.py:102-135; omega_prime/dashboard/collectors/tasks_json.py:42-54; omega_prime/dashboard/model.py:629-652,94-115]05 alone owns all this work after01;03 never edits model concurrently. Proposed mapping object is `{pane_id: string, workspace?: string}`; use the locked pane regex, keep workspace descriptive rather than passing it into argv, and allow fallback only for the exact existing owner kind `"herdr-pane"` with validated ref. [VERIFIED: omega_prime/dashboard/model.py:291-304; .planning/phases/106-command-center-polish/106-CONTEXT.md:16-17]

For SSE lookup, validate a bounded decoded task ID and perform exact membership lookup against server-owned snapshot active+idle+finished identities. Never form filesystem paths from it. Task IDs may contain encoded slashes, so use an explicitly reviewed route capture/decoder rather than silently limiting IDs to a colon-only regex. Resolve/revalidate mapping during capture so a removed or changed task cannot retain an arbitrary stale pane authorization. Unknown task should be an HTTP404 before reserving a stream; known task without mapping should emit the documented no-session state and invoke no runner. State vocabulary/events are new proposed APIs and must be frozen in05 tests, not claimed existing constants.

Capture the approved argv only, timeout without a shell, no input API. Stream capacity is checked before a long-lived response starts, rejects excess viewers explicitly, and is released on every exit. Redact complete text before clipping so a secret straddling a clip boundary is not exposed; strip OSC/CSI and other controls, retaining only intended text/newlines/tabs. Cap final UTF-8 output by bytes, not Python characters, to last200 lines and64KB; test multibyte content and secret patterns independently. The existing assignment regex handles a finite named list and delegates to shared `redact_text`; it is not proof that every uppercase secret assignment/JWT variant is covered. [VERIFIED: omega_prime/dashboard/redact.py:24-27,60-89] Apply targeted additional terminal patterns, never globally weaken the shared redactor. Never include raw subprocess stderr, command arguments or pane output in error messages.

Use one EventSource per mounted task panel; close it and clear reconnect timers on task change/unmount. Render `<pre>{text}</pre>`, pause only auto-scroll while continuing capture, and copy only already sanitized text. MDN documents explicit EventSource.close and text/event-stream framing. [CITED: https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events] The existing dashboard hook demonstrates local fake EventSource testing and cleanup rather than requiring a live server. [VERIFIED: omega_prime/dashboard/frontend/src/hooks/useDashboard.test.tsx:5-9]

## Don't Hand-Roll

| Problem | Don't build | Use instead |
|---|---|---|
| App server/security | Second HTTP server, SPA catch-all | Existing create_app, guards and routes. [VERIFIED: omega_prime/dashboard/app.py:127-190,204-210] |
| SSE ASGI transport | Buffering response middleware or WebSocket | Existing _SSEResponse and tested disconnect behavior. [VERIFIED: omega_prime/tests/test_dashboard_app_fixes.py:96-138] |
| Durable IDs | Browser counter or new idle-only registry | Existing number_snapshot and atomic bridge transaction. [VERIFIED: omega_prime/dashboard/task_ids.py:114-134] |
| Greptile parsing | Second score parser | Existing _greptile with appropriate row-local callbacks. [VERIFIED: omega_prime/dashboard/collectors/github.py:362-398] |
| Secret handling | Client redaction or HTML terminal renderer | Existing redact_value plus terminal-specific coverage, plain text. [VERIFIED: omega_prime/dashboard/redact.py:60-89] |
| New navigation | Full router package/server fallback | Small native hash adapter, per locked route decision. [VERIFIED: .planning/phases/106-command-center-polish/106-CONTEXT.md:14-14] |

## Runtime State Inventory

This phase changes projections/schema rather than renaming identifiers; the inventory is still included because it changes existing model consumers.

| Category | Items found / observation | Required action |
|---|---|---|
| Stored data | Bridge identity registry and row validation are persisted via `_write_locked`; registry keys derive SHA256 canonical identities. [VERIFIED: omega_prime/dashboard/status_file.py:183-197,231-250] | Keep identities/counters/version stable; include idle in observation, not a new persisted group. Optional pane field round-trips without deleting old fields. Test only temporary stores. |
| Live service config | Uplift explicitly excludes “Moving the server onto 7421 or editing Ove's tasks file.” [VERIFIED: /sandbox/home/refs/106-UPLIFT.xml:83-88] | Not inspected or modified; host integration is separately assigned. No dashboard restart here. |
| OS-registered state | CLI constructs pidfile using `base_home / ".omega-prime" / "dashboard" / "dashboard.pid"`. [VERIFIED: omega_prime/dashboard/__main__.py:220-225] | No rename/re-registration. Isolated host HOME only; no access to real pidfile. Other OS registrations were not inventoried because no identifier rename is proposed. |
| Secrets/env | Token lookup remains `env_vars=("GITHUB_PAT_DESK_LEAD",)`; default port reads `"OMEGA_PRIME_DASHBOARD_PORT"`. [VERIFIED: omega_prime/dashboard/collectors/github.py:85-87; omega_prime/dashboard/__main__.py:207-209] | No key rename or credential migration. Never print values; tests use synthetic fixtures. |
| Build artifacts | Vite writes `new URL('../static/cc', import.meta.url)` with `emptyOutDir: true`. [VERIFIED: omega_prime/dashboard/frontend/vite.config.ts:12-17] |04 then05 rebuild at ordered barriers, preserving licenses/fonts. No stale source-hand-edited generated bundle. |

## Common Pitfalls

1. **Smaller pages with old page ceiling:** 3×30 cannot reach100 merges. Dedicated closed scan bound and early-stop regression are mandatory; capacity itself must not produce a false partial banner. [VERIFIED: omega_prime/dashboard/github_data.py:11-14]
2. **Row-local failure accidentally poisons CI:** `_greptile` invokes its supplied error callback. Branch calls need local error capture, PR calls retain section behavior. [VERIFIED: omega_prime/dashboard/collectors/github.py:379-384]
3. **Rich terminal tasks vanish from detail:** removing terminal rows from active exposes only FinishedItem's smaller existing fields. Preserve existing detail evidence through the new terminal projection, and do not use duplicate active rows as a compatibility shim. [VERIFIED: omega_prime/dashboard/model.py:161-175,638-661]
4. **Idle IDs reset or are never numbered:** existing number_snapshot enumerates only active/waits/finished; add idle in01 and test transitions/restarts. [VERIFIED: omega_prime/dashboard/task_ids.py:118-133]
5. **Routing tests accept synthetic clicks but refresh fails:** current App initializes view with `useState<View>('overview')`, selected with `useState<string | null>(null)`. [VERIFIED: omega_prime/dashboard/frontend/src/App.tsx:10-17] Test initial deep link, remount, hashchange and actual Back/Forward separately.
6. **Empty results hide recovery controls:** current Tasks wraps its entire filter controls with ViewState empty handling. [VERIFIED: omega_prime/dashboard/frontend/src/views/Tasks.tsx:8-11] Keep bucket-clear/search/density controls available even when the list is empty.
7. **Terminal cap is not a subprocess-memory cap:** truncating completed stdout bounds published frames, not captured memory. Recommendation: bound reader buffering, terminate/kill timed-out process and reap it. [ASSUMED: A2 — exact subprocess capture strategy remains an internal design choice; tests must enforce output/time limits.]
8. **Terminal sanitizer clips before redaction:** boundary-spanning secrets and multibyte sequences escape or exceed cap. Redact full bounded capture, then final byte-safe clipping, and never stream stderr.
9. **A fixed CSS hash fails after a correct rebuild:** migrate the hardcoded test under04 custody as documented; do not weaken HTTP package-data coverage. [VERIFIED: omega_prime/tests/test_dashboard_app_fixes.py:80-84]
10. **Approved plan split becomes an accidental third wave:** document only the05-owned prerequisite artifact exception, and leave real05 execution in wave2. Never disguise that exception as04 writing05's file.

## Code Examples

### Existing injected-transport seam

DATA_Z9C3B6HR_START
```python
# Existing signature, not a new API:
def make_github_collector(
    *, env: Mapping[str, str] | None = None,
    transport: Callable[[str, Mapping[str, str]], tuple[int, bytes]] | None = None,
    now_fn: Callable[[], datetime] | None = None,
    cache_ttl: float = 90.0,
    repo: str | None = None,
) -> Callable[[], SourceResult]:
```
DATA_Z9C3B6HR_END
[VERIFIED: omega_prime/dashboard/collectors/github.py:70-76] Use URL/query-aware fake responses, deterministic clock advances beyond TTL, and fail on unexpected endpoints. Existing tests use injected transport and parse query state, proving no network is needed for regression construction. [VERIFIED: omega_prime/tests/test_dashboard_github_command_center.py:13-50]

### Existing event-source test technique

DATA_N5V8F1LP_START
```typescript
vi.stubGlobal('EventSource', FakeEventSource)
```
DATA_N5V8F1LP_END
[VERIFIED: omega_prime/dashboard/frontend/src/hooks/useDashboard.test.tsx:8-9] Extend the same fake with recorded URL, named terminal/state events and closed-count assertions. New terminal event names belong to05's proposed contract; do not import a product module that does not exist into baseline regression tests.

### Existing canonical numbering

DATA_A4R8D6TJ_START
```python
return f"{seq:05d} - {redact_value(title)}"
```
DATA_A4R8D6TJ_END
[VERIFIED: omega_prime/dashboard/task_ids.py:15-18] Render published display_id as the numbered title; never derive a new display sequence in frontend routing.

## State of the Art

No technology migration or dependency upgrade is justified. Replace only the old local patterns: in-memory navigation with locked fragment routes; unconditional synthetic branch concatenation with authoritative dedup; ambiguous branch Greptile labels with explicit provenance; active-only task iteration with deliberate active/idle coverage. These recommendations address opened existing implementations rather than a speculative ecosystem trend. [VERIFIED: omega_prime/dashboard/frontend/src/App.tsx:10-17; omega_prime/dashboard/frontend/src/views/CiCd.tsx:10-18,44-44; omega_prime/dashboard/task_ids.py:114-133]

## Validation Architecture

### Existing infrastructure and commands

The pytest source setting is `testpaths = ["omega_prime/tests"]`, `pythonpath = ["."]`. [VERIFIED: pyproject.toml:72-74] Frontend test config uses `environment: 'jsdom'`, `setupFiles: ['./src/test/setup.ts']`, `include: ['src/**/*.test.ts', 'src/**/*.test.tsx']`, `testTimeout: 10000`. [VERIFIED: omega_prime/dashboard/frontend/vite.config.ts:19-25] Build-assets test is gated by `process.env.CC_CHECK_BUILD !== '1'`. [VERIFIED: omega_prime/dashboard/frontend/src/test/build-assets.test.ts:11-12]

**The commands below are executable instructions for the outer execution/integration owner; none ran during research.** Suggested commands assume existing dependencies are restored. Durations are unmeasured; do not claim a quick command takes under30s until actually timed.

```sh
# Repository root — focused existing seams, then full requested dashboard gate
python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_dashboard_model.py omega_prime/tests/test_dashboard_task_ids.py omega_prime/tests/test_dashboard_snapshot_contract.py
python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_dashboard_github_command_center.py omega_prime/tests/test_dashboard_github_cache.py
python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_dashboard*.py
python3 -m pytest -q -p no:cacheprovider omega_prime/tests/ -k dashboard

# Run these from omega_prime/dashboard/frontend; do not run parallel builders
npm test -- --run
npm run build
CC_CHECK_BUILD=1 npm test -- --run src/test/build-assets.test.ts

# Repository root, only where configured/available
python3 -m ruff check omega_prime/dashboard omega_prime/tests/test_dashboard*.py
python3 -m mypy omega_prime/dashboard
polyglot-check --changed
python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_docs.py omega_prime/tests/test_public_repo.py
```

Existing package scripts/config ground the commands above; polyglot-check is contract-mandated but was not discoverable on PATH or by filename search. Do not invent a repository replacement. Supplied lint baselines contain `Found 27 errors.` and `Found 7 errors in 1 file (checked 23 source files)`, respectively. [VERIFIED: /sandbox/home/refs/baseline-ruff.txt:28-29; /sandbox/home/refs/baseline-mypy.txt:8-8] Those are supplied baseline observations, **not rerun results**. Compare later diagnostics to supplied baseline and distinguish unrelated broad-suite collection failures from feature regressions.

### Named regression matrix

All filenames/titles below are **proposed tests to author**, not tests observed passing. Existing test adaptations stay in their sole owner's custody. AC labels quote the context's acceptance indexing rather than invent tracker requirements. [VERIFIED: .planning/phases/106-command-center-polish/106-CONTEXT.md:35-37]

| Owner / requirement | Proposed executable node or Vitest title | Required behavioral assertion / baseline route |
|---|---|---|
|01 AC1/2|`test_dashboard_github_merges_polish.py::test_closed_pages_avoid_legacy_1_2mb_truncation`|Fake100-row closed response totals1.2MB; slice by actual per_page. Fixed code asks30 and receives valid under-cap JSON; baseline asks100 and gets its bounded truncated read. Assert actual merges and clean section, not merely absence of an error string.|
|01 AC1/2|`::test_oversize_response_reports_too_large_and_keeps_stale_merges`|Warm valid cache, advance clock, inject cap+1 body; assert reason, old rows, original fetched_at and stale flag. Test default _request via fake opener as well as injected transport so the real cap regression is covered.|
|01 AC1/2|`::test_closed_paging_stops_at_max_merged_rows_per_target`|More than100 merged rows available; call log proves stop after fourth30-row page, while both targets remain eligible. Closed-unmerged rows do not satisfy target.|
|01 model addendum|`test_dashboard_model_buckets.py::test_done_and_failed_are_finished_only`|Use normalize_tasks existing seam; neither state in active, both results correct. Existing state-order test must intentionally remove done/failed from active expectation.|
|01 model addendum|`::test_unknown_exited_and_quiet_sessions_are_idle_only`|Unknown pane, stopped matched session, missing live agent, quiet sandbox enter idle; live working/idle/blocked and working sandbox remain active; blocked wait retained.|
|01 model addendum|`::test_idle_changes_snapshot_hash_and_keeps_number_across_restart`|Use existing normalize_tasks, number_snapshot/poller; assert serialized idle, hash change and durable seq/timeline through active-idle-active. Baseline gets missing idle via dict.get, not missing dataclass attribute import.|
|02 AC3|`CiCd.dedup.test.tsx` — `prefers backend branch rows even when fallback SHA is stale`|Render existing CiCd; backend one+fallback same repo/branch produces one; differing branches remain distinct, PR remains; row evidence shows backend wins.|
|02 AC3|same — `adds fallback only when repository branch has no backend row`|No backend branch gives one fallback; repeated backend IDs cannot produce duplicate card/key warnings.|
|03 AC4|`test_dashboard_github_greptile_polish.py::test_branch_uses_merged_pr_score_and_actual_reviewed_sha`|Call existing collector with default inventory and validated head; fake commits/pulls association, then actual PR-head review; assert score, PR number, merged provenance and nonfabricated SHA.|
|03 AC4|`::test_direct_commit_is_not_applicable_and_lookup_failure_is_row_local`|Empty lookup versus403/offline/malformed association produce different row states; CI section stays clean, other rows healthy, token absent.|
|03 AC4|`CiCd.greptile.test.tsx` — `renders merged PR score, no PR, unavailable and unreported reviewed SHA`|Render existing CiCd for four cases; test0/5 as a real score, no four-unknown text. Construct review payload locally rather than importing new helper/type into baseline.|
|03 AC1|`CiCd.test.tsx` — `shows recent_merges failure with original successful section freshness`|Render existing CiCd with structural snapshot fixture: genuine recent_merges partial/stale reason too_large and older successful section fetched_at, newer aggregate fetched_at and still-newer receivedAt, healthy ci_cd/repositories and retained merges. Assert section/reason/stale/original-success notice plus clean rerender removal.03 passes cc.meta to04's shared ViewState githubMeta contract without editing shared files; base fails visible section/freshness behavior, not future helper imports.|
|04 AC5|`App.routes.test.tsx` — `opens and clears each bucket and restores initial hash`|Render existing App; parameterize all buckets, count/list parity, active-toggle/All, empty message, unknown notice; baseline uses App seam not new router import.|
|04 AC5|same — `preserves bucket origin through task detail refresh Back and Forward`|Encoded slash-containing technical ID, remount and browser hash history; no server navigation.|
|04 AC6|`TaskDetail.routes.test.tsx` — `opens numbered task detail with recorded links PR evidence wiring and events`|Navigate via App from sidebar/list/Overview; published path_or_url links render; explicit missing details remain honest; unknown task not-found.|
|04 idle addendum|`Overview.idle.test.tsx` — `excludes idle sessions from Active and renders the separate group`|Provide active live+idle payload, assert Active count unchanged and idle group visible in Overview/sidebar/Tasks. Access through existing components, not a new selector import.|
|04 AC6|`Tasks.density.test.tsx` — `toggles compact field rich rows without losing bucket controls`|Semantic controls/fields, not brittle pixel snapshots; empty bucket still allows clearing.|
|05 AC7|`test_dashboard_terminal.py::test_task_mapping_roundtrips_validation_collector_and_snapshot`|Existing write_tasks/read_tasks/collector/normalize seams; mapping survives real path; invalid pane rejected, explicit owner fallback only. Baseline silently strips field, which is a behavioral failure.|
|05 AC7|`test_dashboard_terminal_api.py::test_terminal_route_resolves_only_task_owned_pane_and_uses_read_argv`|Call existing create_app with controlled poller/current task on both revisions; send identical GET and assert HTTP200 before inspecting real emitted frames and exact runner argv. Actual base404 is missing-feature behavioral evidence, not collection/import failure or unsafe baseline output.|
|05 AC7|`::test_terminal_sanitizes_redacts_caps_and_sends_only_changed_frames`|SAME real create_app route-to-wire contract on both revisions: assert HTTP200 first, then inspect actual emitted sanitized frames from synthetic runner capture inputs containing ANSI/OSC/control/token/multibyte/long/repeated text. Base legitimately fails200 versus404 before safety assertions; no test-only emitter/sanitizer substitute and no invented unsafe output. Security assertions prove the actual polish implementation, not baseline vulnerabilities.|
|05 AC7|`::test_terminal_disconnect_releases_capacity_and_stops_reads`|SAME real create_app/raw ASGI contract, assert HTTP200 before lifecycle assertions; base404 establishes missing feature only. On polish controlled disconnect/timeout/saturation/recovery proves actual runner invocation cessation and cleanup. Missing executable and gone pane are distinct safe states.|
|05 AC7|`::test_terminal_has_no_input_route_or_subcommand`|Assert exact capture allowlist and all non-GET rejected, no input API accepted; this security invariant may already pass baseline, so pair with functional terminal regression rather than falsely claim old behavior was insecure.|
|05 AC7|`TerminalPanel.test.tsx` — `streams plain text and cleans reconnect on task change`|Use fake EventSource via existing App route for baseline; inspect rendered literal text, state changes, close/reconnect, paused scroll, copy and unmount. Do not make absence of TerminalPanel import the red signal.|

### Baseline transplant strategy

1. Outer driver prepares a **separate disposable checkout/worktree** at the supplied base; never reset the active polish worktree, touch7421 or use the real HOME. Baseline git command, to execute later: `git worktree add --detach /tmp/cc106-baseline ad3942b`. Check destination availability first and choose another temporary destination if occupied. No worktree command was run here.
2. Copy **tests and test-only fixtures/harnesses**, never polished production modules or generated assets, into that checkout. Restore the same approved locked dependencies/cached npm tree. Run the same focused nodes against baseline and polish and save stdout/stderr/exits. A nonzero import/collection/setup failure is not behavioral red evidence.
3. Merges tests import existing make_github_collector; realistic fake opener models the old bounded1MB read. Distinguish a1.2MB aggregate100-row page that fixed pagination avoids from a single genuinely oversized response that fixed code must classify too_large.
4. Model tests import existing normalize_tasks and use to_dict/get to observe absent idle without collection errors. Mapping round-trip tests import existing bridge and normalization APIs; baseline stripping the field is the assertion failure.
5. UI tests use App/CiCd/Overview/Tasks that exist on baseline and locally construct new-shaped JSON as a structural fixture; baseline must fail on count, route, wording or link behavior. Do not import future routing helpers or future TerminalPanel directly in transplanted tests. Existing App hook mocking pattern provides the fixture seam. [VERIFIED: omega_prime/dashboard/frontend/src/App.test.tsx:3-8]
6. Terminal integration harness mounts the existing create_app with a seeded poller snapshot and sends the SAME real route-to-wire GET on both revisions. Keep collection independent of future modules; detect the supported terminal_service parameter if needed to instantiate/inject the actual service with synthetic runner/clock inputs on polish, otherwise call the existing baseline signature. Assert HTTP200 before inspecting actual frames or lifecycle. Actual baseline404 legitimately proves the requested endpoint is absent, not that baseline emits unsafe text or violates caps. Never register a test route, transplant product code or supply an emitter/sanitizer replacement. On polish inspect real emitted frames for token/control/cap/changed-frame guarantees and actual disconnect cleanup. Direct helper/security tests against the actual new implementation are additional polish-only coverage, not transplantable FAIL-base claims.
7. Do **not** demand preexisting invariants become red: GET-only/no-input may already pass. Pair unchanged security checks with failing positive terminal behavior. Any new regression whose red status is required by context must have an observable failing assertion and a recorded baseline harness, not a blanket claim that every safety test failed before.

### Sampling / Wave 0 gaps

No continuous mid-flight checking. At settled authorized checkpoints, run focused owner tests; the single integration owner runs complete suites after all changes land.04/05 execute their one build each at the ordered barriers, then built-asset checks against that actual build. Phase gate requires honest full-suite outcomes plus separate live evidence; a focused pass cannot replace a failed broad collection.

Wave0 gaps: author proposed regression files; freeze link/wiring/review/pane payload shapes;05 authors its single prerequisite component; restore npm cache/tree if needed; provide lint/polyglot tooling through the approved environment. None of these gaps authorizes a sixth plan or a research-stage build.

## Security Domain

Configuration explicitly enables security at level1. [VERIFIED: .planning/config.json:10-13] Existing guards enforce method before Host and inject CSP/nosniff/referrer headers into successful/error responses. [VERIFIED: omega_prime/dashboard/app.py:23-27,144-190]

ASVS category names below follow the project's requested V2–V6 checklist taxonomy; they are not a claim of certification or an asserted current ASVS edition. [ASSUMED: A3 — confirm edition if formal compliance is requested.]

| Checklist category | Applies | Control / test |
|---|---|---|
| V2 Authentication | No new login; local boundary remains relevant | No external auth or token sent to browser; existing broker stays server-side. |
| V3 Session management | Browser login session not introduced | Stream lifetime/reconnect cleanup still tested; not an authenticated session system. |
| V4 Access control | Yes | Exact loopback Host, task-to-pane server resolution, no arbitrary pane/query override, no CORS expansion. |
| V5 Input validation | Yes | Bounded task ID decoding, positive PR number, existing safe reference rule, pane regex, escape terminal text and safe links. |
| V6 Cryptography | No new cryptography | Existing SHA256 identity registry unchanged; do not treat it as encryption. |

| Threat | STRIDE | Required mitigation |
|---|---|---|
| Arbitrary terminal disclosure | Information disclosure / elevation | Resolve exact authorized task record, validated pane only, no arbitrary browser pane selection. |
| Token exposure in pane text | Information disclosure | Server strip/redact/cap, no raw stderr/logs, synthetic token matrix. |
| ANSI/HTML active content | Tampering | OSC/CSI/control stripping plus escaped pre text, CSP unchanged. |
| Stream/subprocess exhaustion | Denial of service | Concurrent cap, short runner deadline, connected-viewer-only polling, cleanup on every exit. |
| DNS rebinding/cross-origin reading | Spoofing / information disclosure | Existing Host/method/CSP guards remain outermost on new route. |
| Redirected GitHub credential request | Information disclosure | Existing `_NoRedirect` and API-host-only GET policy preserved. [VERIFIED: omega_prime/dashboard/collectors/github.py:39-61] |

## Environment Availability

Observed via version/installed-metadata commands only, not test/build execution:

| Dependency | Available | Observation / fallback |
|---|---|---|
| Node / npm | Yes | v24.21.0 /11.19.0 from version commands; do not infer every package engine constraint was checked. |
| Python | Yes |3.12.3; installed metadata finds pytest9.1.1, Starlette1.7.0, Uvicorn0.54.0, httpx0.28.1. |
| Frontend node_modules | Not found by tool glob at discovery | Restore existing lock from approved cache; no network dependency during test execution. Cache completeness unmeasured. |
| Herdr | No command on PATH | Offline injected runner; real pane stream and five screenshots require host handoff. Supplied help documents `herdr pane read [OPTIONS] <PANE_ID>`, source `recent`, format `text`. [VERIFIED: /sandbox/home/refs/herdr-pane-read-help.txt:3-19] |
| Ruff / mypy | Not installed in observed Python metadata/PATH | Execution environment must provide pinned dev tools; do not falsely report lint green. |
| polyglot-check | Not on PATH; no matching repository filename discovered | Host/tooling prerequisite for mandated changed-files gate; no fabricated replacement. |
| Context7 MCP / ctx7 | Not exposed / no CLI on PATH | Used provider seam then official-source WebSearch fallback; direct reads of official pages failed to connect. |
| Live GitHub/Herdr/7441 | Not exercised | Outer host verification only; never use7421 to close evidence gaps here. |

All environment rows except separately cited host-help facts derive from the recorded commands in Sources and discovery tool observations. [VERIFIED: session runtime/version metadata observations]

**Missing dependencies with fallback:** Herdr for offline behavior, Context7 for documentation lookup. **Missing execution prerequisites:** npm dependency restoration/cache, lint/type tools and polyglot-check; the planner must assign environment preparation or explicit host gates, not silently omit required acceptance. No dependency install was attempted here.

## Assumptions Log

| # | Claim | Section | Risk / handling |
|---|---|---|---|
| A1 | Precise live-agent meaning for missing host agent metadata is not established by scrubbed shape alone. | Model audit | Test present-status/stop-reason cases; retain non-OMP working panes; confirm ambiguous live host cases in host verification. |
| A2 | Bounded subprocess reader strategy is an internal implementation recommendation, not measured behavior. | Pitfalls |05 must prove buffering/time/cancellation behavior with fake runner/process tests. |
| A3 | Checklist category labels do not establish current ASVS-edition conformance. | Security | Confirm edition only if formal compliance becomes in scope; no certification claim. |

The approved product defaults are **not** reopened assumptions.30-row page size staying under1MB is not guaranteed: honest too_large handling remains mandatory regardless of fixture/live observations. [VERIFIED: /sandbox/home/refs/106-UPLIFT.xml:91-96; .planning/phases/106-command-center-polish/106-CONTEXT.md:11-11]

## Open Questions / Explicit Evidence Limits

- No blocking product decision remains. The terminal prerequisite exception is explicitly approved and must be represented in plan05 task1 plus04 read-first/integration conditions.
- Host-specific Herdr failures do not yet provide verified closed-pane stderr/status distinctions. Fake outcomes can prove UI handling, but exact host classification must be confirmed without exposing raw stderr. Do not mark every nonzero read as pane-closed if Herdr itself is unavailable.
- Registry currency, publish dates and engine compatibility were not researched because no package installation/upgrade is designed. Existing declared versions are reported literally, not certified current.
- Live GitHub clean-banner/merge parity, terminal load/redaction with real output, visual density/AA and five screenshots remain host evidence. None is a planning-stage success claim.

## Sources

### Opened repository sources (HIGH confidence for quoted definitions)

- Backend: collector/github_data, model, poller, task_ids, app, status_file, tasks_json, Herdr and redact modules, cited at each finding.
- Frontend: App/types/selectors/links, hook and tests, Overview/Tasks/CiCd/detail/sidebar/cards, package/vite configuration and build-assets tests, cited at each finding.
- Phase105 plans01–05 were read at their ownership/interface/verification sections; they establish existing integration conventions, not overrides of the new approved addendum.
- Full supplied106 uplift, all supplied sibling reference files, current106 context, roadmap and planning config were opened. Source paths and discrete values are quoted beside verified findings; proposed paths/symbols/tests are explicitly marked proposals.

### Official protocol references (MEDIUM)

- [GitHub commit-associated PR endpoint](https://docs.github.com/en/rest/commits/commits?apiVersion=2022-11-28): surfaced by WebSearch with official source attribution; direct page read failed. Default-branch commit association behavior supports the locked lookup design. [CITED: https://docs.github.com/en/rest/commits/commits?apiVersion=2022-11-28]
- [MDN SSE](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events): framing, text/event-stream and close lifecycle surfaced via official-source search; direct read failed. [CITED: https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events]
- [MDN hashchange](https://developer.mozilla.org/en-US/docs/Web/API/Window/hashchange_event): native fragment navigation surfaced via official-source search; direct read failed. [CITED: https://developer.mozilla.org/en-US/docs/Web/API/Window/hashchange_event]

The research-plan seam selected jina; no jina MCP was exposed, so the documented fallback used WebSearch. Confidence seam returned `MEDIUM` for `--provider websearch --verified`; it returned LOW for webfetch, so failed direct reads were not promoted to verified facts. Digests were cached via research-store under the three returned keys. Graphify status reported disabled; no graph context was fabricated. Configured agent-skills query returned no skills block.

### Executed metadata commands (not verification gates)

- `node .claude/gsd-core/bin/gsd-tools.cjs query init.phase-op 106` and agent-skills/graphify/confidence queries; exit0 for combined metadata call. Init returned expected phase directory, no existing research, commit_docs false at that time.
- `node --version`, `npm --version`, `python3 --version`: observed versions above. The following combined `command -v herdr ctx7 uv ruff mypy` returned1 with no paths; this is recorded as missing commands, not a test failure.
- `python3 -c "import importlib.metadata as m,shutil,json; names=['pytest','starlette','uvicorn','httpx','ruff','mypy']; found={d.metadata['Name'].lower():d.version for d in m.distributions()}; print(json.dumps({'packages':{n:found.get(n) for n in names},'commands':{n:shutil.which(n) for n in ['herdr','ctx7','polyglot-check','uv','ruff','mypy']}}))"`: exit0, installed metadata/PATH observations reported above.

## Metadata

**Confidence breakdown:** Standard stack HIGH for opened declarations and observed installed metadata, not registry currency; architecture HIGH for existing boundaries and approved ownership, proposed terminal implementation unexecuted; pitfalls HIGH for source contradictions and supplied baseline facts, MEDIUM for protocol references. **Research validity:** repository findings apply to the opened source snapshots; refresh after relevant source changes. No production code, test source, static build or dependency lock was changed by this research. Tests/build/lint and baseline transplantation remain unverified by instruction, as does rule adherence by future agents.
