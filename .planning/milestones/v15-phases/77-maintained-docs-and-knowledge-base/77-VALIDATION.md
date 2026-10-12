---
phase: 77
slug: maintained-docs-and-knowledge-base
status: validated
nyquist_compliant: true
wave_0_complete: true
created: 2026-10-11
updated: 2026-10-12
audit_state: A
source_revision: 1f0adf62a1668d3f3a0d3149987543cdd5f5b5cd
manual_judgment: partial
phase_acceptance: pending
---

# Phase 77 — Independent Validation Re-audit

**Automatable consumer gaps: FILLED, 12/12 proposal groups. Human acceptance: PARTIAL.** All seven plans, 16 tasks, eight requirements, 26 R-oracles and eight evaluation dimensions are mapped below. `nyquist_compliant: true` means no missing or uncertain **required automatable consumer coverage** remains in this bounded audit; it does **not** mean all 26 oracles have human approval, all eight requirements are canonically accepted, or the phase/milestone is complete. No Human requirement is waived or replaced by a prose/unit-text pin.

## Authority, reconstruction and execution boundary

- Current interface/source binding: `local://v15-phase77-interface-final-gate-inputs.json` and `local://v15-phase77-interface-correction-binding.json`, revision above. Approved source union is **53 read paths**: original50 plus reachable `omega_prime/grokbot/SETUP.md`, `omega_prime/grokbot/oneclick.py` and existing `omega_prime/tests/test_grokbot_oneclick.py`. The exact six changed-path hashes are in the interface binding; no final whole-source fingerprint is manufactured. Existing77-04task2 scope/action/acceptance was reconciled, retaining seven plans/16tasks/34raw truths/eightrequirements. Parent reports query-plan structure valid/0errors/0warnings; this author did not run it.
- Prior reconstruction independently read all seven PLAN task/acceptance/verify blocks and seven SUMMARYs; `.planning/REQUIREMENTS.md:29-39`; `77-AI-SPEC.md:460-479,528-555`; initial validation/proposals; importer guards and authored tests. The prior filesystem re-audit inspected exported sync and six filesystem consumers. This follow-through re-read the retained 12-group/26-oracle/eight-dimension/16-task/eight-requirement map, reconciled77-04 PLAN/SUMMARY, corrected interface paragraphs, real-process stdio assertions/output routing and exact parent receipts. SUMMARY `requirements-completed` records contributions, not canonical acceptance.
- **This auditor ran NONE:** no build/test/lint/type/format/scan/runtime/gate, remote call, commit or lifecycle command. Product/source/tests are frozen read-only. The sole write is this `77-VALIDATION.md`; no test, fixture, proposal, SECURITY, STATE, ROADMAP, REQUIREMENTS or SUMMARY edits.
- Passing execution is **parent-owned evidence**, not inferred from a guard, a test name or another grader's verdict. Source inspection independently checks that each test reaches the public consumer/fault boundary and asserts the demanded effects. Auditor debug iterations: **0**; no fabricated three-iteration failures.
- Project skill discovery found no `.claude/skills/*/SKILL.md` or `.agents/skills/*/SKILL.md`; config enables Nyquist and has no `agent_skills` mapping. LSP is not mounted in this auditor's inventory; bounded source reads/searches are used, not invented LSP proof or root-wide ghost-context search.
- All 21 primary body bytes and the admitted manifest remain unchanged under the parent contract. E1 remains its **named historical source snapshot**, not a newly executed current-source HTTP run. Literal untrusted archival bodies, including ordinary `bearer credentials` prose and archived example markers, are **not diagnostic input**. Full error/status input uses the unchanged shared secret detector before clipping/echo. No body redaction or new model/framework/gate is introduced.
- A handled single-writer rollback is not atomic visibility to readers, SIGKILL/crash safety or parallel-writer coordination. Failed partial restoration leaves a recoverable backup, not a claim that the output tree is already fully restored. Cleanup failure **after successful commit** reports failure while the complete new edition remains published and a private prior backup remains; it is not rollback or a restored-prior result.
- Filesystem revision `acc2aa8659327e11bf22a0357a66b8c86726bda4` and its four-path binding remain historical unchanged-consumer evidence. Current full2352 preceded the later **test-only** `--skip-doctor` isolation addition; current two strict cases and quality checks followed it. Fresh mandatory17-file prior selection now passed at the frozen interface revision/current isolated fixture: **632passed/1warning/113.41s**, E18. The prior34/34human_needed `77-VERIFICATION.md` is **STALE** after source/PLAN/SUMMARY corrections; canonical re-verification/fingerprint and Human dispositions are parent-owned and pending.

## Test Infrastructure and Commands

Commands are verification-map entrypoints, not commands executed by this author.

| Ref | Command / observed infrastructure | Parent result and boundary |
|---|---|---|
| K | `.venv/bin/python -m pytest omega_prime/tests/test_kb_sync.py -q -rA` | Existing pytest/std-lib HTTP seams; current consumers included in focused and full parent runs. No separately invented K-only count. |
| N | `.venv/bin/python -m pytest omega_prime/tests/test_tool_catalog.py omega_prime/tests/test_greptile_config.py omega_prime/tests/test_docs.py -q -rA` | Real catalog current/drift/missing-definition, roster-order, rule/scope and once-only nav consumers; included in current full run. |
| C | `.venv/bin/python -m omega_prime.tooling.catalog --check` | Retained actual owned CLI exit0; current full-run catalog test also calls real `main(["--check"])`. |
| F | `.venv/bin/python -m pytest omega_prime/tests -q` | **2352 passed,16warnings,176.35s**, `artifact://792`, interface binding E14. This full run preceded the two tests' later isolation-only `--skip-doctor` flag; not a current-final-test-file full rerun. Historical full2350/16/157.34s (`artifact://729`) and2344/16/153.37s (`artifact://705`) keep their original bindings. |
| Focused receipt | Filesystem binding E12; interface binding E14 | Historical filesystem200/1warning/7.98s retains six parameters; interface launcher/receipt/rerun90/1warning/28.29s preceded the final isolation flag. Exact focused argv/individual-case output is not supplied by these bindings and is not manufactured. |
| Runner config | `pyproject.toml:53-55`: testpaths `omega_prime/tests`, root pythonpath | Existing framework/dependencies only; exact installed pytest version not established here. Package floor >=3.11 differs from exercised CPython3.12.3 and retained CI3.12–3.14. |
| Other checks | Interface binding E14; older structural/assembly receipts | Parent Ruff lint exit0,401files already formatted, mypy exit0/339sources after the isolation-only change, with unchanged repo `ignore_missing_imports=true`. Older26structural evals/assembly remain named historical evidence, not fresh reruns. **26 structural controls are NOT R01–R26** or a model/source-faithfulness score. |
| Fresh prior lane | `local://v15-phase77-stdio-prior-regression.json` | **17selected files,632passed/1warning/113.41s**, runner wall115.59s/bound600s/exit0 at current frozen revision/current isolated stdio fixture, E18. Selection is listed; receipt records command normalization but not an exact argv log. Prior filesystem-revision630/92.04s and initial458-pass snapshots retain historical bindings, not relabeled current. |
| L | `.venv/bin/python -m pytest omega_prime/tests/test_grokbot_oneclick.py -q -rA -k test_served_stdio_is_protocol_only_and_reads_the_owned_workspace` | Verification-map entrypoint for the two existing real-process parameters. Parent reports **2passed/1warning/23.65s** after isolation flag, E14; exact parent argv is not asserted. Actual default-doctor SDK smoke is separate E15, not waived by `--skip-doctor`. |

`wave_0_complete: true` records that no required automatable proposal or framework bootstrap remains, not that Human/hosted/live work passed. Parent-only final verification ownership is retained; no duplicate runs. Observed elapsed times are single-run facts, not performance promises.

## Evidence Register

| Ref | Receipt | Actual proof / limits |
|---|---|---|
| E1 | `local://v15-phase77-remediated-client-runtime.json` | **Historical named importer SHA256** `691c009318a975f1d46287208e74b06090c583d6713d79c4d342450dbf8cf95f`, exit0; differs from current outer-wrapper source SHA. Actual owned loopback JSON **and** SSE each replayed all21 captured primary bodies exactly, visible trust header, canonical namespace endpoint, coherent clocks, shuffled no-churn bytes/mtimes/inodes, late-fetch preservation, second real publication-write rollback and backup cleanup. Retained actual21HTTP proof is not silently rebound to current source; fixture clocks are not vendor retrieval. |
| E2 | `local://v15-phase77-fresh-primary-import.json` | Retained actual authenticated mounted-tool reads through production sync/throwaway bridge: 24 reads, synced21, `swcstudiospace/omes-bot`, namespace `3b0877f2-8580-4305-b246-a1cf4cfb31f3`, docs version `2026-10-08-1791571202911-c82b36`; original before-listing clock null. Not a direct env-key CLI HTTPS run. |
| E3 | `local://v15-phase77-fresh-mirror-admission.json:12-213` | Independent21/21 exact full-body/path/count/hash/clock/trust admission: 239020 UTF8 body bytes /238985 declared characters, headers excluded. Second24 fresh reads returned unchanged, every document/manifest byte/mtime retained, original clock unknown, provider account writes0. Completeness is not accuracy, rights or injection immunity. |
| E4 | `local://v15-phase77-fresh-unchanged-reads.json` | Retained second actual authenticated read observations. Raw upstream legacy field is archival response evidence, not request/lookup authority. Later clocks remain receipt observations, not rewritten provenance. |
| E5 | `local://v15-phase77-audit-reproduction-after.json` | Prior remediation snapshot direct reproductions: missingindex/oversizeddiscovery/samenamespace duplicate → GreptileError +prior bytes preserved; response.read/validation/publication exception and traceback markers absent; CLI status exit3/no stderr leak. These seven retained observations do not alone cover P01–P12; inspected consumers +current E12 do. |
| E6 | `local://v15-phase77-canonical-cli-proof.json` | Retained actual owned empty-credential HOME/state setup, doctor, stdio launcher dry-run and catalog exit0. Honest ultrathink/substrate skips and SSE-port warning. No listener attachment/account/provider proof. |
| E7 | `local://v15-phase77-root-cli-proof.json` | Actual README oneclick --work-root --dry-run and isolated readiness exit0; readiness distinguishes owned effects, defaults/prereqs and model doubles. Not fresh clone/install/live model/backend. |
| E8 | `local://v15-phase77-extension-example-proof.json` | Runnable documented example returns budget_guard at201messages/no model. `test_modes.py:116-154` supplies real conversation before-model mutation, first-stop order and zero model calls, included in F. |
| E9 | `local://v15-phase77-diagram-surface-proof.json` | Isolated owned Chrome/CDP/Mermaid12.1.0 rendered and parent inspected four architecture +one loop diagram; layout and terminal semantics corrected. Not complete Human source-faithfulness or hosted GitBook. |
| E10 | `local://v15-phase77-canonical-links-proof.json` | Retained actual local MarkdownIt consumer:25root/canonical files,170internal links/assets,0issues; fourteen remote links not fetched. Expanded current linked-guide audit is E17, not a retroactive change to E10. |
| E11 | `local://v15-phase77-root-nav-surface-proof.json` | Actual local rendered21 canonical navigation targets/21 unique hrefs, HTTP200/headings, landing inspected. Not all prose visually reviewed or remote GitBook; responsive image rule belongs only to throwaway preview. |
| E12 | `artifact://729`; `local://v15-phase77-filesystem-remediation-binding.json`; `local://v15-phase77-final-regression-proof.json` | Retained filesystem revision `acc2aa8659327e11bf22a0357a66b8c86726bda4`: parent full2350/16warnings/157.34s, focused200/1warning/7.98s, prior17-file630/1warning/92.04s within600s, exit0. These unchanged-consumer results remain applicable with interface full E14; **not** current-head focused/prior executions. Older2344/194/458 retain their own snapshots. |
| E13 | `local://v15-phase77-direct-filesystem-before.json`; `local://v15-phase77-direct-filesystem-after.json`; current filesystem binding | Direct exported filesystem reproduction before: raw **OSError**, message+formatted traceback leak=true; after: **GreptileError**, both leak=false. These small receipts omit the fault's exact path/argv/state and cannot alone prove all six cases; the current real-sync parameterized consumer and E12 supply named state/privacy coverage. |
| E14 | `artifact://792`; `local://v15-phase77-interface-correction-binding.json` | Parent full2352/16warnings/176.35s; launcher/receipt/rerun90/1warning/28.29s. Full/focused preceded the test-only isolation flag; **current two strict stdio parameters2/1warning/23.65s and Ruff0/401format/mypy0/339sources followed it**. Source six-path hashes and chronology bind these receipts. Fresh mandatory prior is separately completed E18, not an update to this earlier binding. |
| E15 | `local://v15-phase77-stdio-interface-before.json`; `local://v15-phase77-stdio-interface-after.json`; interface binding | Before: actual SDK invalid-JSONRPC logs from launcher banners/doctor stdout; SDK tolerated them, probeexit1 came from wrong camelCase result access, **not launcherexit1**. After: actual SDK initialize+read_file on launcher environment, launcher environment+`--json`, direct-server`--work-root` each returned exact independently seeded `owned-proof.txt` contents, exit0/no parse errors. Stderr contains default doctor/preflight, warnings and JSON report. Captured before/after receipts retain original source labels; interface binding associates the corrected runtime with current frozen source. No live provider/backend/account proof. |
| E16 | `local://v15-phase77-gateway-doc-smoke.json` | Real stdlib HTTP to an owned bound/non-listening loopback socket produced `gateway_unreachable` in mission/LLM error rows. Unauthenticated `https://owned.invalid` returned `non_loopback_refused` before any non-loopback network call. No real backend/mission or successful authenticated non-loopback proof; readiness `feature_disabled`/blocked are different classifications. |
| E17 | `local://v15-phase77-linked-guide-links-after.json` | Expanded actual local consumer:26files/176internal links/assets/0issues;14remote links **not fetched**. Supports reachable shipped SETUP/canonical referral corrections, not remote publication or prose/source-faithfulness approval. |
| E18 | `local://v15-phase77-stdio-prior-regression.json` | Current frozen `1f0adf62a1668d3f3a0d3149987543cdd5f5b5cd`,17explicit selected files,632passed/1warning/113.41s; runner wall115.59s/bound600s/exit0. Includes current isolated stdio cases and real KB sync consumers; normalization `query normalize-test-command then run-with-timeout600`. Parent-owned selected prior proof, not final-full-after-flag/hosted/remote/live/clone/image-publication certification. |

## Coverage Vocabulary

**Code GREEN / FILLED:** a real passing parent consumer proves the named bounded behavior. **Human PENDING / PARTIAL:** interpretation/approval remains even where Code is green. **MISSING:** required automatable behavior lacks passing evidence. Current missing automatable outcomes: **0**; no required test is SKIP-to-green. Human-only checks remain mandatory and separately routed.

Tests may use a scripted HTTP peer or fault injection without becoming mock-echo tests: they call real `McpHttpClient`, `sync`, `main`, catalog registry/render/main, and inspect independently expected body/manifest/filesystem/output effects. The two stdio parameters launch real child processes, initialize then call real `read_file`, and compare to independently seeded workspace bytes. No mocked dispatch/sync verdict, copied production stamper oracle, default/text pin or nonempty-only body assertion fills a gap.

## Per-Task Verification Map — all16

Task IDs follow each PLAN's task order; commands K/N/C/F/L expand above. “Human pending” is not whole-task acceptance.

| Task ID | Plan / wave | Requirement | Threat refs | Command / type | Passing bounded Code evidence | Remaining Human judgment |
|---|---|---|---|---|---|---|
| 77-01-01 | 01 /1 | KB-02/01/03 | T-77-01/02/03 | K/F integration +CLI | **GREEN:** P01–P11, FS01–FS06, E1/E5/E12/E13; public complete admission/privacy/preservation/no-churn; cleanup-after-commit truthfully distinct | KB-02 edge and prohibition dispositions; concurrency limit |
| 77-01-02 | 01 /1 | KB-02/01/03 | T-77-01/02/03 | K/F integration | **GREEN:** P01–P11 +six filesystem consumers with independent prior/new/backup effects; correlated valid fixtures, adverse replies remain adverse | No new prose/source pins; historical rights/authority separate |
| 77-01-03 | 01 /1 | KB-02 | T-77-03 | manual workflow inspection; K supports effects | **GREEN supporting mechanism:** distinct CLI states and zero-write unchanged, E1/P03/P08 | Workflow source: single invocation/keyskip/30min/git-status-gated PR inspected; hosted jobs unobserved; IN02 below |
| 77-02-01 | 02 /2 | KB-01/02 | T-77-04/05 | K/F integration acceptance | **GREEN:** E1–E4 all21 exact primary bodies/provenance; P01/P05/P08 future bounded admission | Accuracy/rights not inferred; no forever-fixed21 size |
| 77-02-02 | 02 /2 | KB-03/01 | T-77-06 | K/N/F +local rendered acceptance | **GREEN:** P09 negative effects; E3/E10/E11 visible separate history/nav | Historical/current truth, guide-no-copy/rights interpretation |
| 77-03-01 | 03 /1 | DOC-02/01 | T-77-07/08 | F +rendered acceptance | **GREEN:** E9 all5 diagrams render; E12 affected regressions | R21/R22 material claim→symbol→applicable receipt, process/storage/security semantics |
| 77-03-02 | 03 /1 | DOC-03/01 | T-77-07/08 | F integration/example | **GREEN:** E8 actual hook order/first-stop/model suppression | Patterns/registry/approval/signing/license/source applicability |
| 77-04-01 | 04 /1 | DOC-01/04/03 | T-77-09/10 | F +owned CLI | **GREEN:** E6/E7 actually exercised named local routes | URLs/install/roots/Python/prerequisite/license applicability; no clone certification |
| 77-04-02 | 04 /1 | DOC-01/04/03 | T-77-09/10 | L/F integration +actual SDK/HTTP/local links | **GREEN bounded consumer:** E14 two strict normal/JSON stdio parameters initialize+owned read; E15 three actual SDK workspace routes/default doctor stderr; E16 actual gateway refusal/error; E17 reachable SETUP links. Same reconciled task, no new proposal group | Driving/FAQ/redteam/shipped SETUP source/authorization/preparation-vs-client attachment; no live account/backend; doctor isolation and full/prior chronology below |
| 77-05-01 | 05 /1 | DOC-01/04/02 | T-77-11/12 | F +owned CLI | **GREEN regressions:** E6/E12, not live connector proof | Types/config/errors/transport/persistence/approval claims; no live substrate.probes |
| 77-05-02 | 05 /1 | DOC-01/04/02 | T-77-11/12 | F +owned CLI | **GREEN supporting local proof:** E6/E7/E12 | Optional native/migration prerequisites/config-vs-runtime |
| 77-06-01 | 06 /3 | DOC-01/02/03/04, KB-03 | T-77-14 | N/F +owned CLI/SDK | **GREEN:** E7 retained root routes; E15 actual stdio environment/direct-server workspace effects; E17 expanded local links; E14 regressions | Root/count/compatibility/model/source-delivery claims; SSE-only launcher forwarding is not every-transport forwarding |
| 77-06-02 | 06 /3 | DOC-05, KB-03 | T-77-13 | N +local rendered acceptance | **GREEN:** E10/E11 internal reachability,21 unique nav pages; retained nav consumer | Historical/current separation/polish; not remote publication |
| 77-06-03 | 06 /3 | DOC-01/05 | T-77-14 | manual only | No fabricated automated wording test; privacy Code support P10/P11 | One honest Unreleased entry/convention/no invented release/date |
| 77-07-01 | 07 /1 | DOC-05 | T-77-15 | C/N/F integration | **GREEN:** current --check0, stale1, P12 real missing-definition refusal/output preserved | Capability/approval/inventory interpretation |
| 77-07-02 | 07 /1 | DOC-05 | T-77-16 | N/F +manual config | **GREEN bounded consumers:** roster-once, drift/missingdef, nav, rule/scope, E12 | Config vs connected/published, pin-removal intent; no remote sync proof |

All16 have their required verification route mapped, including two expressly manual-only PLAN routes (workflow/changelog). No task is promoted to canonically accepted solely because its applicable Code lane is green.

## Requirement-to-Evidence Map — all8

| Requirement | Required outcome | Code status | Human acceptance / limits |
|---|---|---|---|
| KB-01 | Read all21 documents/index with repo/namespace/version/fetch/completeness | **GREEN:** E1–E4, P01/P04/P05/P08 | Actual retained edition complete; provenance interpretation/rights remain bounded. Vendor non-BMP count units unknown; synthetic codepoint/UTF16-mismatch controls do not discover them. |
| KB-02 | Canonical refresh rejects incomplete/truncated input and preserves prior | **GREEN:** P01–P11/FS01–FS06/E1/E5/E12/E13 | Single-writer exception rollback only; cleanup-after-commit failure leaves a complete new edition +private prior backup, not restored prior. Manual edge/prohibitions/workflow judgment pending. |
| KB-03 | One historical mirror separate from current canonical docs | **GREEN applicable Code:** P09/E3/E10/E11 | **PARTIAL:** history/current-authority/rights/no-copy judgment pending. |
| DOC-01 | Audited current README/docs URLs/install/counts/verified-unverified claims | **GREEN applicable Code:** E6/E7/E14/E15/E17 | **PARTIAL:** corrected launcher environment/SSE-only forwarding and reachable guide backed by owned effects; current source/license/prereq/Python/live applicability review pending. |
| DOC-02 | Detailed source-linked execution/state/security diagrams | **GREEN applicable Code:** E9/E12 | **PARTIAL:** diagram semantics, owners/auth/approval/signing/source-faithfulness require Human. |
| DOC-03 | Source-linked patterns and existing extension conventions | **GREEN applicable Code:** E8/test_modes/F | **PARTIAL:** guide/source/registry/policy applicability pending; no plugin loader proof invented. |
| DOC-04 | Runnable usage/workflows/config/prereqs/Grok preparation | **GREEN applicable Code:** E6/E7 retained, E14 strict two-case owned consumer, E15 three actual SDK routes/default preflight, E16 actual gateway errors | **PARTIAL:** normal/JSON protocol and workspace contents proven, not attachment/live inference/mission. `--skip-doctor` is test isolation only; actual default doctor observed separately. Full/prior freshness limits remain explicit. |
| DOC-05 | Repository/GitBook nav/catalog/internal links/assets | **GREEN applicable Code:** E10/E11 retained, E17 expanded26file176links, P12/current+drift/retained nav | **PARTIAL:** capability/config/history/polish/connected-vs-published interpretation pending;14remote links/publication unclaimed. |

All eight canonical requirements remain **Pending** in REQUIREMENTS. This artifact neither edits those statuses nor treats automated coverage as source/license/production certification.

## P01–P12 Closure and Meaningfulness Review

Names below are exact current tests in `omega_prime/tests/test_kb_sync.py` unless qualified. All are included in passing parent E12; commands K or N. Original `local://v15-phase77-nyquist-test-proposals.md` is historical NOT-RUN-at-baseline text, not the current execution status.

Shared independent oracle: `_prior` seeds via real sync, adds maintained README/unrelated/outside sentinel, and deliberately alters a prior file mode/mtime. `_snapshot` (`:189-222`) captures whole relative pathset, file bytes/modes/mtimes/inodes, directory path/mode/inode, and symlink identities **without following them**. Directory mtime is intentionally excluded because legitimate replace/rollback changes it; file/header/manifest clocks are not excluded. `_refuses` requires **GreptileError**, not arbitrary AssertionError, and whole-tree equality. `_assert_edition` (`:771-816`) compares each full body separately from its header to independent corpus bytes, reconciles pathset/counts/hash/UTF8/version/clock/trust links, and excludes client credentials. This is not `_stamped` echo or substring-only content proof.

| Proposal | Exact test refs / behavior | Result / classification |
|---|---|---|
| P01 | `test_paginated_discovery_and_twenty_plus_one_publication_preserve_every_body` (`:820-850`, JSON/SSE): target on discovery page2;20+1 before/after pages with legal reorder;21 distinct independent multilingual bodies. `_assert_document_observations` (`:1879-1896`) requires exactly one request per manifest document and externally brackets read clocks. E1 supplies actual loopback canonical endpoint/transport/no-churn effects. | **FILLED /green**, R02; real parser +sync publication, no wiring-only proof. |
| P02 | `test_invalid_page_counters_preserve_complete_edition` (`:867-917`): discovery/list total/returned missing/null/bool/string/negative, mismatch, overshoot, changing total, earlyzero; no document fetch after invalid metadata. `test_duplicate_discovery_match_or_scan_cap_refuses_refresh` (`:923-935`): same/different/cross-page duplicate and scan cap. | **FILLED /green**, R03–R05; whole prior/sentinels preserved, not guessed first match. |
| P03 | `test_noncomplete_cli_outcomes_preserve_existing_edition` (`:941-965`): exits2/3/0 on missingkey/unenrolled/validempty, no-key zero requests, complete prior preserved. `test_valid_empty_publication_writes_nothing` supplies explicit empty status/no output with null docs version. | **FILLED /green**, R06; positive nullable-empty proof, not malformed response treated empty. |
| P04 | `test_index_and_exact_path_set_faults_preserve_prior` (`:983-1022`): missingindex with either flag, flag/type mismatch, duplicates within/across pages, invalid element, alias-only identity. `test_near_equal_safe_paths_publish_distinct_full_bodies`; `test_output_path_escape_cannot_modify_outside` (`:1053-1098`): unsafe strings/root/ancestor/parent/prior/stale/manifest symlinks/unowned collision. `test_noncanonical_discovery_namespace_refuses_refresh` (`:1839-1850`) refuses missing/null/empty/int canonical identity even with legacy alias. | **FILLED /green**, R07/R13; exact paths/full surviving tree/symlink identities/outside, not missing-index confounded by traversal. |
| P05 | `test_invalid_full_document_preserves_prior` (`:1129-1193`): absent/null/list document, missing/null/int/empty+count0 body, invalid counts, separate repo/ns/path/section/version, flag/reason/prefix/fullcount/serialized cap, UTF16 mismatch. `test_unicode_character_counts_publish_exact_full_body` (`:1201-1207`) passes BMP/nonBMP codepoints. `test_capped_listing_refuses_refresh_without_fetching_documents` (`:1854-1875`) separately covers cap flag/reason. | **FILLED /green**, R08–R11; characters !=bytes; no inferred vendor Unicode unit or80KiB exhaustive transport certification. |
| P06 | `test_page_or_after_fetch_publication_change_preserves_prior` (`:1214-1257`): independent page/after version/ns/repo/index/total/path axes. After-fetch changedtotal has a **real zero-progress continuation** (`:1253-1256`); peer exhaustion is AssertionError and cannot satisfy `_refuses`. | **FILLED /green**, R12; earlier exhausted fixture was corrected, not a false caught-AssertionError success or weakened assertion. |
| P07 | `test_manifest_failure_after_document_replacements_restores_complete_prior` (`:1260-1282`) asserts changed real index bytes/inode and new path before manifest fault. `test_failure_after_stale_removal_restores_complete_prior` (`:1814-1835`) real unlink/newmanifest/newpath then fault. `test_restore_failure_retains_recoverable_original_and_reports_failure` (`:1286-1337`, API/CLI) performs one real restore then fails second; failure/exit1,0700backup retained; reconstructs **every original file+manifest** from backup OR already-restored target with bytes/mode/mtime/inode. FS05/FS06 below add real-sync cleanup-after-rollback/commit distinctions. | **FILLED /green**, R15; publication rollback restores prior; failed recovery reconstructible; cleanup-after-commit failure is a complete new edition +private old backup, never falsely “prior restored”. |
| P08 | `test_body_only_change_publishes_exact_new_edition`; `test_version_only_change_is_not_unchanged`; `test_path_only_change_prunes_only_owned_paths` (`:1346-1373`): each isolates one axis, exact edition/manifest and unowned sentinels. `test_shuffled_listing_is_unchanged_without_inode_or_provenance_churn` (`:1376-1382`) compares whole snapshot. E1/E3 also preserve actual21 no-churn. | **FILLED /green**, R16/R17; combined body+path test is not sole oracle. |
| P09 | `test_archival_directives_are_literal_data_not_requests_or_execution` (`:1385-1427`): attacker organization/URL, harmless command/approval text, ordinary bearer prose and marker preserved literally; configured endpoint/list-derived ns/path/tool set only, outside/approval untouched; private env/client/session material absent from provenance. | **FILLED /green**, R19; no directive executed; not downstream LLM injection immunity. |
| P10 | `test_diagnostic_secret_after_clip_is_withheld_and_prior_survives` (`:1471-1491`): tool/RPC/path/metadata/structured errors with marker **after** >200chars. `test_unavailable_secret_gate_fails_closed_at_real_consumer` (`:1495-1524`): raised scanner/import unavailable. `test_direct_read_parse_publication_errors_never_leak_in_traceback` (`:1555-1630`, API/CLI): read/JSONUTF8/SSEUTF8/JSONdecode/tooldecode/backup/publication/partialrestore/path. `test_final_cli_status_metadata_is_gated` (`:1638-1681`): repo/out ×unenrolled/empty/synced/unchanged. FS01–FS06 (`:1899-1972`) add exported sync filesystem failures. Benign/auth controls remain useful. | **FILLED /green**, R20; safe error type/message/trace plus actual prior/new/backup effects; retained API/CLI/direct-client consumers unchanged. New six are API parameters, not six independently executed CLI variants. No helper/default/source pins or archive redaction. |
| P11 | `test_malformed_or_mismatched_mcp_reply_cannot_publish` (`:1703-1774`): wrong991/bool/string/missing IDs, wrong/missing JSONRPC version, initialize boolID, SSE only unrelated991/992, absent/nonobject result/envelope, invalidJSON/UTF8, structured error. `test_direct_call_tool_errors_withhold_secrets_without_sync_wrapper` (`:1789-1811`) separately tests direct read/parsers/tool/RPC errors with traceback; cannot rely on sync wrapper privacy. Valid `_CorrelatedJson` fixtures use the actual request ID (`:63-66`); explicit wrong-ID fixtures are bytes and remain wrong. | **FILLED /green**, R02/R09/R20; proper valid fixture migration, no relaxed wrong-ID expectation or reconnect/retry scope. |
| P12 | `test_tool_catalog.py::test_unknown_rostered_definition_refuses_catalog_without_overwriting_output` (`:37-61`): real YAML roster with nonexistent definition, real registry/render/main, visible SystemExit naming absent tool, preexisting output bytes/mode/mtime/inode unchanged. Existing actual --check0/stale1 and roster order retained. | **FILLED /green**, R26 Code portion; no fake registry/schema echo or copied families/header. Human approval/capability meaning still pending. |

Alias-bearing cases are deliberately **negative fixtures**, not legacy positive schemas or fallback consumers. PLAN01's old literal “zero alias matches in tests” check must not erase these meaningful refusal tests or misclassify upstream raw evidence; production only consumes/sends canonical namespaceId. No source-string pin is introduced to make that distinction look exercised.

### Six final filesystem consumers — current source binding

All six parameters of `omega_prime/tests/test_kb_sync.py::test_direct_filesystem_failures_withhold_credentials_and_preserve_truthful_state` (`:1899-1972`) call the **real exported `sync`** with the production client/parser and scripted valid publication, never a mocked sync verdict. `_prior` seeds through real sync; failure injection occurs only at the named I/O boundary. Each requires `pytest.raises(GreptileError)` and `_assert_secret_free` (`:1430-1433`), inspecting **both `str(error)` and formatted traceback** for the recognized marker, long token fragment and client token. Parent E12 is the passing execution binding; E13 is the separate before/after reproduction, not a fabricated six-case transcript.

| Case | Injected consumer boundary | Independently asserted filesystem effect | Map / parent result |
|---|---|---|---|
| FS01 `prior-manifest-read` | Only `Path.read_bytes` at prior `manifest.json` raises marker-bearing OSError | Entire prior/sentinel snapshot equal, including pathset/bytes/modes/mtimes/inodes | P10/R20; K/F and current focused, **green** |
| FS02 `prior-document-read` | Only prior `docs/a.md` read fails; other reads remain real | Entire prior/sentinel snapshot equal | P10/R20; K/F and current focused, **green** |
| FS03 `root-preparation` | Real sync reaches faulted `ensure_private_dir` | Entire prior/sentinel snapshot equal; no backup/new edition | P10/R20; K/F and current focused, **green** |
| FS04 `backup-creation` | Real sync reaches faulted `tempfile.mkdtemp` | Entire prior/sentinel snapshot equal; no backup/new edition | P10/R20; K/F and current focused, **green** |
| FS05 `cleanup-after-rollback` | Manifest publication fails after real document writes; then `shutil.rmtree` fails | Every original snapshot row restored with bytes/mode/mtime/inode, new path absent; exactly one cleanup call leaves a backup under the owned parent with mode0700 | P07/P10/R15/R20; K/F and current focused, **green** |
| FS06 `cleanup-after-commit` | Complete real publication/stale pruning succeeds; only final `shutil.rmtree` fails | Independent exact new edition/pathset/manifest/body/count/hash/clock/trust oracle passes; unowned/outside sentinels unchanged; mode0700backup contains original index/document/manifest bytes | P07/P10/R15/R20 outcome distinction; K/F and current focused, **green** |

**Bounded result:** six real consumer parameters filled, not six new proposal groups. R15's afterwrite/manifest/stale commit-fault rollback controls remain unchanged and passing; FS06 is **not** evidence that a cleanup failure restores prior. It returns failure with a complete new edition and recoverable private prior copy, as the corrected docs/changelog describe. No claim covers partially destructive `rmtree` failure, arbitrary credentials/Python source frames, crash/SIGKILL, reader atomicity or concurrent writers. These limits remain explicit, not an untested scenario silently promoted to green or a license to weaken acceptance.

### Connected stdio and linked-guide follow-through — R24 / DOC-04 Code facet

`omega_prime/tests/test_grokbot_oneclick.py::test_served_stdio_is_protocol_only_and_reads_the_owned_workspace` (`:1353-1462`) supplies **two existing-file parameters**, `json_report=False/True`, not two new proposal groups. Each independently seeds `{"path": "owned-proof.txt", "content": "owned stdio workspace\n"}` in a private temporary workspace; owns HOME/config/cache/state/absent credential-store paths and child process group; supplies `OMEGA_PRIME_WORK_ROOT`; then launches the real launcher/server from outside the install root. It sends initialize, initialized notification and `tools/call read_file`; every stdout line consumed before the matching responses must parse as JSON with `jsonrpc == "2.0"`, correlated responses must lack errors, and the single tool content must decode to the **exact independent seeded object**. Banners, blank lines and launcher JSON reports fail, not merely “host exists”/schema/nonempty/echo assertions. Cleanup is in `finally`, with bounded wait and owned-group termination.

E14 supplies current passing2-case execution after `--skip-doctor` isolation. That flag avoids the **install-root doctor's audit-file probe**; it is not a doctor/preflight requirement waiver, authentication/approval relaxation or live-capability waiver. The strict permanent cases do **not** exercise default doctor themselves. E15 separately observed actual default preflight/doctor and optional JSON report on stderr while all three SDK routes returned exact owned file contents without parse errors. Source `oneclick.py:330-335,481-499,524-527,558-570` derives served-stdio routing and passes the selected stream to preflight/reports; dry-run/SSE JSON remain stdout. Existing parent launcher/receipt tests remain the bounded regression support for those unchanged CLI contracts, not a new remote SSE run.

**WARNING / bounded coverage:** permanent strict parsing covers the received lines through initialize and the requested tool result; cleanup drains process output but does not assert every trailing shutdown byte or future tool/optional MCP feature. SDK success alone was demonstrably insufficient before the fix, so it is not substituted for the strict wire oracle. E15's recorded doctor checks include install-root audit-path writability and existing environment warnings; this is not proof of wholly temporary doctor I/O or all-environment health. Exact SDK results/read effects, current strict assertions and separately visible default diagnostics substantiate the required bounded Code facet, not source presence.

E16 supports architecture's real `gateway_unreachable`/`non_loopback_refused` distinction without starting a mission/backend or crossing the non-loopback network boundary; it does not exercise successful bearer-authenticated remote gateway operation. E17 adds shipped-guide reachability, not client-account attachment/publication. Full2352 was **before** the isolation-only test change; current2 and quality were **after** it. Fresh17-file prior now passed632/1warning/113.41s at current frozen revision/current isolated fixture, E18; no final-full-after-flag pass is claimed. R24 remains Human PARTIAL; no26/26 Human pass or requirement promotion.



## R01–R26 Oracle Map — distinct from structural controls

| Oracle | Code coverage / precise receipt or test | Overall judgment / known limit |
|---|---|---|
| R01 complete exact21 | E1/E2/E3, independently every full retained body/provenance | **COVERED bounded edition**; no accuracy/rights inference |
| R02 pagination/JSON/SSE/session | P01/P11 +E1; valid correlated fixtures/wrongID rejection | **COVERED** named offset/response admission; not full MCP optional-feature certification |
| R03 malformed counters | P02 both discovery/list boundaries; no document fetch | **COVERED** whole prior preserved |
| R04 zero-progress beforetotal | P02 zero-early +P06 real continuation | **COVERED**, distinct from validzero R06 |
| R05 ambiguity/scancap | P02 same/different/cross-page duplicate/scancap | **COVERED**, no guessed first match |
| R06 noncomplete outcomes | P03 +explicit validempty/no-key tests | **COVERED**, exits2/3/0 and full prior/no-key zero calls |
| R07 index/path/flag | P04 both missingindex flags/type/duplicate/invalid | **COVERED**, count alone insufficient |
| R08 absent/null/malformed/empty Markdown | P05 full document cases | **COVERED**, empty HTTPbody test not substituted |
| R09 capped sourceprefix | P05 caps/prefix +P11 malformed replies | **COVERED bounded cap observations**, no suffix/retry/excerpt recovery |
| R10 count units/types | P05 BMP/nonBMP positive, UTF16 mismatch negative +E3 actual counts | **COVERED fail-closed rule**, vendor future Unicode unit unknown |
| R11 foreign identity | P05 separate repo/ns/path/section | **COVERED**, complete prior survives |
| R12 unstable editions | P06 separate page/after axes +existing mixedversion | **COVERED**, genuine total continuation supplied |
| R13 ownership/confinement | P04 safe-distinct bodies/unsafe strings/symlink/collision whole-tree oracle | **COVERED**, no outside changes; not parallel TOCTOU certification |
| R14 late fetch | `test_late_fetch_failure_preserves_whole_prior_set` +E1 each actual21 transport | **COVERED**, known legacy failure not relabeled fixed |
| R15 afterwrite/manifest/stale/recovery | P07 +FS05/FS06 +historical E1 second real final-target write, current E12 | **COVERED bounded commit-fault rollback and cleanup outcome distinctions**; partialrestore recoverable ≠already restored; cleanup-after-commit failure leaves complete new edition +private prior backup |
| R16 unchanged clocks/reorder | P08 whole snapshot +E1/E3/E4 | **COVERED**, full header/manifest/inodes/nochurn |
| R17 separate substantive axes | P08 three independent successful consumers | **COVERED**, full new edition/prune only owned |
| R18 unknownoriginal vs freshclock | E2/E3/E4; P01 externally bracketed request/read clocks | **COVERED retained observation**, no new clock/provenance API |
| R19 directives stay data | P09 literal full bodies/requests/outside/approval negative effects | **COVERED importer boundary**, not model immunity or Human authority waiver |
| R20 privacy/API/CLI/gate | P10/P11/directcall +FS01–FS06/E13, current E12, unchanged shared full-input detector | **COVERED named boundaries**, safe GreptileError message/trace and truthful prior/new/backup effects; new six directAPI, retained CLI coverage unchanged; not all credentials/arbitrary Python exception frames |
| R21 architecture/execution claims | E9 rendering +E12 regressions | **PARTIAL /Human pending** claim→source→applicable receipt |
| R22 storage/security/host/signing | E6/E7/E9 retained +E16 real loopback refusal/non-loopback pre-network rejection +corrected registration-specific paragraphs | **PARTIAL /Human pending** owner/store/policy/auth/approval/signing; no bearer-authenticated remote success |
| R23 extension guide | E8 +`test_modes.py::test_extension_hook_runs_before_the_model_call`, `::test_first_extension_stop_wins_and_skips_the_model` in F | **PARTIAL /Human pending**; ordered hook effects green, source-guidance applicability not autoaccepted |
| R24 installation/config/Grok prep | E6/E7 retained; L/E14 strict normal+JSON initialized owned read; E15 actual SDK three workspace routes/default doctor stderr; E16 gateway effects/E17 linked guide; E18 fresh prior | **PARTIAL /Human pending** roots/Python/license/prereqs/client attachment/local-vs-live; isolation flag is not doctor/live waiver; full-before-flag/current632selected-prior chronology retained |
| R25 nav/history/assets | E10/E11 retained, E17 expanded26file176links and `test_docs.py::test_summary_links_resolve_exactly_once` in F | **PARTIAL /Human pending** history/no-copy/polish; local Code reachability green,14remote links not fetched |
| R26 catalog current/drift/missingdef | C/P12/real catalog tests in F | **PARTIAL /Human pending** capabilities/approval/inventory interpretation; Code gap FILLED |

Totals: **20 bounded COVERED, 6 PARTIAL due mandatory Human judgment, 0 MISSING automatable oracles**. These are not a26/26 overall acceptance rate.

## Evaluation Dimensions — all8

| AI-SPEC dimension | Current Code proof | Human / overall boundary |
|---|---|---|
| Correctness /complete identifiable edition | E1–E4/R01/R18/P01/P05 | Code green; actual edition only; provenance/rights interpretation separate |
| Failure preservation /substantive change | P07/P08/FS05/FS06/R14–R17, historical E1 +current E12 | Code green; original restoration/reconstruction distinguished from committed-new cleanup failure; no kill/concurrency/partial-cleanup promise |
| Tool/format /truthful completion | P01–P06/P11 retained status consumers; L/E14 strict initialized stdio owned read/E15 SDK | Code green named parser/count/index/identity/protocol-output effects, not full optional-protocol or trailing-shutdown certification |
| Safety /instruction following | P04/P09/P10/P11/FS01–FS06, E5/E13 retained, E14 current runtime regression/E16 gateway refusal | Code green named error/output/filesystem/refusal effects; Human authority/license/signing remains pending |
| Source faithfulness /factual accuracy | E8 effects/E9 renders retained; E15 workspace/default diagnostics/E16 actual errors/E17 links | **Human primary; PARTIAL**, interface claim correction has actual effects, not a model judge/symbol-count/wording score |
| Task completion /runnable onboarding | E6/E7 retained named local routes; L/E14 and E15 three real initialized owned workspace reads | **Human PARTIAL**, preparation not clone/install/hosted compatibility/live account attachment; default doctor separately observed, not waived |
| Discoverability /generated integrity | E10/E11 retained/E17 expanded local links/P12/current0/stale1/missingdef | Code green local surface; **Human PARTIAL** catalog/config/history/polish,14remote links unobserved |
| Behavioral regression /pass rate | E14 full2352 before test-only flag; current2 and quality after; E18 fresh632/17files at current isolated fixture; historical E12 focused200/prior630 and structural26 separate | Current named Code consumers and fresh mandatory prior green; no final-full-after-flag receipt or independent/Human gate substitution |

No inference call, LLM judge, calibration, new framework, telemetry or evaluation spend. No averaging a Human/source/publication failure into a passing score.

## Initial Findings — Independent Current Guard Recheck

These were source-derived BLOCKER candidates at the initial baseline, not auditor-run failures. They no longer represent open automatable coverage blockers after the owner's remediation and actual passing consumer proofs.

| Initial finding | Actual current placement (`omega_prime/greptile/kb_sync.py`) | Parent proof / disposition |
|---|---|---|
| B01 incomplete/duplicate discovery | `:360-361` overshoot rejects before admission; `:385-388` any second matching repo rejects | Historical E5 oversized/same duplicate +P02/current E12 — **CLOSED bounded behavior** |
| B02 nonempty noindex | `:490-494` flag agreement plus mandatoryindex when total>0; totalzero exception retained | Historical E5 missingindex +P03/P04/current E12 — **CLOSED**, no traversal confound |
| B03 response.read ungated | `:179-191` read inside transport try; OSError converted and cause suppressed | E5 read +P10/P11 directcall/E12 — **CLOSED** |
| B04 API/decoder/raw causes | `GreptileError.__init__ :65-77` full-input gate; parser failures `:899-911`, publication/restore `:749-764` use `from None`; path validators raise gated error | Historical E5 validation/publication +P10/P11 API/CLI/directcall/current E12 — **CLOSED named boundaries**; final outer filesystem gap separately covered below |
| B05 final status metadata | `main :305-327` all four branches call `_print_safe_status :868-879` with safe status fallback/validated counts | Historical E5 status +P10 eight repo/out×status cases/current E12 — **CLOSED output boundary**; archival data not newly redacted |
| B06 wrong JSON response | `_request :151-163` JSONRPC2.0 +exact integer currentID before result/tool use; SSE `:924-929` exactID selection then same admission | P11 JSONwrong991/SSEunrelated991/992/IDtype/version cases +P01 validJSON/SSE/current E12 — **CLOSED**, valid fixtures correlated without weakening wrongID |

Final residual filesystem placement: exported `sync :210-276` encloses prior reads (`_read_prior :647,669`), root preparation/backup creation (`_commit :732-733`) and final cleanup (`:769-771`); outer `except OSError :273-276` converts through the existing full-input `_remote_failure`/`GreptileError` gate with `from None`. E13 demonstrates the original raw-message/trace leak and corrected direct result; FS01–FS06 +current E12 prove named consumer effects. This is source inspection plus parent execution, not an auditor-run check or an independent security gate verdict.

Current catalog guard independently inspected: `catalog.py:133-150` builds the real registry and refuses absent roster definition; `:176-189` renders before any output write. P12 therefore reaches the actual absent-definition boundary and preserves output, not a fake schema echo.

Corrected SECURITY/user-guide paragraphs distinguish `requires_approval=True` registrations from ungated coding write/patch/edit/run_terminal with host-user authority/sandbox need (`SECURITY.md:96-111`, `docs/user-guide.md:41-46`). FAQ (`:116-122`) says local requested review standards are not every-PR remote review proof. These are claim corrections, **not runtime approval expansion, signing, principal mutation, protected write or deployment proof**.

**IN02 remains INFO:** `.github/workflows/kb-refresh.yml:50-60` date-named branch collision can fail loudly on a same-day rerun. This is existing red retry robustness, not one of the closed high admission/privacy defects. Carry it in the parent's decision ledger; no unapproved force-checkout, branch-replacement/API-retry expansion, hiding or automatic waiver. Workflow hosted execution remains unobserved.

## Manual-Only Judgments and Required Human Routing

| Behavior / requirements | Why no automatic wording test | Required owner action / supporting evidence |
|---|---|---|
| Current docs/source-faithfulness/process/storage/security — DOC-01/02/03 | Rendering/symbol presence/green suite cannot adjudicate material source claims | Runtime maintainer/docs reviewer traces each claim to current symbol and applicable receipt; E8/E9 support but do not sign off |
| Safe runnable route/prerequisites/live distinctions — DOC-01/04 | Compatibility/authorization/optional service claims need interpretation | Onboarding/security reviewers judge URL, packagefloor>=3.11 vs recommendation/exercise3.12 vs retainedCI3.12–14, roots/submodules/license/private secrets/approvals; E14/E15 strict+SDK owned reads/default diagnostics and E16 errors support bounded preparation, not account attachment |
| History/provenance/rights/authority — KB-01/03 | Exact bytes do not prove factual truth, permission or safe operator belief | KB/security/vendor reviewers keep untrusted history separate from current truth; original clock unknown; no redistribution entitlement or content-granted authority |
| Workflow/changelog honesty — KB-02/DOC-01/05 | Hosted transitions/release belief not established by local config | Workflow/changelog owner reviews honest skip/change-only/Unreleased; records IN02; no fabricated hosted or release proof |
| Catalog/config/nav/polish — DOC-05, KB-03 | --check/reachability do not prove handlers, hosted publication or semantic readability | Docs/runtime reviewers judge capabilities/per-tool approval, inventory vs exercised effects, one history/current route, GitBook mapping and visual polish; E10/E11 local only |

All **seven unique unclassified edge assumptions** remain human-unverified (`verification: null`, `resolution: null`): KB-02, KB-03, DOC-01, DOC-02, DOC-03, DOC-04, DOC-05. Automated regressions do not automatically resolve those PLAN fields.

All **19 unique descriptorless prohibitions**, **28 physical PLAN copies**, remain human-unverified and retained:

| Requirement | Unique pending prohibitions |
|---|---|
| KB-01 (3) | No synthesis-as-current-truth; no completeness-as-correctness/injection-immunity; no implied vendor redistribution entitlement |
| KB-02 (3) | No destruction of last recoverable complete edition; no empty/skipped/not-run-as-complete; no weakening shared detector |
| KB-03 (3) | No historical directive/claim as execution/credential/approval authority; no fetched text expanding permission; no stale security/toolchain as current without source confirmation |
| DOC-01 (3) | No Python floor/recommended/exercised/CI conflation; no live/account promise without credentials/proof; no upstream/provider relicensing inference |
| DOC-02 (2) | No native GSD as signed Swarm/protected-write/deploy proof; no automatic human-required approval implied by policy diagram |
| DOC-03 (2) | No registered-undispatched inventory as live-tested; no seam/approval bypass or weakened controls |
| DOC-04 (2) | No local double/preparation as live Grok; no auth/approval disabling or embedded credentials to make route work |
| DOC-05 (1) | No source wording/default/copied-schema pins making refreshed prose appear verified |

No fabricated `check_*` descriptors/scalars, automatic waivers or phase76 carryover. Fresh phase77 user/Human dispositions are still required; this audit does not modify any PLAN prohibition/assumption or canonical status.

## Deliberate Non-Claims

No hosted CI/jobs; direct environment-key CLI remoteHTTPS; remote GitBook publication/14external-link fetches; fresh clone/install/source push/PR/merge; live Grok/model/provider/RedTeam/backend mission; imagepublication/deployment; protected `.swarm/` write/signing. No production/inference grade, universal credential detector guarantee, crash/concurrent publication guarantee, or vendor rights determination. These are neither concealed local-double passes nor newly demanded phase77 platform scope.

No canonical whole-goal VERIFICATION/phase completion/source publication/milestone closure is authored here. Parent owns independent gates, decision ledger, lifecycle, index and final acceptance.

## Validation Sign-Off

- [x] Prior all7 PLAN/SUMMARY reconstruction retained; final map, reconciled77-04task2, current interface paragraphs/stdio assertions/output routing and exact parent interface/full/runtime/link bindings independently re-read; filesystem consumer inspection retained with unchanged source.
- [x] All16 tasks/all8 requirements/all26 R-oracles/all8 dimensions mapped; Human judgments routed separately.
- [x] P01–P12 existing-file consumer coverage is meaningful and parent-exercised; no unknown required automatable scenario claimed passed from source alone.
- [x] Whole prior pathset/filebytes/mode/mtime/inode/symlink/outside preservation; real afterwrite/manifest/staleremoval/partialrestore reconstruction; separate body/version/path changes covered.
- [x] Full21 pagination, correct valid correlation/wrongID refusal, null/count/cap/alias boundaries, literal-body/directive effects, >200-char privacy/unavailablegate/directAPI/causes/status covered with bounded proofs.
- [x] Six initial source candidates retain passing consumer coverage; final six filesystem parameters prove safe type/message/trace and truthful prior/new/backup effects. IN02 retained INFO, not silently “fixed”.
- [x] R24/DOC-04 adds two real strict initialized owned-read parameters plus three actual SDK workspace routes/default doctor stderr, actual gateway error/refusal and expanded local links; source/schema/echo proof is not substituted.
- [x] Fresh mandatory17-file prior receipt received for the current interface revision/current isolated stdio fixture: E18,632passed/1warning/113.41s, runner wall115.59s/bound600s/exit0.
- [ ] Full-suite execution after the isolation-only test flag received; full2352 retains its before-flag chronology, current2/quality are after-flag.
- [x] `nyquist_compliant: true` and `wave_0_complete: true` justified for the **automatable coverage lane only**.
- [ ] Mandatory source/license/signing/polish/prerequisite/Human applicability judgments complete.
- [ ] Seven unclassified assumptions and19unique/28physical prohibitions have fresh Human/canonical-owner dispositions.
- [ ] Independent whole-phase review/security/eval/verifier/whole-goal acceptance complete.

**Result:** GAPS FILLED for the12 automatable proposal groups by the parent, **PARTIAL overall acceptance**. **Approval:** Pending. No test was authored or run by this re-auditor.

## Validation Audit — Initial 2026-10-11 (historical)

Initial StateB reconstruction at `256baac892ddcd0d2572d52354e9d47a55107122`:2covered/14partial task rows;1boundedcovered/7partial requirements;5covered/18partial/3missing R-oracles. Twelve proposal groups NOT RUN then; six source-derived BLOCKER candidates; no auditor execution. That snapshot is superseded for Code coverage, not retroactively edited into a passing result.

## Validation Audit — Remediation Re-audit 2026-10-11

- Binding: HEAD `75744eb10126d1be50f11c0b54bbc095cf6c6371`; current importer SHA in E1 agrees with frozen rereview input.
- Required automatable proposal groups: **12 found historically,12 resolved by existing parent-authored passing consumers,0 escalated,0 skipped**. Current R-oracles:20boundedcovered/6Humanpartial/0missingautomatable. No blanket26/26 acceptance.
- Parent execution: focused194passed/1warning/7.74s; full2344passed/16warnings/153.37s (`artifact://705`). Current JSON/SSE real21 runtime and seven after-reproductions retain their named limits.
- Author execution/new tests/source edits/commits: **0**. Sole file for parent commit consideration: `.planning/phases/77-maintained-docs-and-knowledge-base/77-VALIDATION.md`.
- Human flags:7unique assumptions +19unique/28physical prohibitions unverified; no waiver. All canonical requirements/whole-phase acceptance remain pending.

## Validation Audit — Final Filesystem Binding 2026-10-12

- Current revision: `acc2aa8659327e11bf22a0357a66b8c86726bda4`, exact four-path correction binding `local://v15-phase77-filesystem-remediation-binding.json`; importer SHA256 `63cd9c8320262b13859caf5e8e37b1f5d5a27c2abdfa815b3ea878fb7c5ef112`, test SHA256 `baedae225f3d1c3f6cec2424a844ccfec800487356d3753270ffcac25a1e98cd`. No final whole-source fingerprint invented.
- Counts unchanged: **12/12 automatable proposal groups filled; six added filesystem parameters green;20boundedcovered/6mandatoryHumanpartial/0missingautomatable R-oracles;16tasks/eightrequirements/eightdimensions mapped**. No arbitrary credential/frame/crash/concurrency certification, overall26/26approval, phase pass, UAT or canonical requirement promotion.
- Parent-only current proofs: full2350passed/16warnings/157.34s (`artifact://729`); focused200passed/1warning/7.98s; fresh mandatory17-file prior630passed/1warning/92.04s, bound600s/exit0 (`local://v15-phase77-final-regression-proof.json`). Historical2344/194/458 snapshots and actual21JSON/SSE proof remain named with original bindings.
- R15/P07 and R20/P10 now explicitly include FS01–FS06. Required existing full-body/parser/catalog coverage remains unchanged; no helper-only/source/default/wiring pin replaces it. Cleanup-after-commit failure is distinguished from restored prior.
- Author runtime/checks/new tests/product/shared-planning edits/commits: **0**. Only modified file for the parent's commit: `.planning/phases/77-maintained-docs-and-knowledge-base/77-VALIDATION.md`.
- Residual boundaries: no separate six-case CLI execution or partial destructive cleanup proof claimed; E13's small before/after receipt omits exact fault details, so it is supporting evidence only. No missing required automatable consumer coverage found in this bounded map. Six Human-partial oracles, seven unclassified assumptions,19unique/28physical prohibitions and all eight canonical requirement acceptances remain pending fresh Human/owner disposition. No independent gate alone authorizes advancement.

## Validation Audit — Source/Interface Binding 2026-10-12

- Frozen revision: `1f0adf62a1668d3f3a0d3149987543cdd5f5b5cd`; six-path correction hashes in `local://v15-phase77-interface-correction-binding.json`. Approved source union53, same7plans/16tasks/34rawtruths/eightrequirements; existing77-04task2 reconciled without dropping an outcome. Historical filesystem binding above is not current-head full/prior execution.
- Counts retained: **12/12 proposal groups filled,26R-oracles/eightdimensions/16tasks/eightrequirements mapped;20boundedcovered/6mandatoryHumanpartial/0missing required automatable consumers within the bounded map**. R24 now includes exact initialized owned-read effects and strict normal/JSON received-frame parsing; R22/R25 and DOC-01/04/05 receive applicable gateway/link/workspace evidence without Human promotion.
- Parent execution only: E14 full2352/16warnings/176.35s before isolation flag; launcher/receipt/rerun90/1warning/28.29s; current strict2/1warning/23.65s and Ruff0/401format/mypy0/339sources after flag. E15 three actual SDK exact workspace reads/no parse errors/default doctor+JSON stderr; E16 actual refused loopback connection and pre-network unauthenticated non-loopback refusal; E17 26files/176internal links/0issues/14remote not fetched. Fresh mandatory17-file prior completed at frozen interface revision/current isolated fixture: E18,632passed/1warning/113.41s, runner wall115.59s/bound600s/exit0; prior630 is historical. No source change after freeze reported by parent.
- **Confounders retained, not greenwashed:** SDK originally tolerated invalid frames; its probeexit1 was wrong result-field access, not launcher failure. Strict cases skip only install-root doctor audit probing, while actual default doctor is separately observed; permanent strict tests do not assert trailing shutdown output/all optional protocol features. Local preparation/read/error/link success does not prove live account/model/provider/backend/mission/publication, successful authenticated non-loopback access or whole-environment doctor isolation.
- Source/PLAN/SUMMARY corrections make the historical34/34human_needed canonical VERIFICATION stale. Fresh whole-goal fingerprint/re-verification and actual Human dispositions remain pending: all six Human-partial oracles, seven unique assumptions,19unique/28physical prohibitions and eight canonical requirement acceptances. Prior10/21accepted does not accept the remaining contributions; no phase78/milestone/publication/signing/mission authorization follows.
- Auditor checks/runtime/tests/debugiterations/product/sharedlifecycle edits/commits: **0**; no new permanent tests. Sole file for parent commit consideration: `.planning/phases/77-maintained-docs-and-knowledge-base/77-VALIDATION.md`. Result: automatable consumer gaps FILLED by existing parent-exercised tests; **PARTIAL overall acceptance**, with freshness and environment/protocol limits explicitly retained.

## Validation Audit 2026-10-12

| Metric | Count |
|---|---|
| Gaps found | 0 |
| Resolved | 0 |
| Escalated | 0 |
