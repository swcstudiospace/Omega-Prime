---
phase: 78
review: 78-REVIEW.md
titles: json
findings:
  - id: WR-01
    severity: warning
    disposition: fixed
    title: "Tagline misdescribed as the whole `pyproject.toml` description"
  - id: IN-01
    severity: info
    disposition: fixed
    title: "Test comment claims properties the test does not enforce"
  - id: IN-02
    severity: info
    disposition: skipped
    title: "SVG external-reference checks are brittle substring matches"
  - id: IN-03
    severity: info
    disposition: fixed
    title: "No link coverage for the phase's main deliverable"
  - id: WR-02
    severity: warning
    disposition: fixed
    title: "Contrast checker errors on legitimate SVG fills"
  - id: WR-03
    severity: warning
    disposition: fixed
    title: "Text is measured against the full-canvas ground only"
  - id: WR-04
    severity: warning
    disposition: fixed
    title: "Link resolver rejects legitimate URL forms"
  - id: WR-05
    severity: warning
    disposition: fixed
    title: "Shell-command extractor over- and under-matches"
  - id: IN-04
    severity: info
    disposition: fixed
    title: "Heading/anchor resolver gaps (all latent today)"
  - id: IN-05
    severity: info
    disposition: fixed
    title: "Canvas/viewBox check raises instead of failing cleanly"
  - id: IN-06
    severity: info
    disposition: fixed
    title: "Dim-text negative control is coupled to banner layout"
open: 0
total: 11
recorded: 2026-10-12T21:13:22.543Z
---

# Phase 78: Code Review Disposition

| Finding | Severity | Disposition | Source |
|---------|----------|-------------|--------|
| WR-01 | warning | fixed | 9314ca3: the page now says the tagline is the opening sentence of the pyproject package description |
| IN-01 | info | fixed | 08cf459: the test comment states only the fact that assets/icon.svg carries no <title> |
| IN-02 | info | skipped | Info severity; the raw-substring href/http checks are deliberately stricter than an attribute parse, so a false positive can only fail safe |
| IN-03 | info | fixed | 08cf459: test_relative_links_images_and_anchors_resolve checks every relative link, image and heading anchor in docs/*.md, README.md, CHANGELOG.md and CONTRIBUTING.md, with a negative control |
| WR-02 | warning | fixed | 305de61: colours parse from hex (3/4/6/8 digits), rgb()/rgba() and the basic keywords; fill, currentColor and fill-opacity inherit; ancestor opacity multiplies (probe cases: keyword, inherited dim, alpha hex, faded group) |
| WR-03 | warning | fixed | 305de61: each run is measured against the topmost filled rect under its anchor point, placed through translate/scale (others refused); gradients sampled between stops; fill-opacity honored (probe cases: light-on-light fails, dark-on-light and moved badge pass) |
| WR-04 | warning | fixed | 305de61: any URI scheme and protocol-relative target is skipped and query strings are stripped (valid-form cases: ?plain=1, data:, //host) |
| WR-05 | warning | fixed | 305de61: continuations joined, comments dropped, zsh fences and quoted script paths read, missing parent package reported (parametrized block table) |
| IN-04 | info | fixed | 305de61: indented ATX and Setext headings and attribute-anywhere id/name anchors resolve; HTML ids compare case-sensitively |
| IN-05 | info | fixed | 305de61: viewBox splits on commas or whitespace; a non-px canvas size fails naming the asset and attribute |
| IN-06 | info | fixed | 305de61: the negative control is a synthesized SVG table independent of the shipped banner |

Dispositions: `open` (recorded, not yet triaged), `fixed`, `skipped`, `deferred`.
Set `deferred` by hand and put the reason in the Source cell; both are preserved. A `|` in the reason is kept as prose and escaped on the next run.
Re-running the gate keeps every row it can. A row the current review no longer reports is kept and its Source cell flagged, so a finding does not leave this record silently. ONE exception: when a finding id is REUSED by a different finding, the earlier decision cannot keep a row — the id is taken — and it is dropped. A RECORDED decision (anything but `open`) is named on the console when that happens; a row still at `open` is replaced silently, because `open` records no decision to lose.
