# Phase 100 Plan 01: Verification gate — claim audit and minimal fixes (Tasks 1–2) — Summary

Scope executed: Task 1 (read-only claim-discipline audit) and Task 2 (smallest fixes for
QA-02-breaking findings only). Task 3 (gate run) is intentionally NOT executed here; the
orchestrator runs all gates once after these edits and records the exit codes. No gate
command was run, and nothing was staged or committed, per the assignment.

## Task 1 finding list (file and line per hit)

Scope scanned (read-only grep + full reads): `omega_prime/grokbot/listing/README.md`,
`listing/CHANGELOG.md`, `listing/LICENSE.md`, `listing/prompts/install.md`,
`listing/prompts/follow-up/INDEX.md`, `follow-up/01-verify-install.md`,
`02-help-tour.md`, `03-health-check.md`, `04-first-real-task-code-review.md`,
`05-troubleshooting.md`, root `README.md`, `docs/developer-guide.md`, root `CHANGELOG.md`
(v20 entry, lines 1–10), `docs/SUMMARY.md`.

### Breaking (fixed in Task 2)

- **F2 — uncited template clause.** `omega_prime/grokbot/listing/README.md:9`: "Omega Prime
  is one Python process with one tool registry, an MCP tool host and a Grok Bot template.
  The evidence for this description is claims-table row 9 below." Row 9 covered the
  process/registry/host but never mentioned the template, so the template clause had no
  canonical-file citation (criterion 3 / D-13(e)). Fixed by extending row 9 (line 37).
- **F3 — unproven-label casing.** `docs/developer-guide.md:90`: "Live import on a Grok Bot
  or Cursor account is unproven until you run the [user UAT] rows." The named unexecuted
  routes (live Grok Bot import, Cursor import) carried the label only in lowercase, while
  the v20 label convention is uppercase UNPROVEN (D-14). Fixed with a one-word casing
  change; meaning unchanged.

### Known Gap 1 — already fixed, no edit

- **F1 — 98 Gap 1.** `omega_prime/grokbot/listing/README.md:5` no longer contains the
  blocking sentence "This is a documented setup path that is verified locally in this
  repository". Current line 5 cites `[SETUP.md](../SETUP.md)`, labels live import
  UNPROVEN, and conditions any live claim on the user UAT rows. Compliant; left alone.

### Compliant / advisory (left alone, with reason)

- **F4 — INDEX conditional.** `listing/prompts/follow-up/INDEX.md:3`: "Five prompts to run
  after step 4 of the install prompt has completed." Conditional sequencing instruction,
  not an assertion that a live run succeeds (98 W2, advisory); line 13 labels live import
  UNPROVEN. "Five" is an in-repo-verifiable file count, not a measured result.
- **F5/F8/F9 — prompt purpose statements.** `01-verify-install.md:3`, `02-help-tour.md:3`,
  `04-first-real-task-code-review.md:3` describe what each prompt does when run,
  conditional on the install having finished; each file carries an evidence-level line
  citing its command file and an UNPROVEN label for live runs. Not live claims.
- **F6/F7 — doctor-output descriptions.** `03-health-check.md:3`,
  `05-troubleshooting.md:21,29` ("six checks" shape, `checks` object, doctor-record
  write) describe `/omega-doctor` behavior and cite the linked command file (98 W4,
  advisory: no test pins them, but citation + UNPROVEN labels are present).
- **F10 — install prompt.** `listing/prompts/install.md:25,27`: UNPROVEN label plus
  conditionals ("Whether the Bot adds the server itself… are user UAT rows 1 and 5").
  Install block and stdio line untouched per constraints.
- **F11/F12 — root README.** `README.md:5,13` describe what the install prompt asks the
  Bot to do (prompt content, verifiable in `install.md`), not an executed live result;
  `:19` names the Cursor secondary route and `:40` labels "Live Grok Bot and Cursor
  import is UNPROVEN" with an explicit negation ("Nothing here claims a connected Bot, a
  published listing, or a marketplace entry"). Negations are not claims.
- **F13 — root CHANGELOG v20 entry.** `CHANGELOG.md:5-7` name their subject files inline
  (self-citing change descriptions); `:8` carries the UNPROVEN label; `:10`
  ("curated 24 -> 27 and total 58 -> 61") states in-repo-verifiable inventory counts
  covered by the catalog gate, not measured live results.
- **F14 — listing CHANGELOG.** `listing/CHANGELOG.md:17` UNPROVEN + negations ("no live
  Bot run, no marketplace entry and no measured result"). Keep-a-Changelog heading style
  is 98 W1, advisory; untouched.
- **F15 — listing LICENSE notice.** `listing/LICENSE.md:13` UNPROVEN + "makes no claim
  about Grok Bot import". Compliant.
- **F16 — claims-table UNPROVEN/PARTIAL rows.** `listing/README.md:34-36` label every
  unexecuted route (custom Command server on the Bot computer, Bot-added server +
  install-prompt run, Cursor Import-from-Repo appearance/loading). Row 8's PARTIAL level
  is an allowed test level. The row-6-vs-SETUP.md level mismatch is 98 W3, advisory;
  untouched.
- **F17 — banner/icon references.** `listing/README.md:3,39,44` embed or name brand art
  with an explicit "not screenshots" disclaimer and a source citation (`banner.svg`,
  `icons/icon-512.png`; all files exist on disk). `assets/` row in developer-guide
  (`:267`) is a layout listing, not a published-icon claim.
- **F18/F19 — pointers and negations.** `listing/README.md:46-56` (licence/changes
  pointers, "Not claimed here" list). Not claims.
- **F20 — docs/SUMMARY.md.** Pure navigation links; no prose claims.
- **F21 — developer-guide diagnostics.** Version matrices (`3.12–3.14`), parity counts
  (`5089 passed, 2 failed`), `0.9.8` CLI notes, crate/skill/seat totals are moved
  pre-existing content with provenance (VENDOR.md, CI), not live-Bot measured results.
  `:224` ("A published CLI on `PATH` is the wrong oracle") concerns a third-party
  binary and asserts no self-publication. `:35` ("unproven live integrations") is a
  generic category phrase cited to `local-validation.md`, not a named route.
- **F22/F23 — developer-guide seat section.** `:88-94` (after F3 fix) name the routes,
  carry the UNPROVEN label, and disclaim seat-side attach operations ("a local smoke
  prompt or template check does not prove them"); `:114` ("preflight, not account
  attachment") is a negation.

No file in scope contains a positive live-connection claim, a published-icon or
marketplace-listing claim, a measured timing/percentage, or a screenshot claim. Every
named unexecuted route carries the UNPROVEN label after the F3 fix.

## Task 2 edits (exact files changed)

1. `omega_prime/grokbot/listing/README.md` (line 37, claims-table row 9): claim cell gains
   "with a Grok Bot template"; source cell gains "template
   [OMEGA_PRIME.md](../templates/OMEGA_PRIME.md)". One row, still 3 cells, level
   unchanged (`local proof`); the new relative link resolves to the existing
   `omega_prime/grokbot/templates/OMEGA_PRIME.md`. Row numbering (prose references to
   rows 9/10) unchanged.
2. `docs/developer-guide.md` (line 90): "is unproven" → "is UNPROVEN". One word.

Not touched: install block, stdio line, badge, root LICENSE, `omega_prime/` code,
ROADMAP.md, STATE.md, anything outside the listed v20 files.

## What remains

- Task 3 (orchestrator): run each D-02–D-12 gate command exactly once on the changed
  tree and record exit codes, including the post-fix claim re-sweep and the LICENSE
  sha256 pin check.
- No finding was left unresolvable. Both breaking findings (F2, F3) were fixed above;
  all other hits were classified compliant or advisory with reasons and intentionally
  not edited. 98 W1–W4 remain advisory per D-15.

## Gate results (orchestrator, final tree at 507e508)

| Gate | Command | Exit | Result |
|------|---------|------|--------|
| Lint | `.venv/bin/python -m ruff check omega_prime/` | 0 | All checks passed |
| Format | `.venv/bin/python -m ruff format --check omega_prime/` | 0 | 576 files already formatted |
| Types | `.venv/bin/python -m mypy omega_prime/` | 0 | Success, 443 source files |
| Evals | `.venv/bin/python -m omega_prime.evals.runner omega_prime/evals/cases` | 0 | 26 passed, 0 failed |
| Prompt assembly | `bash omega_prime/scripts/assemble-prompts.sh --check` | 0 | OMEGA_PRIME up to date |
| Catalog | `.venv/bin/python -m omega_prime.tooling.catalog --check` | 0 | up to date |
| Plugin | `.venv/bin/python -m omega_prime.tooling.plugin --check` | 0 | 65 generated files up to date |
| Docs and public repo | `pytest omega_prime/tests/test_docs.py omega_prime/tests/test_public_repo.py` | 0 | 31 passed |
| Provenance | `sidecar/provenance.py check`, `pack-diff`, `surface` | 0, 0, 0 | pin f6d2fc2 agrees; 25 files byte-identical; 14 tool names agree |
| Readiness | `.venv/bin/python -m omega_prime.grokbot.readiness --root . --json --output /tmp/r.json` | 0 | JSON report written |
| Full suite | `.venv/bin/python -m pytest omega_prime/tests -q -p no:cacheprovider` | 0 | 4729 passed, 3 skipped |

Claim-discipline audit: two breaking wording findings fixed in 507e508 (listing README claims-table row 9, developer-guide UNPROVEN label). All other findings were compliant and left unchanged.
