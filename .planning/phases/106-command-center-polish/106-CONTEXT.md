# Phase 106: Command Center polish — locked context

## Authority and scope

Task 00025. Planning by Sol; execution by Luna. Pass `orchestrate workflowz ultrathink` verbatim to every downstream stage. The complete second uplift is [106-UPLIFT.xml](106-UPLIFT.xml), copied byte-for-byte as the UPLIFT.md-equivalent reference. Its original goal and 12:54 addendum remain authoritative. All defaults c1–c5 were approved at 18:33 AEDT: “Yes, build 00025 with the defaults”. The parent addendum at 18:39 AEDT adds the model/Idle fixes below without a new uplift round.

This stage writes planning artifacts only, commits locally, and does not push. Sandbox paths override host worktree paths: current repository `/sandbox/work`, feature branch `feat/command-center-polish`, base `ad3942b`. Do not switch branches. Host verification is separately performed on 127.0.0.1:7441 with throwaway HOME; never touch the live 7421 service, its worktree, real tasks file or real dashboard home. No remote is supplied; draft PR remains a later authenticated host action, not a fabricated URL.

## Locked decisions

1. Recent merges: use smaller closed-PR pages, detect bodies over the cap by a bounded limit+1 read and a distinct `too_large` reason, and stop at MAX_MERGES. Never unbounded reads, silent truncation, hidden banners or swallowed errors. Preserve cached merges as stale on genuine failure, deadline/budget/cache, unauthenticated behavior and GET/no-redirect/token guarantees.
2. CI/CD: backend `ci_cd` is authoritative. One branch card per repository+branch even with mixed stale/current SHA values; synthetic `latest_main_ci` only where that repository+branch has no backend branch row. Stable unique keys; preserve PR rows.
3. c1: lookup the merged PR producing the validated branch-head SHA using GET `/repos/{owner}/{repo}/commits/{sha}/pulls`, inside existing bounds/cache. Attach score, reviewed_sha and source `merged_pr` with validated PR number. Empty lookup means `not_applicable` / “No PR review for this commit”. Lookup failure is row-local `unavailable`, not section-wide partial. PR body-marker scores receive reviewed_sha where derivable; otherwise “reviewed SHA not reported”. Never the four-unknown string.
4. c2: hash routes `#/overview`, `#/tasks`, `#/tasks?bucket=waiting|working|pr-gate|finished`, `#/tasks/<id>`, `#/cicd`, `#/repositories`. Refresh/Back/Forward restore. Unknown bucket -> All plus notice; unknown task -> “Task not found”. Summary cards/sidebar toggle buckets; clicking active bucket or All clears. Detail Back preserves bucket context.
5. Buckets use snapshot waiting_on_ove, non-terminal active, pr_gate and finished (deduplicated by id). Task detail includes numbered title, status, timeline, linked PR CI/Greptile joined from ci_cd, Notion/Linear links, agent/owner, model/plan, wiring branch/worktree, events and terminal mount. Compact field-rich rows include id/title/state/age/owner/PR+CI/score; offer compact/comfortable toggle.
6. c3: optional `herdr_pane` validated in status_file with `^w\d+:p\d+$`, surfaced through tasks_json/snapshot. Owner fallback only when owner explicitly identifies a Herdr pane. Browser sends task id only; server validates/looks up task and resolves pane. No automatic bridge population or writes to real task files.
7. c4: GET `/api/tasks/{task_id}/terminal` SSE behind existing loopback/Host/GET/CSP guards. Exact argv `["herdr","pane","read",pane,"--source","recent","--lines","200","--format","text"]`; short timeout, about 1/s only while connected, changed frames plus heartbeats and capped concurrent streams. Strip ANSI/control sequences, cap last 200 lines and 64 KB, apply redact_value plus Bearer, ghp_/github_pat_, sk-, JWT-like and KEY=value secret patterns before emission. Plain-text rendering, no innerHTML. Live/no session attached/pane closed/Herdr unavailable/reconnecting; pause/resume auto-scroll, copy, last update. No input endpoint, POST, WebSocket, send-text/send-keys/run or shell path.
8. c5: original 12:54 scope belongs within these five plans, not a new phase. Preserve uplift graph n1–n8 ordering and tracker mapping; do not rerun uplift or create tracker rows.

## Approved model-bucket addendum (18:39 AEDT)

- Plan 106-01: tasks_json `done`/`failed` go only to finished with result `ok`/`failed`, never active. Non-terminal behavior unchanged.
- Plan 106-01: Herdr unknown/non-live-agent panes, panes whose matched omp session has stop_reason, and quiet sandbox_e2e runs go to new `Snapshot.idle_sessions: list[Task]`, included in to_dict/content_hash/API payload, not active. Live working/idle/blocked agent panes remain active; blocked waiting item remains. Working sandbox_e2e remains active. Audit poller/app/task_ids and every active consumer; allocate ids consistently across idle tasks.
- Plan 106-04: types/selectors/Overview/sidebar/Tasks render “Idle sessions”, excluded from Active/Working count/bucket. Collapsed default is allowed.
- Regression gates: done+failed finished-only; unknown/stopped/quiet idle-only while working remains active; frontend idle rendering and Active count exclusion. Every new regression must fail on ad3942b and pass after implementation.

## Execution shape and ownership

Exactly five PLAN.md files, exactly two waves: wave 1 = 01/02/04; wave 2 = 03/05. File-disjoint means within a wave: the contract explicitly requires cross-wave edits to collector and model files. Ownership resolves to bot-00-omega-prime under omega_prime/ownership.yaml, last match wins. Plans must list exact files and explicitly separate source ownership from prerequisite/integration ordering. 04 alone rebuilds/commits static/cc in wave 1 after 02 is integrated; 05 alone rebuilds/commits it in wave 2 after 03 is integrated. No extra execution plan. Explicitly coordinate the 05-owned TerminalPanel placeholder import before 04 builds, without permitting 04 to own that file or leave a broken import.

## Evidence and verification

Read every reference in `/sandbox/home/refs`: README-REFS.md, ADDENDUM-model-buckets.md, uplift, baseline ruff/mypy, scrubbed Herdr snapshot shape and pane-read help. Herdr is absent in sandbox; fake runner proves offline behavior; real live terminal/screenshots remain host evidence. Existing lint/type failures are recorded inputs, not newly reproduced failures or permission to introduce more.

Each plan must name exact regression test nodes/titles for the outer driver to transplant and run against ad3942b. A missing module/import is not sufficient evidence of the behavioral regression: adapt tests to existing public entrypoints or document a minimal baseline harness. All new regressions must FAIL on ad3942b and PASS after change; do not claim this during planning. Python command: `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/ -k dashboard`. Frontend npm ci/test/build run in omega_prime/dashboard/frontend with registry.npmjs.org explicitly selected. No GitHub/Herdr network dependency in offline tests. Verify full suites, configured lint/types, polyglot-check --changed at execution; record pre-existing limits honestly.

Uplift acceptance criteria are indexed AC1–AC9 in source order. AC1/2 merges and clean banners; AC3 dedup; AC4 Greptile; AC5 buckets; AC6 detail/density; AC7 terminal; AC8 fail-before/pass-after and suites; AC9 five screenshots and local commits. Live GitHub and five screenshots (Overview, bucket, detail with terminal, CI/CD, Repositories) belong to isolated host 7441 verification, not an offline claim. No merge, auto-merge, force-push, deploy, token helper or secret disclosure.

## Trackers

All Task/Issue/Subissue IDs and Notion/Linear URLs are preserved in 106-UPLIFT.xml/ISSUES. Commit trailers cite the actual implemented planning nodes/steps, not future product completion. No new tracker IDs are invented.

## Deferred / out of scope

Interactive terminal control, bridge population of herdr_pane, live task-file writes, moving the 7421 service, unrelated redesign/data sources, changing GitHub/Greptile/CI settings, push/merge/deploy. `rule adherence by future agents` remains unverified in every receipt.
