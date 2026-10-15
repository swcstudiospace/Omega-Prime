# Phase 98: Listing package and prompts - Context

**Gathered:** 2026-10-15
**Status:** Ready for planning
**Mode:** Auto-generated from requirements LST-01..03 and INS-01..03, ROADMAP Phase 98 success criteria, and repository evidence (discuss step run inline; the decisions below are the answers).

<domain>
## Phase Boundary

A Grok Bot operator can read the listing, paste the install prompt, and run the follow-up prompts. Every claim shows its evidence level and source. Requirements: LST-01, LST-02, LST-03, INS-01, INS-02, INS-03.

Out of scope here: root README and root CHANGELOG (Phase 99), the verification gate (Phase 100), any live Grok Bot claim, any screenshot, any marketplace listing.
</domain>

<decisions>
## Implementation Decisions

### Install prompt (INS-01)
- D-01: `omega_prime/grokbot/listing/prompts/install.md` contains the install task block verbatim from `omega_prime/grokbot/SETUP.md` section 1: the fenced `text` block that opens `<task name="setup-omega-prime" version="1">` and closes `</task>`. Source: SETUP.md section 1. A test fails on drift.
- D-02: The stdio command in that block equals the `mcp.json` `omega-prime` entry (`bash`, `omega_prime/scripts/grokbot-1click.sh`, `--bootstrap`, `--transport`, `stdio`), with `${CURSOR_PLUGIN_ROOT}` replaced by `/workspace/omega-prime` as SETUP.md step 3 states. A test fails on drift. Source: `mcp.json`, SETUP.md step 3.

### Follow-up prompts (INS-02, INS-03)
- D-03: `omega_prime/grokbot/listing/prompts/follow-up/` holds five files: verify the install, a `/omega-help` tour, a `/omega-doctor` health check, a first real task through `/omega-code-review`, and troubleshooting through the Bot's own diagnostics.
- D-04: Each follow-up prompt names exactly one `/omega-*` command, and that command must have a file in `omega_prime/grokbot/plugin/commands/`. Prefer a distinct command per prompt. The planner confirms the mapping against that directory; `omega-help`, `omega-doctor`, `omega-code-review`, `omega-commands`, `omega-onboard`, and `omega-connectors` all exist there now.
- D-05: Each follow-up prompt states what it does not prove, and labels its claim with an evidence level (Local proof, Optional capability, or Unproven external). Source for the levels: `docs/marketing.md` "Evidence levels".
- D-06: An index file lists the five prompts. A test fails when a prompt file is missing from the index, or when a prompt names a command with no file in `plugin/commands/` (INS-03).
- D-07: Every new prompt file wraps its body in the v17 XML envelope, `<task name="..." version="1">`, matching the install block. The `prompt_format --check` gate does not scan `grokbot/listing/`, so a test in this phase enforces the envelope for every listing prompt.

### Listing package (LST-01..03)
- D-08: `omega_prime/grokbot/listing/README.md` is the listing page. It links to the install prompt and the follow-up prompts, and does not duplicate their text. Each claim cites its source file and evidence level.
- D-09: `omega_prime/grokbot/listing/CHANGELOG.md` follows Keep a Changelog and has a `v20` entry for this kit. The root `CHANGELOG.md` is not touched in this phase.
- D-10: `omega_prime/grokbot/listing/LICENSE.md` is a licensing notice only. It names AGPL-3.0-only, the copyright holder "Spectrum Web Co" (from the source file headers), and points to the unchanged root `LICENSE`. It contains no new license text.

### Claim discipline
- D-11: No file in this phase claims a live Grok Bot connection, a published icon, a marketplace listing, a measured number, or a screenshot. Live import stays UNPROVEN until the user runs the 77.1 UAT rows.

### Existing assets reused (already committed on main by Phase 97)
- Icons: `omega_prime/grokbot/listing/icons/`. Banner: `omega_prime/grokbot/listing/banner.svg` and `banner.png`. The README may reference them but must not redraw them.

## Canonical references
- `omega_prime/grokbot/SETUP.md` section 1 (install block), `mcp.json` (stdio entry), `docs/marketing.md` (evidence levels), `omega_prime/grokbot/plugin/commands/` (command files), `.planning/milestones/v19-ROADMAP.md` is not relevant here.
- Existing test `test_setup_prompts_run_the_declared_stdio_command` must keep passing.
</decisions>

<code_context>
## Existing Code Insights
- `omega_prime/grokbot/listing/` already holds `icons/`, `banner.svg`, `banner.png` (Phase 97, committed).
- `omega_prime/tooling/prompt_format.py` scopes the XML gate to seat prompts, skills, routines, commands, and runtime builders. Listing prompts are outside it (see D-07).
- `omega_prime/tests/test_grokbot_listing_assets.py` is the existing listing test module; new listing tests belong there or in a sibling module that follows its style.
</code_context>

<specifics>
## Specific Ideas
- The install block must be copied byte-for-byte; the drift test compares the file contents with the SETUP.md block, not a paraphrase.
- Keep the follow-up prompts short. Each names one command, says what it checks, and says what it does not prove.
</specifics>

<deferred>
## Deferred Ideas
- Root README and CHANGELOG changes: Phase 99.
- Verification gate and full pytest run: Phase 100.
</deferred>
</content>
