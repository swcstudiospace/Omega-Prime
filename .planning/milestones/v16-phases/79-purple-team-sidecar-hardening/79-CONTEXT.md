# Phase 79: Purple-team sidecar hardening - Context

**Gathered:** 2026-10-13
**Status:** Ready for planning
**Mode:** Auto-generated (discuss skipped via workflow.skip_discuss)

<domain>
## Phase Boundary

Make the pinned T3MP3ST red-team sidecar safe under constant upstream upgrades and gate every engagement behind a signed rules-of-engagement artifact, shipping fail-closed (BLOCKED) until the user signs. Scope: pin-freshness automation (Dependabot gitsubmodule, CI provenance/equivalence/surface-drift checks, Node-dep supply-chain audit), engagement guardrails (RoE artifact, fail-closed BLOCKED startup, kill switch, logged state machine), and the simulation library with detection validation and findings pipeline. Out of scope: live engagements (BLOCKED by user decision 2026-10-13), third-party targets, changes to the default-off `redteam.enabled` gating of the 11 tempest_* tools.

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
All implementation choices are at Claude's discretion — discuss phase was skipped per user setting. Use ROADMAP phase goal, success criteria, and codebase conventions to guide decisions.

### Recorded decisions binding this phase
- SHIPS BLOCKED: no live engagement until the user signs a rules-of-engagement artifact (user answer 2026-10-13; `.planning/decisions.md` row 1).
- Sidecar test scope: LLM + own endpoints (documented default q2; `.planning/decisions.md` row 3).
- Greenfield assumption overridden: the sidecar exists (submodule pin `831a6b0`, adapter `omega_prime/tools/redteam.py`, vendored pack `omega_prime/redteam/pack/`, default-off gating) — remediation is per-row, not greenfield (`.planning/sidecar-audit.md`).

</decisions>

<code_context>
## Existing Code Insights

Verified evidence (`.planning/recon-evidence.md` R6, file:line): gating `omega_prime/config.py:52-56,126-129,100-115`; registration `omega_prime/mcp_server.py:747-776`; adapter `omega_prime/tools/redteam.py:5-7` (pure HTTP proxy, never imports redteam/); pack `omega_prime/redteam/pack/` + provenance `omega_prime/redteam/__init__.py:4-6`, `VENDOR.md:84-89`; consistency surfaces `omega_prime/setup_check.py:125-138`, `omega_prime/tooling/catalog.py:39,77`, `docs/tool-catalog.md:1238-1315`, roster yaml:189-200; bootstrap never inits redteam (`omega_prime/tests/test_grokbot_bootstrap.py:756-757`); Docker excludes /redteam (`.dockerignore:7`, `omega_prime/grokbot/deploy.py:274`). Upstream pin drift: local `831a6b0` (v1.2~1) vs upstream main `06c11a5` with v1.2+v1.3 tags. Gap table: `.planning/sidecar-audit.md` (15 rows, all owned here). Full update plan: `.planning/updates/plan-01.md` (U1; config flags `redteam.roe_required`/`redteam.roe_file` + `OMEGA_PRIME_REDTEAM_KILL`, default-off/most-restrictive per repo config style).

</code_context>

<specifics>
## Specifics

- New sidecar assets live under `sidecar/` at repo root (harness, roe.schema.json, roe.yaml.template, contract/bot-interface.json, VERSION, cases/*.yaml, reports/ per plan-01), kept out of the Docker context and the Bot bootstrap path (redteam stays absent there — invariant test exists).
- Consistency lesson applies to any roster/tool surface change: setup_check gated set, catalog FAMILIES, docs/tool-catalog.md and roster yaml must move together. Preferred: no new tool names in this phase.
- CI additions go in existing workflow style (`.github/workflows/`), explicit minimum permissions, fail-closed gates.

</specifics>

<deferred>
## Deferred Items

- Live engagement runs (BLOCKED pending signed RoE artifact — user-side).
- Native Bot palette / SEED-010, swarm bridge / SEED-003, substrate fixes / SEED-002 (v17 candidates per decisions.md row 8).

</deferred>
