# Phase 100 plan index: omp engine wiring

Index for the six execution plans of Phase 100 (wiring phase 4). Written by
the planning session on 2026-10-21. **Nothing below is implemented, started,
or verified**; each linked plan is a future instruction for the separately
authorized execution session. Plan tasks are not marked complete anywhere in
this directory, and must not be by later readers without executed evidence.
Trackers: `no tracker IDs provided` (G4/G5 are supplied audit gap labels, not
tracker identifiers).

Session plan actually followed for planning: `100-CONTEXT.md` § "Session
plan" (n1 understand → n5 synthesize). Source digest: `100-RESEARCH.md`.
Validation model and observed baseline: `100-VALIDATION.md`.

## Plan graph

| Plan | Wave | Title / scope | depends_on | autonomous | Requirements |
| --- | --- | --- | --- | --- | --- |
| `100-00-PLAN.md` — `100-00` | 1 | Evidence & preflight: permitted installed-omp probes (loader/discovery contract, rpc-ui protocol fixtures, provider-free), Phase 97/99 prerequisite discrepancy check, real-GSD availability check, baseline gate observation | `[]` | per its frontmatter | ENG-100-06 evidence base; enables 01-05 |
| `100-01-PLAN.md` — `100-01` | 2 | MCP attachment: work-tree `.omp/mcp.json` exclusive-owner lease with safe backoff, discovery-based `attached`/`attach_failed`, outer cleanup/crash-recovery lifecycle | `[100-00]` | per its frontmatter | ENG-100-01, ENG-100-02 |
| `100-02-PLAN.md` — `100-02` | 3 | Skills: real discovery roots via supported additive path, `--skills` name globs from checked-in catalog, real GSD exposure and proof, user sources preserved | `[100-01]` | per its frontmatter | ENG-100-03 |
| `100-03-PLAN.md` — `100-03` | 2 | UI protocol/service only: verified rpc-ui envelope types/validation/event filter, `max` (never `auto`), live clarification service boundary, request-lifecycle state machine | `[100-00]` | per its frontmatter | ENG-100-04, ENG-100-05 (service half) |
| `100-04-PLAN.md` — `100-04` | 4 | UI driver/caller/control: single-owner dispatcher, production caller interaction context, operator delivery/reply/cancel, status/watch; pending asks have no separate bridge deadline, but the preserved outer turn timeout still applies | `[100-02, 100-03]` | per its frontmatter | ENG-100-04, ENG-100-05 (integration half), ENG-100-06 caps boundary |
| `100-05-PLAN.md` — `100-05` | 5 | Docs, integrated proof, exact four gates, verification receipt | `[100-04]` | per its frontmatter | ENG-100-07 (+ proof of record for 01-06) |
Execution order (waves): 100-00 → {100-01 ∥ 100-03} → 100-02 → 100-04
→ 100-05.
Authoring order follows those dependencies; parallel authoring only for
independent 100-01 and 100-03 work in wave 2.
Every plan/task subagent edits its assigned files only. The main agent performs
targeted assertions after each wave's union lands and the final exact four
gates only after all implementation changes land.

## Shared-file ownership (conflict contract)

| File / area | Owner and sequence |
| --- | --- |
| `omega_prime/engine/omp_driver.py` | 100-01 attachment, then 100-02 skills, then 100-04 driver/control integration |
| `omega_prime/engine/mcp_bridge.py` | 100-01 attachment, then 100-02 skills extension on acquired workspace lease |
| `omega_prime/engine/attach.py` | 100-01 attachment status, then 100-04 runtime integration |
| `omega_prime/engine/protocol.py`, `omega_prime/engine/hitl.py`, `omega_prime/tests/test_hitl.py` | 100-03 only: protocol, service, and its tests |
| `omega_prime/agent/runtime.py`, `omega_prime/mcp_server.py`, control files, clarification consumers, doctor | 100-04 only |
| `omega_prime/tests/test_engine.py` | 100-01, then 100-02, then 100-04; no 100-03 edits |
| `omega_prime/tests/test_tools.py`, `test_grokbot_e2e.py`, `grokbot/readiness_local.py`, `grokbot/emulator.py` | 100-04 clarify-consumer integration |
| `docs/setup.md` | 100-01 attachment documentation, then 100-05 review/update |
| `docs/driving-the-bot.md` | 100-05 |
| `docs/tool-catalog.md` | 100-04 regenerates after contract changes; 100-05 checks current output |
| `.receipts/bot-00-omega-prime/phase-100-omp-engine-wiring.json` | 100-05 |
| `.planning/phases/100-omp-engine-wiring/*` | planning corrections; execution appends evidence only |

Cross-plan interfaces: 01 produces one acquired workspace lease and artifact
registration seam that 02 extends without reacquiring; 01 attachment state is
consumed by 04. Plan 03 owns the verified rpc-ui protocol/service consumed by
04. Plan 02 owns skill selection consumed unchanged by 04. For publication,
registration, cleanup, operator delivery, control-path isolation, CLI identity,
and confirm semantics, follow the concrete cross-plan contract in plan text;
no second lease or weaker fallback is permitted.

## Requirement and acceptance coverage

| Requirement | Covered by |
| --- | --- |
| ENG-100-01 MCP discovery | 100-01 (contract evidence: 100-00) |
| ENG-100-02 ownership and cleanup | 100-01 |
| ENG-100-03 skills | 100-02 (sources: 100-00) |
| ENG-100-04 live HITL | 100-03 + 100-04 |
| ENG-100-05 request lifecycle | 100-03 (state machine) + 100-04 (identity plumbing, events/status) |
| ENG-100-06 compatibility and safety | 100-00 (max/login evidence), 100-03 (`max` in `THINKING_LEVELS`), 100-04 (uncapped waits, no policy approval), all plans (shared login/roles preserved) |
| ENG-100-07 verification and docs | 100-05, with per-plan tests from 01-04 |

Success criteria 1-5 map 1:1 to the rows in `100-VALIDATION.md` § 2
(criteria → owning plans and required evidence). No criterion is satisfied by
planning; all are future execution claims.

## Execution stop conditions (missing prerequisites — stop and report, never shrink scope or fabricate)

1. Phase 97–99 verification or merge status is not a Phase 100 stop
   condition, per the operator's parallel-worktree override.
2. **Installed protocol unavailable**: 100-00 cannot establish the
   loader/discovery contract or provider-free rpc-ui
   `extension_ui_request`/`extension_ui_response` fixtures from permitted
   evidence: stop before the dependent executable changes (01/02/03/04).
   Never invent config keys, wire fields, or method names.
3. **Real GSD missing**: no discoverable GSD skill sources/names or real
   provider-free resolution proof: execution is BLOCKED; no placeholder, fake
   skill, hardcoded historical path, glob invention, skip, or unverified
   acceptance waiver.
4. Phase 100 preserves the existing Phase 97 `_TURN_TIMEOUT` and related
   arguments and adds no new cap. A pending ask has no separate bridge
   deadline but may end at the existing outer turn timeout until Phase 97
   merges; do not claim uncapped outer-turn behavior.
5. **New phase-caused regression**: route to the responsible 100-01..100-04
   owner for scoped repair, then main-agent re-verification of the affected
   wave union. Do not repair the unrelated 27-node environmental baseline;
   report unrelated failures without claiming acceptance.

## Constants for every plan

- Environment: existing `/workspace/omes-bot/.venv/bin/python`, venv `bin` on
  `PATH`, `PYTHONPATH=/workspace/omes-bot-wt/p4`, run from this worktree; no
  package installs; never read/print secrets or the omp login store.
- Scope guards: no global `~/.omp/agent/mcp.json` mutation; preserve the
  existing `_TURN_TIMEOUT` and related arguments; add no session/turn caps;
  no `auto` thinking level; no policy-file approval of sends/merges/deploys,
  no Phase 101 LocalExecutor work, no connector restarts or deployments.
- Local commits only (`Refs: no tracker IDs provided`); never push, open a
  PR, merge, or enable auto-merge.
- Verification receipt before any completion claim (plan 05, using the
  checked-in verification-receipts skill); `rule adherence by future agents`
  listed under `unverified`.
- Future exported-symbol changes in `omega_prime/engine/` require an LSP
  references check first; owner verification happens centrally (main agent);
  subagents edit only.
