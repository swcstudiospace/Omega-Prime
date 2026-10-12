---
phase: 77-maintained-docs-and-knowledge-base
verified: 2026-10-12T23:00:00Z
status: passed
score: 34/34 must-haves verified
covered_files: [".github/workflows/kb-refresh.yml", ".planning/REQUIREMENTS.md", ".planning/phases/77-maintained-docs-and-knowledge-base/77-01-PLAN.md", ".planning/phases/77-maintained-docs-and-knowledge-base/77-01-SUMMARY.md", ".planning/phases/77-maintained-docs-and-knowledge-base/77-02-PLAN.md", ".planning/phases/77-maintained-docs-and-knowledge-base/77-02-SUMMARY.md", ".planning/phases/77-maintained-docs-and-knowledge-base/77-03-PLAN.md", ".planning/phases/77-maintained-docs-and-knowledge-base/77-03-SUMMARY.md", ".planning/phases/77-maintained-docs-and-knowledge-base/77-04-PLAN.md", ".planning/phases/77-maintained-docs-and-knowledge-base/77-04-SUMMARY.md", ".planning/phases/77-maintained-docs-and-knowledge-base/77-05-PLAN.md", ".planning/phases/77-maintained-docs-and-knowledge-base/77-05-SUMMARY.md", ".planning/phases/77-maintained-docs-and-knowledge-base/77-06-PLAN.md", ".planning/phases/77-maintained-docs-and-knowledge-base/77-06-SUMMARY.md", ".planning/phases/77-maintained-docs-and-knowledge-base/77-07-PLAN.md", ".planning/phases/77-maintained-docs-and-knowledge-base/77-07-SUMMARY.md", ".planning/phases/77-maintained-docs-and-knowledge-base/77-UAT.md", "CHANGELOG.md", "CONTRIBUTING.md", "README.md", "SECURITY.md", "docs/README.md", "docs/SUMMARY.md", "docs/agent-loop.md", "docs/architecture.md", "docs/build-aesthetics.md", "docs/connectors.md", "docs/driving-the-bot.md", "docs/faq.md", "docs/gitbook-sync.md", "docs/greptile.md", "docs/grok-bot-native.md", "docs/marketing.md", "docs/migration.md", "docs/red-team.md", "docs/setup.md", "docs/substrate.md", "docs/tool-catalog.md", "docs/tool-host.md", "docs/user-guide.md", "kb/README.md", "kb/manifest.json", "omega_prime/greptile/kb_sync.py", "omega_prime/grokbot/templates/OMEGA_PRIME.md"]
covered_digest: "v3:sha256:91275560665137691f77e4dec77988d566b211f72669c4862ea00f0366c5c6c2"
behavior_unverified: 0
overrides_applied: 0
re_verification:
  previous_status: passed
  previous_score: 34/34
  gaps_closed: []
  gaps_remaining: []
  regressions: []
---

# Phase 77: Maintained docs and knowledge base Verification Report

**Phase Goal:** Publish source-verified canonical guidance beside one complete, refreshable historical Greptile mirror.
**Verified:** 2026-10-12T23:00:00Z at HEAD 2ccb3e8
**Status:** passed
**Re-verification:** Yes — regeneration at HEAD 2ccb3e8. The prior report (verified 2026-10-12T21:30:00Z at 787df2e, 34/34) is superseded because covered docs changed: 0067526 (banner/social-card assets), 89ac138 (new docs/marketing.md + nav), 9314ca3/08cf459/305de61 (phase-78 doc/test guards), and 2ccb3e8 (Bot template absolute package paths; docs/local-validation.md 77/77/11 counts; docs/setup.md loopback-bearer + CI-docs-job prose; docs/driving-the-bot.md template paraphrase path; docs/marketing.md ledger range; CHANGELOG Fixed entries). The kb/ mirror is byte-unchanged (`git diff 787df2e HEAD -- kb/` empty) and every other phase-77 core doc (greptile.md, architecture.md, agent-loop.md, build-aesthetics.md, SECURITY.md, connectors/substrate/tool-host, grok-bot-native.md, migration.md, user-guide.md, faq.md, red-team.md, tool-catalog.md, gitbook-sync.md, CONTRIBUTING.md, kb/README.md, kb_sync.py, kb-refresh.yml) is byte-unchanged since 787df2e. No overrides were carried (prior report recorded none) and none are added.

## Denominator reasoning (no silent narrowing)

34 raw obligations as 34 rows: 29 PLAN truths (77-01: 4, 77-02: 8, 77-03: 4, 77-04: 4, 77-05: 3, 77-06: 3, 77-07: 3) plus 5 ROADMAP success criteria (SC1..SC5). Where a PLAN truth restates a roadmap SC, both rows are retained and cross-referenced. Same 16 tasks, 7 plans, 34 truths, 8 requirements — no new requirement, no suppressed acceptance. This regeneration re-checks every changed-doc claim (template path, loopback host, CI job, counts) against current source; unchanged-truth evidence from the prior report is carried only where neither the artifact nor its cited source changed (kb/ mirror, kb_sync.py strict client, untouched canonical pages).

## Goal Achievement

### Observable Truths — PLAN truths (29)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| P01 | 77-01 strict client sends namespaceId at every call site, zero repoNamespaceExternalId refs; malformed/truncated/mixed-version/count-inconsistent/unstable/unsafe candidate fails visibly with prior mirror + manifest byte-identical | ✓ VERIFIED | Re-read at HEAD: `kb_sync.py` 14 `namespaceId` refs, 0 `repoNamespaceExternalId` refs (byte-unchanged source). `test_kb_sync.py` 195/195 pass in this run |
| P02 | Unchanged accepted publication performs zero writes, retains original fetch provenance | ✓ VERIFIED | Carried: mirror + manifest byte-unchanged since prior admission receipt; no new edition published (`git diff 787df2e HEAD -- kb/` empty) |
| P03 | Missing key (exit 2), unenrolled repo (exit 3), valid empty publication (exit 0, no writes) stay distinct non-complete outcomes | ✓ VERIFIED | Carried: exit-code separation in `main()` unchanged; exercised by kb_sync suite (195 pass) |
| P04 | Concurrency: single-writer staging with commit-exception rollback only; no SIGKILL-mid-commit / no parallel-writer guarantee; cancel-in-progress is only control | ✓ VERIFIED | Carried: staging + commit-exception rollback source unchanged |
| P05 | Exactly 21 listed paths incl. index.md with full exact bodies, repo swcstudiospace/omes-bot, namespace 3b0877f2-…, section docs, version 2026-10-08-1791571202911-c82b36, fresh clocks, counts 239020 UTF-8 bytes / 238985 chars | ✓ VERIFIED | Re-read at HEAD: manifest `complete: true`, `retrieved_documents: 21`, `total: 21`, `indexPresent: true`, repo + namespaceId + sectionVersions.docs exact; recomputed sums from per-document `utf8_bytes`/`chars` = 239020/238985 |
| P06 | Retained snapshot is expected-path/body/version oracle; changed-publication rule admits complete stable NEW snapshot; no offline CLI / provenance API created | ✓ VERIFIED | Carried: mirror unchanged; no offline CLI or provenance API in source |
| P07 | Original listingBefore null is captured-history metadata only, never substituted with fresh import times; no live writes/reviews/deployments | ✓ VERIFIED | Carried: committed manifest retains fresh actual production observation clocks |
| P08 | KB-01 adjacency edge: exact full-relative-posix-path equality; touching strings never merge; unique-path-set equality vs 21 retained paths | ✓ VERIFIED | Carried: adverse adjacency consumers in kb_sync suite (195 pass) |
| P09 | KB-01 empty edge: total-zero publication valid empty (exit 0, no writes); empty/missing body rejected under locked empty-document refusal | ✓ VERIFIED | Carried: locked refusal in source unchanged |
| P10 | KB-01 ordering edge: listing/page order insignificant; sets + index presence compared | ✓ VERIFIED | Carried: shuffled-listing consumer unchanged |
| P11 | KB-01 concurrency edge (same as P04 at mirror level) | ✓ VERIFIED | Same evidence as P04; alias retained as separate row |
| P12 | kb/README.md names repo, states predates-v14 untrusted-evidence limits, stays trust guide not second index; docs/greptile.md states strict outcomes, keeps kb/ outside GitBook, links canonical surfaces without copying | ✓ VERIFIED | Carried: both files byte-unchanged since 787df2e; trust banners, link-not-copy, kb/-outside-GitBook intact |
| P13 | Architecture + execution/state/security diagrams identify actual current source symbols with ownership and policy/credential/approval checks; core loop / native libs / RedTeam backend (external, default-off) / remote services distinguished | ✓ VERIFIED | Carried: architecture.md, agent-loop.md byte-unchanged; IntakeStore paragraph (orders list, flock sidecar, separate events.ndjson) still matches `lead.py` |
| P14 | Catalog/schema counts are inventory, never execution evidence; stale blanket claims reconciled, not copied | ✓ VERIFIED | Re-read: `catalog --check` exits 0 ("up to date") in this run; tool-catalog.md byte-unchanged |
| P15 | Extension guidance states real registration-order first-stop-wins before_model contract, no invented loader/discovery/install API | ✓ VERIFIED | Carried: extension prose and `extensions.py` symbols unchanged |
| P16 | SECURITY.md matches actual handling (private token-file/env, owned-host auth, privileged approval, no credential examples, native≠signed-Swarm); no unsupported paths | ✓ VERIFIED | Carried: SECURITY.md byte-unchanged; 16/16 threats closed |
| P17 | Setup/usage use actual omes-bot URLs with recursive-submodule clone, real roots, runnable no-credential CLI/readiness/doctor/workflows/config aligned to exercised behavior | ✓ VERIFIED | Re-read at HEAD: setup.md 2ccb3e8 delta re-checked line-by-line — `--verify` loopback bearer host at `/mcp` matches `plugin.py` `run_self_test`/`HTTPS_PATH` source ("verify a loopback bearer host at `/mcp`"); "(a public HTTPS host is not provable locally)" matches loopback-only provability; CI `docs` job prose (Python 3.12, `pip install -e . pytest`, test_docs + test_public_repo, both `--check` lanes) matches `.github/workflows/ci.yml` docs job exactly; exit classes 10–13 with one stderr JSON line confirmed against launcher `classify` sites; Bootstrap stdio/`--bootstrap-only` claims unchanged |
| P18 | Python claims distinct: floor >=3.11, recommended/exercised 3.12, CI 3.12–3.14; never collapsed | ✓ VERIFIED | Re-read at HEAD: `requires-python = ">=3.11"` in pyproject.toml; CI matrix `["3.12","3.13","3.14"]`; setup.md + README keep floor/recommended/matrix distinct; delta adds no Python claim |
| P19 | Secrets private-only, never in argv/examples/output; no public share/AddBot/live attachment claimed; approvals required | ✓ VERIFIED | Carried: setup.md delta adds no secret handling; `--token` deprecated-with-warning and mode-0600 mint prose unchanged |
| P20 | Red-team prose: root TS source pinned-optional, Python pack ordinary content, backend separately started, auth/network prerequisites explicit | ✓ VERIFIED | Carried: red-team.md byte-unchanged |
| P21 | Connector/substrate/tool-host prose matches current source interfaces; URLs/floor/counts honest | ✓ VERIFIED | Carried: all three guides byte-unchanged |
| P22 | Canonical arch/pattern/validation pages and historical mirror linked distinctly; no synthesis copied as architecture, no canonical body duplicated | ✓ VERIFIED | Carried: link-don't-copy rule intact; SUMMARY delta adds only the Marketing line |
| P23 | Optionals in grok-native/migration carry actual prerequisites with blocked/live limits; config/runtime distinct; evidence ledger with CONFIRMED/PARTIAL/UNPROVEN levels; six-row user UAT all UNPROVEN | ✓ VERIFIED | Carried: both files byte-unchanged; local `--verify` proof stated as owned-local-process only |
| P24 | Root README/CONTRIBUTING use actual URLs, recursive clone, floor vs 3.12 vs CI distinct, pinned Prime, full-suite prerequisite, optional red-team, in-process claims coherent | ✓ VERIFIED | Re-read at HEAD: README delta is additive only — Marketing link paragraph (matches existing docs/marketing.md) and assets-row `social-card.png rendered from social-card.svg` (matches 0067526); no prior claim altered; CONTRIBUTING byte-unchanged |
| P25 | Every canonical page exactly once in SUMMARY with resolving relative links; kb/ linked once via Greptile page; .gitbook.yaml unchanged | ✓ VERIFIED | Re-read at HEAD: SUMMARY delta adds exactly one line (`- [Marketing](marketing.md)`); Marketing appears exactly once; kb/ still not added as 21 pages; test_docs.py nav contract 11/11 pass in this run (guards expanded since prior 2/2 to cover marketing/links/anchors/H1/commands); .gitbook.yaml untouched |
| P26 | CHANGELOG one Unreleased entry describing phase-77 union, no invented version/date/operator claim | ✓ VERIFIED | Re-read at HEAD: phase-77 Unreleased entry intact (delta is additive — phase-78 marketing block and 2ccb3e8 Fixed blocks); no version/date/operator invention; new Fixed entries (template absolute paths, 77/77/11 counts, loopback bearer wording) each match the exercised source change |
| P27 | tool-catalog.md prose = inventory of registered definitions; generated output never hand-edited; fresh output passes --check | ✓ VERIFIED | Re-read: `catalog --check` exit 0, `plugin --check` exit 0 ("50 generated files up to date") in this run |
| P28 | gitbook-sync.md matches read-only .gitbook.yaml root mapping + single-copy nav; no unimplemented sync mechanism | ✓ VERIFIED | Carried: both files byte-unchanged |
| P29 | Pin cleanup: removed literal/loop pins, statusCheck/rules defaults, TOP_LEVEL_KEYS allowlist; no new pins; test_docs.py nav contract untouched | ✓ VERIFIED | test_tool_catalog + test_greptile_config 4/4 pass in this run; pin-absence holds (no re-pins in the delta) |

### Observable Truths — ROADMAP success criteria (5, aliases noted)

| # | Criterion | Status | Evidence (alias) |
|---|-----------|--------|------------------|
| SC1 | All 21 exposed Greptile documents incl. index imported with repository, namespace, version, fetch and completeness provenance | ✓ VERIFIED | Aliases P05+P06+P07. Manifest re-read + recomputed sums as above; mirror byte-unchanged |
| SC2 | Refresh uses namespaceId, rejects incomplete/truncated retrieval, preserves previous complete mirror; unchanged bodies cause no timestamp-only churn | ✓ VERIFIED | Aliases P01+P02+P04. 14/0 namespace refs re-read; suite 195 pass |
| SC3 | README, contribution/setup, existing docs contain current URLs and exercised claims; historical synthesis clearly separate from canonical docs | ✓ VERIFIED | Aliases P12+P17+P19+P24. Changed-doc claims (template path, loopback host, CI job) re-checked line-by-line above; driving-the-bot.md paraphrase `omega_prime/prompts-assembled/OMEGA_PRIME.xml` consistent with template's absolute `/workspace/omega-prime/omega_prime/...` given the documented clone root, and both files exist on disk |
| SC4 | Architecture, execution/state/security diagrams, patterns/extensions, runnable usage/config/workflows link to current source and honest prerequisites | ✓ VERIFIED | Aliases P13+P15+P16+P21+P23. Untouched core pages carried; setup.md additions match CI/launcher source |
| SC5 | Repository/GitBook navigation, generated catalog, internal links and assets consistent | ✓ VERIFIED | Aliases P25+P27+P28. Both `--check` lanes exit 0 in this run; test_docs 11/11; new Marketing page linked once in SUMMARY + README landing with resolving links |

**Score:** 34/34 truths verified (29 plan + 5 SC). Behavior_unverified: 0.

### Required Artifacts

All covered source paths exist and are substantive: kb_sync.py (14 namespaceId / 0 legacy refs), 21 mirror bodies + manifest.json + kb/README.md, 26 canonical doc pages plus new docs/marketing.md (phase-78 owned, nav-linked), kb-refresh.yml, root pages, SETUP.md, oneclick.py, `grokbot-1click.sh` (exit classes 10–13 with `classify` sites), tooling/plugin.py (`run_self_test` loopback bearer host at `/mcp`), mcp.json. No stub, no missing artifact. Wired: sync→manifest→mirror→guides→SUMMARY chain intact; grok-bot-native caller→SETUP target by real relative links; template absolute paths resolve to checked-in files (`omega_prime/prompts-assembled/OMEGA_PRIME.xml`, `omega_prime/contracts/tool-rosters/omega-prime.yaml` both exist).

### Key Link Verification

| From | To | Via | Status |
|------|----|-----|--------|
| McpHttpClient.call_tool | staged-set commit → manifest | strict validation in sync() | WIRED (kb_sync suite 195 pass) |
| kb-refresh.yml | change-only PR gate | git status of kb/ | WIRED (workflow unchanged) |
| retained snapshot bodies | mirror files | production importer → exact files | WIRED (mirror byte-unchanged, manifest re-read) |
| README/guide | canonical docs | link, never copy | WIRED (test_docs 11/11) |
| catalog page | catalog output | `--check` lane | WIRED (exit 0 this run) |
| plugin set | generated commands + manifests | `plugin --check` lane | WIRED (exit 0 this run) |
| grok-bot-native caller | shipped SETUP.md | real relative Markdown links, agreeing claims | WIRED (claims match launcher source) |
| setup.md CI prose | ci.yml docs job | Python 3.12 + base install + both test files + both --check lanes | WIRED (workflow lines re-read, exact match) |
| setup.md --verify prose | plugin.py run_self_test | loopback bearer host at /mcp | WIRED (docstring + HTTPS_PATH re-read) |
| driving-the-bot.md prose | OMEGA_PRIME.md template | paraphrase path == absolute template paths under clone root | WIRED (both files exist, paths consistent) |
| SUMMARY/README | docs/marketing.md | single Marketing entry + landing link | WIRED (test_docs 11/11) |
| architecture.md prose | lead.py IntakeStore | `orders` list, atomic replace, flock sidecar, separate events.ndjson | WIRED (carried, source unchanged) |

### Data-Flow Trace (Level 4)

Mirror bodies flow from retained snapshot → production importer → exact files (manifest re-read, recomputed 239020/238985 sums match). No hollow props, no static fallbacks in publication paths. Changed docs add no new data-flow surface (prose + nav + changelog only).

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| KB sync suite | `.venv/bin/python -m pytest omega_prime/tests/test_kb_sync.py -q -p no:cacheprovider` | 195 passed | ✓ PASS |
| Docs nav contract (expanded: marketing, links, anchors, H1, commands) | `.venv/bin/python -m pytest omega_prime/tests/test_docs.py -q -p no:cacheprovider` | 11 passed | ✓ PASS |
| Catalog/greptile consumers | `.venv/bin/python -m pytest omega_prime/tests/test_tool_catalog.py omega_prime/tests/test_greptile_config.py -q -p no:cacheprovider` | 4 passed | ✓ PASS |
| Catalog currency | `.venv/bin/python -m omega_prime.tooling.catalog --check` | up to date, exit 0 | ✓ PASS |
| Plugin currency | `.venv/bin/python -m omega_prime.tooling.plugin --check` | 50 generated files up to date, exit 0 | ✓ PASS |
| Manifest sums | recompute utf8_bytes/chars from kb/manifest.json | 239020 / 238985, match | ✓ PASS |
| Template paths resolve | `ls omega_prime/prompts-assembled/OMEGA_PRIME.xml omega_prime/contracts/tool-rosters/omega-prime.yaml` | both exist | ✓ PASS |

Parent regression receipts at HEAD 2ccb3e8 (cited, not re-run): full pytest 2544 passed; ruff, ruff format, mypy, plugin/catalog/assemble/setup_check, evals, readiness, readiness --include-prime (77/77/11), actionlint all exit 0.

### Probe Execution

No phase-declared probes for phase 77 (77-PROBES.md is a planning aid, not a runnable probe set). No probe execution required.

### Requirements Coverage

| Requirement | Source Plans | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| KB-01 | 77-01, 77-02 | 21 exposed Greptile documents with provenance | ✓ SATISFIED | P05–P11, SC1 |
| KB-02 | 77-01, 77-02 | namespaceId refresh, fail-visible, prior preserved | ✓ SATISFIED | P01–P04, SC2 |
| KB-03 | 77-01, 77-02, 77-06 | One refreshable historical mirror separate from canonical docs | ✓ SATISFIED | P12, P22 |
| DOC-01 | 77-03, 77-04, 77-05, 77-06 | Audited README and existing docs, current URLs/install/counts | ✓ SATISFIED | P17–P19, P23, P24, P26 |
| DOC-02 | 77-03, 77-05, 77-06 | Source-linked architecture/execution/state/security diagrams | ✓ SATISFIED | P13–P14 |
| DOC-03 | 77-03, 77-04, 77-06 | Source-linked design patterns and extension conventions | ✓ SATISFIED | P15 |
| DOC-04 | 77-04, 77-05, 77-06 | Runnable usage/workflows/config/prerequisites/Grok preparation | ✓ SATISFIED | P17, P20, P23 |
| DOC-05 | 77-06, 77-07 | Consistent navigation, generated catalog, links/assets | ✓ SATISFIED | P25, P27, P28, SC5 |

All 8 phase-77 requirement IDs from PLAN frontmatter accounted for; REQUIREMENTS.md maps exactly these 8 to phase 77 (all Complete) — no orphans. Human canonical acceptance recorded in 77-UAT.md tests 8–10 (all pass, source human, updated 2026-10-12T11:32:22Z). The user additionally passed 77 UAT tests 3–10.

### Anti-Patterns Found

None blocking. Grep over all changed docs for TODO/FIXME/XXX/TBD/not-yet-implemented returns zero stub markers; the two literal "placeholder" hits in CHANGELOG.md document `${VAR}` per-consumer placeholder syntax (evidence-tagged prose, not stubs). No TBD/FIXME/XXX in phase-touched source. No empty-return stubs.

### Human Verification Required

None pending. The three human-authority items (7 assumptions, 19 prohibitions, 8 requirement acceptances) were decided by a human in 77-UAT.md tests 8, 9, 10 — all `result: pass`, `source: human`. The user passed UAT tests 3–10. All changed claims in this delta are source-checkable and were source-checked (template paths, loopback host, CI job, counts). No new human-check items arise.

### Gaps Summary

No gaps. All 34 obligations verified at HEAD 2ccb3e8: the kb/ mirror is unchanged with provenance intact, every changed-doc claim matches current source, both generator lanes exit 0, the expanded docs nav suite (11 tests) passes with the new Marketing page linked exactly once, and the human authority items remain human-signed. Phase goal achieved. Ready to proceed.

---
_Verified: 2026-10-12T23:00:00Z_
_Verifier: Claude (gsd-verifier)_
