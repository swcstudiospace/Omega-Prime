---
phase: 98-listing-package-and-prompts
verified: 2026-10-15T13:57:02Z
status: passed
score: 4/4 success criteria verified
covered_files: [".planning/phases/98-listing-package-and-prompts/98-01-PLAN.md", ".planning/phases/98-listing-package-and-prompts/98-02-PLAN.md", "LICENSE", "mcp.json", "omega_prime/grokbot/SETUP.md", "omega_prime/grokbot/listing/CHANGELOG.md", "omega_prime/grokbot/listing/LICENSE.md", "omega_prime/grokbot/listing/README.md", "omega_prime/grokbot/listing/prompts/follow-up/01-verify-install.md", "omega_prime/grokbot/listing/prompts/follow-up/02-help-tour.md", "omega_prime/grokbot/listing/prompts/follow-up/03-health-check.md", "omega_prime/grokbot/listing/prompts/follow-up/04-first-real-task-code-review.md", "omega_prime/grokbot/listing/prompts/follow-up/05-troubleshooting.md", "omega_prime/grokbot/listing/prompts/follow-up/INDEX.md", "omega_prime/grokbot/listing/prompts/install.md", "omega_prime/tests/test_grokbot_listing.py", "omega_prime/tests/test_grokbot_listing_assets.py"]
covered_digest: "v1:sha256:f4e68a9e014b657d9866e89ddd9a28b1b72120e474c6800637d5ee08eb02aee5"
behavior_unverified: 0
overrides_applied: 0
gaps: []
deferred: []
---

# Phase 98: Listing package and prompts — Verification Report

**Phase Goal:** A Grok Bot operator can read the listing, paste the install prompt and run the follow-up prompts, and every claim shows its evidence level and source.
**Verified:** 2026-10-15T13:57:02Z
**Status:** passed
**Re-verification:** Yes — no code changes since the prior report (tree at `31d975d`, `git status --porcelain` clean for the listing, tests, and root files). All four ROADMAP success criteria re-checked with fresh evidence below.

**Digest scheme:** `covered_digest` is `v1:sha256:` + sha256 over the newline-joined, path-sorted `sha256sum`-style lines (`<hex>  <path>`) of the 17 covered files. (The prior report's digest used an undocumented scheme that no longer reproduces — concat-bytes, concat-hex, sha256sum-lines, and git-blob variants were all tried against the current tree and none match `ebc793bd…`. The scheme is now recorded here so future re-verifications reproduce it. `98-01-PLAN.md` was added to the covered set since this verification is scoped against both plans.)

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
| --- | --- | --- | --- |
| SC1 | `listing/README.md` links to the install prompt and the follow-up prompts rather than duplicating them, and each claim cites its source file and evidence level | ✓ VERIFIED | `](prompts/install.md)` ×1 (line 13), `](prompts/follow-up/INDEX.md)` ×2 (line 17 + claims row 12); `setup-omega-prime` ×0, `<task` ×0. Exact header `\| Claim \| Evidence level \| Source \|` present; 14 `^\\| ` lines = header + separator + 12 data rows. Line 5 cites `[SETUP.md](../SETUP.md)` inline and labels live import UNPROVEN with the UAT link — the prior gap stays closed. Tests `test_readme_claims_table_cites_levels_and_sources`, `test_listing_relative_links_and_anchors_resolve` pass. |
| SC2 | `prompts/install.md` matches the SETUP.md section 1 task block verbatim, and its stdio command equals the one declared in `mcp.json`; drift fails a test | ✓ VERIFIED | SETUP.md slice (`<task name="setup-omega-prime" version="1">` through first `</task>`) byte-identical to install.md's only ` ```text ` fence body (1014 bytes each, `IDENTICAL`). `mcp.json` declares `bash ${CURSOR_PLUGIN_ROOT}/omega_prime/scripts/grokbot-1click.sh --bootstrap --transport stdio`; placeholder-substituted it equals install.md's `running exactly:` line. Tests `test_install_prompt_is_verbatim_setup_section_one`, `test_install_prompt_stdio_command_matches_mcp_json` pass. |
| SC3 | Each of the five follow-up prompts names the exact `/omega-*` command it sends and states what it does not prove; unknown commands or index gaps fail a test | ✓ VERIFIED | `follow-up/` holds `01-verify-install.md` (`/omega-roster`), `02-help-tour.md` (`/omega-help`), `03-health-check.md` (`/omega-roster`), `04-first-real-task-code-review.md` (`/omega-code-review`), `05-troubleshooting.md` (`/omega-doctor`) + `INDEX.md`. All four command files exist in `plugin/commands/`; every prompt has `## What this does not prove`; INDEX.md lists all five exactly once. Tests `test_every_omega_command_name_has_a_command_file`, `test_each_follow_up_prompt_sends_one_command_and_states_what_it_does_not_prove`, `test_follow_up_index_lists_each_prompt_exactly_once`, `test_follow_up_commands_match_pinned_mapping` pass. |
| SC4 | The listing `CHANGELOG.md` has a v20 entry, and `LICENSE.md` names AGPL-3.0-only and points to the unchanged root `LICENSE` | ✓ VERIFIED | `grep -c '^## \['` = 1, the line is `## [v20] - Unreleased`; root-history link `](../../../CHANGELOG.md)` present. `LICENSE.md` contains `AGPL-3.0-only`, `Copyright (C) 2026 Spectrum Web Co`, `](../../../LICENSE)`. `sha256sum LICENSE` = `git show HEAD:LICENSE \| sha256sum` = `6c944d7a…4b51a` (pinned value holds). Tests `test_root_license_is_pinned_and_matches_git_head`, `test_license_notice_names_license_holder_and_root_file`, `test_changelog_has_one_v20_entry_and_links_root_history` pass. |

**Score:** 4/4 truths verified (0 present, behavior-unverified)

### Required Artifacts

| Artifact | Expected | Status | Details |
| -------- | -------- | ------ | ------- |
| `omega_prime/grokbot/listing/README.md` | Listing page, prompt links, 12-row sourced claims table | ✓ VERIFIED | Links, header, 12 rows; no install block; line 5 cited |
| `omega_prime/grokbot/listing/prompts/install.md` | Verbatim install prompt, one text fence | ✓ VERIFIED | Fence body byte-identical to SETUP.md section 1; stdio line matches mcp.json |
| `omega_prime/grokbot/listing/prompts/follow-up/01,02,03-health-check,04,05` | Five prompts, pinned commands, "does not prove" sections | ✓ VERIFIED | Pinned mapping holds; each has the heading + UNPROVEN |
| `omega_prime/grokbot/listing/prompts/follow-up/INDEX.md` | Index listing each prompt exactly once | ✓ VERIFIED | Five-row table, Sends matches pinned mapping |
| `omega_prime/grokbot/listing/CHANGELOG.md` | Single v20 entry, root-history link | ✓ VERIFIED | Exactly one `## [` heading |
| `omega_prime/grokbot/listing/LICENSE.md` | Notice naming AGPL-3.0-only, holder, root LICENSE | ✓ VERIFIED | All literals present; no license body text |
| `omega_prime/tests/test_grokbot_listing.py` | Drift/command/index/licence/claim/link tests | ✓ VERIFIED | Passes; pins root LICENSE sha256 |
| `omega_prime/tests/test_grokbot_listing_assets.py` | Asset tests | ✓ VERIFIED | Passes alongside the listing tests |

### Key Link Verification

| From | To | Via | Status | Details |
| ---- | --- | --- | ------ | ------- |
| `listing/README.md` | `prompts/install.md`, `prompts/follow-up/INDEX.md` | relative markdown links | ✓ WIRED | Both targets exist and resolve (link test passes) |
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
| Listing + asset tests pass | `python3 -m pytest omega_prime/tests/test_grokbot_listing.py omega_prime/tests/test_grokbot_listing_assets.py -q` | 27 passed in 0.44s | ✓ PASS |
| Install fence identical to SETUP section 1 | Python extraction + comparison | IDENTICAL, 1014 bytes each | ✓ PASS |
| stdio command matches mcp.json | Placeholder substitution + `grep running exactly` | `bash /workspace/omega-prime/omega_prime/scripts/grokbot-1click.sh --bootstrap --transport stdio`, exact match | ✓ PASS |
| Root LICENSE pinned and equal to HEAD | `sha256sum LICENSE` vs `git show HEAD:LICENSE \| sha256sum` | `6c944d7a…4b51a` both | ✓ PASS |
| Tree clean | `git status --porcelain -- <listing, tests, LICENSE>` | empty | ✓ PASS |

### Probe Execution

No phase-declared or conventional probes for this phase. Skipped.

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
| ----------- | ---------- | ----------- | ------ | -------- |
| LST-01 | 98-01 | Listing page, install/follow-up links, sourced claims | ✓ SATISFIED | SC1 |
| LST-02 | 98-01 | Changelog follows Keep a Changelog, has v20, points to root history | ✓ SATISFIED | SC4 |
| LST-03 | 98-01 | LICENSE.md names AGPL-3.0-only and holder, points to root LICENSE | ✓ SATISFIED | SC4 |
| INS-01 | 98-02 | install.md verbatim from SETUP section 1; stdio matches mcp.json; drift test | ✓ SATISFIED | SC2 |
| INS-02 | 98-02 | Five follow-up prompts, each naming its command and what it does not prove | ✓ SATISFIED | SC3 |
| INS-03 | 98-02 | Test fails on unknown command or missing index entry | ✓ SATISFIED | SC3; enforced by `test_every_omega_command_name_has_a_command_file` and `test_follow_up_index_lists_each_prompt_exactly_once` |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
| ---- | ---- | ------- | -------- | ------ |
| — | — | None found | — | Forbidden-claim grep (`connected Bot`, `published listing`, `marketplace listing`) returns 0 hits in all 10 listing markdown files; every file carries UNPROVEN (test-enforced) |

### Human Verification Required

None. Live Grok Bot import remains UNPROVEN by design and is tracked in `docs/grok-bot-native.md#user-uat`, as the phase requires.

### Gaps Summary

No gaps. All four success criteria hold on the current tree with no code changes since the prior report.

---

_Verified: 2026-10-15T13:57:02Z_
_Verifier: RulingHeron (gsd-verifier)_
