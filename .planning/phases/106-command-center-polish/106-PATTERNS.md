# Phase 106: Command Center polish — Pattern Map

**Mapped:** 2026-10-21 · **Directive:** `orchestrate workflowz ultrathink`

Planning-only copy points, not implementation or verification evidence. Read locked CONTEXT, complete UPLIFT, model addendum and UI-SPEC; no `106-RESEARCH.md` existed at discovery or the final pre-write check. This map complements research/design rather than restating their acceptance criteria. All existing analog paths below were confirmed tracked with `git ls-files`; no installed/runtime mirrors are analogs. Ownership of this artifact resolves through `omega_prime/ownership.yaml` `**` to `bot-00-omega-prime` (last match wins).

## File Classification

Paths below are relative to `omega_prime/dashboard/`, except explicitly prefixed test paths. New module names are proposals for the planner, not claims that those files already exist.

| New/modified surface | Role | Data flow | Closest tracked analog / copy point | Match |
|---|---|---|---|---|
| `collectors/github.py`, `github_data.py` | service / utility | bounded request-response / transform | Same files: `_request`, `make_github_collector`, `merge_row`, `_greptile` | exact |
| `model.py` | model | transform | Same file: `Snapshot`, `normalize_tasks`, terminal `omega_job` branch | exact |
| `task_ids.py` | service | file-I/O / transform | `number_snapshot`, `_record`, `_observe` | exact |
| `poller.py`, `app.py` empty payload | service / controller | batch / request-response | `_build_snapshot`, `_empty_snapshot` | exact |
| `status_file.py`, `collectors/tasks_json.py` | model / service | file-I/O / validation | `_validated`, `make_tasks_json_collector` | exact |
| New `terminal.py`; terminal route in `app.py` | service / route | subprocess-I/O / streaming | `collectors/herdr.py::_default_runner`; `app.py::_SSEResponse`, `create_app.stream` | role + flow, split analog |
| `frontend/src/types.ts` | model | transform | Existing `Task`, `Wiring`, `Snapshot`, `CiRow` | exact |
| `frontend/src/App.tsx`; new small hash-route utility/hook | component / utility / hook | event-driven | `App` state boundary; `hooks/useDashboard.ts` effect cleanup | role-match; no router analog |
| `frontend/src/lib/selectors.ts` | utility | transform | `taskSelection`, `selectedTask`, `observedActivity`, `correlateWiring` | exact |
| `frontend/src/views/{Tasks,Overview}.tsx`, `components/{CommandSidebar,SummaryCards,TaskDetail}.tsx` | component | event-driven / transform | Existing same-named components | exact |
| `frontend/src/views/CiCd.tsx` | component | transform | `CiCd` owns row assembly; private `CiCard` owns evidence wording | exact |
| New `frontend/src/components/TerminalPanel.tsx` | component | streaming / event-driven | `hooks/useDashboard.ts`; `TaskDetail` panel structure | role + flow, split analog |
| `frontend/src/styles.css` | config | presentation transform | Existing panel/list/notice/responsive classes; UI-SPEC defines changed layout | exact |
| Python regression files listed below | test | request-response / transform / streaming / file-I/O | Existing fixtures, fake transport/runner and raw ASGI drivers | exact |
| Frontend regression files listed below; new terminal/router tests | test | event-driven / streaming / transform | Vitest + Testing Library + `FakeEventSource` | exact / role-match for new files |
| `static/cc/**` | generated config/assets | batch | Generated output only, never a source analog | generated |

## Pattern Assignments

### 1. Collector bounded transport, cache and review enrichment

**Sources:** `omega_prime/dashboard/collectors/github.py` and `omega_prime/dashboard/github_data.py`.

- Imports already separate transport/projection/model/redaction (`github.py:22–28`); keep `SourceResult` and pure projection helpers rather than introducing a second HTTP client.
- `_request` (`52–61`) constructs GET, checks the API origin, uses the no-redirect opener and 2.5-second timeout. Its current `response.read(1_000_000)` is the defect, **not** a safe bound-detection recipe: implement bounded limit+1 detection there and preserve a distinct `too_large` classification through `get`/`fail` (`118–179`). Exercise the real `_request` with a fake opener too: injected transport alone cannot prove the production read length.
- Reuse the shared `get` accounting, not another network path (`118–125`):

```python
if not url.startswith(API + "/") or time.monotonic() >= deadline:
    raise RuntimeError("budget")
with budget_lock:
    if budget[0] <= 0:
        raise RuntimeError("budget")
    budget[0] -= 1
```

- `pages` (`136–152`) and the closed-PR call (`274–286`) are the edit seams. `github_data.py:8–14` defines `MAX_MERGES=100`, `MAX_PAGES=3`, `PAGE_SIZE=100`; the smaller closed-page limit must use its own effective page size consistently for query, slicing and short-page detection. `merge_row` (`70–76`) rejects merely closed, unmerged PRs. Stop on collected **merged** rows, not raw closed rows; reaching the requested result limit is not itself a fetch error.
- Preserve repository-local cached replacement (`github.py:337–350`):

```python
if partial["recent_merges"] and isinstance(previous_cc, dict):
    old_merges = previous_cc.get("recent_merges", [])
    if failed_merge_repos:
        cc["recent_merges"] = [item for item in cc["recent_merges"] if item.get("repo") not in failed_merge_repos] + [item for item in old_merges if item.get("repo") in failed_merge_repos]
    else:
        cc["recent_merges"] = old_merges
```

- `make_github_collector` retains TTL/token/deadline behavior; publication (`353–356`) redacts both rich and legacy records with the actual token in `extra=(token,)`. Keep original section `fetched_at` and stale state on genuine failure.
- Branch construction is inside the validated-head block; `_greptile` (`362–398`) already accepts `get`, `on_truncate`, `on_error`. For merged-PR enrichment use **row-local callbacks**, not `mark_ci_failure` / `mark_ci_truncated`, whose current semantics mark the entire section partial. Preserve those semantics for existing open-PR review failures. Reuse `_SAFE_REF`, positive-int/non-bool PR validation and `_safe_url` (`405–410`). Body-marker fast path currently returns `reviewed_sha=None`; change only when evidence permits a SHA, never infer a head match from absent evidence.

### 2. Idle/finished classification and every identity consumer

**Source:** `omega_prime/dashboard/model.py`.

- `Task.to_dict` (`94–120`) and `Snapshot.to_dict` / `content_hash` (`185–200`) are the redacted serialization boundary. Add `idle_sessions: list[Task]` through this boundary; a frontend-only filter cannot correct API counts or identity allocation.
- Copy terminal-vs-active control flow from the `omega_job` loop: append `FinishedItem` and `continue` when the run has a result. Apply that structure to the `tasks_json` loop (`611–662`), which currently appends every task to active and separately finishes only done. Preserve canonical identity (`task_id` containing `:` unchanged, otherwise `tasks:{task_id}`), done→ok and failed→failed.
- Herdr classification belongs after the pane/session join (`258–311`); matched sessions are marked claimed, so moving a stopped pane must not accidentally also recreate it as an unmatched finished session. Preserve the blocked waiting derivation and its `related_task_id`. Quiet sandbox E2E classification belongs in its own loop (`432–448`), not a blanket rule moving all quiet/idle tasks. Existing unmatched `omp_session` quiet behavior is intentionally different.
- Audit positional `Snapshot(...)`: normalization returns `Snapshot(active, prs, waiting_list, finished)` (`719`) and tests use this order. Append the new field or convert affected callers deliberately; do not silently shift positional meanings.

**Consumers that must be updated or intentionally retained:**

| Consumer | Existing behavior / required disposition |
|---|---|
| `task_ids.py:number_snapshot:114–137` | `rows = [*snapshot.active, *snapshot.waiting_on_ove, *snapshot.finished]`; include Idle Task rows in the same identity registry, sorted allocation, numbering and timeline observation. No second registry. |
| `poller.py:_build_snapshot:177–232` | Builds through `normalize_tasks`, optional `number_snapshot`, `snapshot.to_dict()`, recursive redaction, canonical hash. New serialized group flows automatically; add publication/hash regression rather than duplicate assembly. |
| `app.py:_empty_snapshot:193–201` | Explicit empty API shape must include Idle. |
| `frontend/src/lib/selectors.ts:3–35` | Identity lookup/activity must see Idle without placing Idle in Working or duplicating All rows; waiting keys currently use `wait:` and task keys `task:`. |
| `frontend/src/views/Overview.tsx:11–12` | Current terminal exclusion omits `failed`. Share bucket selectors/counts, not another inline state predicate. |
| `frontend/src/views/CiCd.tsx:18` | Correlation currently sees active+finished only; deliberately include Idle where wiring evidence remains relevant, coordinated with the CiCd owner. |
| `frontend/src/components/CommandSidebar.tsx:7`, `views/Tasks.tsx:9` | Both derive selection from `taskSelection`; separate Idle disclosure from main queue and keep Idle detail lookup. |
| `static/app.js:82` legacy active table | Intentionally continue displaying only backend Active; no need to re-add Idle into Active or redesign legacy UI. |

`test_dashboard_model.py::test_tasks_json_state_mapping_and_order` (`156–178`) currently asserts the defective done/failed-in-active behavior and must change. Exact-key contracts needing the additive field: `test_dashboard_model.py::test_empty_sources_and_hash_stability`, `test_dashboard_snapshot_contract.py::test_persisted_task_numbering_reaches_snapshot`, `test_dashboard_app.py::test_static_snapshot_and_health_headers`.

### 3. Hash routing replaces the current React state boundary

**Source:** `omega_prime/dashboard/frontend/src/App.tsx:10–17`:

```tsx
const dashboard = useDashboard()
const [view, setView] = useState<View>('overview')
const [selected, setSelected] = useState<string | null>(null)
const snapshot: Snapshot = dashboard.data ?? {}
```

Replace view/selection ownership here with parsed hash state; retain one dashboard subscription and the existing shell. Search/density/disclosure are local presentation state, not additional competing route state. No existing hash router was found: do not describe current callbacks as history-aware.

- `selectors.ts:3–24` is the copy point for stable identity Map dedup and lookup. Existing keys are **not raw task IDs**: e.g. fixture raw `worker/1` becomes selection key `task:worker/1`. Define one explicit route/raw-identity conversion; encode/decode once, resolve actual rows, preserve waiting related-task behavior and PR-gate record shape. Do not guess task IDs from sequence numbers or PR numbers.
- `SummaryCards.tsx:4–6` is presently static `<article>` rendering. `CommandSidebar.tsx:5–8` and `Tasks.tsx:7–11` currently accept selection/view callbacks. Migrate all these consumers to real hash destinations and the shared bucket contract; keep skip-link `#main-content` working without resetting navigation.
- `TaskDetail.tsx:4–13` already discriminates Task/WaitingItem/FinishedItem using `'state' in item` / `'ask' in item`; reuse the definition-grid, timeline and safe-link rendering in the dedicated detail page. Its current null state means “Select a task”, not route-level “Task not found”. Preserve loading before asserting unknown ID.
- `selectors.ts:37–46::correlateWiring` demonstrates **evidence-only, ambiguity-rejecting** joins: repository, branch, corroborated SHA; only a unique match is returned. It is not a ready-made PR-link detail join. Extend deliberately for explicit PR URLs/unambiguous repo+branch and retain source provenance.
- Metadata seams requiring explicit projection: Python `_wiring` omits worktree; `types.ts:3` omits it too. `status_file.py:_links:63–76` produces `path_or_url`, but `types.ts:2::LinkRecord` / TaskDetail expect `url`. `tasks_json` normalization currently supplies no model/plan. Reconcile reported fields at the existing projection/type boundary; never fabricate missing evidence.

### 4. CI/CD source precedence belongs in `CiCd`, not `CiCard`

**Source:** `omega_prime/dashboard/frontend/src/views/CiCd.tsx:9–18,26,31–44`.

```tsx
const detailedRows = cc?.ci_cd ?? []
// Current defect after constructing branchRows from repositories:
const rows = [...branchRows, ...detailedRows]
```

`CiCd` assembles rows and React keys; private `CiCard` formats one row. Plan 02 changes assembly/tests only; plan 03 changes review evidence/wording. Prefer backend branch rows by **repository+branch**, regardless of stale/current SHA mismatch; only synthesize when that pair has no backend branch. Do not use SHA as the fallback eligibility key, and do not deduplicate away PR rows. Keys must distinguish PR vs branch and remain unique after mixed cached/current input. Existing `CiCard` `review?.score != null` preserves zero correctly; retain that convention while replacing its four-unknown sentence.

04 owns the optional `ViewState.githubMeta?: GitHubMeta` shared section-notice input and migrates Overview/Repositories;03 owns the CiCd caller migration after04, passing the full `cc?.meta` including sections read-only. Section identity, actual reason and original successful section fetched_at/stale evidence must survive; aggregate meta.fetched_at/receivedAt cannot replace section freshness.03's local CiCd.test.tsx title `shows recent_merges failure with original successful section freshness` uses a genuine recent_merges failure with distinct section/aggregate/received timestamps plus clean rerender.03 edits no shared ViewState/types/selectors or05 files and never builds assets.

## Shared Patterns: SSE, Guards, Validation and Tests

### Existing server stream and subprocess boundaries

- `omega_prime/dashboard/app.py::_DashboardGuards:127–190` wraps all routes with exact Host, GET-only and security headers. The actual server is **Starlette/ASGI**, not the uplift's approximate “stdlib server” description. Register the terminal GET within `create_app`; preserve `_SECURITY_HEADERS`, including `connect-src 'self'`, without a new origin or SPA fallback.
- Copy `_SSEResponse` (`30–124`) for disconnect-vs-next-event cancellation and no Content-Length. `create_app.stream` (`308–336`) provides named-event framing, changed-data suppression and heartbeats:

```python
yield "retry: 3000\n\n"
# Existing snapshot event wire format:
yield f"event: snapshot\ndata: {json.dumps(current, separators=(',', ':'), default=str)}\n\n"
# Existing heartbeat wire format:
yield ": heartbeat\n\n"
```

The terminal event name/payload is new: define it once for server, panel and fake EventSource; do not subscribe only to `onmessage` when emitting named events. Stream-slot release/capture shutdown belong in lifecycle cleanup; existing SSE response cancellation alone is not evidence that the terminal's own resources were released.
- `collectors/herdr.py:16–22` is the argv/timeout/no-shell runner seam; `make_herdr_collector:90–119` injects `Callable[[list[str]], str]` and maps missing executable/timeout/OSError to safe states. Copy the injection style, **not** its snapshot command or snapshot-specific error text. The terminal uses the contract's exact `pane read` argv, server-resolved validated pane only, connected viewers only and bounded concurrency.
- `status_file.py:_validated:103–119` explicitly allowlists task fields. New `herdr_pane` must be validated there (`^w\d+:p\d+$`) or it is silently dropped; `collectors/tasks_json.py:42–53` already forwards validated task rows. Extend `Task.to_dict` too. No bridge population or real tasks-file writes.
- `redact.py:60–89::redact_value` recursively scrubs mappings/text, named env values and secret assignments; reuse it before SSE emission. `redact_line` (`92–98`) clips to one 120-character line and is **not** a terminal sanitizer. ANSI/control stripping, terminal token coverage including JWT-like text, last-200-line and UTF-8 byte caps need new terminal-specific handling. Assert final emitted redacted bytes stay within 64 KB without cutting a secret before matching it. Render as a React text child in `<pre>`, never HTML.
- Frontend `hooks/useDashboard.ts:17–85` shows effect-scoped EventSource, named listener, disposed guard, close-on-error, reconnect timer and cleanup. Reuse lifecycle discipline for `TerminalPanel`, **not** snapshot polling as a fallback terminal transport; close when task changes/unmounts. Auto-scroll pause does not pause the stream. `lib/links.ts::safeHref:1–11` is the existing UI URL guard; backend GitHub projections retain stricter `_safe_url`.

### Exact regression conventions to reuse (not newly executed)

All Python paths below are under `omega_prime/tests/`; frontend paths under `omega_prime/dashboard/frontend/src/`.

| Existing test node/title | Concrete convention / adaptation |
|---|---|
| `test_dashboard_github_command_center.py::test_default_scope_projects_org_pr_branch_checks_and_actual_merges` (`13–68`) | Nested `transport(url, headers)`; `urlsplit`/`parse_qs`; bytes JSON; fixed UTC `now_fn`; record calls; raise on unexpected URL. Add explicit commit→pulls fixtures before broad `/pulls` handling. Existing body-SHA-null expectation at line 61 must change only where SHA becomes derivable. |
| `test_dashboard_github_cache.py::test_ttl_403_and_missing_token_preserve_original_successful_section_data` (`9–48`) | Mutable `now=[datetime(...)]`, `mode=[...]`, advance 91 seconds; assert cache identity/no extra requests, retained rows, section timestamp/stale and no fixture token leakage. Reuse for `too_large` warm-cache failure. |
| `test_dashboard_github_cache.py::test_one_target_failure_retains_only_that_repository_while_other_refreshes` (`82–110`) | Repository-local stale/current mixing, not wholesale fallback. `test_greptile_request_failure_is_partial_not_missing_review_evidence` (`112–144`) protects existing PR failure semantics; branch-local unavailable must not weaken it. |
| `test_dashboard_model.py::test_pane_session_pid_join_and_redaction` (`34–70`) | `source(name, items, extra)` helper (`14–15`), fixed `NOW`; pane `session_path` joins an omp session; assert identities/state/projection. Use aged timestamps, stop_reason and live-agent fixtures for Idle vs Working, not wall-clock sleeps. |
| `test_dashboard_task_ids.py::test_number_snapshot_numbers_derived_rows_and_records_observed_changes` (`86–110`) | `tmp_path / 'tasks.json'`; direct Snapshot + numbering; repeated observation doesn't duplicate timeline, state change does. Extend across Active↔Idle moves and poller restart with the same identity. |
| `test_dashboard_snapshot_contract.py::test_persisted_task_numbering_reaches_snapshot` (`9–42`) | Inject `poller._results`, `_build_snapshot`, restart using same temporary registry; exact public payload shape. Prove Idle numbering/publication/hash, not just dataclass presence. |
| `test_dashboard_process_collectors.py::test_herdr_snapshot_allowlist_redaction_and_invocation` (`90–141`) | Fake `runner(argv)` records calls, returns fixture text; assert exact argv and secret absence. Terminal test must assert only `['herdr','pane','read',pane,'--source','recent','--lines','200','--format','text']`, never input subcommands. `test_default_runners_pass_exact_timeouts` (`202–230`) monkeypatches subprocess.run to inspect kwargs offline. |
| `test_dashboard_redaction.py::test_assignment_secrets_are_redacted_in_strings_and_nested_values` (`110–131`) | Table of value/expected pairs; test nested values, idempotence and benign strings. `SECRETS` (`15–23`) generates synthetic values with `token_hex`; extend terminal tests with ANSI-wrapped/token-split text, multibyte caps and JWT-like strings. Never use real secrets. |
| `test_dashboard_app.py::test_sse_emits_retry_snapshot_and_update` (`320–412`) | Raw ASGI scope + receive/send + asyncio Queue driver; TestClient buffers whole SSE bodies. Inspect event/data/heartbeat without network or a real server. `test_method_and_host_guards` (`105–127`) covers exact Host and every non-GET method; extend to terminal route. |
| `test_dashboard_app_fixes.py::test_sse_disconnect_ends_waiting_stream_without_external_cancellation` (`96–138`) | `asyncio.Event` triggers real `http.disconnect`, asserts no Content-Length and final body; extend with terminal runner-call cessation and released stream capacity. |
| `views/CiCd.test.tsx` / `CI/CD > shows PR checks, default-branch evidence and labels unsupported wiring unknown` | `render(<CiCd snapshot={...} data={...} loading={false} error={null} receivedAt={Date.now()} live refresh={vi.fn()} />)`; role/text queries. Current fixture has a PR plus fallback, not duplicate backend branch evidence: add same-pair same/different-SHA branch fixtures, preserve PR assertion, count headings/cards and check provenance. |
| `lib/selectors.test.ts` / `dashboard selectors > deduplicates active and finished identities and preserves server numbering` | Pure selector assertions using shared `test/fixtures.ts`; fixture deliberately duplicates `worker/1` across active/finished. Add Idle and heterogeneous Waiting/PrRow fixtures; preserve numbering rather than frontend renumbering. |
| `App.test.tsx` / `Command Center navigation > renders all views and changes views without document navigation` | Mock `useDashboard`, Testing Library `fireEvent`/`waitFor`, assert pathname unchanged. Add hash reset in beforeEach; set deep link **before** render; dispatch `hashchange` inside `act`; exercise Back/Forward and origin preservation. Migrate button queries when navigation becomes links. |
| `views/Tasks.test.tsx` / `Tasks > filters by title without changing canonical numbering and selects detail inline`; `views/Overview.test.tsx` / `Overview > shows real counts and honest absence of observed merge events` | Existing component test props/fixtures are copy points, not acceptance of inline-only detail. Update route expectations; assert Working excludes done/failed/Idle, Idle group renders and resolves detail. |
| `hooks/useDashboard.test.tsx` / `useDashboard transport > fetches once and consumes the named snapshot SSE event` (`5–9`) | `FakeEventSource` listener Map, `vi.stubGlobal`, `act`, `renderHook`, unmount→closed; fake timers for failure. Extend fake to record URL and emit the terminal event name/status, then test reconnect, task change, cleanup, plain-text rendering, pause/copy. Restore timers/globals in afterEach. |

New regressions must be named explicitly by each PLAN and transplanted against `ad3942b` using existing public entrypoints/minimal baseline harness. A missing new terminal/router import is not a behavioral failure. Terminal API nodes keep the SAME real create_app→GET→raw ASGI wire contract on both revisions and assert HTTP200 before frame/lifecycle assertions: actual baseline404 is legitimate missing-feature red evidence only. Fake runner inputs/clock, never a route/emitter/sanitizer implementation; no invented unsafe baseline output. Test fixtures may detect supported production injection parameters without importing future modules at collection. Sanitizer/cap/lifecycle assertions inspect the actual polish implementation; additional direct helper/security tests are polish-only coverage, not transplantable FAIL-base claims. No tests, builds, lint, live services or browser verification ran for this map; supplied baseline lint/type failures remain inputs, not rerun results.

## No Exact Analog Found / Integration Boundaries

- Hash routing/history/origin restoration has no existing implementation; use the approved UI-SPEC route contract, with App as the replacement seam.
- Terminal sanitization/stream capacity/task→pane resolver and terminal payload states are new behavior. Reuse the two split analogs above, not a terminal emulator or a second server.
- Keep exactly five plans/two waves. `CiCd.tsx` crosses 02→03; collector crosses 01→03; model crosses 01→05. Coordinate Idle correlation edits in CiCd with its owner. 04 owns wave-1 generated assets after 02 integration; 05 owns wave-2 assets after 03 integration. 04 must have a buildable terminal mount contract without importing a missing 05-owned file or claiming a placeholder is live capture.
- Main planning handoff owns the receipt for this artifact; record runtime/live GitHub/live Herdr/screenshots and `rule adherence by future agents` as unverified where not exercised.
