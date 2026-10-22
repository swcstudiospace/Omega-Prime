---
phase: "106"
slug: "command-center-polish"
status: draft
shadcn_initialized: false
preset: none
created: "2026-10-21"
---

# Phase 106 — UI Design Contract

> Planning-only visual and interaction contract. Implementation, regression execution, browser verification and checker approval are not claimed by this document. Downstream stage directive: `orchestrate workflowz ultrathink`.

## Authority and Scope

The approved [106-CONTEXT.md](106-CONTEXT.md), complete [106-UPLIFT.xml](106-UPLIFT.xml), and `/sandbox/home/refs/ADDENDUM-model-buckets.md` are authoritative. Defaults c1–c5 are approved; no design questions remain. The context's stronger repository+branch de-duplication rule takes precedence over the uplift's narrower repository+branch+SHA wording, including stale/current mixtures. The addendum's Idle separation takes precedence over the current Active calculation.

Retain the existing white/purple Command Center, local fonts, sidebar, panels, source-health presentation and responsive shell. This is not a redesign, package migration or new data-source project. `.planning/REQUIREMENTS.md` describes older v16 phases and supplies no additional phase-106 visual decisions. No phase-106 RESEARCH.md existed at this contract's initial discovery; repository source and the approved context supply the existing-stack evidence.

Preserve exactly five implementation plans and two waves: wave 1 = 106-01 / 106-02 / 106-04; wave 2 = 106-03 / 106-05. Model classification belongs to 01; de-duplication to 02; review wording to 03; navigation, detail, density and Idle presentation to 04; terminal to 05. This spec creates no sixth plan. Respect the context's static-asset integration ownership: 04 builds after 02 integration; 05 builds after 03 integration. Plan 04 must coordinate its terminal mount with the 05-owned component without importing a missing module or claiming a temporary panel implements live capture.

## Design System

| Property | Value |
|----------|-------|
| Tool | none — existing manually authored React/CSS system, retained |
| Preset | not applicable |
| Component library | none; reuse repository components and semantic HTML |
| Icon library | none; retain existing text marks with decorative marks hidden from assistive technology |
| Font | DMSans for body/control text, Anybody for headings, SpaceMono for IDs, timestamps and terminal; existing local WOFF2 assets only |
| Styling | Existing `src/styles.css`, Tailwind import and Vite integration; use existing panel, notice, field, state-pill and focus patterns |
| Initialization gate | React/Vite detected; no `components.json` found. Approved no-redesign/no-unapproved-scope defaults select the existing manual system, not shadcn initialization. No dependency install, preset command or question is required. |

Evidence: `omega_prime/dashboard/frontend/package.json`; `src/styles.css`; `src/components/{CommandSidebar,SummaryCards,TaskDetail,ViewState}.tsx`; `src/views/{Overview,Tasks,CiCd}.tsx`; `src/lib/{selectors,time}.ts`; `src/types.ts`. Existing CSS defines local fonts, `--purple`, `--purple-dark`, `--ink`, `--muted`, `--line`, `--panel`, `--green` and `--red`. Filesystem discovery found neither project CLAUDE.md nor `.claude/skills/` / `.agents/skills/`; the read-only configured-skills query returned no skills. No installed design-system inventory is asserted; Component Inventory is intentionally omitted for `Tool: none`.

## Spacing Scale

| Token | Value | Usage |
|-------|-------|-------|
| xs | 4px | State-pill internal spacing; label-to-value gap |
| sm | 8px | Compact row padding; inline metadata and control gaps |
| md | 16px | Comfortable row padding; metadata groups |
| lg | 24px | New detail sections and vertical section gaps |
| xl | 32px | Major content separation |
| 2xl | 48px | Large empty-state breathing room, not routine row gaps |
| 3xl | 64px | Reserved page-level space; not added around compact lists |

Exceptions, all multiples of 4: retain existing 20px panel padding and use 12px for wrapped control gaps when needed. New compact controls have a minimum 44px hit height; comfortable rows have a minimum 64px height. Compact rows have a minimum 44px height, not a fixed height that clips wrapping content. Existing shell dimensions, borders, radii and untouched spacing remain unchanged; this is not a whole-stylesheet normalization.

## Typography

Exactly four sizes and two weights for new or materially changed phase surfaces:

| Role | Size | Weight | Line Height |
|------|------|--------|-------------|
| Body | 14px | 400 | 1.5 |
| Label / metadata / terminal | 12px | 400; 600 only for control labels | 1.5 |
| Heading | 20px | 600 | 1.2 |
| Display / numbered task title | 28px | 600 | 1.2 |

Use 600 for row titles and primary control labels at the corresponding size; all other values use 400. SpaceMono terminal text uses its existing 400 font asset, never synthesized 600. Do not add font downloads or new families. Existing unmodified branding and dashboard typography remain as-is. Titles and paths wrap in detail; row title truncation must preserve a full accessible link name.

## Color

| Role | Value | Usage |
|------|-------|-------|
| Dominant (60%) | `#ffffff` / existing `--panel` | Sidebar, panels, detail and terminal text surface |
| Secondary (30%) | `#faf9ff`; existing `#f1edff` selected treatment | Page ground, restrained grouping and selected navigation backgrounds |
| Accent (10%) | `#6846d7` / `--purple`; `#5132ba` / `--purple-dark` for link text | Selected bucket indicator, task/PR links, selected density control and existing brand marks |
| Text / muted / border | `#242338` / `#65647b` / `#e9e7f2` | Existing ink, secondary evidence and separators |
| Success / failure status | `#16845b` / `#ba3e54` | Existing status dots with adjacent textual status; never the sole state signal |
| Warning notice | Existing `.notice-partial`: `#fffaf0` ground, `#765315` text | Genuine partial/stale source warnings only |
| Destructive | not applicable | No destructive actions or destructive CTA in this phase |

The 60/30/10 split describes visual emphasis, not an exact pixel quota. Accent is reserved for the specific elements above, not every interactive control or all terminal text. Use existing `#9278e8` 3px focus-visible outline, with offset and no clipping. Keep focus visible on both white and selected lavender backgrounds; verification must inspect contrast and distinguish focus from selection. Do not introduce a dark terminal theme, new semantic palette or ANSI colors.

## Copywriting Contract

Copy below is the product contract, not evidence that these states currently exist.

| Element | Copy |
|---------|------|
| Primary navigation CTA | `View tasks` → `#/tasks` |
| Bucket controls / All | `Working`, `Waiting on Ove`, `PR gate`, `Finished`, `All` |
| Overview working card | Retain visible `Active work`; accessible name `View Working tasks, {count}`; count uses the same Working selector |
| Active bucket accessible label | `{bucket}, {count} {task/tasks or PR/PRs}. Selected; activate to show All.` |
| Bucket heading / count | `{bucket}` with `{count} task` / `{count} tasks`; PR gate uses `{count} PR` / `{count} PRs` |
| Waiting empty heading / body | `Nothing waiting on you` / `Choose All to view the rest of the work queue.` |
| Working empty heading / body | `No work in progress` / `Choose All to view waiting and finished work.` |
| PR gate empty heading / body | `No PRs at the gate` / `Choose All to view the rest of the work queue.` |
| Finished empty heading / body | `No finished tasks yet` / `Choose All to view current work.` |
| All empty heading / body | `No task records available` / `Task records will appear here when reported by a source.` |
| Search empty heading / body | `No task matches the current filters` / `Clear the search or choose All.` |
| Unknown bucket notice | `Unknown bucket. Showing All tasks.` |
| Unknown task heading / recovery | `Task not found` / `This task is not in the current snapshot. View tasks.` |
| List return | `Back to {bucket}` or `Back to All tasks` |
| Idle disclosure | `Idle sessions ({count})`; empty expanded body `No idle sessions.` |
| Density labels | `Row density`, `Compact`, `Comfortable` |
| Initial task load | `Loading task details…` |
| Snapshot failure | `Dashboard data could not be refreshed. Showing the last available snapshot.` with `Retry`; without prior data, omit the last-snapshot sentence |
| Partial source notice | `Partial source data · {section}: {actual reason}. {stale suffix}`; suffix when cached data exists: `Showing last successful data · stale.` |
| Missing field | `Unavailable`; missing age uses existing `Age unavailable`; do not invent values |
| Empty timeline / events | `No recorded timeline events.` / `No recent events available.` |
| No linked PR / trackers | `No linked PRs available.` / `No tracker links available.` |
| Branch review found | `Greptile {N}/5 · from #{PR}` |
| Branch review not applicable | `No PR review for this commit` |
| Branch review unavailable | `Greptile review unavailable` |
| PR review without reported SHA | `reviewed SHA not reported` |
| Terminal heading / permanent qualifier | `Live terminal` / `Read only · redacted · last 200 lines` |
| Terminal connecting / connected empty | `Connecting to terminal…` / `Live · no output yet` |
| Terminal live | `Live` with `Last updated {Sydney timestamp}` |
| Auto-scroll control / paused status | `Pause auto-scroll` / `Resume auto-scroll`; status `Auto-scroll paused` |
| Terminal copy action / success | `Copy output` / `Output copied` |
| Terminal copy failure | `Could not copy output. Select the text and copy it manually.` |
| Terminal missing session | `No session attached` / `This task has no mapped Herdr pane. Other task details remain available.` |
| Terminal closed | `Pane closed` / `This session has ended. Other task details remain available.` |
| Terminal unavailable | `Herdr unavailable` / `Terminal output cannot be read on this host. Other task details remain available.` |
| Terminal reconnecting | `Reconnecting…` / `Showing the last received output while the connection is restored.`; omit last-output sentence when none exists |
| Terminal before first frame | `Last updated: not yet received` |
| Destructive confirmation | None — no delete, retry-job, send-input, approval, merge or session-control action is added |

## Navigation and Bucket Interaction

1. Canonical routes are `#/overview`, `#/tasks`, `#/tasks?bucket=waiting`, `#/tasks?bucket=working`, `#/tasks?bucket=pr-gate`, `#/tasks?bucket=finished`, `#/tasks/<id>`, `#/cicd`, `#/repositories`. Hash routing must not require server fallback or CSP changes. Encode/decode the existing validated task identity once; never use a display number as a guessed identity or a route value as a filesystem path/pane argument.
2. Overview summary cards and matching sidebar bucket links have actual href destinations. The active bucket's href targets `#/tasks` so activating it clears the filter; All always targets `#/tasks`. Expose the current bucket with `aria-current="page"`, not color alone. Do not model route links as ARIA tabs. Links activate with Enter and retain standard modified-click behavior; buttons, including density and disclosures, activate with Enter/Space.
3. Waiting uses `snapshot.waiting_on_ove`; Working uses non-terminal `snapshot.active`; PR gate uses `snapshot.pr_gate`; Finished uses `snapshot.finished`, de-duplicated by id. Do not reinterpret these lists from CI colors, source names or Herdr status in the browser. Terminal `done` and `failed` tasks are Finished only, preserving result `ok` or `failed`. Idle rows never contribute to Active/Working. Count, Overview, sidebar and filtered list share these selectors.
4. A bucket view has a visible heading, count, All control, search and density toggle before rows. The heading count reflects the bucket before text search; the list additionally reports `{count} shown` after search. Changing a bucket does not silently combine it with an incompatible legacy state filter. Keep controls reachable at narrow widths even when the legacy desktop work queue is hidden.
5. Unknown bucket values resolve to All with the documented polite status notice; do not show an unexplained empty view. A known task is not declared missing while the initial snapshot is still loading. An unknown task after loading renders the documented not-found state and recovery link inside the retained shell.
6. Every task title/ID link in Overview, sidebar, bucket lists and Idle groups opens its own detail route. PR gate records retain safe PR source links; expose a task link only when an actual associated task is resolved, never fabricate a task for an unmatched PR.
7. Record the originating list route/bucket on task navigation in history state and preserve that context for the current task in session storage so refresh of an internally opened detail does not lose its Back target. Browser Back/Forward restores actual route state. A directly opened detail with no origin uses All as its explicit Back target, not an unrelated historical bucket. Search and density may be retained in the same list context; no new public routes are required.
8. On user route changes, move focus to the page heading (programmatically focusable, not a routine tab stop); on return to a list restore the initiating task link when still present, otherwise the list heading. Snapshot polling does not steal focus or reset the route, selection, density or Idle disclosure. Preserve the existing skip link and main landmark; hash parsing must not mistake the skip-link fragment for a bucket/task navigation request.

## Task Detail and Density

### Detail content and evidence rules

Use a dedicated content page, not the existing list/detail split as the only way to inspect a task. Preserve the shell; place Back above the numbered title, then status/identity, evidence sections and terminal. Desktop uses the existing two-column definition-grid pattern for metadata, followed by full-width timeline/PR/events/terminal sections. At 760px and below, stack sections and allow all long values to wrap without horizontal page scrolling.

| Section | Required display / evidence |
|---------|-----------------------------|
| Identity | `{display_id} - {title}`, e.g. the approved numbered-title format; status pill; missing ID explicitly unavailable, never fabricated |
| Agent and plan | Owner/agent label plus reported model and plan/stage/phase fields; source-backed only; retain `Unavailable` when not reported |
| Wiring | Repository, branch, head SHA and worktree when supplied by the task/wiring projection; no inferred path from a branch name |
| PR evidence | Linked PR number/title/source, CI text plus decorative dot, Greptile score/state and review provenance; join from `ci_cd` by safe explicit PR link or unambiguous repository+branch evidence |
| Trackers | Actual Notion/Linear entries from task links, using the existing safe-link handling; no generated tracker records or new source fetch |
| Timeline/history | Recorded timestamp and state entries, ordered consistently with existing timeline semantics; do not manufacture progression |
| Recent events | Existing observed task events, separately labeled; avoid treating terminal lines as persisted task history |
| Terminal | The read-only panel described below; task metadata remains visible in every stream state |

Missing metadata retains the field label and documented unavailable value. Zero PRs, trackers, timeline entries or events render their documented local empty state; no whole-page failure. Do not guess owner/model/plan for a waiting or finished record. Preserve result/receipt/summary evidence when supplied. Multiple PR matches render separately only with corroborated relationships; ambiguous wiring is unavailable, not a guessed current PR. Safe external links keep the existing new-tab/rel behavior and a visible source label.

### List layout

Default new task lists to Compact (approved sensible default). The two-state density control uses native buttons in a group labeled `Row density`, one `aria-pressed="true"` at a time. Compact and Comfortable change padding/min-height, not which evidence exists. Do not reduce type size to claim greater density.

Desktop row order: numbered ID; linked title; textual state pill; age; owner/agent; linked PR number with CI text/dot; Greptile score/state. Compact uses 8px vertical padding and a 44px minimum height; Comfortable uses 16px and a 64px minimum. Use a CSS grid/list with independent task and PR anchors, never an entire button containing nested links. Accessible task link name includes full ID/title even when visually ellipsized. Missing PR/score/age/owner stays explicit rather than displaying green or zero.

At narrow widths, ID/title/status form the first line and metadata wraps below in the same semantic row. Do not remove evidence columns entirely, horizontally scroll the page or shrink touch targets. Detail title/path/URLs can break with `overflow-wrap:anywhere`; terminal output alone may horizontally scroll for preserved whitespace.

### Idle sessions

Render an `Idle sessions` group with its own count in Overview, sidebar and Tasks/All list, using a native disclosure (collapsed initially). It is not a fifth status bucket and adds no new route. Working/bucket results do not include Idle rows. In All, Idle entries appear only in their separate group, not duplicated into the main work queue. The group remains reachable in the mobile Tasks/Overview surfaces even if desktop sidebar details are hidden.

Use `snapshot.idle_sessions` as classified by plan 01. Unknown/non-live-agent panes, matched sessions with stop_reason, and quiet sandbox_e2e belong here. A live agent with status idle remains Active under the approved backend rule; the label `idle` alone must not move it into this group. Live working/idle/blocked panes and working sandbox_e2e remain Active, and blocked waiting items remain Waiting. Idle task IDs still resolve to detail and share stable identity allocation. Disclosure state survives snapshot refresh while mounted; no new preference service is introduced.

## Source Notices and CI/CD Evidence

- Clean GitHub metadata renders no `Partial source data` notice on Overview, CI/CD or Repositories. Do not hide genuine warnings by dropping metadata or clearing errors in the UI.
- Genuine failures name the affected section and actual reason (including `too_large`, rate limit, offline or reported error), retain previous merges visibly marked stale, and retain last successful source freshness separately from snapshot fetch time. Use the existing amber notice and polite status semantics; a global snapshot failure keeps the existing alert/Retry pattern. Local task, Idle, bucket and detail navigation remain usable.
- A failed branch review lookup shows only that row's unavailable label; it does not turn the entire CI/CD section partial. No four-unknown Greptile sentence is permitted. Score, not-applicable and unavailable must be distinct; an unavailable lookup is not a direct-commit assertion.
- Render exactly one branch card per repository+branch, with backend `ci_cd` authoritative even when cached/current SHAs differ; synthesize `latest_main_ci` only when that repository+branch has no backend branch card. Preserve PR cards and stable keys. This rule is visual as well as data-level: one Omega-Prime main card and one coding-fleet main card with the approved live dataset.
- Reuse the same review wording/provenance in detail and dense rows. Body-marker PR reviews without derivable reviewed SHA say the documented not-reported wording; do not claim head match without evidence.

## Read-Only Terminal Interaction

The panel is a white existing-style panel with SpaceMono plain text, visible heading, read-only qualifier, status text/dot, last-update time and quiet controls. No terminal emulator, ANSI colors, input, contenteditable region, command form, attach button, WebSocket or session-control action is allowed.

- The browser requests only GET `/api/tasks/{task_id}/terminal` over SSE. It never sends or selects a pane ID. Validated pane mapping and all ANSI/control stripping, redaction and 200-line/64 KB bounds happen server-side before emission. Render received text as a React text child in `<pre>`, never HTML/markdown or clickable terminal links. Copy only this already sanitized/redacted displayed buffer.
- Mount the stream only for the viewed detail; close it when leaving/changing tasks. Changed frames update output; heartbeats must not fabricate new output or advance its last-updated timestamp. Show a real frame timestamp using the existing Australia/Sydney formatting, not an invented live clock. Keep the prior timestamp visibly unchanged during reconnect/unavailable states.
- Pause affects auto-scroll only, not transport or capture. Default follow-tail is enabled. Scrolling away from the bottom pauses follow-tail; the explicit Resume control returns to the latest tail. New frames never move the user back to the bottom while paused, and the 200-line cap still applies. Expose the control's state with an accessible label/pressed state.
- Copy output is disabled until output exists; success/failure is announced politely without moving focus. Do not add download/export actions. Native selection/manual copy remains possible.
- Live, paused auto-scroll, no session attached, pane closed, Herdr unavailable and reconnecting are distinct visible states. Paused is independent of connection status: a paused panel can be live or reconnecting. Connecting and live-with-empty-output are not mistaken for missing sessions. Status transitions do not blank the task detail.
- On reconnect show the last redacted buffer when available with the reconnecting state, never label it current live output. Pane closed/unavailable can retain the last buffer clearly labeled as last received; no session attached has no misleading live indicator. All missing/closed/unavailable states use their documented local copy and never expose raw stderr, pane paths or secret-bearing diagnostics.
- Use a labeled, keyboard-scrollable read-only region around the non-editable `<pre>`; it is not an input and must not forward any key to Herdr. Limit output viewport to 320px on desktop and 240px at the existing mobile breakpoint; allow internal horizontal/vertical scroll and text selection. Keep toolbar wrapping above the viewport. Do not put streaming text in an aria-live region; announce only discrete status/copy changes so a screen reader is not flooded every second.
- Preserve existing reduced-motion behavior; no flashing live indicator or mandatory smooth scrolling. No extra telemetry, terminal input endpoint, auto-population of task mappings or writes to the real tasks file.

## UI Considerations

Applicable state considerations resolved: 8 categories covered by explicit contract acceptance criteria; 2 additional visual backstops; 0 unresolved design decisions. `covered` means a specified truth to implement and verify, not a claim of exercised verification. `backstop` means explicit evidence is required; absent evidence remains human-needed, never a silent pass.

| Category | Element(s) | Status | Resolution / acceptance criterion |
|----------|------------|--------|-----------------------------------|
| empty | Bucket/All/search lists, Idle disclosure, detail sections, terminal | covered | Zero records show the matching Copywriting Contract empty state; absent pane shows no-session copy; a connected empty stream shows live-with-no-output, not a false error. |
| loading | Snapshot, deep-linked detail, terminal | covered | Preserve the existing initial dashboard skeleton; deep-linked task load uses the loading row and does not prematurely show not-found. Terminal connects independently; refreshing with cached data does not blank content or steal focus. |
| error | Snapshot, unknown bucket/task routes, terminal/copy control | covered | Use documented alert/Retry for snapshot failures, polite fallback notice for unknown bucket, explicit not-found after load, and independent terminal/copy states; other detail/navigation remains operable. |
| populated | Bucket rows, Idle, metadata, PR/timeline/events, terminal | covered | Each correct route displays its own heading/count and prescribed evidence; task click opens numbered detail; Idle opens actual detail; mapped terminal displays only sanitized plain text. |
| partial | Source sections, branch review, missing task fields | covered | Retain stale merges with real section reason/freshness; review lookup failure stays row-local; missing detail labels remain visible with documented unavailable values; clean metadata removes partial notices. |
| overflow | Rows, sidebar, detail, terminal | covered | Wrap narrow-screen metadata and long detail paths; preserve internal sidebar scrolling; terminal alone preserves whitespace in a bounded scroll viewport; no clipped controls or horizontal page overflow at 320px. |
| zero-one-many | Each bucket, PRs, timeline, Idle | covered | De-duplicate finished identities and branch cards; zero uses empty copy, one uses singular counts, many uses plural; Idle count is separate and excluded from Active/Working. |
| long-text | Numbered titles, links, owner/model, branches/worktrees, notices | covered | Detail values wrap anywhere where needed; list ellipsis retains the full accessible name; notices wrap and do not push recovery controls off-screen; no title/URL HTML interpretation. |
| overflow / long-text | Five host screenshot views and responsive dense rows | backstop | Host 7441 evidence must inspect white/purple continuity, visible density improvement, full detail readability, wrapped toolbar and no page overflow; use long-title/path fixtures at 320px and desktop width. Missing screenshots remain unverified. |
| populated / partial | Real live terminal and real GitHub source views | backstop | Host 7441 must demonstrate real changing redacted terminal output and live-data clean banners/one-card counts. Herdr is absent in sandbox; fake-runner and mocked SSE evidence cannot discharge real-host acceptance. |

### Explicit Acceptance Criteria and Evidence Ownership

| Acceptance | Required observable UI evidence | Named verification limit |
|------------|---------------------------------|--------------------------|
| AC1 / AC2 | Live-data Overview, CI/CD and Repositories have no false partial notice; recent merges includes Omega-Prime again. A genuine-failure scenario retains stale data and the true reason. | Only isolated host 127.0.0.1:7441 with throwaway HOME can supply live GitHub/screenshot evidence; planning/source inspection is not evidence of success. |
| AC3 | CI/CD shows one backend-authoritative default-branch card per repository+branch, including mixed stale/current SHA fixtures. | Later Vitest regression plus host screenshot; not exercised by this UI research task. |
| AC4 | Found/not-applicable/unavailable review fixtures show exact documented wording and no four-unknown string, including detail/rows. | Later Python/Vitest checks prove fixture paths; real GitHub data is separate host evidence. |
| AC5 | All four buckets open their exact hash URL, selected bucket/All clears, unknown bucket falls back visibly; refresh and browser Back/Forward restore routes. | Later route tests and keyboard/browser interaction; a screenshot alone cannot prove navigation or history. |
| AC6 | Task links from every surface open full numbered detail; origin bucket Back survives internal navigation/refresh; both density settings preserve fields; unknown task recovers. Idle renders separately and never increases Active/Working. | Later frontend tests, responsive visual inspection and keyboard traversal. Model done/failed/idle classification also needs plan-01 Python evidence. |
| AC7 | A mapped task visibly updates plain-text redacted output; all six required states, pause/resume auto-scroll, copy and actual last-update time are correct; unmapped task remains usable. | Herdr absent in sandbox. Fake runner/mocked SSE can prove offline paths only; a real pane on host 7441 must prove live capture. No screenshot can alone prove server redaction or the absence of input paths. |
| AC8 | Regression tests fail behaviorally on ad3942b and pass after implementation; full prescribed suites/build/lint/types run with existing baseline limits recorded. | No product code, tests, builds, lint or browser checks run in this planning assignment. Execution/outer driver owns fail-before/pass-after evidence; missing-module failure is insufficient. |
| AC9 | Save five actual screenshots from host 7441: Overview, a bucket, task detail with live terminal, CI/CD, Repositories; retain local commits on the feature branch. | No screenshots captured here. Name artifacts and timestamps in the later receipt; never substitute mocks for real-terminal screenshot or use live 7421/real HOME. Draft PR remains an authenticated later host action. |

Accessibility acceptance: native keyboard route links and toggle/disclosure buttons; visible focus and active indication; coherent heading/landmark order; labeled search/density/terminal controls; 44px minimum new control targets; textual CI/connection states; no full terminal aria-live spam; reduced-motion support; no focus theft on polling. Check at 320px and a desktop width, with long titles/paths and zero/one/many rows. This contract does not claim comprehensive WCAG certification or screen-reader testing.

## Registry Safety

| Registry | Blocks Used | Safety Gate |
|----------|-------------|-------------|
| none | none | not applicable — existing manual system retained, no shadcn or third-party registry block declared or fetched |

Do not install new blocks, run preset initialization or introduce external UI assets under this phase. Existing source components remain the reuse baseline.

## Checker Sign-Off

- [ ] Dimension 1 Copywriting: PASS
- [ ] Dimension 2 Visuals: PASS
- [ ] Dimension 3 Color: PASS
- [ ] Dimension 4 Typography: PASS
- [ ] Dimension 5 Spacing: PASS
- [ ] Dimension 6 Registry Safety: PASS
- [ ] Dimension 7 Inventory Provenance: PASS (Tool: none exemption to be confirmed)

**Approval:** pending independent checker. Draft contract only; no implementation or verification pass asserted.
