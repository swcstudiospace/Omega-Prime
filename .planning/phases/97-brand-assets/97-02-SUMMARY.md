---
phase: 97-brand-assets
plan: 02
status: complete
requirements: [BRD-02]
---

# Phase 97 Plan 02: Grok Bot listing banner from the Phase 78 banner - Summary

The Grok Bot listing banner was made from the Phase 78 banner `assets/banner.svg`: same ground, gradients, pattern and mark, with the text replaced by four listing lines. A 1200 by 400 PNG was rendered from it through `omega_prime/scripts/render_raster.py`, and five tests now guard the text, contrast, claims and digest.

## What was built

- `omega_prime/grokbot/listing/banner.svg`: a copy of `assets/banner.svg` with the text changed to:
  1. `Omega Prime` (title, fill `#f0f3f6`, 84px)
  2. `One Grok programming bot.` (tagline, fill `#9aa4b2`, 36px, unchanged)
  3. `Local proof: MCP tool host and readiness report` (capability line, fill `#9aa4b2`, 26px)
  4. `Evidence levels: local proof, optional capability, unproven external` (legend, fill `#9aa4b2`, 20px)
  The `<desc>` text describes the same four runs. The Phase 78 line about Hermes and Omp was removed because it names no evidence level.
- `omega_prime/grokbot/listing/banner.png`: 1200 by 400, rendered by `render_raster.py` from `banner.svg`. Its `omega-source-sha256` tEXt value is the SHA-256 of `banner.svg`. The PNG was not hand-edited. A visual check of the render shows all four lines inside the canvas and clear of the mark.
- `omega_prime/tests/test_grokbot_listing_assets.py`: five banner tests appended, and `_text_contrast_failures` imported from `omega_prime/tests/test_public_repo.py`:
  - `test_listing_banner_text_contrast_matches_phase78`: the WCAG AA guard passes, and the text fills are a subset of the Phase 78 fills.
  - `test_listing_banner_names_title_tagline_and_evidence_levels`: the four runs are exact, no run or desc names Grok Bot import, the capability line starts with `Local proof: `, and the legend names only the three levels.
  - `test_listing_banner_makes_no_live_grok_claim`: no run or desc matches connect, attach, live, works with or running.
  - `test_listing_banner_png_is_stamped_from_its_svg`: PNG is 1200 by 400 and its digest matches the SVG.
  - `test_listing_banner_is_self_contained`: no href, xlink, external URL or script, after the SVG namespace declaration is removed.

## Files created or changed

| File | Change |
| --- | --- |
| `omega_prime/grokbot/listing/banner.svg` | created (commit `326b25e`) |
| `omega_prime/grokbot/listing/banner.png` | created (commit `75b8f9a`) |
| `omega_prime/tests/test_grokbot_listing_assets.py` | modified: imports, five banner tests and helpers appended (commit `f3beb88`) |

Not touched: `.planning/STATE.md`, `.planning/ROADMAP.md`, `omega_prime/sessions.db`, `uv.lock`, `assets/`.

## Verification

Exit codes are from the commands as run on the final tree.

| Command | Exit |
| --- | --- |
| `.venv/bin/python -c "import xml.dom.minidom ..."` on banner.svg (parses, prints `1200 400 0 0 1200 400`) | 0 |
| `grep -c` for each of the four exact text lines | 1, 1, 1, 1 |
| `grep -c "Grok Bot import"` on banner.svg | 0 (grep exit 1, no match, expected) |
| `grep -c "Hermes and Omp"` on banner.svg | 0 (grep exit 1, no match, expected) |
| `grep -ciE "href\|xlink\|https?://\|<script"` on banner.svg | 1 (the `xmlns` URI only; see Deviations) |
| same check with the `xmlns` declaration removed | 0 matches |
| live-claim grep (`connected`, `attached`, `live grok`) on banner.svg | 0 matches (grep exit 1, expected) |
| `python3 omega_prime/scripts/render_raster.py omega_prime/grokbot/listing/banner.svg` | 0 |
| digest and size check on banner.png (1200 by 400, SHA-256 of banner.svg present) | 0 (`banner png ok`) |
| re-render of banner.png produced identical bytes | yes (no diff) |
| `.venv/bin/python -m pytest omega_prime/tests/test_grokbot_listing_assets.py omega_prime/tests/test_public_repo.py -q` | 0 (33 passed) |
| `.venv/bin/python -m pytest omega_prime/tests/test_grokbot_listing_assets.py -q` | 0 (14 passed) |
| Mutation check: tagline fill changed to `#4b5260` | pytest exit 1 (`test_listing_banner_text_contrast_matches_phase78` fails); banner restored to committed bytes afterwards |
| `.venv/bin/ruff check omega_prime/tests/test_grokbot_listing_assets.py` | 0 |
| `.venv/bin/ruff format --check omega_prime/tests/test_grokbot_listing_assets.py` | 0 |
| `.venv/bin/python -m mypy omega_prime/tests/test_grokbot_listing_assets.py` | 0 |
| `.venv/bin/mypy --version` (bare shim) | 127 (known shebang issue from 97-01; not used) |

Full pytest suite was not run, per instructions.

## Deviations

1. **Task 1 external-reference grep (plan criterion wrong, no file change).** The plan expects `grep -ciE "href|xlink|https?://|<script"` on banner.svg to print 0. The root `<svg>` element carries `xmlns="http://www.w3.org/2000/svg"`, which is a namespace identifier and not a reference. The Phase 78 source has the same declaration. The existing `test_svgs_valid_and_self_contained` removes it before checking, and the listing check does the same. With the declaration stripped the count is 0. Because of the namespace match, the plan's combined verify chain exits 1 at the `! grep -qi...` step.
2. **Test E as written would fail (fixed in the test).** Plan Task 3 said `test_listing_banner_is_self_contained` should check the raw text for `http://`. That always fails because of the namespace declaration. The test now strips `xmlns="http://www.w3.org/2000/svg"` first, matching the existing asset test.
3. **Legend level check made case-insensitive (fixed in the test).** The plan asks for `Local proof`, `Optional capability` and `Unproven external` as capitalised substrings. The legend text is lowercase (`Evidence levels: local proof, optional capability, unproven external`), so a case-sensitive check fails. The test lowercases the legend and checks the three level names. The exact-text test already fixes the legend string.
4. **Test count (count only, no code change).** The plan says the listing file reports 10 tests. The file has 5 test functions from 97-01 (9 items, since the usage test is parametrised) plus 5 new functions, so pytest reports 14 items. The five new functions match the plan.
5. **Lint fix (no behaviour change).** Ruff's RUF005 flagged `texts + [_desc_text(raw)]` in the new banner test. It is now `[*texts, _desc_text(raw)]`.
6. **mypy invocation.** As in 97-01, the bare `.venv/bin/mypy` shim is broken (exit 127), so mypy was run as `.venv/bin/python -m mypy`.

## Notes for the orchestrator

- `git status` shows many tracked `.planning/phases/*` files as deleted in the working tree, and `.planning/` files as modified. None of that is from this plan. Nothing under `.planning/` was staged or committed. `omega_prime/sessions.db` and `uv.lock` remain untracked and were not staged.
- The 97-02 PLAN and CONTEXT files are not in the commit history; they were read from the working tree.

## Commits

- `326b25e` feat(97-02): add Grok Bot listing banner from Phase 78 banner (Task 1: `banner.svg`)
- `75b8f9a` feat(97-02): render Grok Bot listing banner PNG from SVG (Task 2: `banner.png`)
- `f3beb88` test(97-02): guard Grok Bot listing banner text, contrast and PNG digest (Task 3: test file)

`97-02-SUMMARY.md` itself is not committed.

## Self-Check: PASSED

- `banner.svg`, `banner.png` and the test file exist, and each is in the commit list above.
- All three commits are in `git log`.
- The listing tests and public-repo tests pass (33 items), and the contrast test fails on the dimmed mutant.
