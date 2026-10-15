# Phase 100 Context — Verification gate

**Goal** (ROADMAP.md): The v20 kit passes the repository's own gates and makes no claim the evidence does not support.

**Depends on**: Phase 99 (root README links to the listing prompts; developer guide under `docs/`).

**Requirements**: QA-01, QA-02.

**Success criteria** (ROADMAP.md Phase 100, verbatim):

1. `ruff check`, `ruff format --check`, `mypy omega_prime/`, the full pytest suite, `omega_prime.tooling.catalog --check` and `omega_prime.tooling.plugin --check` each exit 0 on the changed tree
2. No new or changed file claims a live Grok Bot connection, a published icon, a marketplace listing, a measured number or a screenshot
3. Each factual claim in the new and changed docs cites a canonical file, and each route that has not been executed is labelled UNPROVEN

## Repo evidence (read 2026-10-15, branch v20-grok-bot-launch-kit)

- v20 new files (Phases 97–98): `omega_prime/grokbot/listing/README.md`, `omega_prime/grokbot/listing/CHANGELOG.md`, `omega_prime/grokbot/listing/LICENSE.md`, `omega_prime/grokbot/listing/prompts/install.md`, `omega_prime/grokbot/listing/prompts/follow-up/01-verify-install.md`, `02-help-tour.md`, `03-roster-health-check.md` (98-02 summary notes the name is misleading — it sends `/omega-doctor`; the 98 verification report records the user override mapping 01 `/omega-roster`, 02 `/omega-help`, 03 `/omega-doctor`, 04 `/omega-code-review`, 05 `/omega-doctor` triage, and names the file `03-health-check.md`), `04-first-real-task-code-review.md`, `05-troubleshooting.md`, `omega_prime/grokbot/listing/prompts/follow-up/INDEX.md`, `omega_prime/tests/test_grokbot_listing.py`, plus Phase 97 icons and banner (`omega_prime/grokbot/listing/icons/`, `banner.svg`, `banner.png`).
- v20 changed files (Phase 99): root `README.md` (rewritten Grok-Bot-first), `docs/developer-guide.md` (new, moved sections), root `CHANGELOG.md` (v20 block under Unreleased), `docs/SUMMARY.md` (one guide line), `docs/README.md`, `docs/migration.md`, `docs/driving-the-bot.md`, `docs/setup.md`, `docs/agent-loop.md`, `docs/marketing.md`, `CONTRIBUTING.md` (99-02 plan), `omega_prime/tests/test_docs.py` (two new tests).
- Known open items entering Phase 100 (source: `98-VERIFICATION.md`, status `gaps_found`, score 3/4):
  - **Gap 1 (SC1/LST-01, blocking):** `omega_prime/grokbot/listing/README.md` line 5, "This is a documented setup path that is verified locally in this repository", is an uncited claim with no claims-table row, source file, or evidence level. Fix: add a claims-table row (e.g. local proof citing `plugin.py verify_stdio` and `docs/local-validation.md`) or delete the sentence.
  - **W1 (advisory):** listing `CHANGELOG.md` uses `Not changed` and `Evidence` headings, not Keep a Changelog types.
  - **W2 (advisory):** `prompts/follow-up/INDEX.md` line 3, "Five prompts to run after step 4 of the install prompt works", is conditional but fails a literal "works must be absent or labelled UNPROVEN" grep. Suggested rewording: "Five prompts to run once step 4 of the install prompt has run."
  - **W3 (advisory):** `README.md` row 6 labels the stdio-server claim UNPROVEN while `SETUP.md` section 1 labels the same claim CONFIRMED; the two should agree (README is the conservative label).
  - **W4 (advisory):** prompts 03/05 describe `/omega-doctor` output (six checks, `checks` object, per-check `green` flag, roster doctor-record write, verified by hand against `omega_prime/tools/lead.py` lines ~956-1015) with no test pinning them.
- License pin (source: 98 summary, 98 verification, 99 CONTEXT D-09): root `LICENSE` sha256 `6c944d7a32f9355777b7f4c70243a01bf2ea55c429a69599f53c4595d6b4b51a`, equals `git show HEAD:LICENSE`.
- Live import stays UNPROVEN until the user runs the six 77.1 UAT rows (source: REQUIREMENTS defaults, 98 verification "Human Verification Required", 99 CONTEXT D-11).

## Decisions (locked)

- **D-01** — The gate set is the CI-equivalent command list, run exactly once after all fixes are done, with each command's exit code recorded as evidence. (Source: QA-01; Phase 100 criterion 1.)
- **D-02** — Gate command 1: `.venv/bin/python -m ruff check omega_prime/`. Must exit 0. (Source: QA-01; criterion 1.)
- **D-03** — Gate command 2: `.venv/bin/python -m ruff format --check omega_prime/`. Must exit 0. (Source: QA-01; criterion 1.)
- **D-04** — Gate command 3: `.venv/bin/python -m mypy omega_prime/`. Must exit 0. (Source: QA-01; criterion 1.)
- **D-05** — Gate command 4: `.venv/bin/python -m pytest omega_prime/tests -q -p no:cacheprovider`. Must exit 0. (Source: QA-01; criterion 1.)
- **D-06** — Gate command 5: `.venv/bin/python -m omega_prime.evals.runner omega_prime/evals/cases`. Must exit 0. (Source: QA-01 repo gate.)
- **D-07** — Gate command 6: `bash omega_prime/scripts/assemble-prompts.sh --check`. Must exit 0. (Source: QA-01 repo gate, v17 XML prompt standard.)
- **D-08** — Gate command 7: `.venv/bin/python -m omega_prime.tooling.catalog --check`. Must exit 0. (Source: QA-01; criterion 1.)
- **D-09** — Gate command 8: `.venv/bin/python -m omega_prime.tooling.plugin --check`. Must exit 0. (Source: QA-01; criterion 1.)
- **D-10** — The docs and public-repo tests are part of the gate set: `.venv/bin/python -m pytest omega_prime/tests/test_docs.py omega_prime/tests/test_public_repo.py -q -p no:cacheprovider` must exit 0, including `test_setup_prompts_run_the_declared_stdio_command`, `test_relative_links_images_and_anchors_resolve`, `test_summary_links_resolve_exactly_once`, and `test_readme_links_resolve`. (Source: QA-01; Phase 99 CONTEXT D-13; Phase 100 criterion 1 "full pytest suite" made explicit for the v20-touched docs.)
- **D-11** — The provenance check is part of the gate set: `.venv/bin/python sidecar/provenance.py check`, `.venv/bin/python sidecar/provenance.py pack-diff`, and `.venv/bin/python sidecar/provenance.py surface` must each exit 0. (Source: QA-01 repo gate.)
- **D-12** — The readiness JSON is part of the gate set: `.venv/bin/python -m omega_prime.grokbot.readiness --root . --json --output /tmp/r.json` must exit 0 and produce a JSON file at `/tmp/r.json`. (Source: QA-01 repo gate.)
- **D-13** — The claim-discipline audit scans every v20 new or changed file — `omega_prime/grokbot/listing/**`, root `README.md`, `docs/developer-guide.md`, root `CHANGELOG.md`, `docs/SUMMARY.md`, and the changed `docs/` pages (`docs/README.md`, `docs/migration.md`, `docs/driving-the-bot.md`, `docs/setup.md`, `docs/agent-loop.md`, `docs/marketing.md`) plus `CONTRIBUTING.md` and `omega_prime/tests/test_docs.py` where they carry prose claims — for: (a) live-Grok connection claims, (b) published-icon or marketplace-listing claims, (c) measured numbers (timings, counts, percentages), (d) screenshots, (e) unlabelled factual claims with no canonical-file citation and no evidence level. Each finding is listed with its file and line. (Source: QA-02; Phase 100 criteria 2 and 3.)
- **D-14** — Each route that has not been executed (live Grok Bot import, Cursor Teams/Enterprise import from repo, any marketplace or published-listing route) must carry the UNPROVEN label in every v20 file that names it. A file naming such a route without the UNPROVEN label is a QA-02 finding. (Source: QA-02; criterion 3; REQUIREMENTS defaults "Live Grok Bot import stays UNPROVEN"; 99 CONTEXT D-11.)
- **D-15** — Only findings that break a gate (exit code != 0) or a QA-02 rule (D-13/D-14) are fixed, with the smallest correct edit: add the missing citation/evidence-level row or UNPROVEN label, delete the uncited sentence, or reword to a negation/conditional that makes no claim. Advisory style notes (98 W1 headings, W3 level wording, W4 untested doctor quotes) are not edited unless they fail a gate or QA-02 rule. (Source: QA-02; 98-VERIFICATION.md Gaps Summary "Once Gap 1 is fixed, SC1 passes; W1–W4 advisory".)
- **D-16** — The known 98 Gap 1 sentence (`omega_prime/grokbot/listing/README.md` line 5, "This is a documented setup path that is verified locally in this repository") is fixed first: either a claims-table row with source file and evidence level, or deletion of the sentence. (Source: 98-VERIFICATION.md Gap 1; Phase 100 criterion 3.)
- **D-17** — No product behavior, license text, or license choice changes in this phase. Root `LICENSE` stays byte-for-byte unchanged (sha256 pinned in repo evidence). No marketplace publication, website, outreach, hosted deployment, published container image, new icon mark, or 77.1 live UAT run. (Source: REQUIREMENTS Out of scope; 99 CONTEXT D-09.)
- **D-18** — Gates are not re-run per fix. Audit first, fix second, run the full gate set once last. The plan records each gate command's exit code as evidence; any non-zero exit fails the phase. (Source: QA-01; assignment constraint.)

## Deferred Ideas

- Marketplace publication, website, outreach campaigns, hosted deployment, published container image (REQUIREMENTS out of scope).
- Running the 77.1 live UAT rows (user-owned).
- Screenshots of a live Bot session.
- Designing a new icon mark; changing the root `LICENSE` text or license choice.
- Renaming `03-roster-health-check.md` (98-02 summary follow-up note) unless the audit shows it breaks QA-02.

## Claude's Discretion

- Exact grep patterns for the claim sweep, provided they cover all five D-13 classes and report file and line for each hit.
- Whether a positive-looking hit is a negated/conditional non-claim (e.g. "not proof of a connected account", "once step 4 has run"), with a one-line justification per such hit in the summary.
- The v20 CHANGELOG heading-style fix (W1) is discretionary: fix only if it breaks a gate or QA-02 rule, otherwise leave and note.
