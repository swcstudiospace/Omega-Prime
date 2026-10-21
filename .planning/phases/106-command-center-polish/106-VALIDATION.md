---
phase: 106
slug: command-center-polish
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-10-21
---

# Phase 106 — Draft Validation and Outer-Driver Contract

Directive: `orchestrate workflowz ultrathink`. This is a planning artifact, not an execution receipt or a claim of compliance. No product tests/builds/lint/formatters, baseline transplant, live GitHub/Herdr check or screenshot were run by the planner. Parent owns plan validation and local planning commit; no PR, push, merge or deploy. All five plans and this document resolve to `bot-00-omega-prime` under `omega_prime/ownership.yaml` (last match wins).

## Authority and acceptance labels

Read 106-CONTEXT.md, complete 106-UPLIFT.xml, 106-ADDENDUM-model-buckets.md, 106-RESEARCH.md, 106-PATTERNS.md and 106-UI-SPEC.md. `AC1`–`AC9` are local source-order labels for the nine uplift acceptance items, not invented tracker or old v16 REQUIREMENTS.md IDs. The older REQUIREMENTS document is intentionally not rewritten. Approved defaults c1–c5 and the18:39 model addendum stand; no new design question/uplift/tracker row.

## Execution graph and command/artifact dispatch

Exactly two waves and five PLANs:

| Plan | Source wave/dependencies | Exclusive source custody | Integration-only barrier |
|---|---|---|---|
|106-01|1, none|GitHub bounds/merges, model/Herdr classification, numbering, cold app shape, own Python regression/contract modules|No frontend build; poller audited read-only;03 extends GitHub and05 extends model/app later.|
|106-02|1, none|CiCd.tsx, CiCd.test.tsx, new CiCd.dedup.test.tsx|No generated assets/types/fixtures/selectors; source integrated before04 build.|
|106-04|1, none|Global route/types/selectors/review helper, Overview/Tasks/sidebar/detail/density/notices, own tests; app-fixes/static tests|Only wave1 builder; waits02 source and05 task1 artifact. Never authors TerminalPanel.tsx.|
|106-03|2, after01/02/04|GitHub collector/helper+command-center/cache/Greptile tests; CiCd files/tests|Consume04 ViewState githubMeta section-notice contract read-only and migrate CiCd caller. No model/app/types/selectors/terminal/shared-component edits or asset build; source integrated before05 build.|
|106-05|2, after04; explicit task1 exception below|Terminal service/app/model/status_file/pane types, TerminalPanel/CSS/own tests, docs/changelog|Only wave2 builder, after03 source. No GitHub/CiCd/shared route/detail/style/fixture edits.|

The complete `files_modified` manifest in each plan is canonical, including generated `static/cc/index.html`, `assets/*`, `licenses/*` only in04 and05. Generated subtree patterns denote exact build-owned manifest scope, not guessed asset hashes or broad source custody. No same-wave source/test paths overlap. Cross-wave overlap is deliberate:01→03 GitHub,02→03 CiCd,01→05 model/app,04→05 pane types/generated assets. Global wave completion ensures01 model is present before05, and04 types before03. Source readiness and build readiness are different.

### Task-level exception mapping (mandatory outer-driver behavior)

1. Dispatch key `106-05:task-1:terminal-mount`: send only05 task1 instructions to the05 owner, explicitly `Execute only 106-05 task 1; stop after publishing the mount artifact; do not run tasks 2–3 or verify commands`. One allowed artifact is `omega_prime/dashboard/frontend/src/components/TerminalPanel.tsx`; named export `TerminalPanel({taskId}: {taskId: string})`, exact interim text `Terminal streaming not connected`, no connection. Record artifact path/export/text as prerequisite-ready, not05 complete. Do NOT invoke a generic whole-plan executor for this preflight.
2. Dispatch01/02/04 wave1 source work;04 reads but never edits that component. It must exist before04 imports it, not merely mocked during tests.
3. At a stopped-writer wave1 integration gate:02 source complete,04 source complete,01 payload contracts complete;04's sole builder restores dependencies if needed and runs full frontend tests/build/asset inspection plus relevant Python serving contracts. No parallel build.04 owns generated changes at this point.
4. Dispatch03 and05 tasks2–3 source work in wave2.05 replaces its prerequisite component completely with real SSE and does not leave interim copy in final source.03 changes no05 files.
5. At a stopped-writer wave2 gate:03 source integrated;05 alone performs the final frontend build and generated manifest commit. Main runs consolidated full suites/tool/security checks once after all writers land. Targeted commands in plans supplement those gates; no mid-flight checking against sibling partial source.

This is the approved narrow prerequisite-artifact exception, not a sixth plan, third wave or permission to execute all05 early. A placeholder is not the final AC7 deliverable. Its static artifact check is integration-only, not a FAIL-base/PASS-polish product regression.

### Preserved uplift topology/tracker map

The source graph is n1 understand → n2 decompose → n3/n4/n5/n6 design → n7 critique → n8 synthesize. Do not regenerate it. n1 prior host observations and n2 decomposition are supplied evidence, not permission to reread7421. n3 options/defaults are settled, not reopened.

| Original node | Existing Linear issue | Plan/evidence mapping |
|---|---|---|
|n1|SPE-18242|Read supplied reproduction evidence/source; base behavioral assertions in01–05, no live7421 call.|
|n2|SPE-18243|Existing A–G units preserved in exactly five plans and this custody matrix.|
|n3|SPE-18244|01 tasks1/verification, original steps1–5.|
|n4|SPE-18245|02 repository+branch step1;03 merged-PR/provenance/wording/row-local steps2–5.|
|n5|SPE-18278|04 routes/buckets/detail/density/tests, original steps1–6.|
|n6|SPE-18279|05 mapping/capture/GET SSE/sanitizing/no-input/UI/tests, original steps1–7; narrow mount prerequisite does not change the graph.|
|n7|SPE-18246|01 bounds/cache;02 stale dedup;03 budget/provenance;04 route/security;05 terminal/isolated host; outer review before completion.|
|n8|SPE-18247|Original steps1–5 map to01/02/03/04/05; step6 full suites+host five screenshots; step7 local-only handoff.|

Task Notion source: https://www.notion.so/400bc1a0c7ae81aa8cded119bd92df7c . All per-node/per-step actual Linear IDs and Notion/Linear URLs remain verbatim in106-UPLIFT.xml/ISSUES. Later commit trailers cite only implemented source nodes/steps using that mapping; parent planning commit cites planning work, not future product completion. Any later authorized draft-PR body cites supplied identifiers/URLs and receipt; no PR is opened in this assignment and no URL is fabricated.

## Prerequisites and honest existing limitations

These are grounded executable instructions for the later driver, not commands already run here. Every command's nonzero exit is an observable failure unless a different signal is stated. Keep stderr and exit status; no `|| true`, redirected-error hiding, `--passWithNoTests`, skipped collection or altered lint configs.

- Existing frontend declaration scripts: `test=vitest`, `build=tsc -b && vite build`; vite writes `../static/cc` with `emptyOutDir=true`. No new packages/versions/lock are designed. Existing package audit/admission remains the phase105 locked tree; unexpected new dependencies require audit and block. Do not mistake a research declaration for a registry-current security verdict.
- Research observed Node24.21.0/npm11.19.0/Python3.12.3; pytest/Starlette/Uvicorn/httpx installed; node_modules not discovered; Herdr/ruff/mypy/polyglot-check absent. These are inputs, not newly rerun observations.
- Prior phase recorded broad pytest collection exit2 on missing `mcp`/`apscheduler`, and real setuptools package-data collection unavailable. Full Python requirement remains unmet until an approved environment supplies prerequisites and actual commands pass. No Python dependency installation is authorized by these plans. Focused dashboard tests do not erase the full gate's failure.
- Supplied `/sandbox/home/refs/baseline-ruff.txt`:27 errors; baseline-mypy.txt:7 errors in1 file/23 source files. Do not rerun to confirm ground truth. Later configured lint/type checks compare diagnostic changes and distinguish pre-existing from new. Missing tooling is not a green result or an exemption.
- Bootstrap command is exactly `npm ci --registry=https://registry.npmjs.org` in frontend cwd. The registry is reachable bootstrap, separate from the offline test requirement; this plan does not demand `--offline` and does not claim a networkless install. No upgrade/unlocked install/system package workaround.

### Safe tool/module availability probes (later outer owner)

| Cwd | Exact command | Failing signal |
|---|---|---|
|root|`node --version`|Nonzero exit or missing version.|
|root|`npm --version`|Nonzero exit or missing version.|
|root|`python3 --version`|Nonzero exit or missing version.|
|root|`python3 -c "import pytest,starlette,uvicorn,httpx; print('dashboard runtime imports available')"`|Nonzero exit or absent confirmation.|
|root|`python3 -c "import mcp,apscheduler,setuptools; print('full collection prerequisites available')"`|Nonzero exit; record the exact missing module without suppressing it.|
|root|`python3 -m ruff --version`|Nonzero exit/unavailable module.|
|root|`python3 -m mypy --version`|Nonzero exit/unavailable module.|
|root|`command -v polyglot-check`|Nonzero exit/no executable; no invented repository replacement.|
|host only|`command -v herdr`|Nonzero exit/no path; fake runner covers sandbox, not live acceptance.|
|host only|`herdr pane read --help`|Nonzero exit or help lacks pane read/recent/lines/text controls. No real pane argv/output is logged.|

Tool probes do not print environment values, credentials, pane output or real task data. Host screenshot tooling is outer-driver supplied; use the managed browser's navigation/screenshot tools if available, not an invented project npm Playwright script or a new browser dependency. A missing browser is a recorded host prerequisite, never permission to substitute fake screenshots.

## Named behavioral regression inventory

All paths below are new tests to author, not observed existing/passing tests. For every row marked regression, outer driver must record FAIL on `ad3942b` and PASS after implementation with an assertion excerpt. Collection/import/setup failure is NOT behavioral red evidence. Existing guards, fallback-only, security/no-input and static serving checks that already pass are preservation-only and must be labelled separately.

| Plan | Exact regression path/node or title | Base behavioral harness / red assertion |
|---|---|---|
|01|`omega_prime/tests/test_dashboard_github_merges_polish.py::test_closed_pages_avoid_legacy_1_2mb_truncation`|Existing make_github_collector; fake opener respects old read cap; aggregate100-row response1.2MB, request-aware30-row response under cap. Actual merges/clean metadata fails base.|
|01|same file `::test_oversize_response_reports_too_large_and_keeps_stale_merges`|Existing collector/_request fake opener; warm cache+91s clock advance+1,000,001byte response. too_large versus old generic error assertion fails base, stale/fetched_at retained.|
|01|same file `::test_closed_paging_stops_at_max_merged_rows_per_target`|Call-log per-target assertions four30-row pages for100 merges, ten-page maximum sparse candidates; both repos eligible before global sort/cap. Base pagination assertions fail.|
|01|`omega_prime/tests/test_dashboard_model_buckets.py::test_done_and_failed_are_finished_only`|Existing normalize_tasks; active membership/results/rich detail assertions fail base.|
|01|same file `::test_unknown_exited_and_quiet_sessions_are_idle_only`|Existing normalization/collector seams; dict.get idle avoids missing attribute setup failure; includes live-idle/blocked/working preservation and stopped precedence.|
|01|same file `::test_idle_changes_snapshot_hash_and_keeps_number_across_restart`|Existing normalize/number/poller APIs plus temporary registry; dict.get payload assertion fails before identity/hash assertions; no import of new dataclass field required.|
|02|`omega_prime/dashboard/frontend/src/views/CiCd.dedup.test.tsx` — `prefers backend branch rows even when fallback SHA is stale`|Existing CiCd public render; same/different SHA pair plus both repos+PR fixture; duplicate card/evidence assertion fails.|
|02|same file — `adds fallback only when repository branch has no backend row`|Mixed duplicate backend pair+different fallback-only pair; fails base duplicate count/keys while preserving fallback itself. Pure absent-backend fallback may already pass and is preservation-only.|
|03|`omega_prime/tests/test_dashboard_github_greptile_polish.py::test_branch_uses_merged_pr_score_and_actual_reviewed_sha`|Existing collector; fake commits/pulls/merged target/default branch plus real PR-reviewed SHA distinct from merge SHA; baseline null score/provenance assertion fails.|
|03|same file `::test_direct_commit_is_not_applicable_and_lookup_failure_is_row_local`|Existing collector; empty lookup versus403/offline/malformed/ambiguous/truncated cases; missing row states fail base. Existing open-PR partial semantics remain preservation-only.|
|03|`omega_prime/dashboard/frontend/src/views/CiCd.greptile.test.tsx` — `renders merged PR score, no PR, unavailable and unreported reviewed SHA`|Existing CiCd, locally constructed structural payload; no import of04 review helper into base; score0 and exact state-copy assertions fail.|
|03|`omega_prime/dashboard/frontend/src/views/CiCd.test.tsx` — `shows recent_merges failure with original successful section freshness`|Existing CiCd snapshot props, local structural fixture with actual recent_merges partial/stale reason too_large, retained merges, healthy other sections and distinct older section-success/newer aggregate/still-newer received timestamps. Base fails missing section/reason/original-success notice assertions; polish consumes04 ViewState githubMeta contract and clean rerender removes warning without losing cards. No future helper import into base.|
|04|`omega_prime/dashboard/frontend/src/App.routes.test.tsx` — `opens and clears each bucket and restores initial hash`|Existing App/useDashboard mock; all four buckets+empty+unknown notice; route/filter assertions fail base.|
|04|same file — `preserves bucket origin through task detail refresh Back and Forward`|Existing App and real hash/history events; encode slash-containing raw identity, internal origin/remount/direct link; URL/origin assertions fail base.|
|04|`omega_prime/dashboard/frontend/src/components/TaskDetail.routes.test.tsx` — `opens numbered task detail with recorded links PR evidence wiring and events`|Drive existing App from all surfaces, not a future TaskDetail export. Actual path_or_url tracker/CI/worktree/detail assertions fail.|
|04|`omega_prime/dashboard/frontend/src/views/Overview.idle.test.tsx` — `excludes idle sessions from Active and renders the separate group`|Existing App/Overview/Tasks public seams with structural idle fixture; missing Idle group/count contract fails base, no future selector import.|
|04|`omega_prime/dashboard/frontend/src/views/Tasks.density.test.tsx` — `toggles compact field rich rows without losing bucket controls`|Existing App/Tasks; density/fields/recovery-control assertions fail base. Optional test-only old-prop adapter does not copy polished production code.|
|05|`omega_prime/tests/test_dashboard_terminal.py::test_task_mapping_roundtrips_validation_collector_and_snapshot`|Existing write_tasks/read_tasks/collector/normalize; baseline silently strips mapping, assertion fails.|
|05|`omega_prime/tests/test_dashboard_terminal_api.py::test_terminal_route_resolves_only_task_owned_pane_and_uses_read_argv`|SAME real create_app→GET→raw ASGI contract on both revisions: assert HTTP200 before wire/argv assertions. Actual base404 is behavioral absence of the requested feature, not unsafe output.|
|05|same file `::test_terminal_sanitizes_redacts_caps_and_sends_only_changed_frames`|SAME real create_app route-to-wire contract on both revisions: assert HTTP200 first, then inspect actual emitted sanitized frames for token/control/line/byte/changed-frame behavior. Base legitimately fails200 versus404 before safety assertions; never introduce a substitute emitter/sanitizer or claim unsafe baseline output.|
|05|same file `::test_terminal_disconnect_releases_capacity_and_stops_reads`|SAME real create_app/raw ASGI path, assert HTTP200 before lifecycle assertions; base404 is missing-feature red evidence only. Polish controlled runner disconnect/cancel/timeout/saturation/recovery assertions exercise actual implementation lifecycle, not a test substitute.|
|05|`omega_prime/dashboard/frontend/src/components/TerminalPanel.test.tsx` — `streams plain text and cleans reconnect on task change`|Drive existing App through mapped detail with mock hook/fake EventSource; no future TerminalPanel import in transplanted regression. Missing rendered stream/state/lifecycle assertions fail base.|

Additional preservation-only node in05: `omega_prime/tests/test_dashboard_terminal_api.py::test_terminal_has_no_input_route_or_subcommand`. Existing dashboard already rejects non-GET and lacks an input endpoint; pair its unchanged invariant with failing functional mapping/route cases. Additional direct helper tests depending on terminal.py are polish-only tests, not claimed base-transplantable.01/02/03/04 existing test adaptations retain their original behavior coverage, not artificial new regressions.

### Baseline transplant procedure

Outer driver checks temporary destination availability, then uses the supplied base in a disposable checkout: `git worktree add --detach /tmp/cc106-baseline ad3942b` (choose another unused temporary path if occupied). This verification-only checkout is the explicit exception to no execution worktrees; never switch/reset/clean the active feature worktree. Product edits remain `/sandbox/work`/approved host export, not base checkout.

Copy tests and test-only local fixtures/adapters, never polished production source/new helpers/generated assets. Use identical approved locked dependencies. UI baseline mocks the existing useDashboard, initializes hash before render and resets storage/globals/timers. Python baseline uses existing public collector/normalizer/bridge/create_app APIs. Never transplant a future helper import and call its ImportError the regression. Save command, cwd, source SHA, expected assertion, actual exit and sanitized failure excerpt for each row; then run same case against polish. Failure for a different setup error does not meet AC8. Final new-node assertions must all pass after change; existing preservation nodes may pass both sides.

Terminal API fixture contract: keep all three named API feature nodes collectable on both revisions using existing create_app/current poller and raw ASGI APIs. A fixture may detect the supported create_app terminal_service parameter to instantiate/inject the real service with synthetic capture inputs/clock on polish; on base call the existing signature, without importing a future module at collection. Never mount a test route or provide an emitter/sanitizer replacement. Send the identical GET and assert expected HTTP200 before inspecting actual frames. Record ad3942b's actual404 assertion as missing-feature failure only, and polish's actual sanitized-frame/lifecycle results separately. Additional helper/security tests against the actual new implementation are polish-only coverage; select named feature nodes rather than transplanting future-module imports into base collection.

Representative exact focused commands: `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_dashboard_github_merges_polish.py`, same with model_buckets/greptile_polish/terminal modules; frontend `npm test -- --run src/App.routes.test.tsx` and owner-specific test paths listed in PLAN verify tags. Use table rows as exact node/title filters where collecting paired evidence. Each plan supplies its complete focused command; every `<automated>` has immediate `<fails_when>`.

## Mandatory consolidated execution gates (AC8)

Run after all source writers have stopped; no full-suite storm per subagent. Record each command separately even if an earlier command blocks; do not suppress errors. A failed command means that gate is not passed. Existing supplied failures are not repeated merely to confirm them; the mandated final suite/lint run serves actual final-source acceptance.

| Cwd | Exact command | Immediate failing signal |
|---|---|---|
|root|`python3 -m pytest -q -p no:cacheprovider omega_prime/tests/ -k dashboard`|Nonzero exit, zero selected tests or collection blocked. Literal broad dashboard gate is mandatory.|
|root|`python3 -m pytest -q -p no:cacheprovider omega_prime/tests/`|Nonzero exit, zero tests or collection blocked; full Python suite for AC8, not substituted by dashboard-only tests.|
|root|`python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_dashboard*.py`|Nonzero exit/zero cases; additional family coverage, not a substitute for either broad gate.|
|frontend|`npm ci --registry=https://registry.npmjs.org`|Nonzero exit/registry unavailable/lock mismatch/unapproved dependency. Bootstrap once for a settled environment.|
|frontend|`npm test -- --run`|Nonzero exit, zero tests or watch remains. Full frontend suite.|
|frontend|`npm run build`|Nonzero exit, absent TypeScript check/output/import resolution. Only04 wave1 or05 wave2 owner runs.|
|frontend|`CC_CHECK_BUILD=1 npm test -- --run src/test/build-assets.test.ts`|Nonzero exit, zero build checks, missing local output/font/license or external/source-map leak. Must follow actual build.|
|root|`python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_dashboard_app_fixes.py omega_prime/tests/test_dashboard_command_center_static.py`|Nonzero exit/zero cases; linked current CSS must200, no pinned hash; preserve containment/security/package-data proof or honestly record missing setuptools.|
|root|`python3 -m ruff check omega_prime/dashboard omega_prime/tests/test_dashboard*.py`|Nonzero exit/unavailable module; distinguish supplied27 errors from newly introduced diagnostics.|
|root|`python3 -m mypy omega_prime/dashboard`|Nonzero exit/unavailable module; distinguish supplied7 errors and new diagnostics.|
|root|`polyglot-check --changed`|Nonzero exit/unavailable executable; no fabricated equivalent or hidden skip.|
|root|`python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_docs.py omega_prime/tests/test_public_repo.py`|Nonzero exit/zero cases; required docs/changelog consistency.|

`frontend` means exactly `omega_prime/dashboard/frontend`, whose package.json was opened. No npm lint script is invented. Full Python/frontend passes and new-regression paired evidence are distinct; neither proves live GitHub/Herdr or visual acceptance. When environment gaps remain, report incomplete AC8 instead of weakening its definition.

## Safety evidence, not source-string theatre

ASVS L1 configured; any unresolved high/critical finding blocks acceptance. Unique phase threat IDs01–18 are allocated across plans, with shared reserved T-106-SC only. This is a threat/verification strategy, not a certification.

-05 runner records only synthetic fixture argv in-memory and asserts exactly `["herdr","pane","read",pane,"--source","recent","--lines","200","--format","text"]`; never logs real argv/output. No shell/stdin path. Route tests assert bad Host403 and POST/PUT/PATCH/DELETE/HEAD/OPTIONS405 before Host check, no input endpoint/WS accepted, task ID not arbitrary pane, unknown404, invalid mapping/override rejected, max4 streams429/recovery and current-task revalidation.
- Redaction test must inspect actual emitted text, ANSI/OSC controls, Bearer/ghp_/github_pat_/sk-/JWT-like/KEY=value synthetic secret variants, quoted/split/boundary cases, multibyte65536-byte and200-line caps. No secret before redaction clipping; bounded capture overflow discards output, timeout/cancellation kills and reaps, no stderr exposed. Plain-text fake DOM proves HTML remains text. Preservation-only no-input baseline is not misrepresented as a preexisting vulnerability.
- No negative grep for an unqualified word is accepted as security proof. New tests assert behavior and actual argv allowlist, including cancellation. No count/match gates with unmeasured baselines are authored.
- Before sharing execution receipt/logs, use existing literal-text scanner `omega_prime.grokbot.secret_guard.assert_no_secrets`; do not pass a filesystem path as str and accidentally scan only its name. Example later exact command, root cwd: `python3 -c "from pathlib import Path; from omega_prime.grokbot.secret_guard import assert_no_secrets; p=Path('.receipts/bot-00-omega-prime/task00025.json'); assert_no_secrets(p.read_text(), name='phase106 execution receipt'); print('receipt text secret scan passed')"`. Nonzero exit or absent confirmation blocks publication. This scans receipt text, not screenshots or all source files; never claim broader no-secret proof from it. Apply the same API to actual sanitized source/log/evidence text before persistence/sharing; no secret values echoed on error.
- Screenshots use an operator-approved nonsecret pane/task fixture; inspect screenshot for accidental disclosure before saving/share. Do not capture a real secret as a redaction test. Real host no-input proof combines endpoint/method observations and product runner allowlist review/tests; screenshot alone cannot prove no input.

## Host-only7441 gate (AC1–AC7 live portions, AC9)

Prerequisites: approved export/current local feature head, real Herdr CLI with known readable nonsecret pane, authorized GitHub credentials already configured by approved host mechanism, managed browser tooling, and port7441 available. No new auth/token helper, remote setting, GitHub/Greptile/CI configuration or registry/package change. Do not print/copy token values. The earlier7421 observations are supplied input; never contact/restart/move that live service or touch its worktree/HOME/real task file.

Outer owner creates and retains a throwaway host HOME using `mktemp -d /tmp/cc106-home.XXXXXX` (records path privately, never swaps to real HOME), then runs grounded launcher `HOME="$CC106_HOME" python3 -m omega_prime.dashboard --host 127.0.0.1 --port 7441` as a named service with command-tool cwd set to the approved polish checkout. `$CC106_HOME` is exactly that newly created path; assert not the real HOME before launching. Existing launcher has --host/--port, no invented --tasks flag. Write only a separate temporary version1 tasks fixture under that HOME using existing write_tasks, mapping an approved nonsecret real pane through the new validated field; no automatic bridge population. Stop using the same throwaway HOME and `HOME="$CC106_HOME" python3 -m omega_prime.dashboard --stop`, not a generic kill/real pidfile. No server starts in planning.

Host checks (read-only, sanitized evidence only):

1. GET `http://127.0.0.1:7441/api/snapshot` via managed HTTP/browser tool, assert live GitHub meta has no recent_merges:error, recent_merges contains actual Omega-Prime and coding-fleet records (when live data contains both), and no false Partial source data notices on Overview/CI/CD/Repositories. Distinguish genuine too_large/offline/rate-limit failures from clean data; do not hide banners to pass. Confirm old merges remain stale with real reason in a separate controlled-failure fixture, not by changing live GitHub settings.
2. CI/CD has exactly one main card for each target, backend evidence/head wins; compare current live associated merged PR/score/reviewed SHA/default branch to GitHub read-only evidence. Direct commits show no-PR; lookup failure stays row-local unavailable. Do not call unavailable direct commit.
3. Exercise all four bucket links, active-toggle/All, unknown bucket, refresh/Back/Forward, task links from all surfaces, numbered full detail and origin Back after refresh. Inspect Idle separate count, long-title/path fixture, zero/one/many and compact/comfortable, keyboard/focus and320px/desktop wrapping. UI-SPEC backstop remains human-needed without these observations; no WCAG certification claim.
4. Open mapped task detail and observe two real changing sanitized output frames, distinct real frame timestamps, unchanged-heartbeat timestamp, paused scroll while stream continues, resume/copy, then no-session task. Confirm missing/closed/unavailable/reconnecting states with safe approved fixtures; host-specific absent-pane versus Herdr-unavailable classification must be actually observed, not inferred from generic nonzero status.
5. Inspect existing Host/method/CSP/security response headers on terminal, confirm no input controls/endpoint/WS/subcommand in final code/tests, and record no-secret scan plus approved screenshot inspection. Never type/send a command to the pane through the dashboard; no input capability is built or exercised.

Save five actual screenshots via managed browser screenshot tool, with route/viewport/timestamp/source-SHA metadata in receipt (paths are output targets, not claimed existing files):

- `.receipts/bot-00-omega-prime/task00025/overview-7441.png` — `http://127.0.0.1:7441/cc/#/overview`
- `.receipts/bot-00-omega-prime/task00025/bucket-7441.png` — `/cc/#/tasks?bucket=waiting` (or another recorded populated approved bucket)
- `.receipts/bot-00-omega-prime/task00025/task-terminal-7441.png` — actual `/cc/#/tasks/{encoded mapped technical ID}`, live sanitized output, not interim/fake panel
- `.receipts/bot-00-omega-prime/task00025/cicd-7441.png` — `/cc/#/cicd`
- `.receipts/bot-00-omega-prime/task00025/repositories-7441.png` — `/cc/#/repositories`

PNG bytes cannot be text-scanned by assert_no_secrets; visual inspection and a nonsecret fixture are separate evidence. Fakes, source review, tests or the wave1 interim mount cannot discharge live screenshot criteria. Missing host prerequisites keep these backstops unverified; finish all reachable offline work and state exact remaining host evidence.

## AC and multi-source coverage audit

All rows below are planned coverage, not exercised PASS. No source item is silently deferred. Only explicitly out-of-scope/deferred ideas (interactive control, auto bridge population, real-store/7421 changes, settings/publication/deploy) are excluded.

| SOURCE | ID | Feature / constraint | Plan / verification | Planned status |
|---|---|---|---|---|
|GOAL|ROADMAP106|Merges/dedup/review/routes/detail/density/Idle/read-only terminal|01–05; exact manifests, named regressions, final suites+host gate|COVERED|
|REQ|AC1|Live clean errors/banner; honest real-failure states|01 bounded fake+stale tests;04 shared notices/Overview/Repositories;03 CiCd caller and named distinct-timestamp failure regression; host7441 all three views|COVERED|
|REQ|AC2|Recent merges from Omega-Prime again, retain both repos|01 per-target30×10 cap then global sort; host snapshot/live screenshot|COVERED|
|REQ|AC3|One main card per target|02 mixed-SHA branch tests;04/05 builds; host CI/CD count|COVERED|
|REQ|AC4|Merged PR score/no-PR/unavailable, truthful SHA|03 fake GitHub+wording;04 shared detail/row labels; host parity|COVERED|
|REQ|AC5|Bucket routes, toggle/All/history|04 App.routes tests and host keyboard/history|COVERED|
|REQ|AC6|Full numbered task detail and density|01 rich finished/worktree;04 detail/density/links/Idle tests+host|COVERED|
|REQ|AC7|Mapped redacted read-only live terminal/no-session/no-input|05 full-stack fake tests+host real pane/security observations|COVERED|
|REQ|AC8|Every new regression FAILbase/PASSafter; full Python/frontend suites|All named rows using existing public seams, terminal expected200/base404 missing-feature evidence, actual implementation polish-only helper/security coverage, literal broad dashboard/full Python/npm gates and configured tools|COVERED|
|REQ|AC9|Five7441 screenshots/local commits|04/05 generated custody; outer host five PNGs/receipt; parent local-only commits|COVERED|
|CONTEXT|locked1|Smaller closed pages/limit+1/too_large/cache/security|01 task1 and exact named tests|COVERED|
|CONTEXT|locked2|Repo+branch backend precedence even different SHA|02 tasks1–2;03 preserves|COVERED|
|CONTEXT|locked3/c1|Validated associated merged PR, row-local state/provenance/body marker|03 tasks1–2;04 reserved types/labels|COVERED|
|CONTEXT|locked4/c2|Hash routes/history/unknown/clear/origin|04 tasks1–2|COVERED|
|CONTEXT|locked5|Source buckets, complete detail and density|01 metadata preservation;04 tasks1–2|COVERED|
|CONTEXT|locked6/c3|Optional strict pane mapping, server task lookup, explicit owner fallback|05 task2; no bridge population|COVERED|
|CONTEXT|locked7/c4|Exact argv, SSE guards/redaction/caps/lifecycle/plain text/states|05 task2 and safety matrix|COVERED|
|CONTEXT|locked8/c5|Five plans/two waves and n1–n8/tracker fidelity|All manifests/dispatch/topology table; no re-uplift/new trackers|COVERED|
|CONTEXT|addendum terminal states|done/failed Finished-only plus results/rich detail|01 test_done_and_failed_are_finished_only;04 finished detail|COVERED|
|CONTEXT|addendum Idle backend|Unknown/non-live/stopped/quiet only Idle; live remains Active; numbering/API/hash consumers|01 task2–3, poller read-only proof;05 later pane mapping|COVERED|
|CONTEXT|addendum Idle frontend|Separate Overview/sidebar/Tasks group, exclude Working|04 named Idle regression+host|COVERED|
|RESEARCH|architecture|Actual Starlette/_SSEResponse, no new server/router/dependency|04 native hash;05 existing guards/stream reuse|COVERED|
|RESEARCH|pagination|30×10 preserves300 candidates, early cap per target then global sort, original timestamps|01 named merge tests and cache preservation|COVERED|
|RESEARCH|consumer audit|Snapshot positional compatibility, active consumers, durable numbering, rich Finished/worktree, actual links|01 model/number/API;04 path_or_url/types/selectors;03 Idle wiring; legacy/poller intentionally unchanged|COVERED|
|RESEARCH|ownership/build|Terminal prerequisite05 only;04 after02,05 after03; pinned CSS hash removed|04 task3,05 task1/task3; dispatch matrix|COVERED|
|RESEARCH|security/cancellation|Association provenance, memory bound, redaction-before-cap, no-input, stream limit/disconnect|03 and05 exact tests/threats/host confirmation|COVERED|
|RESEARCH|environment/baseline|No import-only regression, no hidden missing deps, supplied lint baselines, offline fake tests|All verify tags; baseline inventory and full gates|COVERED|
|UI-SPEC|explicit eight categories|empty/loading/error/populated/partial/overflow/zero-one-many/long-text|04 tasks1–2,03 task2 CiCd section-notice caller/failure test and05 task2, named tests+preservation fixtures|COVERED|
|UI-SPEC|two backstops|Real GitHub/terminal and responsive visual five views|Structured must_haves backstops04/05 and host procedure|COVERED|

Context uses numbered locked decisions, not D-NN identifiers; no artificial decision/tracker IDs are introduced. AC local labels are the only requirement values. Architectural Responsibility Map agrees: network/normalization/authorization/redaction backend; route/presentation/browser lifecycle frontend; generated output only ordered builders.

## Receipt and completion semantics

Parent plan checker validates frontmatter/structure/source grounding after these artifacts land; planner did not run checkers/tests or commit. Execution writes the prescribed SUMMARY files. Before completion, outer owner writes `.receipts/bot-00-omega-prime/task00025.json` per verification-receipts skill, with actual commands/exits/counts/source SHA, expected baseline failures, preservation-only results, final full suites, generated manifest, host screenshot paths/timestamps, no-secret and no-input evidence, missing tooling/host prerequisites, contract changes and approvals. Always include `rule adherence by future agents` under unverified; never self-approve or invent a remote/PR URL.

A local/offline implementation-complete statement is narrower than phase acceptance. If full suites or live/screenshots remain blocked, retain exact limits and do not mark AC8/AC9 passed. No push, PR create, merge, auto-merge, force-push, deploy, credential helper, secret/real argv disclosure, real task-store write or7421 access. Parent owns local planning commit and subsequent authenticated host handoff.
