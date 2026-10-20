# Phase 103 — Ultrathink integration and cleanup: master index

**Status:** Planning only. No implementation, tests, gates, runtime smoke, or completion is claimed. The five frozen execution plans define the work; this file indexes their order, acceptance, ownership, and dependencies. The fallback six-node plan below is the required lightweight plan because the uplift contains no `ISSUES` root with `TASK`/`ISSUE`/`SUBISSUE` nodes; it is a planning outline, not a second execution DAG.

**Evidence:** See [103-CONTEXT.md](103-CONTEXT.md) and [103-RESEARCH.md](103-RESEARCH.md). PR45 is local-history evidence only, not runtime proof. G8/G12 and `claude-ultrathink` are prompt-supplied and unaudited. Phase 102 and all predecessor completion remain unverified. No tracker IDs or Notion URLs were provided.

## Frozen five-plan execution index

| Plan | Wave | Depends on | Executor ownership / scope |
|---|---:|---|---|
| `103-01-PLAN.md` | 1 | — | Ship bridge gate, explicit server/eval environment, and associated bridge/MCP/eval consumer tests. Owns new shared `omega_prime/integrations/ultrathink.py` and exact parser. |
| `103-02-PLAN.md` | 2 | 103-01 | Readiness snapshot, state directory helper, setup and GrokBotDoctor adapters/tests. Consumes shared parser/helper from 103-01. |
| `103-03-PLAN.md` | 1 | — | Optional native markers and OpenShell test-only executable wiring in tests/config. No production sandbox runner change. |
| `103-04-PLAN.md` | 3 | 103-01, 103-02, 103-03 | `docs/setup.md`, Ultrathink skill, changelog documentation; checker reference only if actual Phase 102 implementation/API is available. |
| `103-05-PLAN.md` | 4 | 103-01, 103-02, 103-03, 103-04 | Parent-only targeted verification, public-path fake-root smoke, gates, receipt and milestone handoff. Phase 102 verification is a prerequisite to implementation, not an assumed fact. |

### Requirement-to-owner matrix

| Requirement | Acceptance contract | Plan owner(s) |
|---|---|---|
| WIRE-07-01 | Exact strings `1`/`true` enable; all else off; stable `ship_disabled` before runner for valid pr/review/merge calls; explicit env only; preserve independent approvals, status/assess and autoMerge behavior. | 103-01; 103-05 verifies |
| WIRE-07-02 | Four readiness states; three executable regular bin files; setup bool/None and doctor JSON shape preserved; report root/effective ship/preferred state dir, never plan existence/freshness; no CLI/config/plan-content reads; default-off healthy. | 103-02; 103-05 verifies |
| WIRE-07-03 | Exact env names and existing state precedence documented; no invented Phase 102 checker; decisions provider untouched. | 103-04 (interfaces from 103-01/02); 103-05 conditional check |
| WIRE-07-04 | Native optional markers and absence-only skips; preserve status test and fail on present broken artifact; test-only `OPENSHELL_BIN` and real selected probe; no build, production runner change, or live gateway change. | 103-03; documentation in 103-04; 103-05 verifies |
| WIRE-07-05 | Honest milestone mapping, no predecessor/implementation/live-proof claims; preserve `77.1 human_needed`. | 103-05 |

## Lightweight six-node fallback plan

1. **Understand**
   1. Re-read the shared contract and three static research reports; treat facts as source evidence, not runtime proof.
   2. Map WIRE-07-01 through WIRE-07-05 to the five frozen plan files and acceptance criteria.
   3. Confirm wave/dependency boundaries and unique ownership of the shared integration helper in 103-01.
   4. Record Phase 102/checker, build, live gateway, and external audit evidence gaps without blocking planning.
2. **Design shared bridge and readiness contracts**
   1. Specify exact-string, fail-closed ship parsing and explicit env propagation without ambient fallback.
   2. Specify approval-before-handler and ship-gate-before-root/runner precedence; keep approval and autoMerge unchanged.
   3. Specify four read-only readiness states and reuse of ordered state discovery without parsing plans.
   4. Specify setup/doctor adapters that preserve existing return and JSON contracts.
3. **Plan optional test and documentation behavior**
   1. Separate native artifact absence from present-but-broken load failures; preserve Python-only status coverage.
   2. Specify test-only `OPENSHELL_BIN` lookup and real probe; do not alter production selection or build binaries.
   3. Map exact environment names and existing state precedence into operator documentation.
   4. Keep gateway, decisions provider, live config, and external audits outside scope.
4. **Order execution and verification**
   1. Run wave 1 plans 103-01 and 103-03 independently; parent runs focused ship/MCP/eval and optional-native/OpenShell checks after both land and before wave 2.
   2. Run 103-02 only after 103-01; parent runs focused Ultrathink/setup/doctor checks before wave 3.
   3. Run docs 103-04 after all wave 1/2 plans; parent runs `test_docs.py`, `test_public_repo.py`, and the actual safely isolated Phase 102 checker only if its real interface is evidenced.
   4. Reserve the focused union, fake-root process smoke, and one exact full CI pass for parent wave 4; workers never run checks.
5. **Critique**
   1. Check every proposed behavior is labeled planned, not implemented or verified.
   2. Check explicit env is authoritative and enablement never implies approval.
   3. Check diagnostics never launch a CLI, read configs/plans, assert plan freshness, or alter resolver semantics.
   4. Check optional dependencies skip only for absence; live gateway and external audit claims remain untouched.
6. **Synthesize**
   1. Keep this index aligned with plan file names, waves, dependencies and requirement owners.
   2. Preserve the seven-phase milestone mapping and `77.1 human_needed`; do not claim phases 97–102 complete.
   3. State all unverified prerequisites and future-agent rule adherence in the parent receipt.
   4. Hand off a planning-only deliverable; parent verification and implementation completion remain pending.

Workers implementing plans MUST skip all gates, tests, linters and formatters. Parent runs focused behavioral checks after each wave before advancing: wave 1 ship/MCP/eval plus optional native/OpenShell; wave 2 Ultrathink/setup/doctor; wave 3 `test_docs.py`, `test_public_repo.py`, and only an evidenced, safely isolated Phase 102 environment checker. Wave 4 runs the focused union and fake-root process smoke, then the exact full gates once: `ruff check omega_prime/`; `ruff format --check omega_prime/`; `mypy omega_prime/`; `pytest omega_prime/tests -q`. Run `bash omega_prime/scripts/assemble-prompts.sh --check` only if prompts change. Use `/workspace/omes-bot/.venv/bin/python`, `PYTHONPATH=/workspace/omes-bot-wt/p7`, and the venv bin directory on `PATH`; do not install packages. No checks are run during planning. No results or coverage claims are asserted here.

No implementation or verification is complete by this index. The parent is responsible for ownership/delivery requirements and the receipt; its `unverified` list must include rule adherence by future agents.
