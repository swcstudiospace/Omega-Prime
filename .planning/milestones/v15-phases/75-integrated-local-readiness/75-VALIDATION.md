---
phase: "75"
slug: integrated-local-readiness
status: validated
nyquist_compliant: true
wave_0_complete: true
created: "2026-10-10"
---

# Phase75 — Validation

## Scope and verdict

The prior independent static review started at **2026-10-10T16:43:33Z** and read all eleven PLAN/SUMMARY sets, task verification blocks, the previous map, REQUIREMENTS and pytest configuration. This narrow update retains that inherited 25-task/seven-requirement assessment and the closed G-75-NYQ-01 eligible-failure/unknown-canonical coverage. It additionally reads the launcher/controller usage guards, their seven new consumer regressions, actual failed-before/repaired-after CLI receipts and the parent's completed post-usage union output. Plan04's exact negative obligations and the eleven-plan task/requirement declarations remain unchanged. This auditor ran **no tests, builds, gates, smokes, linters, typechecks, formatters or commits**, and changed only this file.

**Verdict: GAPS FILLED — 7/7 Phase75 requirements covered and 25/25 tasks mapped green within the stated local boundaries; G-75-NYQ-01 remains FILLED.** Parent-exercised real CLI negatives prove both an eligible production `read_file` failure and a separate unexplained canonical absence survive complete report aggregation as `ok=false` and exit 1. The healthy CLI regression requires exit0, `ok=true` and zero failed checks/tools. The two later-discovered raw usage-publication boundaries are covered by seven passing consumer cases in the observed2129-test union and five actual repaired CLI receipts: exit2, empty stdout, no recognized canary echo and fixed safe labels, with clean usage guidance preserved. Coverage is based on consumer behavior and observed receipts, not source/wiring echoes or aggregate counts. Environment/recognition/integration limitations below remain warnings; this verdict does not close phases76–78 or replace canonical phase verification.

Phase75 retains exactly LOCAL-01..03, FLOW-01..02 and GROK-01..02. All 21 milestone requirements remain in REQUIREMENTS; the other fourteen belong to phases76–78 and are not closed here. Parent owns SECURITY/control/disposition, audit-trail append, source/test repairs, commits and phase transitions.

## Test Infrastructure

| Property | Value |
|---|---|
| Framework/config | Existing pytest; `pyproject.toml` sets `testpaths = ["omega_prime/tests"]`, `pythonpath = ["."]`; existing `.venv/bin/python`, no installation |
| Full regression command (F) | `.venv/bin/python -m pytest omega_prime/tests -q -rs` |
| Intake/slash commands (I / W) | `.venv/bin/python -m pytest omega_prime/tests/test_lead.py omega_prime/tests/test_desk_wiring.py omega_prime/tests/test_desk_lead.py -q` / `.venv/bin/python -m pytest omega_prime/tests/test_omega_commands.py omega_prime/tests/test_omega_surface.py -q` |
| Diagnostic commands (D / V) | `.venv/bin/python -m pytest omega_prime/tests/test_grokbot_secret_guard.py omega_prime/tests/test_grokbot_diag_bundle.py -q` / `.venv/bin/python -m pytest omega_prime/tests/test_grokbot_verify.py omega_prime/tests/test_grokbot_oneclick.py -q` |
| Readiness command (R) | `.venv/bin/python -m pytest omega_prime/tests/test_grokbot_readiness.py omega_prime/tests/test_grokbot_readiness_cli.py omega_prime/tests/test_grokbot_readiness_prime.py -q` |
| Focused negative CLI command (C) | `.venv/bin/python -m pytest omega_prime/tests/test_grokbot_readiness.py::test_cli_negative_owned_read_file_failure omega_prime/tests/test_grokbot_readiness_cli.py -q` |
| Usage-publication cases (U; reproducible selection, not separately executed here) | `.venv/bin/python -m pytest omega_prime/tests/test_grokbot_oneclick.py::test_parser_guard_withholds_recognized_canary omega_prime/tests/test_grokbot_oneclick.py::test_parser_guard_keeps_clean_choice_helpful omega_prime/tests/test_grokbot_readiness_cli.py::test_cli_unknown_secret_shaped_argument_withheld omega_prime/tests/test_grokbot_readiness_cli.py::test_cli_secret_shaped_missing_root_withheld omega_prime/tests/test_grokbot_readiness_cli.py::test_cli_benign_missing_root_stays_helpful -q` |
| SDK/mobile/docs commands (M / X / N) | `.venv/bin/python -m pytest omega_prime/tests/test_mcp_session.py omega_prime/tests/test_platform.py -q` / `.venv/bin/python -m pytest omega_prime/tests/test_mobile.py -q` / `.venv/bin/python -m pytest omega_prime/tests/test_docs.py omega_prime/tests/test_public_repo.py -q` |
| Maintained profiles (L / P) | `.venv/bin/python -m omega_prime.grokbot.readiness --root . --json --output /tmp/phase75-owned-local.json` / same command with an owned distinct output path and `--include-prime` |
| Host self-test (S) | `.venv/bin/python -m omega_prime.grokbot.oneclick --root . --self-test` |

Command letters below refer to reproducible commands, not additional executions by this auditor. Inherited regressions, both aggregation negatives and all seven new usage cases are backed by the parent's completed current2129-test F union. The earlier C selection has its separate 2/2 passing result; C now also selects the three new readiness usage cases, so that earlier result is not presented as a fresh run of today's complete C selection. U is a reproducible seven-case selection, not a separately observed command result. Source scenario assertions count as exercised only where actual profile receipts show successful execution. The targeted 122-test command is not recorded in the summary receipt, so its exact selection is not invented here.

## Current parent-exercised evidence

Authoritative latest receipt: inspected `local://v15-native-phase75-usage-verification.json`, verified at **2026-10-10T17:23:42.930299+00:00**. It records completed F **2129 passed / 0 failed / 0 skipped / 16 warnings /154.84s**, exit0 (`artifact://246`, raw output independently read), postguard healthy L/P profiles, quality gates,22 host checks/shutdown0, usage before/after receipts and preservation. Current scope remains `local://v15-native-phase75-closure-scope.json` (87 paths). The preceding inspected `local://v15-native-phase75-closure-verification.json` snapshot at **2026-10-10T17:13:01.592246+00:00** records2122/`artifact://231` and the exact earlier2/2 C result; it is retained as historical, not relabeled. Negative runtime receipt: `local://v15-native-negative-readiness-cli.json`. The earlier final-verification receipt/`artifact://212`2116-test union and86-path scope also remain provenance. No count is inferred from test additions.

| Evidence | Actual result | Provenance and boundary |
|---|---|---|
| Full current-source pytest union | **2129 passed, 0 failed, 0 skipped, 16 warnings,154.84s**, exit 0 | F; inspected usage receipt `full_pytest`, raw `artifact://246` and parent completion message; includes strict healthy CLI, both aggregation negatives and all seven new usage cases; preceding2122/156.66s/`artifact://231` remains historical; dependency and POSIX fork warnings retained |
| Targeted publication/oneclick/readiness/MCP/docs/public tests | **122 passed**, exit 0, 62.15s | Final receipt `targeted_security_consumer_suites`; not 122 independent requirement proofs |
| Earlier focused readiness suite | **39 passed**, exit 0 | Receipt `readiness_regressions`; retained earlier aggregate, not relabeled as a fresh separate run; current readiness tests also pass in F |
| Earlier actual aggregation-negative CLI regressions | **2 passed, exit0,29.58s** | Closure and usage receipts retain `negative_cli_regressions`; parent job `bg_40` ran the earlier C selection after an unchanged format step; this is not a fresh five-case C run after the three readiness usage additions; no execution by this auditor |
| Throwaway actual negative CLI scenarios | Both **exit1 / ok=false**; output-file JSON matches stdout | `local://v15-native-negative-readiness-cli.json`; eligible-handler retains one failed check even with `read_file` exercised later; separate unknown-canonical case has one failed/unmapped row and named problems |
| Actual launcher/controller usage rejection, before → after | **Five cases:** exit2 and empty stdout throughout; recognized candidate echoed **true → false**; every repaired case has its fixed safe label | Inspected `local://v15-native-launcher-parser-before.json` / `local://v15-native-launcher-parser-after.json` (three cases) and `local://v15-native-readiness-usage-before.json` / `local://v15-native-readiness-usage-after.json` (two cases); actual CLI receipts, not mocked stderr |
| Seven new usage consumer cases | **All pass in F**; three launcher recognized-canary rejections, one clean launcher choice, two controller secret-shaped refusals and one clean controller root | Exact names and behavioral assertions below; no independently run U count claimed; parent also reports clean usage guidance preserved |
| Actual production-loopback child observation | **1 passed**, exit 0, 10.73s | `test_grokbot_readiness.py::test_loopback_spawn_inherits_only_explicit_env`; production `_run_loopback`, real attached emulator/host, reopened Boolean key-presence marker |
| Adversarial ambient-env mutation | Mutant pytest exit **1 at the reopened child-presence assertion**, caller-only credential/endpoint/probe observed present | `local://v15-native-loopback-env-observed-mutation.json`; in-memory production-function mutation in an owned credential-free subprocess; source unchanged; no sentinel values published |
| Real oneclick host conformance | **22 pass / 0 fail / 0 skip**, exit 0, **shutdown=0** | S; usage receipt `host_selftest`, latest post-parser-guard run; earlier raw `artifact://205:4-26` retained; both transport initializations/tool calls, auth/query/origin refusal, manifest, admin scope and approval roundtrip |
| Existing evals | **26 passed**, exit 0 | Final receipt `evals`; not a substitute for consumer effects |
| Latest project checks exercised by parent | Ruff; **399-file** format check; mypy **337 files**; **26 evals**; catalog; assembly; setup **117 served**, all exit0 | Latest usage receipt; prior JSON's398/336 retained as earlier provenance, not relabeled; optional substrate/ultrathink skips remain explicit; no check executed by this auditor |
| Preservation | Frozen guard/security/manifest/diagnostic/interceptor and source session DB SHA values equal; native size/mtime/inode unchanged; no rebuild | Usage receipt `preservation.post_usage_equal=true`, retained matching hashes/native tuple and parent confirmation; upstream/worktrees/.swarm not modified by this audit |

### Complete current roster accounting

| Profile | Canonical rows | Offered | Exercised | Blocked | Feature-disabled | Failed | Exit / ok |
|---|---:|---:|---:|---:|---:|---:|---|
| L: local-only | 165 | 117 | 50 | 67 | 48 | 0 | 0 / true |
| P: include-prime | 165 | 154 | 82 | 72 | 11 | 0 | 0 / true |

Latest postguard complete-profile receipts: `local://v15-native-phase75-usage-local.json`, `local://v15-native-phase75-usage-prime.json`; the inspected usage-verification receipt records both165-row profiles above, exit0/`ok=true`, failed0 and `problems=[]`. Earlier complete artifacts `local://v15-native-phase75-final-local.json` and `local://v15-native-phase75-final-prime.json` remain provenance for detailed consumer effects: the eleven actual `tempest_*` rows are feature-disabled with `redteam.enabled=false verified absent`, empty prerequisites, and no Red Team backend call. L additionally disables the37 Prime names; P enables only Prime, not Red Team. The postguard rerun preserves those counts without treating blocked/disabled rows as exercised. Offered names are a separate comparison, never the expected universe; discovery, approval-only controls and negative refusals do not increment exercised tools.

## Meaningful requirement-to-test mapping

| Requirement | Behavioral regression / exercised consumer proof | Coverage |
|---|---|---|
| LOCAL-01 | `test_loopback_spawn_inherits_only_explicit_env` observes the **actual production-spawned** child; mutation fails at its reopened observation. `test_layout_roots_under_base_and_env_restored`, cleanup/error tests, nested learned-data export, excluded-target aliases, regular credential Python, excluded ancestors and Prime `.env` omission inspect real owned filesystem effects. `test_exported_memory_session_code_imports` keeps legitimate code usable. `test_self_test_leaves_source_schedules_untouched` seeds owned source schedule bytes and verifies unchanged bytes plus empty nested fixture schedules. F/R/V and actual L/P/S; frozen/source DB/native preservation receipt. | covered; owned local isolation, not an OS/network sandbox or arbitrary source-content classifier |
| LOCAL-02 | Existing flag/accounting/validator negatives prove row/status/prerequisite I/O; L/P account all 165 canonical names separately from offered names. `test_cli_negative_owned_read_file_failure` dispatches real `read_file` on an owned directory through the genuine CLI and retains failed-check/global-failure evidence. `test_cli_unexplained_canonical_absence_fails` separately keeps the original roster names plus one owned unknown name and observes its failed/unmapped row, named problem and exit1/ok=false. Earlier C passed 2/2; actual throwaway receipts independently match both outcomes. Controller usage consumers additionally prove unknown-canary and secret-shaped missing-root exit2/no echo/no report creation, while a benign missing root remains helpful (R/U/F). **G-75-NYQ-01 FILLED.** | covered; local CLI negatives, not live integration success |
| LOCAL-03 | L/P execute production emulator/registry/owned-host consumers: reopened file write/read/patch/edit/search, workspace escape refusal, TODO/cache effects labeled in-memory, memory reload, skill bytes, command-log-backed receipts, owned Git/mobile comparisons and actual LSP diagnostics. SDK tests `test_initialized_session_writes_and_reads_real_bytes`, `test_same_real_effect_inside_running_loop`, and platform public `test_mcp_call_runs_the_initialized_session_with_a_real_file_effect` reopen actual file bytes. Mobile valid-XML one-to-two, zero-to-one and container-only cases assert issue counts/IDs/growth, not verdict text. | covered; no external-service mutation claimed |
| FLOW-01 | `test_warm_registry_consumes_independent_intake_and_acks`: runtime precedes independent producer; original registry claims original id/ask, green doctor and queue transitions, public ack persists done. Storage process-race/interleaved-write tests protect updates and claims. L/P `warm-intake-delegation` additionally uses real dispatched file effects before public ack and fresh observer fields. `test_real_doctor_contextual_health_and_repeat`, missing/extra-context/approval tests and real incomplete-desk case prove semantic outcomes; L/P exercise onboard/connectors/python-clean/retain/recall. | covered; no live caller inventory or lease fencing asserted |
| FLOW-02 | L/P `cron-desk-pass-blocked-then-model-double` proves actual child tool turns, persisted file and plain memory via fresh MemoryStore/reopened bytes, separate receipt-backed lead-bank recall, done orders and reopened jobs/rescheduling. It explicitly preserves execution-backed rejection of uncaptured shim commands. Learning skill_view and actual durable journal consumers run. P harness rollback/delete, goal transitions, recipient inbox delivery, bound heartbeat callback/stop/close, driven autonomous turns, reopened RLM metadata/delete and persistent native cells/callable crate probes have effect assertions. | covered; model-double, in-memory messaging and journal boundaries retained |
| GROK-01 | L/P `_loopback_transport` on **both SSE and Streamable HTTP** proves landed owned file effects, denied cache set with no mutation, targeted grant/set/get, revoke/denied replacement, regrant-read of unchanged value, final revoke; audit bytes exclude minted tokens; SIGTERM exit 0. S independently exercises 22 auth/manifest/transport/admin checks. D/V prove collector abort-before-create/replace and diagnostic publication failure/nonzero boundaries, including verifier before/after evidence. V/R/U/F add exact launcher/controller usage consumers and five actual failed-before/repaired-after receipts below; clean guidance remains useful. | covered; approval/cache state is in-memory; secret recognition remains bounded |
| GROK-02 | L/P runtime manifest set/digest/approval agreement plus parent-passed catalog/assembly/setup on current source reconcile the real served surface. Consumer verifier `test_lying_manifest_fails_and_cli_exits_1` rejects dishonest manifest; `test_query_token_accepted_is_a_failure` rejects unsafe auth. N checks doc navigation/links/assets only; claims are separately compared against exercised receipts and explicit provider/device/browser/service/attachment prerequisites. | covered; local preparation, never live Grok attestation |

Source-text, wiring/mock-call copies, help/default/order pins, length-grown assertions and repinned roster counts were not accepted as requirement proof. Pure accounting I/O tests are meaningful **unit** status tests, not production backend or profile execution. Self-test process-seam fixtures prove publication/caller/schedule effects only, not real host startup; the actual S receipt supplies host execution.

## Per-Task Verification Map

All 25 tasks across eleven plans are retained, with all seven Phase75 requirements covered. “green” means the stated behavior has passing consumer tests in the current2129-test F union, the separately parent-passed earlier C negatives and/or an actual L/P/S execution with source effect assertions. It does not mean every test in a file is behavioral or that every command below was separately run.

| Task ID | Plan / wave | Requirement | Observable obligation and meaningful evidence | Command | Status |
|---|---|---|---|---|---|
| 75-01-01 | 01 / 1 | FLOW-01, FLOW-02 | Warm public reads; stale producers/acks; exclusive process claim; corrupt bytes preserved; repeated doctor SHA survives | I, L/P | green |
| 75-01-02 | 01 / 1 | FLOW-01, FLOW-02 | `test_warm_observer_sees_independent_submit`, stale FIFO/merge, process races, renewed lease, replace failure, Unicode and corrupt-file cases | I | green |
| 75-01-03 | 01 / 1 | FLOW-01, FLOW-02 | Warm registry consumes then publicly acks; `test_interleaved_producer_and_claim_survive_dispatch` preserves foreign claim/fields and real artifact | I, L/P | green |
| 75-02-01 | 02 / 1 | GROK-01, GROK-02 | Same actual file str rejection versus clean0600 Path acceptance; wrong mode; slash/generic/PEM rejection without echo; benign inputs pass | D | green |
| 75-02-02 | 02 / 1 | GROK-01, GROK-02 | Doctor/supervisor/audit poison aborts before new file or sentinel replacement; successful bundle reopens private and excludes raw manifest/audit token bytes | D | green |
| 75-03-01 | 03 / 1 | LOCAL-01, GROK-01 | Real isolated S host/auth/transports/shutdown; shared actual child/env isolation; source schedule and caller env tests; verifier guarded publication | S, R/V | green within stated seams |
| 75-03-02 | 03 / 1 | LOCAL-01, GROK-01 | Seeded source schedule preservation; not-ready/verification exception and poisoned report publish safe metadata, exit1; whole-input canary fragments absent; launcher choice/typed-value/unknown-argument refusals exit2 without echo and clean choice remains helpful | V/U/F, S | green |
| 75-04-01 | 04 / 1 | LOCAL-01, LOCAL-02, GROK-01, GROK-02 | Owned roots/export/cleanup; actual production child observation and mutation; both-end admission; full roster helper status tests | R, L/P | green |
| 75-04-02 | 04 / 1 | LOCAL-01, LOCAL-02, GROK-01, GROK-02 | Complete healthy JSON165 rows, manifest, both transport file/cache approval effects and audit/shutdown; actual eligible-handler and unknown-canonical CLI negatives preserve failed checks/rows/problems and exit1/ok=false | L/P, R/C | green; G-75-NYQ-01 FILLED |
| 75-04-03 | 04 / 1 | LOCAL-01, LOCAL-02, GROK-01, GROK-02 | Existing export/env/publication/accounting effects plus both actual aggregation negatives; owned-source preservation/cleanup, private0600 JSON, retained failed check; healthy CLI strictly exit0/ok=true/zero failures; controller usage canary/missing-root refusals exit2 without echo/output-file creation and benign root guidance preserved | R/C/U/F | green; G-75-NYQ-01 FILLED |
| 75-05-01 | 05 / 1 | LOCAL-02, LOCAL-03 | L/P baseline real file/TODO/memory/skill/cache/owned comparison effects and named unconfigured services | L/P | green |
| 75-05-02 | 05 / 1 | LOCAL-02, LOCAL-03 | Actual terminal/code rows reopened; captured receipt accepted and uncaptured rejected; SDK public MCP writes/reads HOME bytes | L/P, M | green |
| 75-06-01 | 06 / 1 | FLOW-01, FLOW-02 | Prepared doctor context, all onboard steps/audits, red doctor remains incomplete, python-clean and retain/recall consumer effects | L/P, W | green |
| 75-06-02 | 06 / 1 | FLOW-01, FLOW-02 | Warm producer/consumer/file/public ack/fresh observer; cron blocked requeue then labeled model-double turns, jobs/orders/receipt and rescheduling persisted | L/P, I | green |
| 75-06-03 | 06 / 1 | FLOW-01, FLOW-02 | Capture → skill_view persisted content; advisor render; durable_status actual INSTALL/cron/turns.sqlite and workflows, not desk-parent journal | L/P | green |
| 75-07-01 | 07 / 1 | LOCAL-02, FLOW-02 | Genuine OFF absence/refusal, ON fixture, harness snapshot rollback/delete, goal transitions and independently read recipient inbox | L/P | green |
| 75-07-02 | 07 / 1 | LOCAL-02, FLOW-02 | Bound heartbeat runner observes scheduled prompt then stop/close drops binding; two actual after_turn payloads continue/stop at budget | P | green |
| 75-07-03 | 07 / 1 | LOCAL-02, FLOW-02 | Model-double RLM metadata reopened after shutdown/delete; native namespace42 and nine callable crate probes; genuine factory prerequisite blocks; real refusal publication test | P, R | green within model/native limits |
| 75-08-01 | 08 / 2 | LOCAL-01, LOCAL-02, FLOW-01, FLOW-02, GROK-01, GROK-02 | Runnable current commands/165 accounting and honest limits compared to receipts; N verifies navigation/links/assets, not capability by prose | N, L/P/S | green for documented exercised scope |
| 75-09-01 | 09 / 1 | FLOW-01, FLOW-02 | Registered/direct real prepared doctor green/repeat; missing/extra context non-green; no approval bypass; incomplete workflow retains later effects | W, L/P | green |
| 75-09-02 | 09 / 1 | FLOW-01, FLOW-02 | Existing real runtime contextual/missing/extra/approval/incomplete-desk regressions, not context echo/schema assertions | W | green |
| 75-10-01 | 10 / 1 | LOCAL-02, LOCAL-03 | Official SDK real effect outside/inside running loop; refusal starts nothing; bounded timeout reaps child/descendant; real child env/stderr isolation | M | green |
| 75-10-02 | 10 / 1 | LOCAL-02, LOCAL-03 | Public platform mcp_call writes/reads real owned-home file; string/missing cwd refusal; canonical source cutover, obsolete client removed with no shim | M, L/P | green |
| 75-10-03 | 10 / 1 | LOCAL-02, LOCAL-03 | SDK real effects/error+result/refusal/timeout/PID cleanup/active-loop/private diagnostics; peer result content not represented as universally secret-safe | M | green |
| 75-11-01 | 11 / 1 | LOCAL-03 | Valid issues-root XML counts one→two and zero→one, metadata-only no growth, added IDs; actual owned Git diff L/P returns1/2 | X, L/P | green |

## Repaired publication and child-observation boundaries

### Current verifier publication proof

The actual before/after receipts use a synthetic exception through the **real verifier `_step` and public formatter**, with no network request:

| Receipt | Full input rejected by unchanged guard | Fragment published | Formatted output accepted by guard | report.ok |
|---|---|---|---|---|
| `local://v15-native-verifier-publication-before.json` (historical failed-before) | true | **true** | true | false |
| `local://v15-native-verifier-publication-after.json` (current repaired boundary) | true | **false** | true | false |

Current `verify.py::_scrub_detail` performs known-token replacement and audit redaction over whole input, unchanged strong classification **before** flatten/clip, then final clipped classification. `_add` makes a nominal pass fail on a trip. `sanitize_report_for_publication` rechecks stored details and full `base_url`/`server_version`; metadata trip adds a failing publication check. The CLI and self-test publish the sanitized report and derive failure from it; CLI exception text also goes through the whole-input guard. Raw verifier producer windows are removed.

Meaningful existing V cases pass in the current2129-test union; the earlier targeted122 selection remains separate provenance:

- `test_scanner_only_candidate_across_clip_edge_is_withheld`: qualifying full generic candidate straddles the old window; fixed safe detail and absent fragments in JSON/human output.
- `test_secret_smuggled_in_passing_detail_fails_closed`: nominal pass becomes fail, report.ok false.
- `test_short_omk_exception_diagnostic_is_withheld`, `test_pem_exception_diagnostic_is_withheld`: exception paths retain failure without fragments.
- `test_known_token_redaction_preserves_passing_outcome`: clean known-token redaction stays passing, preventing reject-everything coverage.
- `test_sanitize_withholds_secret_metadata_and_stays_failed`: injected version metadata cannot publish as success.
- `test_cli_publication_withholds_secret_metadata`: actual owned HTTP health fixture, both human and JSON CLI exit1 and withhold version data.
- `test_cli_usage_error_with_secret_env_label_is_withheld`: actual CLI usage error exits2 without secret label/fragment.
- `test_self_test_withholds_secret_bearing_report`: clean host seam/shutdown0 cannot turn poisoned nominal-pass details/metadata into self-test success; exit1 and fixed labels.

Readiness/Prime additionally retain `test_curate_withholds_spanning_generic_token`, probe failure detail coverage, actual real `rlm_rename` refusal with full low-entropy-prefix/high-entropy-tail candidate, and trailing-secret withholding while earlier safe probe evidence remains unchanged. The latter protects **probe evidence**, not a filesystem report replacement. Collector sentinel preservation is separately proved by D; no mocked writer call is substituted for on-disk bytes.

### Launcher and controller usage publication: actual failure, repair and consumer coverage

These were **observed publication defects**, not hypothetical wiring gaps: raw argparse rejection text and the readiness missing-root diagnostic echoed recognized synthetic canaries before their respective repairs. They extend the existing GROK-01 publication and LOCAL-02 controller coverage, mapped to75-03-02 and75-04-03 above; no requirement or task was added, waived or narrowed.

| Boundary / exact actual CLI case | Historical before receipt | Current after receipt | Observed before → after |
|---|---|---|---|
| Launcher `choice`, `typed-value`, `unknown-argument` | `local://v15-native-launcher-parser-before.json` | `local://v15-native-launcher-parser-after.json` | Each exit2, empty stdout; `candidate_echoed=true` → `false`; repaired `safe_label=true` |
| Controller `unknown-argument`, `missing-root` | `local://v15-native-readiness-usage-before.json` | `local://v15-native-readiness-usage-after.json` | Each exit2, empty stdout; `candidate_echoed=true` → `false`; repaired `safe_label=true` |

All seven exact consumer cases below passed in the parent's completed F union (`artifact://246`). Launcher cases call the real `main` and inspect real captured streams; the two host-entry sentinels only detect forbidden launch, and their mock wiring is **not** the publication proof. Controller cases use real CLI subprocesses. No separate U execution, fresh mutation run or debug iteration by this auditor is claimed.

| Exact regression (path relative to `omega_prime/tests/`) | Type | Observable acceptance |
|---|---|---|
| `test_grokbot_oneclick.py::test_parser_guard_withholds_recognized_canary[choice]` | CLI-entry integration | Real invalid transport choice exits2; stdout empty; complete canary and suffix absent from both streams; fixed `self-test: diagnostic withheld (secret gate tripped)` present; no host entry |
| `test_grokbot_oneclick.py::test_parser_guard_withholds_recognized_canary[typed-value]` | CLI-entry integration | Real integer-port conversion rejection meets the same exit2/no-echo/suffix/safe-label/no-host assertions |
| `test_grokbot_oneclick.py::test_parser_guard_withholds_recognized_canary[unknown-argument]` | CLI-entry integration | Real unknown argument rejection meets the same exit2/no-echo/suffix/safe-label/no-host assertions |
| `test_grokbot_oneclick.py::test_parser_guard_keeps_clean_choice_helpful` | CLI-entry integration | Benign invalid choice still exits2, prints usage/invalid-choice text and actual `stdio`/`sse` options, leaves stdout empty and does not substitute the withheld label |
| `test_grokbot_readiness_cli.py::test_cli_unknown_secret_shaped_argument_withheld` | Integration (real CLI subprocess) | Exit2; stdout empty; recognized argument absent from stdout/stderr; `usage error withheld: secret gate tripped` and ordinary usage present |
| `test_grokbot_readiness_cli.py::test_cli_secret_shaped_missing_root_withheld` | Integration (real CLI subprocess) | Exit2; stdout empty; secret-shaped root absent from streams; fixed withheld label present; owned requested report file never created |
| `test_grokbot_readiness_cli.py::test_cli_benign_missing_root_stays_helpful` | Integration (real CLI subprocess) | Exit2; stdout empty; `no canonical roster` and benign root remain visible; withheld label absent |

Current source explains these observed effects without substituting for them: `oneclick._GuardedParser.error` routes full error text through the existing `_bounded_diagnostic` gate before stderr, keeps ordinary usage and exits2. `readiness._UsageGuardedParser.error` gates argparse messages; `_fail_usage` gates controller diagnostics. `_guard_usage_text` catches **only `SecretLeakError`**, and both parser overrides use typed `NoReturn`; no catch-all or invalid-noqa suppression is claimed as the repair. Clean-control cases prevent reject-everything coverage. The five historical/current receipts establish real failed-before/repaired-after behavior for these exact recognized inputs, not protection for arbitrary secrets or every possible usage form.

### Production child observation, not copied-env proof

`test_loopback_spawn_inherits_only_explicit_env` calls production `local_validation._run_loopback` with the real emulator. Only the disposable mcp_server copy gains an observer after its future import. An explicitly owned marker path is present in both fixture and caller environments, so an ambient-inheritance mutant still starts and records leaked keys. Reopened marker JSON must show the caller-only credential, endpoint and probe keys **all absent**. The source function starts the host using `dict(layout.env)`.

`local://v15-native-loopback-env-mutation-proof.json` is preserved as **NOT PROOF**: initial mutant exited1 because host startup failed before key presence was observed (`actual_consumer_regression_caught=false`). It is not hidden, and it closes no gap. The distinct later `local://v15-native-loopback-env-observed-mutation.json` is the accepted sensitivity receipt: mutant exit1 specifically at the reopened Boolean presence assertion, all three caller-only keys observed present, source unchanged.

## Closed validation gap: G-75-NYQ-01

**Disposition: FILLED.** This was the sole open finding from the prior independent Nyquist PARTIAL, shared by LOCAL-02 tasks75-04-02/03. Parent job `bg_40` ran C after an unchanged format step and supplied **2 passed, exit0,29.58s**. No tests or debug iterations were run by this auditor; no implementation failure or mutation execution is invented.

| Exact regression | Type / command | Observed acceptance and boundary |
|---|---|---|
| `omega_prime/tests/test_grokbot_readiness.py::test_cli_negative_owned_read_file_failure` | Integration (real CLI subprocess); C | Only an owned vetted export's scenario copy is changed. It creates a WORK directory and calls production `probe.call("read_file", ...)` against it; production dispatch fails. The real controller exports that fixture again, runs the fresh product child, collects later scenarios/transports, validates and publishes its report. Assertions require exit1, `ok=false`, a retained failed check naming `read_file`, failed tools, exactly-once unchanged canonical membership, and a `read_file` row that is failed or legitimately exercised with evidence, never blocked/disabled. Reopened output equals stdout JSON, mode0600, caller sentinels absent, original scenario bytes unchanged and outer layout deleted. |
| `omega_prime/tests/test_grokbot_readiness_cli.py::test_cli_unexplained_canonical_absence_fails` | Integration (real CLI subprocess); C | Only an owned canonical roster receives `zz-synthetic-unexplained-absence-canary`; vetted inventory is unchanged and the original baseline names remain. The genuine report has that exact extra canonical row failed/unmapped, nonempty evidence, empty prerequisites, a named absence/unmapped problem, preserved baseline membership, exit1/`ok=false` and private0600 publication. It does not delete the expected universe or inject a prefailed report. |

`local://v15-native-negative-readiness-cli.json` is separate actual throwaway evidence, not a claim that its fixture names/counts are the permanent test's identical output. The eligible-handler case has **one failed check**, exit1/ok=false, `read_file` exercised with real `warm-intake-delegation` and both loopback file-roundtrip evidence, **95 failed tools**, and output matching stdout. Those additional failures follow the early scenario abort; this is a negative fixture, not a healthy readiness profile. The unknown-canonical case uses the distinct name `zz-owned-unexplained-canonical`: **166 rows,117 offered,50 exercised,67 blocked,48 disabled,one failed**, zero failed checks, both named unmapped/absence problems, exit1/ok=false and matching publication.

Production source explains, but does not substitute for, these effects: `ReadinessProbe.call` dispatches the emulator and raises on a failed envelope; `_run_scenario_module` retains that failed check; `_run_profile` continues workflows/loopback and computes failure from checks/tools/problems; the CLI publishes the validated report and derives exit from its `ok`. Later legitimate `read_file` successes do not erase the earlier failure. Both permanent negatives would reject an unconditional-success final report or exit; **no final-aggregation mutation run is claimed**. These cases do not independently isolate every Boolean conjunct (the eligible case also produces failed tool rows), and do not exercise live prerequisites.

`test_cli_full_local_profile` is the complementary healthy consumer test: exit0, `ok=true`, no failed checks and no failed tool rows, not the former permissive `(0,1)` exit assertion. Its inherited strict-source assessment is retained and its current execution is backed by the parent's completed2129-test union, not attributed to the old2116/2122 receipts.

### Historical G-75-NYQ-01 assessment (superseded, preserved)

The following assessment and requested acceptance are retained verbatim from the prior PARTIAL. References to “current” and “open” in this historical block describe that earlier snapshot, not the disposition above.


| Gap ID | Tasks / requirement | Classification | Actual evidence and missing acceptance |
|---|---|---|---|
| G-75-NYQ-01 | 75-04-02, 75-04-03 / LOCAL-02 | **WARNING — passing tests with partial end-to-end negative coverage; ESCALATED to parent** | `test_failed_scenario_module_records_failed_row` (readiness tests925–955) checks only probe rows; accounting negatives456–636 check helper rows/problems; `test_validate_report_rejects_gaps`958–983 calls the validator alone. `test_cli_full_local_profile`1005–1047 permits either exit0 or1 and checks exit↔report.ok but never injects an eligible failure. Healthy current L/P receipts cannot distinguish a regression setting `_run_profile` ok=true despite failed checks/tools/problems (source1639–1641). No current exercised negative through that complete aggregation boundary is supplied. |

**Minimal consumer acceptance for the parent (no implementation change presumed):**

1. In an owned vetted export, induce a real eligible scenario failure without replacing the controller, `_run_profile`, accounting, report builder or CLI. Run the actual readiness CLI. Require exit1, reopened/stdout JSON `ok=false`, the actual failed check retained, and no relabeling as `blocked` or `feature_disabled`; an exercised sibling may remain exercised.
2. In a separate owned fixture, make one ordinary canonical tool genuinely absent/unexplained (or enabled-but-missing), leaving the relevant negative controls real. Require the missing tool's `failed` row plus named accounting problem, global `ok=false` and exit1. This must not be satisfied by a copied report with its ok field already false.

These tests/receipts should fail if the source's final `ok = not failed_checks and not failed_tools and not problems` is replaced by unconditional success. This is a **coverage finding**, not an observed implementation bug or a fabricated failed test. No debug/test iterations were run by this auditor; parent owns generation, execution and any source fix. The original historical Red Team failures below prove a prior accounting defect was observable, not sensitivity of this current eligible-failure aggregation path.

## Historical failed-before provenance (not current passes)

| Historical boundary | Retained observation | Current evidence / distinction |
|---|---|---|
| Red Team roster omission | `local://v15-01a125ac-readiness-local-blocked.json` and Prime counterpart:165 rows,11 `absent-from-offered` failures, ok=false; default37 and Prime0 disabled | Current L/P165-row receipts instead show correct actual off-flag eleven disabled rows. Red Team backend remains uncalled. |
| Self-test/readiness full-input classification before clipping (T-75-03-04 / T-75-04-03) | Full generic token rejected but clipped fragments published/accepted; earlier review reopened this | Current V/R whole-input generic/short-omk/PEM cases and final receipts; verifier's separate actual before/after recorded above |
| Complete alias/optional source set (T-75-04-04) | Alias to excluded state Python and regular Prime `.env` copied | Real filesystem alias/state/learned-helper refusal and Prime private regular omission regressions pass in F; no source fix by this auditor |
| Unverified shipped membership (T-75-04-04) | Missing/malformed Git-less inventory admitted learned Markdown/helper Python | Actual owned missing/malformed provenance cases now exclude both while fresh safe product Python survives |
| Regular Python source-set bypass (T-75-04-04) | Credential-named Python and Python under .planning copied and inventoried | Both omission and absent inventory rows proved; nested export cannot bless planted private/excluded Python; legitimate stock private-named modules remain |
| Prime producer windows (T-75-04-03) | Actual rlm_rename refusal published scanner-clean low-entropy prefix of a rejected full candidate | Current real refusal regression publishes only withheld metadata after full raw classification; no provider/model success inferred |
| Operator guarantees (T-75-08-01) | Earlier broad claims exceeded controls | Current docs retain recognition limits, provenance/trusted public-code distinction and no universal-secret, OS-sandbox or live-provider guarantee; N is navigation/link/asset evidence only |
| Initial environment mutant | Host startup failed before observation | Explicitly NOT PROOF; later independently observed key-presence mutant replaces its evidentiary role, not its historical record |
| Launcher raw argparse usage (existing GROK-01 publication scope) | Three actual recognized-canary cases exited2 but echoed raw choice/typed-value/unknown argument | All three actual after receipts exit2/no echo/fixed label; parameterized real-main consumers plus clean-choice control pass in F |
| Controller raw usage/missing-root publication (existing LOCAL-02/GROK-01 scope) | Two actual recognized-canary cases exited2 but echoed unknown argument or missing-root path | Both actual after receipts exit2/no echo/fixed label; real-subprocess consumers plus benign-root control pass in F |

Earlier154-row/2069/2079/2085/2106/2116/2122 totals in SUMMARYs and prior receipts remain provenance. Current acceptance uses the observed completed2129-test union (`artifact://246`), earlier targeted122 selection, latest22 host checks/26evals,165 healthy canonical rows and separately passing earlier2/2 aggregation negatives. The seven usage cases are counted only because the parent supplied the completed new union, not by arithmetic over authored tests. `local://v15-native-phase75-verification.json`/`artifact://151` are the preceding2106 run; `local://v15-native-phase75-final-verification.json`/`artifact://212` record2116, and the inspected closure snapshot/`artifact://231` records2122. Historical receipts cited only by old SUMMARYs and unavailable in this session are not silently treated as newly inspected/runtime-exercised proof.

## Remaining limitations

- **WARNING — environment-specific coverage:** observed local POSIX seat and existing interpreter/native artifact; heartbeat callback polling and multiprocessing depend on runtime/platform. The full run's16 warnings are retained, not hidden. No portability or deterministic scheduling guarantee follows.
- Live Grok attachment/account/provider, Red Team missions, external service/device/browser transport and genuine factory/run_child resources are **not locally exercised**. Named blocked/disabled rows satisfy truthful local accounting, not those integrations.
- ScriptedModel proves local composition only; public delegate remains blocked. RLM configured connector/host lifecycle and persisted metadata are explicitly a model-double fixture, not successful provider dispatch. Messaging/TODO/cache/approval state have their stated in-memory boundary.
- Durable status reads INSTALL/cron/turns.sqlite/workflows, not the desk-parent STATE/turns.sqlite journal. Plain memory persistence and banked recall are separate checks. Shim receipts pass production structural validation but are still rejected by the execution-backed public checker as uncaptured.
- Intake provides cooperating-POSIX serialized freshness, not lease fencing or transactions spanning unrelated stores. Vetted Git/inherited inventory and trusted public product code remain prerequisites for export; arbitrary secret-bearing source/code/resources are not universally classified.
- Secret protection is recognition-bounded. The repaired full-input-before-loss boundary does not promise recognition of every possible secret. MCP fixed transport/refusal/timeout diagnostics are bounded; untrusted peer tool-result content is still data, not universally secret-safe. No `_shape` change or new protocol was inferred.
- The explicit report output is the only intended external write. No product/source/test/STATE/ROADMAP/REQUIREMENTS/SECURITY/upstream/worktree/.swarm change, rebuild, signed Swarm evidence, remote delivery, deployment or publication is claimed by this auditor.

## Wave0 and Sign-Off

Existing infrastructure is available; no missing framework/scaffold or installation. All25 tasks are mapped green and all7 Phase75 requirements covered within the preserved local limits. The prior sole open G-75-NYQ-01 remains FILLED by both parent-passed behavioral negatives. The later launcher/controller usage defects are covered by the seven passing current consumer cases and five actual repaired receipts, with failed-before evidence retained. No gap was marked SKIP and no unavailable live prerequisite was counted exercised.

- [x] Inherited eleven PLAN/SUMMARY sets and all25 task obligations retained; plan declarations and exact Plan04 negative obligations checked in this narrow recheck.
- [x] Current2129/122/22/26/165 evidence and separate earlier2/2 aggregation-negative result distinguished from historical runs; actual new union output supplied and read, not inferred from additions.
- [x] Actual production child observation independently mutation-sensitive; initial startup-only mutant retained as non-proof.
- [x] Verifier full-input, nominal-pass→fail, metadata and CLI rejection covered with actual before/after provenance.
- [x] No implementation edits, test/gate execution, commits or shared lifecycle writes by auditor.
- [x] Both eligible-failure and unexplained-canonical report-to-CLI negatives proved by parent-exercised consumer tests/receipts; G-75-NYQ-01 FILLED.
- [x] Launcher/controller raw usage defects retain actual before/after receipts; all seven exact consumer cases pass in F, including clean guidance controls; only recognition-bounded safety asserted.
- [x] Nyquist-compliant coverage verdict,7/7 requirements and25/25 tasks; meaningful warnings and canonical parent verification remain separate.

This is an independent validation-coverage verdict only. Security threat closure, code review and canonical phase verification are separate parent-owned gates, not decided here. The prior audit block below is preserved unchanged as historical evidence; parent owns the new audit-trail append.

## Re-audit 2026-10-12 (stale-verification resume, State A)

Triggered because later phase 76/77 commits (`31ed79d`, `c88ac89`, `1f0adf6` and docs commits) changed covered files after the 2026-10-10 verifier. Post-phase-75 source change in this phase's scope is confined to `omega_prime/grokbot/oneclick.py` (stdio JSON framing: `_emit_json(payload, file)` and `json_sink`; +5-line shift, guarded `_bounded_diagnostic`/`_log_tail`/`_GuardedParser` bodies unchanged) plus added tests in `test_grokbot_oneclick.py`/`test_grokbot_readiness_cli.py` and doc edits. All 25 exact regressions cited above still exist by name (`git grep "def <name>"`, one definition each). No requirement, task or verify block changed; no new gap: 0 found, 0 resolved, 0 escalated. Current green evidence is the orchestrator's HEAD `3208b9b` full union (2352 passed / 0 failed / 0 skipped) plus this resume's regression-gate run recorded in `75-VERIFICATION.md`. No test or source file was created or modified by this audit.

## Validation Audit 2026-10-10

| Metric | Count |
|---|---|
| Gaps found | 5 |
| Resolved | 5 |
| Escalated | 0 |

## Validation Audit 2026-10-12

| Metric | Count |
|---|---|
| Gaps found | 0 |
| Resolved | 0 |
| Escalated | 0 |
