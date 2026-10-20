# Phase 99: Solo mode, Desk optional

## Session boundary

Planning only. No application, test, prompt, skill, generated-output, or operator-doc implementation changes in this session. Work stays in this worktree on the current feature branch. Commit planning artifacts locally; never push, open a PR, merge, deploy, restart the connector, touch live settings, or inspect secrets. No new packages. No session or turn time caps.

Ownership: `omega_prime/ownership.yaml` version 1, last match wins. All planning/state/receipt paths in this session match `**`, owned by `bot-00-omega-prime`.

## Locked operator decisions

- v20 Wiring maps wiring phases 1–7 to GSD phases 97–103. This is wiring phase 3 / GSD phase 99.
- Omega Prime is a solo bot. Desk integration remains optional, not deleted.
- Resolve `OMEGA_PRIME_SOLO` once at runtime build. Default enabled for bot-00; `0` restores explicitly configured Desk planes. Do not silently change other seats' defaults.
- Solo docs use substrate `docs_search` with the `question` argument from Phase 97. Events use substrate event emit. Bus operations report `not_configured: solo` unless solo is off and a Desk bus is explicitly configured and permitted.
- Remove the literal `127.0.0.1` bus fallback, including the blocked-binding construction branch. Do not confuse this bus requirement with unrelated loopback transports.
- Solo docs/events report substrate unreachability clearly. Never silently fall back to the Desk or localhost.
- Make desk-gateway on-demand in source prompt/core directives. Regenerate assembled output only with the existing assembler.
- Replace programming-desk skill links with verified substrate-hosted or existing local references. No verified hosted location is supplied: use relevant local material, or remove the link with an explicit note. No fabricated docs, URLs, or claims of equivalent policy.
- Document `OMEGA_PRIME_DESK_BUS_URL`, `OMEGA_PRIME_DESK_DOCS_INDEX`, `OMEGA_PRIME_DESK_NOTIFY_URL`, `OMEGA_PRIME_DESK_CHILD_MODEL`, and `OMEGA_PRIME_DESK_CHILD_PROVIDER` as optional/legacy; explain `OMEGA_PRIME_SOLO` precedence. Child/delegation controls must be described truthfully, not disabled merely because their names include DESK.
- Doctor reports solo state and disabled Desk planes. Review `routines/desk_lead.py` degradation; retain DeskDriver naming/local behavior.

## Requirements and acceptance

Source labels: **G3** and **G11-desk-vars**. These are audit gap labels, not Linear identifiers.

1. No code path connects to a literal `127.0.0.1` bus URL.
2. Fake-substrate tests prove solo docs/events routing; solo-off tests preserve configured Desk planes, including permitted saved bindings. Missing/blocked bindings never create a fallback connection.
3. Substrate outages are explicit, with no false docs/event success and no Desk fallback.
4. Assembled bot-00 prompt does not always load desk-gateway; other prompt text remains intact.
5. Skill link checking finds no dead in-repo refs; programming-desk dependencies are removed/replaced with an honest local-or-note strategy.
6. All five optional Desk vars and solo semantics are documented.
7. Exact gates are green or named baseline-only failures, with command/exit evidence.

## Dependencies and exclusions

Phase 99 depends on Phase 98 (shared memory via substrate, Hindsight fallback), transitively Phase 97 (direct connect, settings loader, no time caps). This checkout's `SubstrateClient.docs_search` currently sends `query`, not the uplift's `question`; prerequisites must be re-read and verified before Phase 99 execution. No completion of Phase 97/98 is inferred from milestone registration. Plan against the required upstream contract; do not quietly implement upstream work as Phase 99.

Out of scope: separate user-desk-lead connector; deleting Desk code; full env table generator (Phase 102); upstream transport/settings/shared-memory implementation; connector restart. The uplift's NEEDS_OVE restart is operator-only after later implementation, not authorized here. Session rules override the uplift's fresh-worktree instruction: remain in the supplied worktree/current branch.

## Planning graph

The supplied CHANGE_PROMPT contains GOT nodes but no ISSUES/TASK/ISSUE/SUBISSUE block. Use this lightweight fallback graph without rerunning uplift or creating trackers. Preserve the supplied n1 inventory, n2 split, n3 routing, n4 prompt/skill, n5 critique, n6 verification intent in the executable plans.

### n1 understand
1. Read planning conventions and state.
2. Inventory runtime, tools, substrate contracts, and tests.
3. Inventory prompts, skills, env docs, and assembly tooling.
4. Record prerequisite gaps and current evidence.

### n2 align
1. Register v20 Wiring in progress.
2. Add all seven named phases 97–103.
3. Copy Phase 99 goal, requirements, and success criteria from the uplift.
4. Point human and machine planning state at Phase 99.

### n3 plan
1. Specify runtime solo resolution, routing, and regression cases.
2. Specify on-demand source prompts and generated output.
3. Specify complete skill-link rewrites with verified local references.
4. Specify optional Desk docs and parent-owned verification.

### n4 critique
1. Check every uplift requirement and exclusion against plans.
2. Check dependency/wave ordering and file-disjoint execution ownership.
3. Check Desk compatibility, blocked bindings, outage, and routine behavior.
4. Validate planning artifacts and record exact gates.

### n5 synthesize
1. Resolve evidence-backed review gaps.
2. Write a planning-only verification receipt.
3. Commit only this session's planning/state/receipt files locally.
4. Report plan paths and upstream execution prerequisites.

## Verification ownership

Future edit workers skip gates, linters, tests, and formatters; the execution orchestrator verifies the union after each wave. Use the supplied Python interpreter with this worktree on PYTHONPATH. Set PATH to its venv bin before running the exact CI commands: `ruff check omega_prime/`; `ruff format --check omega_prime/`; `mypy omega_prime/`; `pytest omega_prime/tests -q`; `bash omega_prime/scripts/assemble-prompts.sh --check` when prompts change. Do not install packages or run live connectors. Link validation must actually cover skill paths; root-doc tests alone are insufficient evidence.

## Trackers and receipt

no tracker IDs provided

No Notion URL was supplied. The external audit path in the uplift is not read: it lies outside the authorized worktree; its findings are prompt-supplied context, checked against local files instead.

Planning receipt: `.receipts/bot-00-omega-prime/phase-99-plan.json`. Future implementation needs its own receipt; planning does not prove implemented solo behavior. Every receipt lists `rule adherence by future agents` under unverified.
