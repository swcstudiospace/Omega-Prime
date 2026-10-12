---
phase: 78-repository-marketing
reviewed: 2026-10-12T00:00:00Z
depth: standard
files_reviewed: 9
files_reviewed_list:
  - assets/banner.svg
  - assets/social-card.svg
  - omega_prime/tests/test_public_repo.py
  - docs/marketing.md
  - docs/SUMMARY.md
  - docs/README.md
  - README.md
  - CHANGELOG.md
  - omega_prime/tests/test_docs.py
findings:
  critical: 0
  warning: 5
  info: 6
  total: 11
status: issues_found
---

# Phase 78: Code Review Report

**Reviewed:** 2026-10-12T00:00:00Z
**Depth:** standard
**Files Reviewed:** 8
**Status:** issues_found

## Summary

Reviewed the eight Phase 78 files at standard depth: read each file in full,
then verified every checkable claim against its cited source. No blockers.
All relative links and anchors in `docs/marketing.md` resolve
(`architecture.md`, `setup.md` sections 2/4/5/6 and "Claim limits",
`user-guide.md` "Receipts"/"Tools and approvals"/"Config and state roots",
`local-validation.md` including the bold "**Prime lifecycles
(`--include-prime` only).**" subsection, `grok-bot-native.md`
`#user-uat`/`#import-evidence-ledger`/`#exit-codes`, `red-team.md`,
`../README.md`, `../CHANGELOG.md`, `../assets/*`, both SVGs and the PNG,
`../omega_prime/grokbot/SETUP.md`); the quoted Grok Bot prompt is verbatim
identical to `SETUP.md` section 1; the `mcp.json` stdio entry matches the
documented server command; "46 commands / 12 curated" is corroborated by both
`SETUP.md` ("Prompting contract") and the CHANGELOG; every demo exit-code
contract matches code (`setup_check.py` 0/1 with skips as `None` never
failing; `catalog.py` 0/1; `plugin.py` 0/1/3/4 plus argparse 2;
`readiness.py` 0/1/2 with "no canonical roster" returning exit 2 and the
report on stdout via `sys.stdout.write`); the unstamped-`.venv` reuse claim
matches the launcher header comments; the RLM/delegate
`not_configured: provider` refusal matches `rlm.py`/`delegate.py`; SVG alt
text matches the rendered strings; both SVGs are self-contained (no
`script`/`href`/`xlink`/remote URL, system font stacks only) with
`<title>`+`<desc>`+`aria-labelledby`; `icon.svg` has no `<title>` as the test
comment states. Greptile rules hold: no secrets in any new content, no
network in the test (file reads + `minidom` only), receipts cited per demo
step, `SUMMARY.md` lists Marketing exactly once with a top-level heading.
The test itself is behavioral per `CONTRIBUTING.md` (consumer-visible
invariants: links resolve, assets render standalone, assistive-tech titles).
One factual imprecision and three robustness nits below. `assets/social-card.png`
was excluded per scope (provenance already verified by the parent).

## Warnings

### WR-01: Tagline misdescribed as the whole `pyproject.toml` description

**File:** `docs/marketing.md:32-34`
**Issue:** Positioning claims the tagline "One Grok programming bot." *is*
the package description in `pyproject.toml`. The actual description is
"One Grok programming bot. Hermes, oh-my-pi, and Prime agent logic in one
Python process." — the tagline is its opening sentence, not the whole
value. Anyone checking the claim finds a mismatch.
**Fix:**
```markdown
Tagline "One Grok programming bot." is the opening sentence of the
package description in `pyproject.toml`.
```

## Info

### IN-01: Test comment claims properties the test does not enforce

**File:** `omega_prime/tests/test_public_repo.py:32`
**Issue:** The comment "assets/icon.svg stays byte-identical to the brand
source and has no `<title>`" states two invariants, but the test asserts
neither: nothing hashes `icon.svg`, and nothing asserts the *absence* of a
`<title>` on it (the second loop only asserts presence on banner/social).
If the icon gains a title or drifts a byte, the suite stays green while the
comment claims otherwise. (Current state verified true by inspection:
zero `<title` occurrences in `icon.svg`.)
**Fix:** Either assert it —
```python
icon_raw = (ROOT / "assets/icon.svg").read_text(encoding="utf-8")
assert "<title" not in icon_raw, "icon.svg must carry no <title>"
```
— or reword the comment to describe only what is checked.

### IN-02: SVG external-reference checks are brittle substring matches

**File:** `omega_prime/tests/test_public_repo.py:24-27`
**Issue:** `"href" not in raw` false-positives if a future `<desc>` ever
contains the word "href", misses case variants, and the `http://` scan
depends on stripping exactly one `xmlns` spelling (single quotes or extra
whitespace would trip it on the namespace itself). Passes today; fragile
tomorrow.
**Fix:** Parse and inspect instead, e.g. walk all elements checking for
`href`/`xlink:href` attributes and for `http` inside only non-namespace
attribute values, rather than scanning the raw string.

### IN-03: No link coverage for the phase's main deliverable

**File:** `omega_prime/tests/test_public_repo.py:13-18`
**Issue:** The test resolves README-relative links only. `docs/marketing.md`
— the largest new file — carries ~30 relative links, two asset embeds and
three `#anchor` references that no test guards; a future broken marketing
link ships green. (All resolve as of this review; this is a gap, not a live
break.)
**Fix:** Generalize the link test to walk `README.md` plus `docs/*.md`,
resolving each relative target against its own file's directory and
checking `#anchors` against the target's headings.

---

_Reviewed: 2026-10-12T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
---

## Follow-up review — diff 9314ca3..08cf459 (2026-10-12)

**Scope:** the Nyquist validation tests added in commit `08cf459`:
`omega_prime/tests/test_docs.py` (+150 lines) and
`omega_prime/tests/test_public_repo.py` (+166 lines).
**Result:** both files run green here (13 passed, repo venv, no network).
Every probe below was executed against the current tree through the real
helpers (probe script kept outside the repo); all are behavioral checks,
not wording pins. Original findings WR-01 / IN-01–IN-03 above are untouched.

**Verified correct — no finding:**
- Duplicate-heading suffixes follow GitHub (`foo`, `foo-1`, `foo-2`).
- `python3 -m` and `VAR=1`-prefixed invocations are detected by `_PY_MODULE`.
- sRGB/contrast math (`12.92`, `0.055`/`1.055`, exponent `2.4`,
  `0.2126`/`0.7152`/`0.0722`, `(hi+0.05)/(lo+0.05)`, source-over alpha
  compositing) is correct. The `0.04045` linearization threshold is the
  sRGB-spec value; WCAG cites `0.03928`, but the two agree everywhere except
  a 0.001-wide band, so no 4.5:1 verdict can flip outside a knife-edge.
- PNG IHDR parse (magic, `IHDR` at 12:16, big-endian `>II` at 16:24) is
  per-spec — IHDR is required to be the first chunk — and truncated files
  still fail on the magic assertion.
- Determinism/hermeticity: sorted page lists, pure helpers, file reads plus
  `minidom`/`struct` only. `find_spec` executes just the two trivial package
  `__init__` files (docstring plus version/exports). No network calls, no
  credential-shaped literals.
- `CONTRIBUTING.md` (behavioral invariants) and `.greptile/config.json`
  (hermetic tests, one H1 per page) hold: the `#4b5260` value in the negative
  control is the test's own synthetic input, not a product wording/colour pin.
- Root-absolute links (`/docs/setup.md`) are correctly rejected — on GitHub
  they resolve to the domain root, not the repo.

### WR-02: Contrast checker errors on legitimate SVG fills

**File:** `omega_prime/tests/test_public_repo.py:75-81`, `157-160`
**Issue:** `_rgb` asserts 6-digit hex only, and line 160 asserts every
`<text>` carries its own `fill`. Probed: `fill="white"` → `AssertionError:
not a solid colour: white`; `fill="#f0f3f680"` (8-digit alpha hex) →
`AssertionError`; text inheriting `fill` from a parent `<g>` (standard SVG
inheritance — and this repo's own banner/social SVGs already set `fill` on
`<g>` elements) → `AssertionError: ... has no explicit fill`. Each case
errors the suite instead of measuring, so a legitimate asset edit reds CI
with a traceback rather than a contrast verdict.
**Fix:** resolve the inherited fill by walking up the ancestor chain, and
accept CSS colours beyond 6-hex (named colours at minimum; drop or
composite the alpha byte):
```python
def _fill_of(node: minidom.Element) -> str:
    while node is not None and not node.getAttribute("fill"):
        node = node.parentNode
    return node.getAttribute("fill") if node is not None else ""
```

### WR-03: Text is measured against the full-canvas ground only

**File:** `omega_prime/tests/test_public_repo.py:109-164`
**Issue:** `grounds` collects only full-canvas `<rect>` fills. Text sitting
on a smaller card/badge rect is checked against the page ground instead —
both directions are wrong. Probed: dark page (`#0b0e13`) + light badge
(`#f0f3f6`) + `#c8cdd4` label returns `[]`, i.e. an unreadable label ships
green (false negative); the mirror case (dark badge on a light page) would
fail a perfectly readable label (false positive). Related gaps in the same
function: gradient interiors between stops are never sampled (a ground
gradient passing through the text colour reads ~1:1 mid-way while both
endpoints pass), and overlay shapes using `fill-opacity` or group-level
`opacity` are composited as fully opaque (only shape-level `opacity` is
read, line 143).
**Fix:** associate each `<text>` with the topmost smaller filled shape
beneath it (document order within the same backdrop) and measure against
that ground; where no local ground exists, keep the full-canvas check.
Sampling gradient midpoints (`(a+b)/2` per stop pair) closes the interior
gap.

### WR-04: Link resolver rejects legitimate URL forms

**File:** `omega_prime/tests/test_docs.py:58-80`
**Issue:** No query-string handling (`[x](grok-bot-native.md?x=1)` →
`"no file docs/grok-bot-native.md?x=1"`); protocol-relative URLs
(`[x](//example.com/x)` → `"no file //example.com/x"`); `data:` URIs in raw
HTML (`<img src="data:image/png;base64,...">` → `"no file
docs/data:image/..."`). All three were probed and all three render fine in
browsers/GitHub, so any of them appearing in future docs fails the suite on
legitimate content.
**Fix:** after splitting off `#anchor`, drop the query string, and skip
non-file schemes plus protocol-relative hosts:
```python
if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith("//"):
    continue
path_part = path_part.partition("?")[0]
```

### WR-05: Shell-command extractor over- and under-matches

**File:** `omega_prime/tests/test_docs.py:83-106`
**Issue:** Probed four cases. (a) Commented lines are scanned:
`# python -m omega_prime.tooling.katalog` inside a bash fence is flagged, so
documenting a removed command in a comment reds CI (false positive). (b)
Backslash continuations are missed: `python -m \` + newline + module is not
detected (false negative). (c) The fence allowlist (`bash`/`sh`/`shell`/
`console`) misses `zsh`/`fish`/`powershell`, so a copied block in a
```zsh fence naming a bogus module ships green (false negative). (d) Quoted
script paths (`bash "omega_prime/scripts/x.sh"`) are missed because
`_SCRIPT` requires the literal `omega_prime/` immediately after the space
(false negative).
**Fix:** normalize each block before matching — join continuations and drop
comment-only lines — and widen the matchers:
```python
block = re.sub(r"\\\r?\n", "", block)
block = "\n".join(
    line for line in block.splitlines()
    if not line.strip().startswith("#")
)
```
plus `("bash", "sh", "shell", "console", "zsh", "fish", "powershell")` and
`["']?` around the `_SCRIPT` path.

## Info

### IN-04: Heading/anchor resolver gaps (all latent today)

**File:** `omega_prime/tests/test_docs.py:29-55`
**Issue:** `_HEADING` anchors at column 0, so an indented heading (`  ##
...`, e.g. inside a list — a real GitHub anchor) is invisible and a link to
it is reported broken (probed). `_HTML_ANCHOR` only matches lowercase `<a`
with `id`/`name` as the first attribute, so `<a href="#x" id="y">` is missed
(probed: only the `# T` heading anchor survived). Both sides are lowercased,
but HTML ids are case-sensitive — `#mixedcase` passes against `id=
"MixedCase"` where browsers would not match (probed, lenient direction).
Setext (`===`/`---`) headings are unsupported. None occur in current docs;
each is a trap for future docs.
**Fix:** allow up to three leading spaces on headings; match `id`/`name` in
any attribute position case-insensitively; compare HTML ids case-sensitively
while keeping heading-slug comparison lowercased.

### IN-05: Canvas/viewBox check raises instead of failing cleanly

**File:** `omega_prime/tests/test_public_repo.py:42-51`
**Issue:** `float("100%")` / `float("1200px")` and comma-separated
`viewBox="0,0,100,100"` (per-spec legal) raise `ValueError` (probed) — the
suite errors with a traceback instead of reporting a mismatch. Percentage
widths are the standard responsive-SVG pattern; a future asset using one
breaks CI opaquely.
**Fix:** split the viewBox on `[,\s]+` and parse dimensions with a
units-tolerant match, asserting with a message:
```python
parts = re.split(r"[,\s]+", root.getAttribute("viewBox").strip())
```

### IN-06: Dim-text negative control is coupled to banner layout

**File:** `omega_prime/tests/test_public_repo.py:173-180`
**Issue:** The control recolors the LAST `<text>` to `#4b5260` and expects
detection — which holds only while that element sits on a dark ground.
Appending light-ground text to the banner, or reordering, makes
`#4b5260`-on-white pass (~7.5:1) and turns this control red on a legitimate
asset change.
**Fix:** select the target by its current fill (e.g. the `#f0f3f6` headline,
known to sit on the dark ground) or synthesize a minimal inline SVG instead
of mutating the banner.

---

_Follow-up reviewed: 2026-10-12_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard, diff 9314ca3..08cf459_
