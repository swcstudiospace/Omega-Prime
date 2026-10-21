# Phase 100: omp engine wiring — context

## Boundary

Planning only. This session may change planning artifacts and its verification receipt, not application code, tests, prompts, installed tools, live settings, or runtime configuration. Execution belongs to a separately authorized session. Work stays in this worktree on its existing feature branch. Commit locally; never push, open a PR, merge, deploy, restart the connector, or touch external checkouts/system configuration.

v20 Wiring maps wiring phases 1–7 to GSD phases 97–103. This is wiring phase
4. The operator authorizes Phase 100 to proceed without checking Phase 97–99
verification or merge gates. Preserve Phase 97's existing
`omp_driver._TURN_TIMEOUT` and related arguments in this worktree; do not
remove that cap or add any new session/turn caps. Pending asks have no
separate bridge deadline, but may end at the existing outer turn timeout
until Phase 97 merges.

## Source of truth

The operator supplied a FIX_PROMPT for G4/G5, titled “omp engine wiring: mcp.json, skills, rpc-ui ask”, with a seven-node GOT, but no ISSUES/TASK/ISSUE/SUBISSUE block. The fallback session plan below applies; the uplift's technical work decomposition remains the execution-planning input. No tracking rows are created.

The referenced external wiring audit is outside the permitted worktree and was not read. Its findings are supplied in the prompt, not independently verified audit evidence. Repository files must be re-read before execution; uplift line numbers are not authoritative. No repository-local gsd-plan-phase skill/workflow was found in the scoped search; plans use the repository's numbered PLAN.md convention and explicit task/verification/dependency sections. No global skill/config files are modified to obtain a planning tool.

## Fixed operator decisions

- rpc-ui asks route to Ove live via clarify HITL by default. Do not add a policy-file answer source as the default or substitute one for live delivery. A policy feature is not required for this phase; if retained from existing code, it cannot approve sends, merges, or deploys.
- Installed `omp` is observed at version 18.12.3 (installed 18.x); its
  observed `--help` lists thinking levels `off, minimal, low, medium, high,
  xhigh, max, auto`, so `max` is accepted. No model session is required to
  establish support. Do not silently add unrelated `auto` support.
- `/skill:` needs rpc or rpc-ui; ask needs rpc-ui. No separate deadline is
  added for human answers. Cancellation must remain usable while an ask is
  pending.
- Do not copy or print the omp login store; link only if the chosen attachment strategy needs a link. Preserve shared model-role configuration and auth behavior.

## Requirements and acceptance

The canonical local requirement labels ENG-100-01 through ENG-100-07 and five uplift success criteria are in `.planning/ROADMAP.md`, under Phase 100. They are planning labels, not Linear identifiers.

Required states: `attached`, `attach_failed` (reported; session continues without Omega tools), `ui_pending`, `ui_answered`, `ui_rejected_stale`.

Required evidence at implementation: discovered Omega tools in a session tool list; real GSD and Omega skills resolved with a name-glob filter; fake rpc-ui ask answered once and stale answer rejected without a hang; pre-existing work-tree `.omp/mcp.json` state preserved/restored; mode docs and green or independently established baseline-only gates. Exercise normal return, launch failure, exception, cancellation, duplicate/stale/cross-job replies, answer schema errors, existing config, and ownership-safe cleanup. Generated config contains no secret values; child resolves environment names. Never mutate global MCP config or overwrite a user's work-tree MCP configuration.

## Uplift graph and execution decomposition

1. **n1 understand:** confirm actual omp MCP/skills discovery and rpc-ui shapes from installed source/probes without credentials or model calls; re-read engine/bridge; preserve accepted `max`; record baseline gate results.
2. **n2 decompose:** MCP attach and skills attach share launch code and run in sequence; UI protocol/HITL adapter can be authored independently, with its launch-loop integration serialized to avoid editing the driver concurrently.
3. **n3 MCP:** compare project-local config against a per-job agent directory, choose one, preserve shared auth/roles, resolve the server command, guarantee ownership-aware cleanup/backoff and crash handling.
4. **n4 skills:** use real search-path discovery plus name globs; prove a GSD and Omega skill resolve. Missing GSD sources are a prerequisite to expose explicitly, not a fake placeholder skill.
5. **n5 UI:** filter extension requests in rpc-ui, deliver ask/select/confirm to clarify, send schema-valid responses by request id once, reject stale/duplicate answers, keep cancellation responsive without human wait deadlines.
6. **n6 critique:** challenge crash leftovers, user-config loss, concurrent attachment, blocked asks, policy approval, incomplete skill globs, and operator visibility of pending asks (job status and doctor where that surface exists).
7. **n7 synthesize:** MCP tests, skills tests, UI fake-session tests, docs, exact gates, receipt. Preserve U1 then U2, U3 parallel only where files are disjoint, U4 last.

## Session plan

### n1 understand
1. Read planning state.
2. Check engine contracts.
3. Locate GSD conventions.
4. Record evidence and gaps.

### n2 register
1. Add v20 in progress.
2. Name phases 97–103.
3. Define Phase 100 requirements and success criteria.
4. Point STATE at 100 without closing historical deferred UAT.

### n3 plan
1. Choose MCP attachment strategy from evidence.
2. Specify real skills attachment.
3. Specify the live HITL bridge.
4. Define dependencies and executable verification.

### n4 critique
1. Check every acceptance criterion and scope constraint.
2. Challenge cleanup and concurrency.
3. Check stale-answer, validation, delivery and cancellation behavior.
4. Resolve actionable planning gaps.

### n5 synthesize
1. Validate planning artifacts.
2. Record verification limits.
3. Write the verification receipt.
4. Commit planning files on the current branch.

## Execution environment and gates

Use the existing `/workspace/omes-bot/.venv/bin/python` with `PYTHONPATH=/workspace/omes-bot-wt/p4`; do not install packages. Put that venv's bin directory on PATH for the exact commands below. Run commands from this worktree. Do not read or print secret/token values.

- `ruff check omega_prime/`
- `ruff format --check omega_prime/`
- `mypy omega_prime/`
- `pytest omega_prime/tests -q`
- `bash omega_prime/scripts/assemble-prompts.sh --check` only when prompts change.

Execution must identify baseline failures by name from observed commands, not infer them from old STATE evidence. This planning session's checks do not prove future implementation behavior. No session or turn deadline is added.

## Trackers

no tracker IDs provided

G4/G5 are supplied audit gap labels, not tracker identifiers. No Linear IDs or Notion URLs were supplied. No PR is opened in this local-only planning session.
