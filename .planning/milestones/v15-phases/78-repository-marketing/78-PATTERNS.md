# Phase 78: Repository marketing - Pattern Map

**Mapped:** 2026-10-12
**Files analyzed:** 9 (4 owned outputs, 3 shared owners, 2 supporting/optional)
**Analogs found:** 8 / 9 (the raster derivative has no in-repo analog; a tool chain is mapped instead)

## Scope and evidence boundary

- Inputs: `78-CONTEXT.md` (locked deliverables, 77.1 import-path alignment, gates), `ROADMAP.md:108-120` (4 success criteria), `REQUIREMENTS.md:44-46` (MKT-01..03) and `REQUIREMENTS.md:48-54` (out of scope). No `78-RESEARCH.md` (research disabled). `local://v15-readiness-brief.md` named by CONTEXT is **not readable** from this agent (`Local file not found`); nothing here is inferred from it.
- No root `CLAUDE.md`, `AGENTS.md`, `.claude/skills/` or `.agents/skills/` exists in the repo.
- Every analog path below was confirmed tracked with `git ls-files` (assets/icon.svg, assets/banner.svg, docs/README.md, docs/SUMMARY.md, docs/setup.md, docs/user-guide.md, README.md, CHANGELOG.md, omega_prime/tests/test_public_repo.py, omega_prime/tests/test_docs.py, omega_prime/grokbot/SETUP.md, mcp.json, .cursor-plugin/plugin.json, omega_prime/scripts/grokbot-1click.sh). None is a gitignored mirror.
- **77.1 live status at mapping time:** `77.1-VERIFICATION.md` frontmatter `status: human_needed`, `score: 42/42`; `77.1-UAT.md:2` `status: testing`, summary `passed: 0`, `pending: 6` (lines 56-63). `STATE.md:29` "the live import stays UNPROVEN until the user reports it." Per CONTEXT, marketing therefore labels the Grok Bot route as a **documented, locally verified setup path**, never as working live import, unless UAT rows are recorded as passed before execution.
- Mapper wrote only this file. No source edits, builds, tests, lint, renders, commits or git writes were run. Tool-availability probes (`command -v`, `--version`, `fc-list`) were read-only.

## File Classification

| New/Modified file | Role | Data flow | Closest analog | Match quality |
|---|---|---|---|---|
| `docs/marketing.md` (new) | component (canonical doc page) | file-I/O (static, derived from canonical docs) | `docs/README.md` (landing/positioning + banner + boundaries) with `docs/user-guide.md` "Receipts"/"Claim limits" and `docs/setup.md` "Claim limits" | role-match (no marketing page exists yet) |
| `assets/banner.svg` (modify, owned) | component (static vector asset) | transform → file-I/O (render) | itself + `assets/icon.svg` (brand source) | exact |
| `assets/social-card.svg` (new) | component (static vector asset) | transform → file-I/O | `assets/banner.svg` | exact role, new dimensions |
| `assets/social-card.png` (new raster derivative) | asset (binary build output) | transform (SVG → PNG) | none in repo (no tracked PNG); tool chain below | no analog |
| `README.md` (shared owner, modify) | component (root landing) | file-I/O | itself (banner line 1, Docs section 263-276, Layout row 291) | exact |
| `docs/SUMMARY.md` (shared owner, modify) | config (GitBook navigation) | transform → file-I/O | itself (section/link list) | exact |
| `CHANGELOG.md` (shared owner, modify) | component (release notes) | file-I/O | itself, `## [Unreleased]` → `### Grok Bot import packaging` (lines 9-66) | exact |
| `docs/README.md` (optional, shared owner) | component (GitBook landing) | file-I/O | itself, "Start by audience" bullets (lines 10-24) | exact |
| `omega_prime/tests/test_public_repo.py` (recommended modify) | test | file-I/O | itself, `test_svgs_valid_and_self_contained` (lines 21-31) | exact |

## Pattern Assignments

### `docs/marketing.md` (component, file-I/O)

**Analog:** `docs/README.md` (whole file, 27 lines) for opening/banner/boundary paragraph; `docs/user-guide.md:83-104` for receipts + claim limits; `docs/setup.md:237-246` for claim limits.

**Opening + banner pattern** (`docs/README.md:1-8`): H1 first line (required by `.greptile/config.json` rule `docs-linked` and `CONTRIBUTING.md:85-86`), asset via `../assets/` because GitBook's content root is `./docs/` (`.gitbook.yaml:2`):
```markdown
# Omega Prime

![Omega Prime banner](../assets/banner.svg)

Canonical guidance for the Python agent, registry-gated tools and MCP host.
The repository includes a Grok Bot template and a repeatable local validation
route. Account/model attachment, optional services and privileged operations
still require their actual prerequisites and authorization.
```
Use a descriptive alt text on new image references (e.g. what the card shows), not just "banner": GitHub/GitBook render SVGs through `<img>`, so the Markdown alt is what assistive tech reads; inner `<title>`/`<desc>` are a second layer.

**Audience-bullet pattern** (`docs/README.md:10-24`): bold lead + links + one boundary sentence:
```markdown
- **Install and check locally:** [Setup](setup.md) and [local validation](local-validation.md).
  These distinguish exercised local effects from disabled features and missing prerequisites.
```

**Boundary paragraph pattern** (`docs/README.md:26-30`):
```markdown
Native Prime helpers
are optional in-process capabilities; the optional Node Red Team gateway is
separately started and default-off. No full upstream application import,
automatic backend launch, live-account proof or deployment is implied by these
pages. Source links and explicit proof limits govern each guide.
```

**Receipts pattern** (`docs/user-guide.md:83-96`) — demo/announcement must cite commands and exit codes (`.greptile/config.json` rule `receipts`, scope `docs/**`, `*.md`):
```markdown
## Receipts

A completion claim cites its evidence: the commands run and their
exit codes. ...
A readiness row of
`blocked` or `feature_disabled` is an honest unexecuted prerequisite, not a
pass; only `exercised` with a verified effect counts as run.
```

**Claim-limits pattern** (`docs/setup.md:237-246`), end the page with it:
```markdown
## Claim limits

- No step above attaches a live Grok account, model, or provider. A local
  model double (for example the `ScriptedModel` used by readiness and
  workflow checks) proves local composition only — never live attachment.
- Missing-key, skipped, and `not_configured` outcomes are unexecuted
  prerequisites, not successful runs.
- No time estimate is given: completion time depends on your machine and
  network, and this page does not measure it.
```

**Evidence-level vocabulary** (`docs/grok-bot-native.md:643-647`): reuse CONFIRMED / PARTIAL / UNPROVEN exactly and link `grok-bot-native.md#import-evidence-ledger` and `grok-bot-native.md#user-uat` rather than restating ledger rows.

**Suggested section map** (derived from MKT-01..03 + success criteria 1-2; headings are the planner's choice): positioning → one-pager/overview → use cases (each tagged local proof / optional capability / unproven external) → reproducible local demo (commands below) → Grok Bot route (documented, locally verified; live UAT pending) → launch announcement → short/social copy (with the exact asset paths) → claim limits. No screenshots (none exist and fakes are banned by CONTEXT).

---

### `assets/banner.svg` (component, transform → file-I/O) — modify

**Analog:** itself (`assets/banner.svg:1-38`) and brand source `assets/icon.svg:1-30`.

**Root element convention** (`banner.svg:1`, `icon.svg:1`): explicit `width`/`height` equal to `viewBox`, `role="img"` + `aria-label`, only the SVG namespace (no `xmlns:xlink`):
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="400" viewBox="0 0 1200 400" role="img" aria-label="Omega Prime banner">
```
Neither existing SVG has `<title>`/`<desc>`. Accessibility upgrade pattern (no source analog; standard SVG): first children `<title id="banner-title">…</title><desc id="banner-desc">…</desc>` and `aria-labelledby="banner-title banner-desc"` on the root. Never use `href`/`xlink` (test ban, below), so no `<use>` reuse of defs and no `<a>`.

**Brand defs to copy verbatim** (`banner.svg:2-15`; gradients identical in `icon.svg:2-11`):
```xml
<linearGradient id="edge" x1="0" y1="0" x2="1" y2="1">
  <stop offset="0" stop-color="#2dd4bf"/>
  <stop offset="1" stop-color="#8b5cf6"/>
</linearGradient>
<radialGradient id="ground" cx="0.28" cy="0.45" r="0.9">
  <stop offset="0" stop-color="#161d27"/>
  <stop offset="1" stop-color="#0b0e13"/>
</radialGradient>
<pattern id="dots" width="36" height="36" patternUnits="userSpaceOnUse">
  <circle cx="3" cy="3" r="2.2" fill="#ffffff" opacity="0.05"/>
</pattern>
```

**Hex-node mark** (`banner.svg:19-35`, scaled from `icon.svg:15-29`): outer hexagon stroke `url(#edge)` width 20, six node circles r=22 fill `#0d1117` stroke `url(#edge)` 9, inner hexagon `#2dd4bf` opacity 0.55 width 7, centre circle r=26 `url(#edge)`. Icon: outer stroke 22, nodes r=26 stroke 10, inner stroke 8, centre r=30 + white 0.25 ring (`icon.svg:28`). Frame: `rx="28"` (banner) / `rx="112"` (icon) with `stroke="#ffffff" stroke-opacity="0.08" stroke-width="2"` (`banner.svg:18`, `icon.svg:13`).

**Typography** (`banner.svg:36-39`):
```xml
<text x="400" y="168" font-family="Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif" font-size="84" font-weight="800" letter-spacing="5" fill="#f0f3f6">OMEGA PRIME</text>
<rect x="404" y="196" width="180" height="8" rx="4" fill="url(#edge)"/>
<text ... font-size="36" font-weight="400" fill="#9aa4b2">One Grok programming bot.</text>
<text ... font-size="26" font-weight="400" fill="#6b7484">Hermes loop + Omp harness + desk packs, one Python process.</text>
```
Note the quoted `'Segoe UI'` uses single quotes inside a double-quoted attribute — keep that (XML-valid).

**Palette contrast (computed, WCAG relative luminance):**

| Foreground | on `#161d27` | on `#0d1117` | on `#0b0e13` | Use |
|---|---|---|---|---|
| `#f0f3f6` title | 15.22 | 16.99 | 17.36 | any text |
| `#9aa4b2` subtitle | 6.72 | 7.50 | 7.66 | body text OK |
| `#6b7484` tertiary | **3.60** | 4.02 | 4.10 | fails 4.5:1; only "large text" at native size, and the README scales a 1200px banner down, so 26px renders well under 24px |
| `#2dd4bf` teal | 9.10 | 10.17 | 10.38 | accent text OK |
| `#8b5cf6` violet | 4.00 | 4.47 | 4.56 | decoration/gradient only, not small text |

Planner action: raise the `#6b7484` line to `#9aa4b2` (or lighter) or drop it.

**Claim check of existing copy:** "One Grok programming bot." = `pyproject.toml:4` description (also `.cursor-plugin/plugin.json` "description", generated from pyproject by `omega_prime/tooling/plugin.py:208,251,268`). "Hermes loop + Omp harness + desk packs, one Python process." is looser than `README.md:9-13` ("combines Hermes conversation-loop and oh-my-pi harness behavior **ports**") and `README.md:295-296` ("Hermes and Omp are not imported"). A source-proven rewording is allowed for the owned banner; the icon stays unchanged (CONTEXT).

---

### `assets/social-card.svg` (component, transform → file-I/O) — new

**Analog:** `assets/banner.svg` (copy root convention, defs, mark, type scale). Change only canvas (recommended 1280×640, 2:1; `[INFERENCE]` GitHub's social-preview guidance, not verified from this repo) and layout: mark left, `OMEGA PRIME` + gradient rule + `pyproject` tagline + one factual line, optional schemeless repo path `github.com/swcstudiospace/omes-bot`. A URL with `http://`/`https://` in text **fails** `test_public_repo.py:30-31`.

Allowed factual lines (each with its source): "Python MCP tool host · stdio and authenticated SSE / Streamable HTTP" (`docs/setup.md:145-150`); "46 `/omega-*` commands through one `omega_command` tool" (`omega_prime/grokbot/SETUP.md:113-120`, `CHANGELOG.md:22-28`); "Repeatable local readiness, no credentials" (`docs/local-validation.md:3-11`); "Prime families and Red Team default-off" (`README.md:146-149`). Do **not** print a fixed tool total (`README.md:130-131`: "The effective count comes from the roster/configuration intersection, not a fixed prose total"; `docs/architecture.md:17-18`), any timing, benchmark, or "works with Grok" claim.

---

### `assets/social-card.png` (raster derivative) — new, no analog

No tracked PNG exists; `.gitattributes:4` already declares `*.png binary`, and `assets/`, `docs/` are not gitignored (`git check-ignore` printed nothing, exit 1). Generate from the SVG, never hand-draw.

Rasterizers present on this machine (probed, nothing installed):

| Tool | Path / version | Notes |
|---|---|---|
| CairoSVG | `/usr/local/bin/cairosvg` 2.9.0 (system `python3` only; **not** in `.venv`) | Primary: `cairosvg assets/social-card.svg -o assets/social-card.png` (renders at the SVG's width/height; `--output-width/--output-height` to force size). Uses fontconfig. |
| Google Chrome | `/usr/bin/google-chrome` 153.0.8010.36 | Cross-check: `google-chrome --headless=new --screenshot=/tmp/card.png --window-size=1280,640 --hide-scrollbars file://$PWD/assets/social-card.svg` |
| Playwright | `/usr/local/bin/playwright`; `~/.cache/ms-playwright/chromium-1248`, `chromium_headless_shell-1248`, `firefox-1555`; module importable in `.venv` and system | Alternative headless render |
| ImageMagick | `/usr/bin/convert` 6.9.12-98 | SVG via internal MSVG (no rsvg delegate listed); lower fidelity for gradients/patterns — not primary |
| Pillow | importable in `.venv` and system `python3` | Inspect output: size, mode, file size |
| Not present | `rsvg-convert`, `inkscape`, `magick`, `resvg`, `chromium`, `firefox`, `wkhtmltoimage` | — |

Fonts: `fc-list` shows **Inter** (all weights, incl. ExtraBold 800) plus DejaVu/Liberation, so the `Inter,…` stack resolves locally. Inspection: the `read` tool decodes PNG inline for the parent's visual check (CONTEXT requires the parent to render and observe). Publishing (GitHub social-preview upload) is a manual settings action and out of scope; generating the file is in scope.

---

### `README.md` (shared owner) — modify

**Analog:** itself.
- Banner (`README.md:1`): `![Omega Prime banner](assets/banner.svg)` — improve alt text if the banner changes.
- Docs section (`README.md:263-276`) is where a one-line pointer to `docs/marketing.md` fits:
  ```markdown
  Start in [docs/](docs/README.md) for canonical setup, usage, process/state/
  security diagrams, ...
  ```
- Layout row (`README.md:291`): `| \`assets/\` | Icon and banner art |` → mention the social card.
- Grok Bot route wording to stay consistent with (`README.md:88-103`): primary Bot-driven setup, secondary Cursor Teams/Enterprise, "Live import on a Grok Bot or Cursor account is unproven until you run the [user UAT](docs/grok-bot-native.md#user-uat) rows."
- Constraint: `test_readme_links_resolve` (`test_public_repo.py:13-18`) checks every relative link/image target exists (regex `\[[^\]]*\]\(([^)#]+?)(?:#[^)]*)?\)`, URLs with `://` skipped). Any new README link to `docs/marketing.md` or `assets/social-card.png` must exist in the same change.
- Constraint: `test_setup_prompts_run_the_declared_stdio_command` (`test_docs.py:28-44`) requires README to contain `bash /workspace/omega-prime/omega_prime/scripts/grokbot-1click.sh --bootstrap --transport stdio` verbatim (from `mcp.json`). Do not reword README line 96.

### `docs/SUMMARY.md` (shared owner) — modify

**Analog:** itself (`docs/SUMMARY.md:1-31`), sections `## Install` / `## Use` / `## Build`, format `- [Title](page.md)`. Add exactly one line for `marketing.md` (e.g. under `## Use` next to `[FAQ](faq.md)`, or a new `## Share` section). `test_summary_links_resolve_exactly_once` (`test_docs.py:16-25`) fails if any link is duplicated, missing, or if any `docs/**/*.md` is not listed — so `docs/marketing.md` and its SUMMARY line must land together.

### `CHANGELOG.md` (shared owner) — modify

**Analog:** `CHANGELOG.md:7-66`. New `### Repository marketing` under `## [Unreleased]` (before `### Grok Bot import packaging`, line 9), with `#### Added` bullets and a closing limits paragraph copying `CHANGELOG.md:60-66`:
```markdown
Live import on a Grok Bot or Cursor account is unproven: ...
remain user UAT. Local proof is the generator `--check`/`--verify`, real
stdio through the declared command and a loopback bearer host. No
marketplace listing, hosted deployment or image publication.
```

### `docs/README.md` (optional) — modify

Add one audience bullet linking `marketing.md` following `docs/README.md:10-24`. Its banner reference `../assets/banner.svg` (`docs/README.md:3`) is the precedent for docs pages; whether GitBook resolves assets outside its `./docs/` root is unverified `[INFERENCE]` — do not claim GitBook rendering.

### `omega_prime/tests/test_public_repo.py` (recommended) — modify

**Analog:** itself, lines 21-31. The only SVG constraint covers a hard-coded tuple; a new `assets/social-card.svg` is unchecked unless added:
```python
def test_svgs_valid_and_self_contained() -> None:
    for name in ("assets/icon.svg", "assets/banner.svg"):
        raw = (ROOT / name).read_text(encoding="utf-8")
        root = minidom.parseString(raw).documentElement
        assert root is not None
        assert root.tagName == "svg"
        assert root.getAttribute("width") and root.getAttribute("height")
        assert not root.getElementsByTagName("script")
        assert "href" not in raw and "xlink" not in raw
        without_ns = raw.replace('xmlns="http://www.w3.org/2000/svg"', "")
        assert "http://" not in without_ns and "https://" not in without_ns
```
Extending the tuple to `assets/social-card.svg` is one-line and runs in CI's `docs` job (`.github/workflows/ci.yml:208-225`: `python3 -m pytest omega_prime/tests/test_docs.py omega_prime/tests/test_public_repo.py -q`, then `catalog --check`, `plugin --check`). Any accessibility assertion added must test consumer-visible behavior (e.g. `<title>` present) and not pin copy (`CONTRIBUTING.md:63-67`).

## Shared Patterns

### Self-contained SVG rules (all assets/*.svg)
**Source:** `omega_prime/tests/test_public_repo.py:21-31`
Well-formed XML (minidom); root `<svg>` with `width` and `height`; no `<script>`; the substrings `href` and `xlink` appear nowhere (including comments, text and ids); no `http://`/`https://` except the SVG namespace; escape `&` as `&amp;`; no external fonts/images (font stacks only).

### Docs navigation and headings
**Source:** `.greptile/config.json` rule `docs-linked` + `omega_prime/tests/test_docs.py:16-25` + `CONTRIBUTING.md:85-87`
Every `docs/*.md` listed exactly once in `docs/SUMMARY.md`, starts with a top-level `#` heading, uses resolving links/assets. Do not hand-edit `docs/tool-catalog.md` or the generated plugin set (`.cursor-plugin/`, `mcp.json`, `omega_prime/grokbot/plugin/`) — `plugin.json`'s description is generated from `pyproject.toml:4`.

### Receipts and no-secrets
**Source:** `.greptile/config.json` rules `receipts` ("Completion claims in docs, summaries, and PR text must cite the command and its exit code. Flag unproven claims.", scope `docs/**`, `*.md`, severity medium) and `no-secrets` (no credential-shaped literals in docs, severity high); `docs/architecture.md:249-250` "A completion claim without a command and an exit code is not a claim." Example tokens must be placeholders like `$ADMIN_TOKEN` (pattern `docs/grok-bot-native.md:446-448`), never values; marketing copy should not include token examples at all.

### Claim derivation ledger (canonical sources)

| Claim usable in collateral | Level | Source |
|---|---|---|
| One Python process, one loop, one registry; no sidecar | local source | `docs/architecture.md:3-5,22-27`; `README.md:17-22` |
| Hermes and oh-my-pi **behavior ports**; not imported; not a whole upstream port | local source | `README.md:9-13,295-296` |
| MCP tool host: stdio, plus authenticated legacy SSE and Streamable HTTP `/mcp` with bearer auth | local proof | `docs/setup.md:145-150`; `docs/local-validation.md:134-148` |
| `oneclick --self-test` conformance smoke: 22 pass, 0 fail, 0 skip, shutdown 0 | local proof (documented number) | `docs/local-validation.md:145-147` |
| Isolated readiness accounts for every roster tool: `exercised` / `feature_disabled` / `blocked` / `failed`; no credentials | local proof | `docs/local-validation.md:3-11,13-31`; `docs/setup.md:119-130` |
| `/omega-onboard`, `/omega-desk`, `/omega-python-clean`, `/omega-connectors` run for semantic results in readiness (ScriptedModel double for cron/desk) | local proof | `docs/local-validation.md:162-172` |
| 46 `/omega-*` commands (12 curated + one per skill/routine) routed through `omega_command`; collision suffix rule | local proof | `omega_prime/grokbot/SETUP.md:111-120`; `CHANGELOG.md:22-28`; `docs/grok-bot-native.md:649-653` |
| `/omega-python` runs ruff and compileall; `/omega-connectors` names missing env vars without asking for a secret | local source | `CHANGELOG.md:99` |
| Approval-gated tools wait for a grant; hash-chained audit log | local proof | `docs/user-guide.md:36-58`; `docs/grok-bot-native.md:403-406`; `docs/local-validation.md:134-145` |
| Secrets never in chat; private env or 0600 token file | policy | `docs/setup.md:75-78`; `omega_prime/grokbot/SETUP.md:42` |
| Prime families and Red Team gateway default-off; opt-in per family | optional capability | `README.md:146-153`; `docs/user-guide.md:60-67` |
| Red Team backend separately started, never auto-launched | optional capability | `README.md:73-80`; `docs/README.md:26-28` |
| Grok Bot route: Bot clones to `/workspace/omega-prime`, `--bootstrap-only`, custom Command server | documented, locally verified; live **UNPROVEN** | `omega_prime/grokbot/SETUP.md:17-84`; `docs/grok-bot-native.md:695` (row 1) |
| Cursor Teams/Enterprise Import from Repo | secondary; PARTIAL/UNPROVEN | `omega_prime/grokbot/SETUP.md:86-109`; `docs/grok-bot-native.md:667-673,696-698` |
| Remote HTTPS alternative | documented; UNPROVEN live | `docs/grok-bot-native.md:249+,700` |
| No public marketplace listing, hosted HTTPS deployment or published image | boundary | `docs/grok-bot-native.md:681-683`; `CHANGELOG.md:60-66` |
| No completion-time claims | boundary | `docs/setup.md:244-246`; `omega_prime/grokbot/SETUP.md:8-9` |

**Do not use:** the 93 s cold-bootstrap figure (`STATE.md:98`, a planning receipt, contradicts "not measured"); any fixed tool total (the "real 154-tool run" in `docs/local-validation.md` describes one run, not a product count); live Grok/provider/account attachment; benchmarks; screenshots; the 77.1 proof receipts under `/tmp/` (not repository artifacts).

### Maintained demo commands (actual, with definitions)

| Command | Defined in | Documented in |
|---|---|---|
| `git clone --recurse-submodules https://github.com/swcstudiospace/omes-bot.git omega-prime` | — | `README.md:55-60`; `docs/setup.md:32-57` |
| `python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'` | `pyproject.toml` | `README.md:57-58` |
| `.venv/bin/python -m omega_prime.setup_check --root .` (exit 0 only when every required check passes; skips are not failures) | `omega_prime/setup_check.py:1-7,221-226` | `docs/setup.md:104-118` |
| `.venv/bin/python -m omega_prime.tooling.catalog --check` | `omega_prime/tooling/catalog.py:167-175` | `docs/setup.md:107` |
| `.venv/bin/python -m omega_prime.tooling.plugin --check` / `--write` / `--verify [--json]` (`--json` only with `--verify`) | `omega_prime/tooling/plugin.py:917-937` | `docs/setup.md:108,115-118`; `CHANGELOG.md:29-33` |
| `bash omega_prime/scripts/assemble-prompts.sh --check` | `omega_prime/scripts/assemble-prompts.sh:1-25` | `README.md:119`; `docs/architecture.md:245` |
| `.venv/bin/python -m omega_prime.grokbot.readiness --root . --json --output readiness-local.json [--include-prime]` (exit 0 only when `ok` true; 1 failure; 2 usage) | `omega_prime/grokbot/readiness.py:211-217` | `docs/local-validation.md:13-31` |
| `.venv/bin/python -m omega_prime.grokbot.doctor --host 127.0.0.1 --json [--auth --strict]` | `omega_prime/grokbot/doctor.py:651-675` | `docs/setup.md:131-138` |
| `OMEGA_PRIME_WORK_ROOT="$PWD" .venv/bin/python -m omega_prime.grokbot.oneclick --dry-run` / `--self-test` | `omega_prime/grokbot/oneclick.py:852-936` | `README.md:120`; `docs/setup.md:186-187` |
| `bash omega_prime/scripts/grokbot-1click.sh --bootstrap-only` | `omega_prime/scripts/grokbot-1click.sh:5-8,90-104` | `omega_prime/grokbot/SETUP.md:192`; `docs/setup.md:163-181` |
| `bash omega_prime/scripts/grokbot-1click.sh --bootstrap --transport stdio` (the `mcp.json` command) | `grokbot-1click.sh:5-6`; `mcp.json:3-12` | `docs/grok-bot-native.md:40-41` |
| `bash omega_prime/scripts/grokbot-1click.sh --verify --json` (exit classes 10/11/12/13, one JSON line on stderr) | `grokbot-1click.sh:8,34-41,105-114` | `docs/grok-bot-native.md:146-161` |
| `.venv/bin/python -m omega_prime.mcp_server --root .` | `omega_prime/mcp_server.py` | `README.md:137`; `docs/setup.md:152-154` |
| Whole-message `/omega-help`, `/omega-code-review src/app.py` via `omega_command` | `omega_prime/grokbot/plugin/commands/omega-*.md` (46 files, counted) | `omega_prime/grokbot/SETUP.md:111-120` |

Demo narrative should run the no-credential sequence (clone → install → `setup_check` → `catalog --check` → `plugin --check` → `readiness --json --output` → `grokbot-1click.sh --verify --json`) and state each expected exit code; the Grok Bot leg is the SETUP.md §1 prompt, labelled with UAT row 1 still pending.

## No Analog Found

| File | Role | Data flow | Reason |
|---|---|---|---|
| `assets/social-card.png` | asset (binary) | transform | No tracked raster asset exists; use CairoSVG (primary) / headless Chrome or Playwright (cross-check) from the table above, inspect with Pillow and the `read` image decoder |

## Metadata

**Analog search scope:** `assets/`, `docs/`, `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `.greptile/config.json`, `.gitbook.yaml`, `.gitattributes`, `.github/workflows/ci.yml`, `mcp.json`, `.cursor-plugin/`, `pyproject.toml`, `omega_prime/tests/` (svg/SUMMARY/assets grep), `omega_prime/grokbot/{SETUP.md,readiness.py,oneclick.py,doctor.py}`, `omega_prime/tooling/{plugin.py,catalog.py}`, `omega_prime/setup_check.py`, `omega_prime/scripts/`, `.planning/{ROADMAP,REQUIREMENTS,STATE}.md`, `.planning/phases/77.1-*/77.1-{UAT,VERIFICATION}.md`.
**Files scanned:** ~35
**Pattern extraction date:** 2026-10-12
