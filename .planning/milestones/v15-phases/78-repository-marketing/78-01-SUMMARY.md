---
phase: 78-repository-marketing
plan: 01
subsystem: repository-collateral
tags: [marketing, docs, positioning, demo, launch, social-copy]
requires:
  - phase: 78-repository-marketing
    provides: 78-02 assets (banner wording and contrast, social card SVG and PNG, the recorded CairoSVG command)
  - phase: 77.1-grok-bot-import-packaging
    provides: the Bot-driven setup prompt, the import evidence ledger and the six UNPROVEN user UAT rows
provides:
  - "docs/marketing.md: positioning, one-pager, eight evidence-tagged use cases, a seven-step no-credential demo with exit-code contracts, the Grok Bot route (UNPROVEN, UAT links), launch announcement, short/social copy, Receipts and Claim limits"
  - "docs/SUMMARY.md lists the page once; docs/README.md has an audience bullet; README.md points at it and names the social card in the assets/ row; CHANGELOG.md has a Repository marketing section with a limits paragraph"
affects: []
actuals:
  tasks: 3
  commits: 1
plan_head_before: 0067526
plan_head_after: 89ac138
commits: 1
tech-stack:
  added: []
  patterns: [evidence-level tag on every use case, link the ledger instead of restating it, demo steps carry exit-code contracts instead of timings]
key-files:
  created: ["docs/marketing.md"]
  modified: ["docs/SUMMARY.md", "docs/README.md", "README.md", "CHANGELOG.md"]
key-decisions:
  - "Evidence levels are local proof, optional capability and unproven external; the Bot and Cursor routes are unproven external with links to the UAT rows"
  - "The page makes no completion claim of its own; the demo gives each command's exit-code contract so readers collect their own receipts"
  - "46 is stated only as the /omega-* command count, never as a tool total"
patterns-established:
  - "Marketing copy cites a canonical file for every factual claim and never restates the evidence ledger"
requirements-completed: [MKT-01, MKT-02]
coverage:
  - id: 78-01-page-and-navigation
    description: docs/marketing.md starts with an H1, is listed exactly once in docs/SUMMARY.md, and every relative link resolves
    requirement: MKT-01
    verification:
      - kind: unit
        ref: omega_prime/tests/test_docs.py#test_summary_links_resolve_exactly_once
        status: pass
      - kind: unit
        ref: omega_prime/tests/test_public_repo.py#test_readme_links_resolve
        status: pass
      - kind: other
        ref: "grep -c -F '[Marketing](marketing.md)' docs/SUMMARY.md: 1"
        status: pass
    human_judgment: false
  - id: 78-01-demo
    description: The seven demo commands run as written and meet their stated exit-code contracts
    requirement: MKT-02
    verification:
      - kind: other
        ref: "parent demo run in a fresh recursive clone of v15-milestone-close at 0067526 with HOME=/tmp/v15-78-demo-1791836909/home: venv exit 0, pip install -e '.[dev]' exit 0, setup_check exit 0, catalog --check exit 0, plugin --check exit 0 (50 files), readiness exit 0 ok (50 exercised / 67 blocked / 48 feature_disabled), grokbot-1click.sh --verify --json exit 0 with stdio '/omega-help answered (46 commands)' and the loopback bearer host answering at /mcp; logs /tmp/v15-78-demo-1791836909/logs/"
        status: pass
    human_judgment: false
  - id: 78-01-claim-discipline
    description: No banned claim language, no live-import claim, no credential value
    requirement: MKT-01
    verification:
      - kind: other
        ref: "grep -c -i -E '93 ?s|\\bseconds?\\b|benchmark|live import works|works with Grok' docs/marketing.md: 0"
        status: pass
      - kind: other
        ref: "secret_guard.assert_no_secrets per line: the only hits in docs/marketing.md are the prose phrases 'bearer host' and 'bearer auth' (the detector false positive recorded as 77.1-SECURITY N-02); no credential value"
        status: pass
    human_judgment: true
---

# Phase 78 Plan 01: marketing page and docs integration

**docs/marketing.md delivers positioning, a one-pager, evidence-tagged use cases, a reproducible no-credential demo, a launch announcement and social copy. Every claim names its source, the Grok Bot route stays UNPROVEN with links to the user UAT rows, and the README, docs landing page, SUMMARY and changelog point at it.**

## What changed

- **docs/marketing.md (new):** Evidence levels; Positioning (the README's behavior-ports wording); One-pager table; eight use cases (four local proof, two optional capability, two unproven external); Reproducible local demo (clone, venv and install, `setup_check`, `tooling.catalog --check`, `tooling.plugin --check`, the readiness profile, `grokbot-1click.sh --verify --json`, each with its exit-code contract and source); Grok Bot route with the canonical SETUP.md prompt; Launch announcement; Short and social copy with the asset table and the raster command; Receipts; Claim limits.
- **docs/SUMMARY.md:** `- [Marketing](marketing.md)` under Use.
- **docs/README.md:** a "Present or announce the project" audience bullet.
- **README.md:** a pointer to `docs/marketing.md` in the Docs section; the assets/ row names the social card. The stdio launch line is unchanged.
- **CHANGELOG.md:** `### Repository marketing` under Unreleased, with Added bullets for the page and the 78-02 assets and a limits paragraph.

## Exercised evidence (parent-run)

- Demo in a fresh recursive `file://` clone of this branch at `0067526`, isolated HOME: every step exited 0. `setup_check` passed; catalog up to date; plugin set 50 files up to date; readiness `ok` true with 50 exercised, 67 blocked and 48 feature-disabled rows; `--verify --json` printed `{"entries": {"https": {... "ok": true}, "stdio": {"detail": "initialize and /omega-help answered (46 commands)", "ok": true}}, "failed": [], "ok": true}`. The GitHub URL in step 1 was cloned for real earlier (77.1, `/tmp/v15-771-ghclone-1791816624/`).
- `.venv/bin/python -m pytest -q -p no:cacheprovider omega_prime/tests/test_docs.py omega_prime/tests/test_public_repo.py`: exit 0, 4 passed.
- Plan assertions: SUMMARY listing 1; banned-claim grep 0; `docs/marketing.md` referenced in README.md, docs/README.md and CHANGELOG.md; H1 first line.
- `python -m omega_prime.tooling.catalog --check`: exit 0.

## Notes

- The secret detector's per-line hits in the new page are prose ("bearer host", "bearer auth"), the same false-positive class recorded in 77.1-SECURITY.md N-02; the CHANGELOG and README hits predate this phase. No credential value appears.

## Task Commits

1. **Task 1: Tracer: marketing page skeleton wired into navigation**: `89ac138`
2. **Task 2: Expand to full MKT-01 and MKT-02 coverage**: `89ac138` (same commit)
3. **Task 3: Single-owner integration (README, docs landing, changelog)**: `89ac138` (same commit)

## Files

- `docs/marketing.md`
- `docs/SUMMARY.md`
- `docs/README.md`
- `README.md`
- `CHANGELOG.md`
