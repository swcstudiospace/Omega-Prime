# Requirements: v20 Grok Bot launch kit

Accepted 2026-10-15 from the request for marketing, banner, icons, README,
CHANGELOG and LICENSE for the Grok Bot, plus the install prompt and follow-up
prompts. User decisions (AskQuestion, 2026-10-15):

- Grok-Bot-facing files live in a new folder `omega_prime/grokbot/listing/`,
  and the root `README.md` is replaced with a Grok-Bot-first version. The
  developer README content is kept under `docs/`, so nothing is lost.
- The root `LICENSE` (AGPL-3.0-only, matching `.cursor-plugin/plugin.json`)
  stays byte-for-byte unchanged. The listing folder gets a licensing notice
  only.
- The follow-up prompts use the standard drafted set (INS-02).
- Tracked as a new milestone, then executed by `gsd-autonomous`.

Defaults, because the request did not settle them:

- No research phase (`workflow.research` is false). The evidence base is the
  repository itself: `docs/marketing.md`, `omega_prime/grokbot/SETUP.md`,
  `mcp.json`, `.cursor-plugin/plugin.json` and the Phase 78 assets.
- Icons are exported from the existing brand (`assets/icon.svg`). No new mark
  is designed.
- `README.md` must keep the on-Bot stdio command. The test
  `test_setup_prompts_run_the_declared_stdio_command` requires it.
- Live Grok Bot import stays **UNPROVEN** in every new file until the user
  runs the six 77.1 UAT rows.

## Listing package

- [ ] **LST-01**: `omega_prime/grokbot/listing/README.md` is the Grok Bot listing page: what it is, the Bot-driven install prompt (linked, not duplicated), the follow-up prompts, claim limits and evidence levels, each claim with a source file.
- [ ] **LST-02**: `omega_prime/grokbot/listing/CHANGELOG.md` follows Keep a Changelog. It has a `v20` entry for this kit and points to the root `CHANGELOG.md` for the history of the repository.
- [ ] **LST-03**: `omega_prime/grokbot/listing/LICENSE.md` is a licensing notice that names AGPL-3.0-only, the copyright holder and the root `LICENSE` file. It contains no new license text, and the root `LICENSE` is unchanged.

## Brand assets

- [x] **BRD-01**: `omega_prime/grokbot/listing/icons/` holds PNG exports of `assets/icon.svg` at 16, 32, 64, 128, 192, 256 and 512 px, rendered through the existing `omega_prime/scripts/render_raster.py` path. The SVG's SHA-256 is recorded so a stale raster fails a test.
- [x] **BRD-02**: `omega_prime/grokbot/listing/banner.svg` and a PNG derivative carry the listing title and tagline. The text names only verified capabilities and the evidence level (no live-Grok claim), and the contrast matches the Phase 78 banner.

## Install and follow-up prompts

- [ ] **INS-01**: `omega_prime/grokbot/listing/prompts/install.md` contains the install task block verbatim from `omega_prime/grokbot/SETUP.md` section 1. A test fails when they drift. The stdio command equals the one declared in `mcp.json`.
- [ ] **INS-02**: `omega_prime/grokbot/listing/prompts/follow-up/` holds five prompts: verify the install, a `/omega-help` tour, a `/omega-doctor` health check, a first real task through `/omega-code-review`, and troubleshooting through the Bot's own diagnostics. Each prompt names the exact `/omega-*` command it sends and states what it does not prove.
- [ ] **INS-03**: A test fails when a follow-up prompt names a `/omega-*` command that has no file in `omega_prime/grokbot/plugin/commands/`, or when a prompt file is missing from the index.

## Root documents

- [ ] **ROOT-01**: Root `README.md` opens Grok-Bot-first with the install prompt link, the follow-up link, the on-Bot stdio command line (required by the existing setup test) and the license badge. The developer content (Architecture, Quickstart, Configuration, Build, Layout, Contributing) moves to `docs/developer-guide.md`, with every relative link fixed and no content dropped.
- [ ] **ROOT-02**: Root `CHANGELOG.md` gets a v20 entry under Unreleased. Existing history is not rewritten.
- [ ] **ROOT-03**: `docs/SUMMARY.md` lists the developer guide once, and every new page resolves through the existing link test.

## Verification and claim discipline

- [ ] **QA-01**: The new and changed docs pass `ruff check`, `ruff format --check`, `mypy omega_prime/`, the full pytest suite, `omega_prime.tooling.catalog --check` and `omega_prime.tooling.plugin --check`.
- [ ] **QA-02**: No file claims a live Grok Bot connection, a published icon, a marketplace listing, a measured number or a screenshot. Each factual claim cites a canonical file, and any unexecuted route is labelled UNPROVEN.

## Out of scope

- Changing the root `LICENSE` text or the license choice. Changing it needs a legal decision.
- Designing a new icon mark. The existing brand is reused.
- Marketplace publication, a website, outreach campaigns, a hosted deployment or a published container image.
- Running the 77.1 live UAT rows. They stay user-owned.
- Screenshots of a live Bot session.

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| BRD-01 | Phase 97 | Complete |
| BRD-02 | Phase 97 | Complete |
| LST-01 | Phase 98 | Pending |
| LST-02 | Phase 98 | Pending |
| LST-03 | Phase 98 | Pending |
| INS-01 | Phase 98 | Pending |
| INS-02 | Phase 98 | Pending |
| INS-03 | Phase 98 | Pending |
| ROOT-01 | Phase 99 | Pending |
| ROOT-02 | Phase 99 | Pending |
| ROOT-03 | Phase 99 | Pending |
| QA-01 | Phase 100 | Pending |
| QA-02 | Phase 100 | Pending |

Coverage: 13/13 v20 requirements mapped to exactly one phase.
