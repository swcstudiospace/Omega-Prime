# Phase 97: Brand assets - Context

**Gathered:** 2026-10-15
**Status:** Ready for planning
**Mode:** Auto-generated (discuss skipped via workflow.skip_discuss)

<domain>
## Phase Boundary

The Grok Bot listing has an icon set and a banner rendered from the existing brand, with no new mark designed.

Requirements: BRD-01, BRD-02.

</domain>

<decisions>
## Implementation Decisions

### Locked by the user (2026-10-15)
- Icons come from the existing brand in `assets/icon.svg`. No new mark is designed.
- Banner text names only verified capabilities and their evidence level. No live-Grok claim.

### Claude's Discretion
All other choices (export tooling, file names, sizes beyond those listed) are at Claude's discretion, guided by the Phase 78 precedent: `omega_prime/scripts/render_raster.py` (CairoSVG), the recorded SHA-256 in the raster, and the contrast of `assets/banner.svg`.

</decisions>

<code_context>
## Existing Code Insights

- `assets/icon.svg` (512x512, teal-to-violet node mark) is the brand source.
- `assets/banner.svg` and `assets/social-card.svg` are the Phase 78 precedents. `assets/social-card.png` was rendered by `omega_prime/scripts/render_raster.py`.
- `docs/marketing.md` records the render command and the SHA-256 contract.

</code_context>

<specifics>
## Specific Ideas

ROADMAP phase 97 success criteria are the source of truth.

</specifics>

<deferred>
## Deferred Ideas

- New icon design, favicon redesign, marketplace upload: out of scope for v20.

</deferred>
