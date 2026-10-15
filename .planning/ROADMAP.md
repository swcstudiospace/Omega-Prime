# Roadmap: Omega Prime

## Milestones

- ✅ **v1 One Omega Prime agent** — Phases 1-12 (shipped 2026-10-03)
- ✅ **v2 Enterprise hardening** — Phases 13-16 (shipped 2026-10-03)
- ✅ **v3 Grok Bot** — Phases 17-20 (shipped 2026-10-03)
- ✅ **v4 Third-party integrations** — Phases 21-25 (shipped 2026-10-03)
- ✅ **v5 Desk packs** — Phases 26-33 (shipped 2026-10-03)
- ✅ **v6 Grok ship** — Phases 34-38 (shipped 2026-10-03)
- ✅ **v7 Substrate surface** — Phases 39-43 (shipped 2026-10-03)
- ✅ **v8 Public launch** — Phases 44-45 (shipped 2026-10-03)
- ✅ **v9 SOTA upgrade** — Phases 46-52 (shipped 2026-10-08)
- ✅ **v10 Prime merge** — Phases 53-61 (shipped 2026-10-09; audit passed)
- ✅ **v11 Grok Bot native** — Phases 62-63 (shipped 2026-10-09; audit passed)
- ✅ **v12 Programming Desk merge** — Phases 64-67 (shipped 2026-10-09; audit passed)
- ✅ **v13 Grok Bot specialisation** — Phases 68-70 (shipped 2026-10-09; audit passed)
- ✅ **v14 Boundary alignment** — Phases 71-74 (shipped 2026-10-10; audit passed)
- ✅ **v15 Integrated Readiness and Repository Delivery** — Phases 75–78 (shipped 2026-10-12; audit 21/22, GROK-03 live import UAT deferred by the user)
- ✅ **v16 Enterprise Hardening and Continuous Verification** — Phases 79–84 (shipped 2026-10-13; audit PASS 13/13, sidecar ships BLOCKED pending signed RoE)
- ✅ **v17 XML Prompt Standard** — Phases 85–88 (shipped 2026-10-14; every first-party prompt wrapped in XML tags like Programming Desk, enforced by a gate)
- ✅ **v18 Operator posture** - Phases 89-95 (shipped 2026-10-14). Seven upgrades accepted when the operator said to continue.
- ✅ **v19 Installer bindings** - Phase 96 (shipped 2026-10-15). A fresh install binds its own Linear and Notion, treats the existing gateways as optional, and fails setup when `prime-agent` is empty.
- [ ] **v20 Grok Bot launch kit** - Phases 97-100

Archives: `milestones/v1-ROADMAP.md` through `milestones/v16-ROADMAP.md`
with phase directories under `milestones/v1-phases/` through
`milestones/v16-phases/`.

## Phases

### 🚧 v20 Grok Bot launch kit (In progress)

**Milestone Goal:** Ship the public-facing collateral for the Grok Bot distribution (listing folder, icons and banner, install and follow-up prompts, Grok-Bot-first root README). Live Grok Bot import stays UNPROVEN until the user runs the 77.1 UAT rows.

- [x] Phase 97: Brand assets (TBD) - Icon PNG set and listing banner exported from the existing brand (completed 2026-10-15)
- [x] Phase 98: Listing package and prompts (TBD) - Listing README, CHANGELOG, LICENSE notice, install prompt and follow-up prompts, all test-linked (completed 2026-10-15)
- [x] Phase 99: Root documents (TBD) - Grok-Bot-first root README, developer guide moved to `docs/`, CHANGELOG v20 entry, docs index (completed 2026-10-15)
- [x] Phase 100: Verification gate (TBD) - Repository gates and claim discipline pass on the changed tree (completed 2026-10-15)

<details>
<summary>✅ v15 Integrated Readiness and Repository Delivery (Phases 75–78) — SHIPPED 2026-10-12</summary>

- [x] Phase 75: Integrated local readiness (11/11 plans) — completed 2026-10-10
- [x] Phase 76: Clean CI/CD (5/5 plans) — completed 2026-10-11
- [x] Phase 77: Maintained docs and knowledge base (7/7 plans) — completed 2026-10-12
- [ ] Phase 77.1: Grok Bot import packaging (INSERTED) (8/8 plans) — executed; live import UAT deferred by the user (`/gsd:verify-work 77.1`)
- [x] Phase 78: Repository marketing (2/2 plans) — completed 2026-10-12

</details>

<details>
<summary>✅ v16 Enterprise Hardening and Continuous Verification (Phases 79–84) — SHIPPED 2026-10-13</summary>

Seven evidence-grounded updates staged from the 2026-10-13 ultrathink run (Linear SPE-10591..10598, graph `ut-mv5xhkf8-8abb3c15`); full archive in `milestones/v16-ROADMAP.md` with phase directories under `milestones/v16-phases/`.

- [x] Phase 79: Purple-team sidecar hardening (3/3 plans) — completed 2026-10-13; sidecar ships BLOCKED pending signed RoE
- [x] Phase 80: Protected release pipeline (2/2 plans) — completed 2026-10-13; `grokbot-image-publish` environment live
- [x] Phase 81: Telemetry export and SLO alerting (2/2 plans) — completed 2026-10-13
- [x] Phase 82: Token rotation and usage quotas (2/2 plans) — completed 2026-10-13
- [x] Phase 83: Sandboxed execution verification (2/2 plans) — completed 2026-10-13
- [x] Phase 84: Live evals and regression tracking (1/1 plan) — completed 2026-10-13

</details>

<details>
<summary>✅ v17 XML Prompt Standard (Phases 85–88) — SHIPPED 2026-10-14</summary>

Every first-party prompt in the application is wrapped in XML tags like
Programming Desk (`prompts/*.xml` + `_shared/core-directives.xml` there; the
same seat-prompt shape here), and a CI gate keeps it that way. Requirements
PROM-01..PROM-08 in `milestones/v17-REQUIREMENTS.md`.

- [x] Phase 85: XML prompt standard and gate (1/1 plan) — completed 2026-10-14
- [x] Phase 86: Runtime-built prompts in XML (1/1 plan) — completed 2026-10-14
- [x] Phase 87: Static prompt documents in XML (1/1 plan) — completed 2026-10-14
- [x] Phase 88: Grok Bot import readiness re-proof (1/1 plan) — completed 2026-10-14

</details>

<details>
<summary>✅ v18 Operator posture (Phases 89-95) - shipped 2026-10-14</summary>

Accepted set from STATE.md after the operator said to continue. Requirements: milestones/v18-REQUIREMENTS.md.

- [x] Phase 89: Purple-cycle schedule (1/1 plan) - completed 2026-10-14
- [x] Phase 90: Cycle and sync backlog on durable_status (1/1 plan) - completed 2026-10-14
- [x] Phase 91: Grant conflicts slash command (1/1 plan) - completed 2026-10-14
- [x] Phase 92: Findings JSONL export (1/1 plan) - completed 2026-10-14
- [x] Phase 93: Purple-cycle lock and deadline (1/1 plan) - completed 2026-10-14
- [x] Phase 94: Readiness slash command (1/1 plan) - completed 2026-10-14
- [x] Phase 95: Audit-chain slash command (1/1 plan) - completed 2026-10-14

</details>

<details>
<summary>✅ v19 Installer bindings (Phase 96) - shipped 2026-10-15</summary>

Requirements: milestones/v19-REQUIREMENTS.md. Phase 77.1 live UAT stays user-owned.

- [x] Phase 96: Per-install Linear, Notion, and optional gateways (1/1 plan) - completed 2026-10-15

</details>

## Phase Details

### Phase 97: Brand assets

**Goal**: The Grok Bot listing has an icon set and a banner rendered from the existing brand, with no new mark designed
**Depends on**: Nothing (first phase of v20)
**Requirements**: BRD-01, BRD-02
**Success Criteria** (what must be TRUE):

  1. PNG icons at 16, 32, 64, 128, 192, 256 and 512 px exist in `omega_prime/grokbot/listing/icons/`, each rendered from `assets/icon.svg` through `omega_prime/scripts/render_raster.py`
  2. The recorded SHA-256 of `assets/icon.svg` matches the file on disk, and a stale icon raster fails the test suite
  3. `omega_prime/grokbot/listing/banner.svg` and its PNG derivative show the listing title and tagline with the same contrast as the Phase 78 banner
  4. The banner text names only verified capabilities and their evidence level, with no live-Grok claim

**Plans**: 2/2 plans complete
Plans:

- [x] 97-01-PLAN.md — icon rasters (wave 1, 2 tasks)
- [x] 97-02-PLAN.md — listing banner and PNG (wave 2, 3 tasks)

### Phase 98: Listing package and prompts

**Goal**: A Grok Bot operator can read the listing, paste the install prompt and run the follow-up prompts, and every claim shows its evidence level and source
**Depends on**: Phase 97
**Requirements**: LST-01, LST-02, LST-03, INS-01, INS-02, INS-03
**Success Criteria** (what must be TRUE):

  1. `omega_prime/grokbot/listing/README.md` links to the install prompt and the follow-up prompts rather than duplicating them, and each claim it makes cites its source file and evidence level
  2. `omega_prime/grokbot/listing/prompts/install.md` matches the install task block in `omega_prime/grokbot/SETUP.md` section 1 verbatim, and its stdio command equals the one declared in `mcp.json`; drift fails a test
  3. Each of the five follow-up prompts in `prompts/follow-up/` names the exact `/omega-*` command it sends and states what it does not prove; a prompt naming a command with no file in `omega_prime/grokbot/plugin/commands/`, or a prompt missing from the index, fails a test
  4. The listing `CHANGELOG.md` has a v20 entry, and the listing `LICENSE.md` names AGPL-3.0-only and points to the unchanged root `LICENSE`

**Plans**: TBD (1-2 expected)

### Phase 99: Root documents

**Goal**: A visitor to the repository root meets the Grok Bot product first, and the developer guide survives intact under `docs/`
**Depends on**: Phase 98 (root README links to the listing prompts)
**Requirements**: ROOT-01, ROOT-02, ROOT-03
**Success Criteria** (what must be TRUE):

  1. The root `README.md` opens Grok-Bot-first with the install prompt link, the follow-up link, the license badge, and the exact stdio line `bash /workspace/omega-prime/omega_prime/scripts/grokbot-1click.sh --bootstrap --transport stdio`, so `test_setup_prompts_run_the_declared_stdio_command` still passes
  2. Every section of the old root README (Architecture, Quickstart, Configuration, Build, Layout, Contributing) is present in `docs/developer-guide.md` with working relative links, and no content is dropped
  3. Root `CHANGELOG.md` has a v20 entry under Unreleased with existing history untouched, and the root `LICENSE` is byte-for-byte unchanged
  4. `docs/SUMMARY.md` lists the developer guide exactly once, and every new page resolves through the existing link test

**Plans:** 2 plans

Plans:
- [ ] 99-01-PLAN.md — Developer guide move, SUMMARY and docs index, re-pointed docs links
- [ ] 99-02-PLAN.md — Grok-Bot-first root README, CHANGELOG v20 entry, outside link repair

### Phase 100: Verification gate

**Goal**: The v20 kit passes the repository's own gates and makes no claim the evidence does not support
**Depends on**: Phase 99
**Requirements**: QA-01, QA-02
**Success Criteria** (what must be TRUE):

  1. `ruff check`, `ruff format --check`, `mypy omega_prime/`, the full pytest suite, `omega_prime.tooling.catalog --check` and `omega_prime.tooling.plugin --check` each exit 0 on the changed tree
  2. No new or changed file claims a live Grok Bot connection, a published icon, a marketplace listing, a measured number or a screenshot
  3. Each factual claim in the new and changed docs cites a canonical file, and each route that has not been executed is labelled UNPROVEN

**Plans**: TBD (1-2 expected)
