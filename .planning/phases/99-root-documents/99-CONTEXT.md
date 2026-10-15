# Phase 99 Context — Root documents

**Goal** (ROADMAP.md): A visitor to the repository root meets the Grok Bot
product first, and the developer guide survives intact under `docs/`.

**Depends on**: Phase 98 (root README links to the listing prompts).
**Requirements**: ROOT-01, ROOT-02, ROOT-03.

**Success criteria** (ROADMAP.md Phase 99, verbatim):

1. The root `README.md` opens Grok-Bot-first with the install prompt link,
   the follow-up link, the license badge, and the exact stdio line
   `bash /workspace/omega-prime/omega_prime/scripts/grokbot-1click.sh --bootstrap --transport stdio`,
   so `test_setup_prompts_run_the_declared_stdio_command` still passes
2. Every section of the old root README (Architecture, Quickstart,
   Configuration, Build, Layout, Contributing) is present in
   `docs/developer-guide.md` with working relative links, and no content is
   dropped
3. Root `CHANGELOG.md` has a v20 entry under Unreleased with existing history
   untouched, and the root `LICENSE` is byte-for-byte unchanged
4. `docs/SUMMARY.md` lists the developer guide exactly once, and every new
   page resolves through the existing link test

## Repo evidence (read 2026-10-15, branch v20-grok-bot-launch-kit)

- `README.md` is 317 lines. Markdown headings that are real sections
  (fence comments at lines 201/212/216 excluded): `# Omega Prime` (line 7),
  `## Architecture` (15), `## Quickstart` (46),
  `### Prepare an owned workspace for Grok Bot` (111),
  `## Configuration` (144), `## Build from source (both toolchains)` (192),
  `## Docs` (263), `## Layout` (280), `## Contributing` (307),
  `## License` (313).
- License badge, line 4, verbatim:
  `[![license](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)`.
- The on-Bot stdio line `bash /workspace/omega-prime/omega_prime/scripts/grokbot-1click.sh --bootstrap --transport stdio`
  is the `mcp.json` declared command
  (`bash ${CURSOR_PLUGIN_ROOT}/omega_prime/scripts/grokbot-1click.sh --bootstrap --transport stdio`)
  with `${CURSOR_PLUGIN_ROOT}` replaced by `/workspace/omega-prime`. It
  currently appears in `README.md` (line 96), `docs/marketing.md`,
  `omega_prime/grokbot/SETUP.md` and
  `omega_prime/grokbot/templates/OMEGA_PRIME.md`.
- `CHANGELOG.md` is 649 lines. It opens with a working preamble
  `## Unreleased` (line 1, one bullet about slash commands), then
  `# Changelog` with Keep-a-Changelog boilerplate, then `## [Unreleased]`
  (line 11), then history ending in `## [0.1.0] — 2026-10-03` (line 621).
- `LICENSE` (678 lines) opens `Copyright (C) 2026 Spectrum Web Co` under
  AGPL-3.0-only terms. `sha256sum LICENSE` =
  `6c944d7a32f9355777b7f4c70243a01bf2ea55c429a69599f53c4595d6b4b51a`
  (same value Phase 98 pinned; equals `git show HEAD:LICENSE`).
- Phase 98 is complete on this branch: both link targets exist —
  `omega_prime/grokbot/listing/prompts/install.md` and
  `omega_prime/grokbot/listing/prompts/follow-up/INDEX.md` (plus the five
  follow-up prompts `01-verify-install.md`, `02-help-tour.md`,
  `03-health-check.md`, `04-first-real-task-code-review.md`,
  `05-troubleshooting.md`).
- `docs/SUMMARY.md` (34 lines) lists every `docs/*.md` page exactly once;
  `test_summary_links_resolve_exactly_once` enforces set equality between
  SUMMARY links and `docs/*.md` files, so the new guide must be listed
  exactly once.
- Tests that pin this phase (all must stay green, see D-12/D-13):
  `omega_prime/tests/test_docs.py` —
  `test_setup_prompts_run_the_declared_stdio_command` (README must contain
  the on-Bot stdio substring; also checks SETUP.md, OMEGA_PRIME.md,
  marketing.md, grok-bot-native.md),
  `test_relative_links_images_and_anchors_resolve` (every relative
  link/image/anchor in root `*.md` plus `docs/*.md` must resolve, anchors
  included),
  `test_summary_links_resolve_exactly_once`,
  `test_every_docs_page_opens_with_a_top_level_heading` (every
  `docs/*.md` must open with `# `),
  `test_documented_shell_commands_name_runnable_modules_and_scripts`
  (moved shell fences must name real `omega_prime.*` modules/scripts);
  `omega_prime/tests/test_public_repo.py::test_readme_links_resolve`
  (every non-`://` README link target must exist);
  `omega_prime/tests/test_grokbot_listing.py::test_root_license_is_pinned_and_matches_git_head`
  (root LICENSE hash pin + HEAD equality).

## Decisions (locked)

- **D-01** — The new root `README.md` opens Grok-Bot-first: product hero
  first, then the install prompt link, then the follow-up link, then the
  stdio line and pointers. Developer content leaves the root.
  (Source: ROOT-01, roadmap criterion 1.)
- **D-02** — The README links the install prompt as
  `omega_prime/grokbot/listing/prompts/install.md` and the follow-ups as
  `omega_prime/grokbot/listing/prompts/follow-up/INDEX.md` (root-relative
  links, no `../`). It never duplicates prompt text.
  (Source: ROOT-01, criterion 1; repo evidence: both files exist.)
- **D-03** — The README contains the exact plain substring
  `bash /workspace/omega-prime/omega_prime/scripts/grokbot-1click.sh --bootstrap --transport stdio`.
  (Source: ROOT-01, criterion 1,
  `test_setup_prompts_run_the_declared_stdio_command`; repo evidence:
  `mcp.json` declared command.)
- **D-04** — The license badge line stays verbatim —
  `[![license](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)` —
  plus a short License pointer naming AGPL-3.0-only and the root `LICENSE`.
  (Source: ROOT-01, criterion 1; repo evidence: `README.md` line 4.)
- **D-05** — These six old sections move into `docs/developer-guide.md`
  with no content dropped: Architecture (from `## Architecture`),
  Quickstart (from `## Quickstart` including
  `### Prepare an owned workspace for Grok Bot`), Configuration, Build
  (from `## Build from source (both toolchains)`), Layout, Contributing.
  The old intro (lines 7–13), `## Docs` and `## License` are superseded:
  the hero replaces the intro, a short Docs pointer block
  (`docs/README.md`, the developer guide, marketing) replaces `## Docs`,
  and D-04 replaces `## License`. Nothing else informative is deleted.
  (Source: ROOT-01, criterion 2.)
- **D-06** — Every relative link carried into `docs/developer-guide.md` is
  re-rooted one level down: `docs/X.md` → `X.md`, and root siblings gain
  `../` (`VENDOR.md` → `../VENDOR.md`, `CONTRIBUTING.md`,
  `SECURITY.md`, `CODE_OF_CONDUCT.md`, `LICENSE`, `assets/banner.svg`,
  `omega_prime/...`, `kb/...`, `mcp.json`, `.cursor-plugin/...`,
  `omega-prime.json`, `prime-agent/`, `redteam/`, `native/`,
  `prompts-assembled/...`, `contracts/...`). Anchors (`#user-uat` style)
  keep working after the move.
  (Source: ROOT-01, criterion 2,
  `test_relative_links_images_and_anchors_resolve`.)
- **D-07** — `docs/developer-guide.md` opens with a `# ` top-level
  heading, and every moved shell fence keeps naming only runnable
  `omega_prime.*` modules and existing `omega_prime/*.sh` scripts.
  (Source: `test_every_docs_page_opens_with_a_top_level_heading`,
  `test_documented_shell_commands_name_runnable_modules_and_scripts`.)
- **D-08** — Root `CHANGELOG.md` gets a v20 entry under the top
  `## Unreleased` preamble (line 1), above the `# Changelog` line. The
  entry contains the literal `v20`, describes the Grok-Bot-first README,
  the developer-guide move, and the docs index update. Every pre-existing
  line stays byte-identical; the diff adds lines only (no `-` lines).
  (Source: ROOT-02, criterion 3.)
- **D-09** — The root `LICENSE` is byte-for-byte unchanged:
  sha256 `6c944d7a32f9355777b7f4c70243a01bf2ea55c429a69599f53c4595d6b4b51a`,
  equals `git show HEAD:LICENSE`, and `git status --porcelain -- LICENSE`
  prints nothing.
  (Source: criterion 3,
  `test_root_license_is_pinned_and_matches_git_head`, REQUIREMENTS
  out-of-scope: license change needs a legal decision.)
- **D-10** — `docs/SUMMARY.md` gains exactly one line for the guide —
  `- [Developer guide](developer-guide.md)` — and no other SUMMARY line
  changes, so the exact-once + set-equality check passes.
  (Source: ROOT-03, criterion 4,
  `test_summary_links_resolve_exactly_once`.)
- **D-11** — The new README keeps the live-import UNPROVEN statement
  (live Grok Bot / Cursor import unproven until the 77.1 UAT rows run)
  and makes no connected-Bot, published-listing, marketplace, measured
  number, timing figure, or screenshot claim.
  (Source: REQUIREMENTS defaults "Live Grok Bot import stays UNPROVEN";
  protects Phase 100 QA-02.)
- **D-12** — The README rewrite and the developer-guide move are one
  atomic task in the single plan 99-01: write the guide first, then the
  README, and verify both (section presence, links, tests) before
  finishing, so no content is ever dropped mid-change.
  (Source: criterion 2 no-drop; assignment constraint.)
- **D-13** — Plan 99-01 must keep green, at minimum:
  `test_docs.py::test_setup_prompts_run_the_declared_stdio_command`,
  `test_docs.py::test_relative_links_images_and_anchors_resolve`,
  `test_docs.py::test_summary_links_resolve_exactly_once`,
  `test_docs.py::test_every_docs_page_opens_with_a_top_level_heading`,
  `test_docs.py::test_documented_shell_commands_name_runnable_modules_and_scripts`,
  `test_public_repo.py::test_readme_links_resolve`,
  `test_grokbot_listing.py::test_root_license_is_pinned_and_matches_git_head`
  (plus the full `test_docs.py`, `test_public_repo.py`,
  `test_grokbot_listing.py` files).
  (Source: ROOT-01..03; assignment constraint.)

## Deferred Ideas

- Marketplace publication, website, outreach, hosted deployment, published
  container image (REQUIREMENTS out of scope).
- Running the 77.1 live UAT rows (user-owned).
- Screenshots of a live Bot session.
- Designing a new icon mark; changing the root `LICENSE` text or license.

## Claude's Discretion

- Exact hero wording of the new README (plain markdown, no emojis, per
  v20 listing convention).
- Which SUMMARY section hosts the guide line (Build fits the moved
  content).
- The CHANGELOG v20 entry's subsection shape under `## Unreleased`
  (must contain literal `v20`, additions only).
- Whether the guide keeps the old `##` headings verbatim or nests them
  under one `# Developer guide` H1 (anchors cited from elsewhere must
  still resolve — check for inbound `#...` links to old README anchors
  before renaming).
