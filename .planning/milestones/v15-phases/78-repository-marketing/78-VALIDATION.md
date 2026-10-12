---
phase: "78"
slug: "repository-marketing"
# status lifecycle: draft (seeded by plan-phase) → validated (set by validate-phase §6)
status: validated
nyquist_compliant: true
wave_0_complete: true
created: "2026-10-12"
audit_state: B
source_revision: 08cf459
---

# Phase 78 — Validation Strategy

> Reconstructed from artifacts (validate-phase State B): research was disabled, so plan-phase seeded no strategy. `nyquist_compliant: true` means no automatable consumer-visible gap remains. Claim honesty and visual quality are judgment checks, listed under Manual-Only with their evidence.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.1.1 |
| **Config file** | `pyproject.toml` `[tool.pytest.ini_options]` (testpaths `omega_prime/tests`) |
| **Quick run command** | `.venv/bin/python -m pytest omega_prime/tests/test_docs.py omega_prime/tests/test_public_repo.py -q -p no:cacheprovider` |
| **Full suite command** | `.venv/bin/python -m pytest omega_prime/tests -q` |
| **Estimated runtime** | quick ~1 s; full ~240 s |

CI runs the quick pair on a base install (`pip install -e . pytest`, `.github/workflows/ci.yml` job `docs`). The new tests import only the standard library and resolve documented modules through `importlib.util.find_spec`, so they need no dev extra.

---

## Sampling Rate

- **After every task commit:** Run the quick run command
- **After every plan wave:** Run the full suite command
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 2 seconds (quick pair)

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 78-02-T1 banner contrast, wording, accessibility | 02 | 1 | MKT-03 | T-78-04 | Banner stays self-contained; every text line reaches WCAG AA 4.5:1 on its ground; aria-labelledby ids resolve | unit | quick pair: `test_svgs_valid_and_self_contained`, `test_svg_text_meets_wcag_aa_contrast_on_its_ground`, `test_contrast_check_flags_dim_text`, `test_svg_accessible_name_references_resolve`, `test_svg_canvas_matches_its_viewbox` | ✅ | ✅ green |
| 78-02-T2 social card SVG and PNG | 02 | 1 | MKT-03 | T-78-04, T-78-05 | Card has no script or external reference; its PNG matches the source canvas; no orphan raster | unit | quick pair: `test_svgs_valid_and_self_contained`, `test_png_rasters_match_their_svg_source_canvas`, `test_svg_canvas_matches_its_viewbox`, `test_svg_accessible_name_references_resolve` | ✅ | ✅ green |
| 78-02-T3 SVG validity test covers the card | 02 | 1 | MKT-03 | T-78-04 | An injected `<script>` fails the test | unit | quick pair: `test_svgs_valid_and_self_contained` | ✅ | ✅ green |
| 78-01-T1 marketing page wired into navigation | 01 | 2 | MKT-01 | T-78-03 | Page listed exactly once in `docs/SUMMARY.md`, opens with an H1 | unit | quick pair: `test_summary_links_resolve_exactly_once`, `test_every_docs_page_opens_with_a_top_level_heading` | ✅ | ✅ green |
| 78-01-T2 MKT-01/MKT-02 content and demo | 01 | 2 | MKT-01, MKT-02 | T-78-01, T-78-02 | Every relative link, image and anchor resolves; every documented `python -m omega_prime.*` module and `bash omega_prime/**/*.sh` script exists | unit | quick pair: `test_relative_links_images_and_anchors_resolve`, `test_link_check_rejects_a_missing_anchor_file_and_image`, `test_documented_shell_commands_name_runnable_modules_and_scripts`; flag contracts: `omega_prime/tests/test_grokbot_plugin.py`, `omega_prime/tests/test_setup_check.py` | ✅ | ✅ green |
| 78-01-T3 README, docs landing, CHANGELOG | 01 | 2 | MKT-01 | T-78-03 | Pointers resolve, anchors included | unit | quick pair: `test_readme_links_resolve`, `test_relative_links_images_and_anchors_resolve` | ✅ | ✅ green |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

Receipt: quick pair `13 passed in 0.33s` at 08cf459 (9 new, 4 existing); `ruff check` and `ruff format --check` clean; `mypy` clean on both files.

Fail-closed evidence (throwaway copies or in memory): the pre-phase banner fails the contrast helper (3.14:1); a changed card viewBox, a renamed `card-desc` id, a resized canvas with a stale PNG, and an orphan PNG each fail their test; a missing anchor, section, page, image or HTML `src` fails the link check, while links inside code fences are ignored; renamed modules or scripts fail the command check.

---

## Wave 0 Requirements

Existing infrastructure covers all phase requirements.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Claims match their cited sources; behavior-ports positioning; 46 used only as a command count; Grok Bot and Cursor routes stay UNPROVEN; no website, outreach, marketplace, deployment or publish claim | MKT-01, MKT-02 | Judgments about meaning; CONTRIBUTING forbids pinning wording | Read each claim against its cited source; run the plan's banned-claim grep (0 hits at 89ac138) |
| Demo exit codes on a fresh clone | MKT-02 | Needs a network clone and install, which a hermetic test cannot do | Run the seven commands in `docs/marketing.md` in a fresh clone; all exited 0 (`/tmp/v15-78-demo-1791836909/logs/`) |
| PNG pixel fidelity to the SVG | MKT-03 | System CairoSVG is outside the project venv; no new dependency | `/usr/local/bin/cairosvg assets/social-card.svg -o <tmp>` and compare; pixel-identical at 0067526 |
| Visual quality: layout, clipping, legibility at thumbnail size | MKT-03 | Needs a person looking at renders | Inspect Chrome and CairoSVG renders and a 400x200 thumbnail; passed (`/tmp/v15-78-visual/`) |
| README pointer, docs landing bullet, CHANGELOG section under Unreleased | MKT-01 | Wording/wiring pins are forbidden | Plan grep checks; link integrity is automated above |
| `assets/icon.svg` unchanged | MKT-03 | Pinning a hash would freeze brand art | sha256 `e023a819520740e3899c1c70ab7bc9bfcc5e43c7b8a833c397dafb248f62ae5b` |
| No credential-shaped literal in `docs/marketing.md` | MKT-02 | Judging a detector hit a false positive needs a person | Per-line `assert_no_secrets`; only the prose "bearer host"/"bearer auth" (77.1-SECURITY N-02) |

Known limits: heading slugs follow GitHub's algorithm (GitBook may slug a few headings differently); the contrast check uses flat gradient stops plus the dots overlay and does not model anti-aliasing; the command check proves modules and scripts exist, not that flags are valid (flag contracts live in the product tests above).

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 2s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** approved 2026-10-12

## Validation Audit 2026-10-12

| Metric | Count |
|--------|-------|
| Gaps found | 6 |
| Resolved | 6 |
| Escalated | 0 |
