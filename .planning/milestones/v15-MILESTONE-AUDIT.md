---
milestone: v15
name: Integrated Readiness and Repository Delivery
audited: 2026-10-12T21:27:30Z
audited_head: 2ccb3e8
status: gaps_found
scores:
  requirements: 21/22
  phases: 4/5
  integration: 24/24
  flows: 4/5
requirement_disposition: 21 satisfied; GROK-03 partial (local proof verified, live import deferred by the user)
gaps:
  requirements:
    - id: "GROK-03"
      status: "partial"
      phase: "77.1"
      claimed_by_plans: ["77.1-01-PLAN.md", "77.1-02-PLAN.md", "77.1-03-PLAN.md", "77.1-04-PLAN.md", "77.1-05-PLAN.md", "77.1-06-PLAN.md", "77.1-07-PLAN.md", "77.1-08-PLAN.md"]
      completed_by_plans: []
      verification_status: "human_needed"
      evidence: "77.1-VERIFICATION.md at 2ccb3e8: 42/42 automated truths (40 direct, 2 overrides for the slow_bootstrap marker and opt-in case removed under the hermetic-tests rule) and 5/5 success criteria. Live Grok Bot / Cursor import (77.1-UAT rows 1-6) is UNPROVEN and deferred by the user until after merge (STATE.md Deferred Verification). Every 77.1 SUMMARY records requirements-completed: [] on purpose."
  integration: []
  flows:
    - name: "User E2E: push to GitHub, Grok Bot import with the SETUP.md prompt, bootstrap, stdio MCP host, /omega-* commands"
      status: "deferred"
      broken_at: "live Grok Bot and Cursor steps (77.1-UAT rows 1-6)"
      evidence: "Every locally verifiable link is connected: mcp.json declared command, launcher bootstrap and exit classes 10-13, plugin --check/--verify, the fresh-clone stdio proof answering /omega-help (46 commands). No live Bot step was run."
tech_debt:
  - phase: milestone (cross-phase)
    items:
      - "GitBook: .gitbook.yaml roots the space at ./docs/ while 34 docs links (21 in docs/marketing.md, including the social-card images) use ../ targets outside that root. They resolve on GitHub and in test_docs; GitBook rendering is an unexercised inference. Switching to absolute GitHub URLs (as docs/greptile.md does) would drop those targets out of the local link check."
      - "Hosted CI has not run the branch delta c26305f..2ccb3e8 yet; the PR runs it."
  - phase: 75-integrated-local-readiness
    items:
      - "Review IN-02..IN-06 skipped (info severity); IN-07 skipped by design (the generated catalog is built once per host process)."
      - "Readiness exercises the curated slash commands but not the generated /omega-<skill>/<routine> commands; pytest (test_command_generation, test_grokbot_plugin_stdio) covers those."
  - phase: 76-clean-ci-cd
    items:
      - "WR-01 deferred: a same-day KB refresh rerun fails loudly (user-accepted boundary, Phase 77 UAT test 8 item 7)."
      - "WR-04 skipped: local full-suite proof is Python 3.12; 3.13 and 3.14 rely on the hosted CI matrices."
      - "IN-01..IN-03 skipped (info severity)."
  - phase: 77-maintained-docs-and-knowledge-base
    items:
      - "IN-02 deferred: a same-day date-named KB refresh branch collision fails loudly; no force-checkout, branch replacement or retry."
  - phase: 77.1-grok-bot-import-packaging
    items:
      - "AR-01 accepted: requirements-lock.txt pins versions with == but no hashes, so --require-hashes is not enforced."
      - "Review IN-01, IN-02, IN-03, IN-06 skipped (info severity)."
      - "77.1-06 and 77.1-08 SUMMARY frontmatter still cite the slow_bootstrap marker and opt-in case removed in 27c86a6; the verification overrides record the removal."
      - "The launcher accepts Python 3.11 while the CI matrices cover 3.12-3.14 (documented; ledger row UNPROVEN)."
  - phase: 78-repository-marketing
    items:
      - "IN-02 skipped: the SVG external-reference checks are raw substring matches, which can only fail safe."
      - "The contrast guard places each text run by its anchor point, not its glyph extents, and models translate and scale transforms only; any other transform fails by name."
nyquist:
  compliant_phases: ["75", "76", "77", "77.1", "78"]
  partial_phases: []
  not_validated_phases: []
  missing_phases: []
  overall: compliant
---

# v15 milestone audit: gaps found (one user-deferred live UAT)

**Scope.** One repeatable, isolated local dry run over the complete roster (75); supported CI gates with retained integration evidence and a validated release boundary (76); source-verified canonical docs beside one refreshable historical Greptile mirror (77); a pushed repository the default Grok Bot can set up with the Python core attached and reachable through generated `/omega-*` commands (77.1); and repository collateral based only on verified capabilities (78).

**Result.** 21 of 22 requirements are satisfied by all three sources (VERIFICATION passed, SUMMARY `requirements-completed`, REQUIREMENTS `[x]`). GROK-03 is partial: its local proof is verified, and its live Grok Bot / Cursor import rows are user UAT that the user chose to run after the merge. That is the only gap; it is a deferral, not a defect. The integration check found one cross-phase blocker and six warnings; the blocker and five warnings were fixed on this branch (2ccb3e8, 305de61) and every phase was then re-verified at 2ccb3e8.

## Phase verification

All reports were regenerated by gsd-verifier at 2ccb3e8 and read through `verification.status`.

| Phase | Status | Score | Security | Nyquist | Review ledger |
|---|---|---|---|---|---|
| 75 Integrated local readiness | passed | 46/46 | threats_open 0 | validated, compliant | 0 open of 17 |
| 76 Clean CI/CD | passed | 28/28 | threats_open 0 | validated, compliant | 0 open of 8 |
| 77 Maintained docs and knowledge base | passed | 34/34 | threats_open 0 | validated, compliant | 0 open of 6 |
| 77.1 Grok Bot import packaging | human_needed | 42/42 automated (2 overrides) | threats_open 0 | validated, compliant | 0 open of 12 |
| 78 Repository marketing | passed | 8/8 | 6/6 closed | validated, compliant | 0 open of 11 |

## Requirement-by-requirement

| Requirement | Phase | VERIFICATION | SUMMARY | REQUIREMENTS | Final |
|---|---|---|---|---|---|
| LOCAL-01 | 75 | passed | 75-03, 75-04, 75-08 | [x] | satisfied |
| LOCAL-02 | 75 | passed | 75-04, 75-05, 75-07, 75-08, 75-10 | [x] | satisfied |
| LOCAL-03 | 75 | passed | 75-05, 75-10, 75-11 | [x] | satisfied |
| FLOW-01 | 75 | passed | 75-01, 75-06, 75-08, 75-09 | [x] | satisfied |
| FLOW-02 | 75 | passed | 75-01, 75-06, 75-07, 75-08, 75-09 | [x] | satisfied |
| GROK-01 | 75 | passed | 75-02, 75-03, 75-04, 75-08 | [x] | satisfied |
| GROK-02 | 75 | passed | 75-02, 75-04, 75-08 | [x] | satisfied |
| CI-01 | 76 | passed | 76-01..05 | [x] | satisfied |
| CI-02 | 76 | passed | 76-01..05 | [x] | satisfied |
| CI-03 | 76 | passed | 76-01..05 | [x] | satisfied |
| KB-01 | 77 | passed | 77-02 | [x] | satisfied |
| KB-02 | 77 | passed | 77-01, 77-02 | [x] | satisfied |
| KB-03 | 77 | passed | 77-02, 77-06 | [x] | satisfied |
| DOC-01 | 77 | passed | 77-03, 77-04, 77-05, 77-06 | [x] | satisfied |
| DOC-02 | 77 | passed | 77-03, 77-05, 77-06 | [x] | satisfied |
| DOC-03 | 77 | passed | 77-03, 77-04, 77-06 | [x] | satisfied |
| DOC-04 | 77 | passed | 77-04, 77-05, 77-06 | [x] | satisfied |
| DOC-05 | 77 | passed | 77-06, 77-07 | [x] | satisfied |
| GROK-03 | 77.1 | human_needed | none (deliberate) | [ ] | **partial** |
| MKT-01 | 78 | passed | 78-01 | [x] | satisfied |
| MKT-02 | 78 | passed | 78-01 | [x] | satisfied |
| MKT-03 | 78 | passed | 78-02 | [x] | satisfied |

No orphaned requirement: every traceability row appears in a phase VERIFICATION report.

## Integration (gsd-integration-checker at 08cf459, fixes at 2ccb3e8)

| Flow | Status | Requirements |
|---|---|---|
| 1. Readiness is what CI runs and retains and what the docs describe | connected (B-1 fixed) | LOCAL-01, LOCAL-02, FLOW-02, CI-01, CI-02, DOC-01, DOC-04 |
| 2. Generated `/omega-*` catalog and manifests agree with the tool catalog, roster, docs and marketing | connected | GROK-02, GROK-03, DOC-05, MKT-01, MKT-02 |
| 3. Every docs page listed once; marketing claims match canonical docs and the import evidence ledger | connected | DOC-05, KB-03, MKT-01..03, GROK-03 |
| 4. CI docs job (base install) covers docs/asset tests, catalog and plugin currency | connected | CI-01, DOC-05, GROK-03, MKT-03 |
| 5. User E2E: push, Bot import, bootstrap, stdio host, `/omega-*` | local links connected; live links deferred | GROK-01..03, DOC-04 |

23 cross-phase links were wired at 08cf459 and one was broken; with B-1 fixed, 24 of 24 are wired. No orphaned export.

Fixed during the audit:

- **B-1** (blocker): `docs/local-validation.md` gave the pre-43f69ae include-prime counts (82 exercised, 72 blocked); it now states 77 / 77 / 11, matching `readiness --include-prime`.
- **W-1**: the marketing page said the Cursor route's ledger rows are PARTIAL or UNPROVEN; they range from CONFIRMED (documented, not exercised) to UNPROVEN.
- **W-2**: the setup-prompt fidelity test now covers `docs/marketing.md` and the Bot template.
- **W-4**: the Bot template's first run read two clone-root-relative paths that do not exist at `/workspace/omega-prime`; it now names the `omega_prime/` paths.
- **W-5**: `docs/setup.md` called the `--verify` loopback host an HTTPS host; it is a loopback bearer host at `/mcp`.
- **W-6**: `docs/setup.md` now documents the CI `docs` job.
- **W-7** (planning): the REQUIREMENTS coverage line no longer says "none deferred".
- Not fixed: **W-3** (GitBook `../` links), listed under tech debt.

The Phase 78 follow-up review of the Nyquist tests (WR-02..WR-05, IN-04..IN-06) was fixed in 305de61.

## Open artifacts (`audit-open`)

- 77.1-UAT.md: 6 pending scenarios (the deferred live import rows).
- 77.1-VERIFICATION.md: human_needed (the same deferral).
- 48-UAT.md (archived v9): 4 pending scenarios, predating this milestone.
- 12 dormant seeds (SEED-001..004, 006..013); not reopened by this milestone.

## Receipts

- Regression gate at 2ccb3e8 (`/tmp/v15-regress-1791839602/summary.txt`): `ruff format --check`, `ruff check`, `mypy omega_prime/`, `python -m omega_prime.tooling.plugin --check`, `python -m omega_prime.tooling.catalog --check`, `bash omega_prime/scripts/assemble-prompts.sh --check`, `python -m omega_prime.setup_check --root .`, `python -m omega_prime.evals.runner omega_prime/evals/cases`, `python -m omega_prime.grokbot.readiness --root . --json` (ok; 165 roster, 117 offered, 50 exercised, 67 blocked, 48 feature-disabled, 0 failed), the same with `--include-prime` (ok; 154 offered, 77 exercised, 77 blocked, 11 feature-disabled, 0 failed) and `actionlint` all exited 0; `pytest omega_prime/tests` exited 0 with 2544 passed.
- Phase 78 demo in a fresh clone (`/tmp/v15-78-demo-1791836909/logs/`): all seven commands exited 0.

## Known limits

- No live Grok Bot, Cursor account, model or provider was attached. Live import is the user's 77.1 UAT, rows 1-6.
- No image publication, deployment, tag, marketplace listing or hosted HTTPS host.
