---
phase: 98-listing-package-and-prompts
verified: 2026-10-15T14:30:00Z
status: passed
score: 4/4 success criteria verified
covered_files: [".planning/phases/98-listing-package-and-prompts/98-02-PLAN.md", "LICENSE", "mcp.json", "omega_prime/grokbot/SETUP.md", "omega_prime/grokbot/listing/CHANGELOG.md", "omega_prime/grokbot/listing/LICENSE.md", "omega_prime/grokbot/listing/README.md", "omega_prime/grokbot/listing/prompts/follow-up/01-verify-install.md", "omega_prime/grokbot/listing/prompts/follow-up/02-help-tour.md", "omega_prime/grokbot/listing/prompts/follow-up/03-health-check.md", "omega_prime/grokbot/listing/prompts/follow-up/04-first-real-task-code-review.md", "omega_prime/grokbot/listing/prompts/follow-up/05-troubleshooting.md", "omega_prime/grokbot/listing/prompts/follow-up/INDEX.md", "omega_prime/grokbot/listing/prompts/install.md", "omega_prime/tests/test_grokbot_listing.py", "omega_prime/tests/test_grokbot_listing_assets.py"]
covered_digest: "v1:sha256:ebc793bdb5ba66fc32575a0ed106f1a3967ab71b235532112183f4f8f67a9807"
behavior_unverified: 0
overrides_applied: 0
gaps: []
deferred: []
---

# Phase 98: Listing package and prompts — Verification Report

**Phase Goal:** A Grok Bot operator can read the listing, paste the install prompt and run the follow-up prompts, and every claim shows its evidence level and source.
**Verified:** 2026-10-15T14:30:00Z
**Status:** passed
**Re-verification:** Yes — this replaces the earlier report in this directory, which recorded one gap (an uncited README sentence on line 5). Commit `030cdd1` fixed that sentence; this report re-checks every success criterion with fresh evidence.

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
| --- | --- | --- | --- |
| SC1 | `listing/README.md` links to the install prompt and the follow-up prompts rather than duplicating them, and each claim cites its source file and evidence level | ✓ VERIFIED | `grep` finds `](prompts/install.md)` (line 13) and `](prompts/follow-up/INDEX.md)` (line 17); `setup-omega-prime` and `<task` absent from README. Table header `\| Claim \| Evidence level \| Source \|` exact match; 12 data rows, each second cell in the allowed set (`local proof`, `optional capability`, `UNPROVEN`, `PARTIAL`) and each third cell containing a relative markdown link (checked by script). Line 5 now reads "The setup path is documented in [SETUP.md](../SETUP.md). Live Grok Bot import is UNPROVEN…" — the claim is cited inline (SETUP.md link) and labelled UNPROVEN with the UAT link; no uncited claim remains. Fix confirmed in `git show 030cdd1`. Tests `test_readme_claims_table_cites_levels_and_sources` + `test_listing_relative_links_and_anchors_resolve` pass. |
| SC2 | `prompts/install.md` matches the SETUP.md section 1 task block verbatim, and its stdio command equals the one declared in `mcp.json`; drift fails a test | ✓ VERIFIED | Python extraction: SETUP.md slice from `<task name="setup-omega-prime" version="1">` through first `</task>` is byte-identical to install.md's only ` ```text ` fence body (1014 bytes each, `IDENTICAL`). `mcp.json` declares `bash ${CURSOR_PLUGIN_ROOT}/omega_prime/scripts/grokbot-1click.sh --bootstrap --transport stdio`; with the placeholder replaced by `/workspace/omega-prime` it equals the `running exactly:` line in install.md (line 15). Tests `test_install_prompt_is_verbatim_setup_section_one` and `test_install_prompt_stdio_command_matches_mcp_json` pass. |
| SC3 | Each of the five follow-up prompts names the exact `/omega-*` command it sends and states what it does not prove; unknown commands or index gaps fail a test | ✓ VERIFIED | `follow-up/` holds `01-verify-install.md`, `02-help-tour.md`, `03-health-check.md`, `04-first-real-task-code-review.md`, `05-troubleshooting.md` + `INDEX.md`. Mapping: 01 → `/omega-roster`, 02 → `/omega-help`, 03 → `/omega-doctor`, 04 → `/omega-code-review`, 05 → `/omega-doctor`; all four command files exist in `plugin/commands/`. Each prompt has a `## What this does not prove` section and each carries UNPROVEN (2 hits per file). INDEX.md table lists all five exactly once with matching Sends column. Tests `test_every_omega_command_name_has_a_command_file`, `test_each_follow_up_prompt_sends_one_command_and_states_what_it_does_not_prove`, `test_follow_up_index_lists_each_prompt_exactly_once`, `test_follow_up_commands_match_pinned_mapping` pass. |
| SC4 | The listing `CHANGELOG.md` has a v20 entry, and `LICENSE.md` names AGPL-3.0-only and points to the unchanged root `LICENSE` | ✓ VERIFIED | `grep -c '^## \['` = 1, the line is `## [v20] - Unreleased`; root-history link `](../../../CHANGELOG.md)` present; Keep a Changelog 1.1.0 attribution present. `LICENSE.md` contains `AGPL-3.0-only`, `Copyright (C) 2026 Spectrum Web Co`, `](../../../LICENSE)` links, and `UNPROVEN`. `sha256sum LICENSE` = `6c944d7a32f9355777b7f4c70243a01bf2ea55c429a69599f53c4595d6b4b51a`, equal to `git show HEAD:LICENSE \| sha256sum`; `git status --porcelain -- LICENSE README.md CHANGELOG.md` prints nothing. Tests `test_root_license_is_pinned_and_matches_git_head`, `test_license_notice_names_license_holder_and_root_file`, `test_changelog_has_one_v20_entry_and_links_root_history` pass. |

**Score:** 4/4 truths verified (0 present, behavior-unverified)

### Required Artifacts

| Artifact | Expected | Status | Details |
| -------- | -------- | ------ | ------- |
| `omega_prime/grokbot/listing/README.md` | Listing page, prompt links, 12-row sourced claims table | ✓ VERIFIED | Links, header, 12 rows with allowed levels + source links; no install block; prose claims cited |
| `omega_prime/grokbot/listing/prompts/install.md` | Verbatim install prompt, one text fence | ✓ VERIFIED | Fence body byte-identical to SETUP.md section 1; stdio line matches mcp.json |
| `omega_prime/grokbot/listing/prompts/follow-up/01..05` | Five prompts, one command each, "does not prove" section | ✓ VERIFIED | Each sends its pinned command; each has the heading + UNPROVEN |
| `omega_prime/grokbot/listing/prompts/follow-up/INDEX.md` | Index listing each prompt exactly once | ✓ VERIFIED | Five-row table, Sends matches pinned mapping, no duplicate links |
| `omega_prime/grokbot/listing/CHANGELOG.md` | Single v20 entry, root-history link | ✓ VERIFIED | Exactly one `## [` heading; Keep a Changelog URL present |
| `omega_prime/grokbot/listing/LICENSE.md` | Notice naming AGPL-3.0-only, holder, root LICENSE | ✓ VERIFIED | All literals present; no license body text |
| `omega_prime/tests/test_grokbot_listing.py` | Drift/command/index/licence/claim/link tests | ✓ VERIFIED | Collects and passes; pins root LICENSE sha256 |
| `omega_prime/tests/test_grokbot_listing_assets.py` | Asset tests | ✓ VERIFIED | Passes alongside the listing tests |

### Key Link Verification

| From | To | Via | Status | Details |
| ---- | --- | --- | ------ | ------- |
| `listing/README.md` | `prompts/install.md`, `prompts/follow-up/INDEX.md` | relative markdown links | ✓ WIRED | Both targets exist and resolve |
| `prompts/install.md` | `omega_prime/grokbot/SETUP.md` section 1 | byte-identical fence (test-enforced) | ✓ WIRED | 1014 bytes identical; drift fails test |
| `prompts/install.md` | `mcp.json` stdio declaration | `running exactly:` line (test-enforced) | ✓ WIRED | Placeholder-substituted command matches |
| follow-up prompts | `plugin/commands/omega-*.md` | command name → file (test-enforced) | ✓ WIRED | All four files exist |
| `follow-up/INDEX.md` | five prompt files | bare-name links, exactly once each | ✓ WIRED | Counter check passes |
| `listing/LICENSE.md` | root `LICENSE` | `../../../LICENSE` links | ✓ WIRED | Target resolves to repo root |
| `listing/CHANGELOG.md` | root `CHANGELOG.md` | `../../../CHANGELOG.md` link | ✓ WIRED | History not duplicated |

### Data-Flow Trace (Level 4)

Not applicable — no rendered dynamic data; all values are static documentation text verified by byte comparison and link resolution above.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
| -------- | ------- | ------ | ------ |
| Listing + asset tests pass | `.venv/bin/python -m pytest omega_prime/tests/test_grokbot_listing.py omega_prime/tests/test_grokbot_listing_assets.py -q` | 27 passed in 2.29s | ✓ PASS |
| Install fence identical to SETUP section 1 | Python extraction + comparison | IDENTICAL, 1014 bytes each | ✓ PASS |
| stdio command matches mcp.json | Python placeholder substitution + `grep running exactly` | exact match | ✓ PASS |
| Root LICENSE pinned and equal to HEAD | `sha256sum LICENSE` vs `git show HEAD:LICENSE \| sha256sum` | `6c944d7a…4b51a` both | ✓ PASS |
| Root files untouched | `git status --porcelain -- LICENSE README.md CHANGELOG.md` | empty | ✓ PASS |

### Probe Execution

No phase-declared or conventional probes for this phase. Skipped.

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
| ----------- | ---------- | ----------- | ------ | -------- |
| LST-01 | 98-01 | Listing page, install/follow-up links, sourced claims | ✓ SATISFIED | SC1 |
| LST-02 | 98-01 | Changelog follows Keep a Changelog, has v20, points to root history | ✓ SATISFIED | SC4; fix commit replaced non-standard `Not changed`/`Evidence` headings with prose |
| LST-03 | 98-01 | LICENSE.md names AGPL-3.0-only and holder, points to root LICENSE | ✓ SATISFIED | SC4 |
| INS-01 | 98-02 | install.md verbatim from SETUP section 1; stdio matches mcp.json; drift test | ✓ SATISFIED | SC2 |
| INS-02 | 98-02 | Five follow-up prompts, each naming its command and what it does not prove | ✓ SATISFIED | SC3 |
| INS-03 | 98-02 | Test fails on unknown command or missing index entry | ✓ SATISFIED | SC3; enforced by `test_every_omega_command_name_has_a_command_file` and `test_follow_up_index_lists_each_prompt_exactly_once` |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
| ---- | ---- | ------- | -------- | ------ |
| — | — | None found | — | `screenshot` grep hits (3) are all negations ("not screenshots", "A live-session screenshot" under "Not claimed here"); `connected Bot` / `published listing` / `marketplace` patterns have no positive-claim hits |

### Human Verification Required

None. Live Grok Bot import remains UNPROVEN by design and is tracked in `docs/grok-bot-native.md#user-uat`, as the phase requires.

### Gaps Summary

No gaps. The single gap from the prior report (uncited README line 5 sentence) was fixed by commit `030cdd1`, which reworded the sentence to cite `[SETUP.md](../SETUP.md)` inline and label live import UNPROVEN with the UAT link. Fresh grep/script evidence above confirms no uncited prose claim remains: every other descriptive paragraph points at its claims-table row (rows 9, 10) or links its source file directly.

---

_Verified: 2026-10-15T14:30:00Z_
_Verifier: ChosenPython (gsd-verifier)_
