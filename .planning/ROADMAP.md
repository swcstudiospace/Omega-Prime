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
- **v15 Integrated Readiness and Repository Delivery** — Phases 75–78 (approved; native GSD execution)

Archives: `milestones/v1-ROADMAP.md` through `milestones/v14-ROADMAP.md`
with phase directories under `milestones/v1-phases/` through
`milestones/v14-phases/`.

## v15 Integrated Readiness and Repository Delivery

All 22 approved requirements remain in scope: the original 21 plus GROK-03, which the user approved on 2026-10-12 as inserted Phase 77.1 (Grok Bot import packaging) ahead of marketing. Latest delivery decisions authorize a pinned T3MP3ST submodule and verified repository publication through the required shipping/review flow. The approved gitlink is included in local reviewed source commit `a3dcadba4d95b9a7376297f839519801ba7c15e3`; remote publication remains pending. Image publication, deployment, tags and live Grok/provider proof are not implied. Native GSD workers and parent-owned verification continue; `.swarm/` untouched and no signedSwarm approval claimed. Prior archives and dormant seeds are not reopened.

### Phase 75: Integrated local readiness

**Goal:** Run a repeatable, isolated local dry run that accounts for the complete roster and proves real tool and workflow effects without Grok credentials.
**Depends on:** v14
**Requirements:** LOCAL-01, LOCAL-02, LOCAL-03, FLOW-01, FLOW-02, GROK-01, GROK-02
**Success Criteria:**
1. One documented command isolates install/home/work/state and inherited credentials, reports every roster tool as exercised, feature-disabled or blocked by a named prerequisite, and fails on actual required-scenario failure or missing accounting.
2. Real local file, memory, receipt and lead transactions run through production dispatch; a warm runtime sees an independently submitted intake order and preserves concurrent persisted changes.
3. Coding, lead and slash workflows produce checked artifacts; enabled cron/learning/messaging/durable compositions are exercised with their actual persistence boundaries stated.
4. SSE and Streamable HTTP both exercise local effects, authentication, approval denial/grant/revocation and clean shutdown with sanitized evidence.
5. Template, roster and setup agree; semantic doctor failures remain failures and live provider/Grok/device/browser prerequisites are explicitly unproven.

**Plans:** 11 bounded plans — ten disjoint implementation slices (01–07, 09–11), then evidence-backed documentation (08). Independent pre-execution checks passed, including the observed mobile lint XML-count repair; prospective threat closure is not an implementation security pass.

### Phase 76: Clean CI/CD

**Goal:** Preserve supported quality/security gates while making integration evidence and validated release artifacts reproducible and safe.
**Depends on:** Phase 75
**Requirements:** CI-01, CI-02, CI-03
**Success Criteria:**
1. Python 3.12–3.14 verification/lint/type matrices and independent dependency/native/license gates remain intact, with explicit minimum permissions, concurrency and bounded jobs.
2. CI runs the maintained isolated readiness command and retains sanitized success/failure evidence alongside existing identity, receipt and JUnit evidence.
3. Hardened container/runtime/SBOM/critical-scan validation remains blocking; publication is default-off, manual and tied to validated bytes and same-commit gates.
4. Release and rollback instructions distinguish validation from actual publishing/deployment; neither is executed by this milestone.

**Plans:** Five checked plans completed with parent source commits/SUMMARYs and actual readiness/retention/context/both-transport image/SPDX/scan/archive evidence. Independent audits found a strict JSON ambiguity gap, now repaired with CLI after-proof; fresh complete source/image seal and final review/regression/canonical acceptance pending. CI02 unclassified and descriptorless prohibition flags remain explicit; no phase requirement closed early.

### Phase 77: Maintained docs and knowledge base

**Goal:** Publish source-verified canonical guidance beside one complete, refreshable historical Greptile mirror.
**Depends on:** Phase 76
**Requirements:** KB-01, KB-02, KB-03, DOC-01, DOC-02, DOC-03, DOC-04, DOC-05
**Success Criteria:**
1. All 21 exposed Greptile documents including index are imported with repository, namespace, version, fetch and completeness provenance.
2. Refresh uses namespaceId, rejects incomplete/truncated retrieval and preserves the previous complete mirror; unchanged bodies do not cause timestamp-only changes.
3. README, contribution/setup and existing docs contain current URLs and exercised claims, with historical synthesized material clearly separate from canonical documentation.
4. Architecture, execution/state/security diagrams, design patterns/extensions and runnable usage/configuration/workflows link to current source and honest prerequisites.
5. Repository/GitBook navigation, generated catalog, internal links and assets are consistent.

**Plans:** Seven plans across three dependency waves.
- [x] 77-01 — Strict preserving namespaceId refresh client (wave1)
- [x] 77-02 — Complete primary mirror and Greptile guides (wave2)
- [x] 77-03 — Source-linked architecture, state/security and extension patterns (wave1)
- [x] 77-04 — Current onboarding, usage and optional Red Team guidance (wave1)
- [x] 77-05 — Integration, substrate, host and migration contracts (wave1)
- [x] 77-06 — Root, navigation and changelog integration (wave3)
- [x] 77-07 — Generated inventory and behavioral consumer boundaries (wave1)

### Phase 77.1: Grok Bot import packaging (INSERTED)

**Goal:** Make the pushed GitHub repository importable into Grok Bot so the default Bot can set up Omega Prime with the Python core attached and reachable through generated slash commands.
**Requirements:** GROK-03
**Depends on:** Phase 77
**Success Criteria:**
1. The repository root carries the import manifest set that the confirmed Grok Bot import path reads, declaring the checked-in skills and the Omega Prime MCP host: a stdio command run on the Bot's own cloud computer by default and a documented public HTTPS (bearer) alternative. No secret is embedded, and each platform fact is recorded as confirmed or assumed with its source.
2. One `/omega-<name>` command per checked-in skill and routine is generated from those files through the existing command catalog and `omega_command`; collisions and reserved names are resolved by a deterministic rule or rejected at generation, and a `--check` mode fails on drift.
3. One idempotent bootstrap/verify entry point prepares pinned dependencies, validates the catalog, starts or health-checks the stdio host and reports distinct missing-prerequisite, install, catalog-drift and host-unreachable failures; a second run is safe.
4. Setup and usage docs describe the GitHub → Grok Bot import flow and the prompting contract; template, manifest, roster and catalog stay consistent; live import on the user's Bot stays explicitly unproven until the user confirms it.
5. Local proof covers manifest validation, the generated-catalog check, and a real stdio initialize plus tool call through the declared command from a fresh-clone layout.

**Plans:** 8/8 plans executed

Plans:
**Wave 1**
- [x] 77.1-01-PLAN.md
- [x] 77.1-02-PLAN.md
- [x] 77.1-03-PLAN.md
- [x] 77.1-04-PLAN.md

**Wave 2** *(blocked on Wave 1 completion)*
- [x] 77.1-05-PLAN.md

**Wave 3** *(blocked on Wave 2 completion)*
- [x] 77.1-06-PLAN.md

**Wave 4** *(blocked on Wave 3 completion)*
- [x] 77.1-07-PLAN.md
- [x] 77.1-08-PLAN.md

### Phase 78: Repository marketing

**Goal:** Deliver polished, accessible repository collateral based only on verified capabilities and limitations.
**Depends on:** Phase 77.1
**Requirements:** MKT-01, MKT-02, MKT-03
**Success Criteria:**
1. Positioning, repository overview, one-pager and use cases accurately distinguish local proof, optional capabilities and unproven external integrations.
2. A reproducible local demo narrative, launch announcement and social copy use actual maintained commands without invented metrics or features.
3. Self-contained branded banner/social assets are accessible, rendered and visually checked; required raster output is actually generated and inspected.
4. Marketing navigation and all claims remain consistent with canonical docs and final integration evidence.

**Plans:** To be planned.

## Progress

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 75. Integrated local readiness | 11/11 | Complete    | 2026-10-10 |
| 76. Clean CI/CD | 5/5 | Complete    | 2026-10-11 |
| 77. Maintained docs and knowledge base | 7/7 | Complete    | 2026-10-12 |
| 77.1. Grok Bot import packaging | 8/8 | Verification pending (live UAT after merge) | — |
| 78. Repository marketing | 0/0 | Not planned | — |
