# Phase 99 Plan 01: Root documents — Summary

## Done

- **Task 1 (ROOT-01):** Created `docs/developer-guide.md` carrying all six
  moved README sections verbatim (Architecture, Quickstart incl.
  `### Prepare an owned workspace for Grok Bot`, Configuration,
  Build from source (both toolchains), Layout, Contributing) with every
  relative link re-rooted one level down (`docs/X.md` to `X.md`, root
  siblings to `../`). Rewrote `README.md` Grok-Bot-first: H1 plus
  one-sentence product line, Bot-driven hero, `## Install` with the install
  prompt link and the exact stdio line, `## Follow-up prompts` with the
  follow-up index link, `## Docs` pointer block, verbatim license badge,
  `## License` pointer naming AGPL-3.0-only, and one UNPROVEN paragraph.
- **Task 2 (ROOT-02):** Added a `### v20 — Grok-Bot-first root` subsection
  under the top `## Unreleased` preamble of `CHANGELOG.md`, above
  `# Changelog`, additions only. `LICENSE` untouched.
- **Task 3 (ROOT-03):** Added exactly one line,
  `- [Developer guide](developer-guide.md)`, to the Build section of
  `docs/SUMMARY.md` beside the architecture/build-aesthetics entries.

## Verification evidence (commands actually run, results observed)

- Guide headings/anchors/links — one chained grep command, exit 0,
  printed `GUIDE_OK`. It confirmed `^# ` plus all seven headings
  (`## Architecture`, `## Quickstart`,
  `### Prepare an owned workspace for Grok Bot`, `## Configuration`,
  `## Build from source (both toolchains)`, `## Layout`,
  `## Contributing`), all eight anchor substrings
  (`prime.kernel.enabled`, `recurse-submodules`, `bootstrap-only`,
  `omega-prime.json`, `OMEGA_PRIME_PRIME_`, `prime-agent.pin.json`,
  `One tool roster`, `CODE_OF_CONDUCT.md`), and the absence of
  `](docs/`, `](VENDOR.md)`, `](LICENSE)`, `](assets/)`.
- README elements — one chained grep command, exit 0, printed
  `README_OK`. It confirmed the install-prompt link, the
  `follow-up/INDEX.md)` link, the `](docs/developer-guide.md)` link, the
  exact stdio substring
  `bash /workspace/omega-prime/omega_prime/scripts/grokbot-1click.sh --bootstrap --transport stdio`,
  the verbatim badge line
  `[![license](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)`,
  the string `UNPROVEN`, and the absence of all six `^##` dev sections.
- CHANGELOG position/history — chained check exit 0, printed
  `CHANGELOG_OK`: first `v20` line precedes the `^# Changelog` line,
  `UNPROVEN` present, and `git diff HEAD -- CHANGELOG.md | grep -c '^-[^-]'`
  equals 0 (additions only).
- LICENSE pin — `sha256sum LICENSE` printed
  `6c944d7a32f9355777b7f4c70243a01bf2ea55c429a69599f53c4595d6b4b51a`
  (matches CONTEXT D-09); `git show HEAD:LICENSE | cmp -s - LICENSE`
  exited 0 (printed `CMP_OK`); `git status --porcelain -- LICENSE`
  printed nothing (printed `LICENSE_CLEAN`).
- SUMMARY exact-once — chained check exit 0, printed `SUMMARY_OK`:
  `grep -cF '](developer-guide.md)' docs/SUMMARY.md` equals 1,
  `git diff HEAD -- docs/SUMMARY.md` has zero `-` lines and at least one
  `+` line.
- `git status --porcelain` (full worktree) shows, for this plan's scope:
  `M README.md`, `M CHANGELOG.md`, `M docs/SUMMARY.md`,
  `?? docs/developer-guide.md`. All other entries (other phases'
  `.planning/` churn, `omega_prime/sessions.db`, `uv.lock`) are
  pre-existing and untouched by this plan.
- No pytest, ruff, mypy, LSP, or formatter was run in this plan, per the
  assignment. No pytest or lint results are claimed here.

## Deviations from the plan (with reasons)

1. **Compatibility anchor in README for the moved build section.** A repo
   grep for inbound `#anchor` links (required by Task 1 before any heading
   rename) found five live links to
   `README.md#build-from-source-both-toolchains` in files this plan may
   not touch (`CONTRIBUTING.md`, `docs/agent-loop.md`,
   `docs/driving-the-bot.md`, `docs/migration.md`, `docs/setup.md`).
   Removing the section outright would break those links and fail
   `test_relative_links_images_and_anchors_resolve`. The guide keeps the
   `## Build from source (both toolchains)` heading verbatim, and the new
   README carries `<a id="build-from-source-both-toolchains"></a>` with an
   HTML comment naming the canonical home, inside the `## Docs` block. No
   `^##` dev section was added back to the root; the anchor is invisible
   prose, not a section or a claim.
2. **CHANGELOG insert repair (self-corrected mid-task).** The first edit
   inserted the v20 block before line 1, duplicating the `## Unreleased`
   header. A follow-up edit removed the duplicate and collapsed the
   resulting double blank line. Final top-of-file shape verified by read:
   `## Unreleased`, blank, `### v20 — Grok-Bot-first root`, four bullets,
   blank, then the pre-existing slash-commands bullet byte-identical.

## Remaining (for the orchestrator)

- Run the gates: the full `test_docs.py`, `test_public_repo.py`,
  `test_grokbot_listing.py` sweep (D-13 node ids), plus any repo-wide
  gates. This plan ran only the grep/cmp/sha256sum/git-status evidence
  above.
- Commit `README.md`, `docs/developer-guide.md`, `CHANGELOG.md`,
  `docs/SUMMARY.md` (and this SUMMARY file per the plan's output step).
  This plan deliberately left everything uncommitted and un-staged.

## Plan decision (recorded by the orchestrator)

99-02-PLAN.md proposed fixing the five outside links by editing CONTRIBUTING.md and the listing README. Its file is renamed to 99-02-PLAN.superseded so GSD does not count it as an open plan. The owner chose to keep 99-01's approach: the README carries the compatibility anchor, and CONTRIBUTING.md and the other outside files stay untouched.
