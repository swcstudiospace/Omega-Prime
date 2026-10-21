# Phase 97: Substrate direct connect — Context

**Gathered:** 2026-10-21
**Status:** Planning complete; five plans staged, none implemented. Independent units are ready; server-specific implementation is blocked pending authoritative schema confirmation.
**Milestone:** v20 Wiring (Phases 97–103), registered in `.planning/ROADMAP.md` the same day.
**Requirements:** W97-01..W97-08 (below). No requirement IDs are created for Phases 98–103.
**Source audit:** `/workspace/omes-bot-wiring-audit/AUDIT.md` — read-only audit of this tree at `44b28df`. Line references below were re-verified in this worktree (`fix/wiring-p1-substrate-direct` @ `44b28df`).

<goal>
## Goal

Connect the Omega Prime connector to Agent Substrate safely and without turn-duration caps: explicit configuration instead of a silent localhost default, an MCP streamable-HTTP/SSE transport, exact tool argument/lease/memory contracts, a guarded settings file loader, honest doctor/setup diagnostics, and uncapped engine/lane behavior — proven against fake servers locally, with no live network dependency.
</goal>

<requirements>
## Requirements (locked labels)

- **W97-01 transport.** Add MCP streamable POST used by `SubstrateClient._mcp_post` (`omega_prime/substrate/client.py:261-275`): send one `Accept: application/json, text/event-stream` header, parse `text/event-stream` SSE `data:` frames (including multiline data), and keep `Mcp-Session-Id` across calls. Today `HttpTransport.post` sends only `Content-Type`/`Content-Length` and decodes plain JSON (`omega_prime/providers/http.py:57-64`, decode ~`:230-236`), so every MCP tool call fails once the URL is right.
- **W97-02 arguments/leases/memory.** Keep `docs_search(query)` stable and send `question`; return the actual `graph_claim` `lease_id` and require it for heartbeat/release/complete through client, substrate tools and lead tools. `memory_write` passes optional `expected_version` through, preserves a caller-provided `idempotency_key`, and generates UUID4 once per logical write only when no key is supplied. `memory_search` sends optional `repo` only when provided. Confirm authoritative server schema before server-specific production changes; current compatibility is unverified.
- **W97-03 explicit configuration/auth/URL.** Unset substrate configuration ⇒ `not_configured` with zero network requests to `http://127.0.0.1:7410`; the localhost default (`substrate/client.py:21`, `substrate/probes.py:24`) is reachable only behind explicit `SUBSTRATE_URL=local` opt-in. Preserve ready binding precedence. Resolve auth as generic `SUBSTRATE_TOKEN` override before `SUBSTRATE_TOKEN_GROK_BOT`; warn when no token is available (anonymous auth), and redact all auth material. Validate explicit URLs as HTTPS except the literal `local` opt-in; reject credential-bearing URLs. Diagnostics show a URL only when HTTPS or under that explicit local opt-in. No live network check in doctor or tests.
- **W97-04 safe settings.** Loader for fixed `~/.omega-prime/settings.json`, overridable via `OMEGA_PRIME_SETTINGS_FILE`; process env wins over file settings. `load_runtime(env=...)` uses the explicitly supplied mapping for the new configuration seam, bypassing settings and ambient environment lookup; do not broaden this into unrelated historical purity fixes. Allowlist exactly five keys: `SUBSTRATE_URL`, `HINDSIGHT_URL`, `ULTRATHINK_ROOT`, `ULTRATHINK_SHIP`, `OMEGA_PRIME_STATE_DIR`; values must be strings. For secret-shaped keys, inspect the key name only, refuse it, and report only that name/reason; parsing the settings JSON necessarily reads its bytes, but code must not inspect, refuse, report, or log a secret-shaped entry based on its value, nor log or report any value. Unknown non-secret keys warn and are ignored. Reject the …
- **W97-05 diagnostics/degradation.** Actual `/omega-doctor` and `setup_check.check_substrate` FAIL when effective substrate configuration is absent, with an actionable env/settings hint. Preserve transport states `not_configured`, `unreachable`, `unauthorized`, `protocol_error`, `tool_error`, and `ok`; binding states such as `ready` or `degraded` are separate. Lead tools fail open with a visible degraded note rather than silence; saved binding precedence and blocking semantics remain intact.
- **W97-06 uncapped engine/liveness/cancel.** `LaneLimits.max_minutes` defaults to `None` = no cap (today `60`, `engine/lanes.py:119`); remove `_TURN_TIMEOUT = 120.0` seconds as a turn deadline (`engine/omp_driver.py:49`, applied at `:292-296`, enforced in `_accept_prompt` `:528-550`). The loop waits on request/socket liveness polling, ends on `agent_end`, and aborts on explicit cancel; dead process/end-of-stream is detected by the poll. Liveness polling is operational detection, NOT a turn deadline. No optional turn/session deadline and no env timeout override may be introduced. Preserve ordinary bounded transport/socket/probe I/O waits; they are not inference-lifetime caps and must not accidentally become one.
- **W97-07 uncapped lanes.** A 600-minute local spec stays local with no `max_minutes` spill reason (`_class_reasons` adds the reason only when `limits.max_minutes is not None`; today `lanes.py:181-182`). `_with_local_caps` (`lanes.py:510-516`) preserves missing minutes as `None`; local `spec.json` persists missing `max_minutes` as explicit JSON `null`, while the 600-minute job-spec routing metadata remains separate and fleet serialization semantics stay unchanged (`JobSpec.to_json` omits unset fields; `fleet_client.py:99-101`; local `_write_spec`, `lanes.py:485-493`).
- **W97-08 docs/local verification.** Fake SSE MCP server tests in existing `omega_prime/tests/test_substrate*.py` conventions (`test_substrate.py`, `test_substrate_probes.py`, `test_substrate_session.py`); a contract test pinned to real server tool schema at execution preflight (never guessed); docs cover settings file, env precedence, allowlist, local opt-in, and no-caps behavior. Parent runs the gates.
</requirements>

<acceptance>
## Acceptance criteria (seven, from the uplift)

1. **SSE fake server:** against a fake SSE MCP server in `omega_prime/tests/`, the client POSTs with a single `Accept: application/json, text/event-stream` header, parses `text/event-stream` `data:` frames (including multiline SSE data), and preserves `Mcp-Session-Id` across calls.
2. **Exact args contracts:** `docs_search` sends `question`; claim `lease_id` flows into heartbeat/release/complete through both tool families; `memory_write` sends a caller-supplied idempotency key or generated UUID4 and includes `expected_version` when given; `memory_search` sends `repo` when given. Pin the server-specific fixture at execution preflight.
3. **Unset ⇒ no localhost request, doctor fails:** with substrate unset, no request reaches `127.0.0.1:7410`, and `omega-doctor`/`setup_check.py` FAIL with the configuration hint instead of skipping. HTTPS URL validation, explicit literal-local opt-in, token precedence and anonymous warning are covered.
4. **Settings loader matrix:** missing file; loaded file; process env wins; secret-shaped name refused without inspecting/logging its value; git-tracked file refused; worktree-internal resolved path refused; group/world-writable permissions refused; malformed JSON rejected atomically — and Main still starts degraded in every missing/rejected/malformed case.
5. **600-minute local:** an eligible 600-minute spec stays local without a `max_minutes` reason. Separately, an unspecified local maximum stays `None` and persists as explicit JSON `null`; preserve a supplied duration as metadata and keep fleet omission semantics unchanged.
6. **No 120s cut, liveness instead:** a simulated turn longer than 120 seconds completes (no deadline fires), a dead simulated process is detected via the request/socket liveness poll, and explicit cancel aborts.
7. **Gates/docs:** the parent-run gate set passes at its cadence, and the docs (settings file, env precedence, allowlist, local opt-in, no-caps semantics, degradation) are updated in the same phase.
</acceptance>

<decisions>
## Locked scope and operator decisions

- Planning only this session: no runtime code change, no live settings mutation, no other checkouts or systems. Planning artifacts may change only under `.planning/`; remove the unrequested `.receipts/Phase97Milestone/gsd-phase97-v20-wiring-planning.json`; the parent owns the sole task receipt and commit.
- Settings path is final, not an open question: fixed filename `~/.omega-prime/settings.json`, override `OMEGA_PRIME_SETTINGS_FILE`.
- No optional turn/session deadline and no env timeout override anywhere in this phase.
- Request/socket liveness polling is operational detection, not an inference-lifetime limit; ordinary transport/socket/probe bounded waits are preserved and must not become session-duration caps; no repo-global eradication of unrelated duration settings is promised.
- Lane acceptance preserves 600-minute job-spec routing metadata while persisting missing local `max_minutes` as JSON `null` (`JobSpec.to_json` omits `None`); these are distinct values/representations.
- Five execution plans are staged, zero implemented; Phase 97 planning is complete and independent units are ready. Authoritative upstream schema confirmation is blocked before server-specific implementation.
- No PR, no push, no merge (explicit user scope); never expose/log secret values or token values.
- Live actions needing Ove's explicit yes stay out of scope: substrate token minting, connector env change/restart, live `https://substrate.swcstudio.space` configuration, Ultrathink install, any push/PR/merge.
- No external local checkouts; public GitHub HTTP reads are allowed for schema evidence only.
- Parent baseline gates are recorded below; no checks were run by this planning worker.
</decisions>

<server_schema>
## Upstream server schema — verification status

- Public GitHub read-only repo/tree/raw checks on 2026-10-21 returned **404**. The permitted authenticated fallback (`gh api repos/swcstudiospace/agent-substrate`) exited 4 because authenticated access was unavailable; no token values were read. The current substrate/grok-bot MCP path and tool types remain **unverified**.
- The audit's protocol-drift mapping (five items: Accept/SSE, `question`, `lease_id`, `expected_version`/`idempotency_key`, `repo`) came from the substrate side at agent-substrate main `05d8f45` and from substrate PRs #79/#80/#84 that were still landing — treat it as audit-sourced evidence, not a pinned contract.
- Consequence for execution planning: confirm the contract at execution preflight against the real server (or its published schema) before server-specific assertions are frozen; never guess the grok-bot MCP path or invent a mount. Until preflight, W97-02 details remain unverified; generic transport and caps work are not blocked.

<baseline>
## Baseline (parent-run, recorded 2026-10-21)

- `ruff check` exit 0; `ruff format` exit 0 (589 files); `mypy` exit 0 (462 files).
- `pytest` exit 1: **27 failed / 4777 passed / 11 skipped**, 15 warnings, 236.78s. Named failure classes: missing/uninitialized `prime-agent` submodule, missing `rlm`, related `oneclick`/setup preflight failures.
- Decision: record this baseline; do not fix those failures. The parent may run the required final gate set and compare against this baseline.
</baseline>

<source_map>
## Source map (verified at `44b28df` in this worktree)

- `omega_prime/substrate/client.py` — `:21` `DEFAULT_BASE_URL = "http://127.0.0.1:7410"`; `:57` constructor default; `:145-169` `memory_write` (no `expected_version`/`idempotency_key`); `:171-190` `memory_search` (no `repo`); `:193` `docs_search` sends `{"query": ...}`; `:229-238` `graph_heartbeat` sends `token`; `:261-275` `_mcp_post`; `:279-300` `_resolve_base` override order (ready binding → constructor URL → default); `:311-319` `_headers` (anonymous when unset).
- `omega_prime/substrate/probes.py` — `:24` same localhost default; `:102` Hindsight disabled when `HINDSIGHT_URL` unset (audit).
- `omega_prime/providers/http.py` — `:57-64` `post()` sends only `Content-Type`/`Content-Length`; `~:230-236` plain-JSON decode raising `ProviderError("response is not JSON")` on SSE.
- `omega_prime/mcp_server.py` — `:887-896` builds `SubstrateClient(env.get("SUBSTRATE_URL","") or SUBSTRATE_DEFAULT_URL, token=SUBSTRATE_TOKEN or SUBSTRATE_TOKEN_GROK_BOT, honor_binding=True)`.
- `omega_prime/setup_check.py` — `:159-166` `_SUBSTRATE_URL_VARS`/`_SUBSTRATE_TOKEN_VARS`; `:168-193` `check_substrate` returns `(None, "substrate env not set — brief/emit stay local")` (skip); `:203` registered in `CHECKS`.
- `omega_prime/integrations/binding.py` — `:61-73` `binding_path()` refuses state inside the repository: reuse this pattern for the settings worktree/tracked rejection.
- `omega_prime/engine/lanes.py` — `:10` docstring "max_minutes is at most 60"; `:119` `LaneLimits.max_minutes: int = 60`; `:181-182` `max_minutes` spill reason; `:485-493` `_write_spec` (`spec.to_json()` + local-only `stage`); `:510-516` `_with_local_caps` fills 60; `:558`/`:592` `_optional_int` parse.
- `omega_prime/engine/fleet_client.py` — `:70-97` `JobSpec` (`max_minutes: int | None = None` at `:84`); `:99-101` `to_json()` "Unset fields are left out".
- `omega_prime/engine/omp_driver.py` — `:47-49` `_READY_TIMEOUT = 15.0`, `_COMMAND_TIMEOUT = 15.0`, `_TURN_TIMEOUT = 120.0`; `:292-296` default timeout; `:528-550` `_accept_prompt` deadline loop raising `EngineError("timeout", "timed out waiting for agent_end")`.
- `omega_prime/engine/attach.py` — callers pass no timeout: route_turn `~:148`, delegate_via_engine `~:262`, ultrathink_engine `~:358` (audit).
- Tests: `omega_prime/tests/test_lanes.py:178-184` (60/61 cap assertions), `:299-303` (`stored["max_minutes"] == 60`); substrate fakes live in `omega_prime/tests/test_substrate.py`, `test_substrate_probes.py`, `test_substrate_session.py`.
- No settings loader exists today: `OMEGA_PRIME_SETTINGS_FILE` appears nowhere in the tree; the only `settings.json` reader is the unrelated per-agent-dir `omega_prime/prime_kernel/factory_api.py:36`.
- Live box (audit, names only): the running connector has none of `SUBSTRATE_URL`/`SUBSTRATE_TOKEN`/`HINDSIGHT_URL`/`OMEGA_PRIME_STATE_DIR`/`ULTRATHINK_ROOT` set; `~/.omega-prime/` holds only `command-log.jsonl` and `jobs/` — no `settings.json`, no `integrations.json`.
</source_map>

<conventions>
## Plan conventions and skill fact

- The `gsd-plan-phase` skill URI was unavailable this session ("Unknown skill"). Phase 97 execution plans must follow the existing structured GSD convention: YAML frontmatter (`phase`, `plan`, `type`, `wave`, `depends_on`, `files_modified`, `autonomous`, `requirements`, `must_haves` with truths/artifacts/key_links/prohibitions) plus the XML body (`<objective>`, `<assumption_delta_decision>`, `<context>` with `@`-references, `<tasks>` carrying `<read_first>`/`<files>`/`<action>`/`<acceptance_criteria>`/`<verify>`, `<threat_model>`, parent-owned `<verification>`), exemplified by `.planning/milestones/v15-phases/77.1-grok-bot-import-packaging/77.1-02-PLAN.md`.
- Current-milestone phase artifacts live under `.planning/phases/<slug>/`; this phase's context file is `97-CONTEXT.md` by assignment (v18/v19 used a bare `CONTEXT.md` — do not rename this one).
- Wave ownership is recorded in the current execution plans: `97-01` owns uncapped engine/lane behavior (wave 1); `97-02` owns generic streamable-HTTP/SSE transport (wave 1); `97-03` owns argument/lease/memory migration (wave 2, depends on `97-02`); `97-04` owns settings, URL/auth configuration, and diagnostics (wave 3, depends on `97-03`); `97-05` owns docs/integration (wave 4, depends on `97-01` and `97-04`). No same-wave implementation plans share files. Authoritative schema confirmation is a preflight for server-specific production changes; generic transport and caps work remain unblocked.
</conventions>

<pitfalls>
## Pitfalls for the execution planner

- Tests live in `omega_prime/tests/`, not `tests/` — the audit's `tests/test_lanes.py` references are `omega_prime/tests/test_lanes.py`.
- `JobSpec.to_json` omits `None` ("Unset fields are left out") — explicit JSON `null` is needed for local `spec.json` only; do not change the fleet payload contract.
- `check_substrate` is registered in `CHECKS` (`setup_check.py:203`); flipping skip→FAIL changes setup exit semantics for installs without substrate — that is the accepted uplift (FAIL with hint), keep the hint actionable.
- Preserve the `honor_binding` override order (`_resolve_base`): ready binding → explicit URL → `not_configured`. The localhost default is removed, not reordered; `SUBSTRATE_URL=local` is the only path back to 127.0.0.1.
- Secret-shaped keys are refused based on key names only; parsing the settings JSON necessarily reads its bytes, but secret values are never inspected, used to decide refusal, logged, or reported. Only the key name and rejection reason may be surfaced.
- `load_runtime(env=...)` uses an explicit mapping for the new settings/configuration seam; do not claim full historical explicit-env purity or broaden changes into unrelated configuration/redteam behavior.
- Do not reintroduce any deadline in `_accept_prompt`: liveness poll + `agent_end` + explicit cancel only; no new env timeout knobs anywhere.
- Do not touch unrelated duration settings (transport/socket/probe timeouts, cron intervals) — bounded waits are operational detection, not caps.
- `mcp_server` construction must guard settings failures so Main starts degraded (missing/rejected/malformed) instead of exiting.
- Rejection reasons must be distinct and separately asserted: loose permissions vs git-tracked vs worktree-internal vs malformed vs secret-name.
- The 600-minute-stays-local change is one slice: `_class_reasons` + `_with_local_caps` + explicit-null persistence + `test_lanes.py:178-184,299-303` updates land together.
- W97-02 argument names are unverified until execution preflight (see <server_schema>) — write the contract test to be pinned at preflight, never hardcode a guessed grok-bot MCP path or mount.
</pitfalls>

<out_of_scope>
## Out of scope

- Phases 98–103 (names only, no requirement IDs): 98 Shared memory via substrate, Hindsight fallback; 99 Solo mode, Desk optional; 100 omp engine wiring; 101 Real U2 LocalExecutor; 102 Schedulers, dead modules, env docs; 103 Ultrathink integration and cleanup.
- All Ove live actions: substrate token minting, connector env changes/restart, live substrate/Hindsight configuration, Ultrathink install, push/PR/merge.
- Live network checks, submodule initialization, external local checkouts, and mutations outside `.planning/` in this worktree are out of scope.
</out_of_scope>

<tracker>
## Tracker

No tracker IDs provided; no Notion URLs. Parent commits cite `no tracker IDs provided`.
</tracker>

<fallback_plan>
## Fallback plan (supplied, verbatim)

### n1 understand
1. Read audit, current planning state, ownership and available workflow.
2. Map transport, argument consumers and public upstream schema evidence.
3. Map config, diagnostics, turn liveness and lane routing.
4. Run exact baseline gates and retain named failures.

### n2 milestone
1. Add v20 Wiring in progress without altering shipped milestones.
2. Add all seven wiring phases numbered 97 through 103.
3. Define Phase 97 goal, eight requirements and seven acceptance criteria from uplift.
4. Point human and machine planning state at Phase 97.

### n3 plan
1. Record locked scope, operator decisions and source map.
2. Write caps and streamable-HTTP plans as independent first-wave units.
3. Write dependent argument migration and config/diagnostic plans.
4. Write validation matrix, docs tasks, wave/file ownership and execution gates.

### n4 critique
1. Review every uplift acceptance criterion and source-derived caller.
2. Challenge secret safety, settings guards, no caps and liveness.
3. Check plan dependencies, overlapping paths and execution readiness.
4. Resolve defects and exercise planning artifact checks.

### n5 synthesize
1. Run final required gates and compare baseline.
2. Validate planning-only scope and phase pointer/plan coverage.
3. Write receipt naming implementation/live verification limits and future-agent adherence.
4. Commit on current branch with no push or PR; report plans and blockers.

Deviations by explicit assignment: gates were parent-run (n1.4, n5.1 — this agent skips all tests/checks/lint/formatters); n2/n3 planning artifacts are limited to the four target files (execution plans are the parent's next step); n5.4 commit is parent-owned — no commit here.
</fallback_plan>

<no_implementation>
## No implementation claim

This file records planning only. No runtime source, tests, docs, live settings, or external systems were changed; no gates were run by this planning worker (baseline results are parent-reported); no substrate, Hindsight, or Ultrathink endpoint was contacted. Phase 97 execution starts only after a separate manual invocation of `/skill:gsd-autonomous 97`; independent units may proceed, but authoritative schema confirmation is required before server-specific implementation.
</no_implementation>
