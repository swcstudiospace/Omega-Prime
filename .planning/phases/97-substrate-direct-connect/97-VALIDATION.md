# Phase 97: Substrate direct connect — Validation

**Execution status:** Local implementation and offline verification ran on 2026-10-20. Static gates and generated catalog checks passed; the full suite has the same 27 baseline failures and zero new failures. Phase verification remains blocked on authoritative current Agent Substrate MCP path/schema evidence; no live endpoint or connector was touched.

## Requirements to plan/task mapping

| Requirement | Owning plan/task | Deterministic acceptance evidence |
|---|---|---|
| W97-01 transport | `97-02-T2` transport and `97-02-T3` response parser/session | Fake raw HTTP transport/server proves MCP Accept header, JSON/SSE parsing, multiline `data:` event assembly, session header persistence, request-ID correlation, error distinctions, and clean handling of malformed/empty streams without affecting ordinary HTTP consumers. No live host. |
| W97-02 arguments/leases/memory | `97-03-T1` schema preflight; `97-03-T2` tools/memory; `97-03-T3` leases/adapters | Preflight-pinned fixture and correlated fakes prove `question`; claim `lease_id` output threaded to heartbeat/release/complete; `expected_version` plus one UUID `idempotency_key` per logical write; optional `repo`; correct text/object result handling in lead and direct tools. Server contract remains unverified until preflight. |
| W97-03 explicit configuration/auth/URL | `97-04-T1` loader/config; `97-04-T2` runtime; `97-04-T3` doctor; `97-04-T4` smoke | No substrate URL means explicit `not_configured` and zero request to 127.0.0.1:7410; `SUBSTRATE_URL=local` is the explicit localhost opt-in; blocking bindings remain blocking and ready binding retains precedence; process env beats settings; generic token resolution precedes Grok token; anonymous mode warns; invalid/credential-bearing URLs never appear in diagnostics. |
| W97-04 safe settings | `97-04-T1` loader/security; `97-04-T2` provenance/startup; `97-04-T4` smoke | Test matrix covers fixed default and override, explicit `load_runtime(env={})` bypass of settings and ambient lookup at the new seam, env precedence, loaded/missing/rejected/malformed status propagation, exact five-key allowlist, all-string values, secret-shaped-name refusal, unknown nonsecret warning/ignore, no partial malformed overlay, lexical/resolved path and all-worktree/tracked guards, `.git` worktree files, writable permissions, symlink/race fail-closed behavior, and degraded startup. No settings contents or secret values in output. |
| W97-05 diagnostics/degradation | `97-04-T2` runtime; `97-04-T3` doctor/setup/failure reporting; `97-04-T4` smoke | Actual `/omega-doctor` and `setup_check` report missing effective configuration as actionable failure; settings status survives Main → stdio/SSE → runtime → actual lead doctor. Rejected/malformed file contributes no overlay but does not prevent startup; substrate tool failures are explicit, not silent empty success. Offline diagnostic never claims network reachability. |
| W97-06 uncapped engine/liveness/cancel | `97-01-T1` engine driver/event-loop/cancel | Virtual-time fake turn exceeds 120 seconds without cutoff; liveness poll distinguishes empty queue, EOF, reader fault, and dead process; queued `agent_end` is not lost; explicit existing interrupt aborts and cleans up, does not trigger Python fallback. No session/turn duration limit or environment override; liveness polling is detection only. |
| W97-07 uncapped lanes | `97-01-T2` lane/persistence behavior | A 600-minute eligible spec stays local with no duration spill reason; absent `max_minutes` stays `None` and local JSON persists explicit null; fleet wire serialization and other resource limits retain existing semantics. |
| W97-08 docs/local verification | `97-05-T1` docs; `97-05-T2` integration and parent handoff | Docs truthfully explain explicit URL, settings, precedence, allowlist, local opt-in, auth warning, degradation, exact argument/lease semantics after preflight, uncapped engine/lane duration and operational liveness, and offline-vs-live limits. Relevant existing docs/catalog tests pass; docs do not claim live configuration or compatibility absent evidence. |

## Seven acceptance criteria (SC) to plan/task mapping

| Criterion | Plan/task | Planned deterministic evidence targets (original plan) |
|---|---|---|
| SC1 SSE fake server | `97-02-T2`, `97-02-T3` | Add `test_mcp_streamable_post_parses_sse_and_reuses_session` in `omega_prime/tests/test_transports.py`; use fake/socketpair server, multiline data frames and `Mcp-Session-Id` across calls. |
| SC2 Exact args contracts | `97-03-T2`, `97-03-T3` (schema preflight in `97-03-T1`) | Add `test_substrate_wire_arguments_and_graph_lease_flow` in `test_substrate_session.py`; add `test_lead_graph_claim_lease_reaches_mutations` in `test_lead.py`; assert memory key fields and repo optionality. Freeze server-specific fixture only after schema preflight. |
| SC3 Unset → no localhost request; doctor fails | `97-04-T2`–`97-04-T4` | Add `test_unset_substrate_never_requests_localhost` in `test_substrate_probes.py` and `test_missing_substrate_configuration_fails_setup_check` in `test_setup_check.py`; ensure doctor hint is actionable and network call count is zero. |
| SC4 Settings loader matrix | `97-04-T1`, `97-04-T2`, `97-04-T4` | Add `test_settings_loader_security_and_precedence_matrix` and `test_rejected_settings_degrade_without_partial_overlay` in focused settings tests; cover each distinct rejection and ensure Main startup survives. Use temporary files/repos only; no real user config. |
| SC5 600-minute local | `97-01-T2` | Update `test_lanes.py::test_file_and_minute_caps` (or split to `test_600_minute_spec_stays_local_without_duration_reason`) plus `test_missing_local_max_minutes_persists_json_null`; preserve file cap assertion. |
| SC6 No 120s cut; liveness instead | `97-01-T1` | Add `test_turn_can_exceed_120_seconds_without_deadline`, `test_dead_process_is_detected_by_liveness_poll`, and `test_explicit_interrupt_cancels_running_engine` in `test_engine.py`; use fake time/events and fake process, never a 121-second sleep. No duration timeout parameter/config is added. |
| SC7 Gates/docs | `97-05-T1`, `97-05-T2`; parent verification | Local gates and docs/catalog checks are recorded below; prompt assembly was not applicable because no prompts changed. Current upstream schema/path compatibility remains unverified. |

The identifiers above were proposed during planning. The implementation adds behavioral tests in the listed files; exact local runs and remaining upstream gaps are recorded below.

## Offline fake-server, engine, and connector smoke

Parent may run an offline integration smoke after all plans land, without accessing real endpoints or credentials:

1. Start an in-process/local fake MCP HTTP/SSE server with a known temporary URL and fixed fake token; exercise initialize/session reuse, direct tool call, JSON and SSE response forms, lease handoff, settings-loaded URL resolution, and safe diagnostic behavior. Use only fixture secrets and assert their absence from all captured diagnostics. Never infer or hardcode the real server mount.
2. Run the engine against the existing fake NDJSON `omp` executable in `omega_prime/tests/test_engine.py` (or deterministic fixture equivalent): advance virtual time beyond 120 seconds, inject output/`agent_end`, dead-process/EOF states, and existing interrupt; assert successful long turn, failure on terminal liveness loss, explicit cancellation cleanup, and no fallback after start. No timed session deadline in the harness.
3. Run lane fake executor/fleet tests: 600-minute stays local; absent local duration is JSON null; existing file/memory/concurrency/capacity behaviors remain.
4. Exercise launcher/runtime against isolated temp environment, settings path, state directory, fake server, and fake connector host only. Verify missing/rejected/malformed settings startup degrades and diagnostic output is safe. Do not edit live settings, restart a live connector, mint credentials, or contact a real endpoint.
5. Any live readiness, actual credentials, endpoint connectivity, real Grok Bot behavior, and authoritative schema unavailable from allowed evidence remain explicitly unverified. Do not call such a smoke “live verified.”

## Parent-run gates and baseline comparison

**Executed:** 2026-10-20 in `/workspace/omes-bot-wt/p1`; Python was `/workspace/omes-bot/.venv/bin/python`, `PYTHONPATH=/workspace/omes-bot-wt/p1`, and `OMEGA_PRIME_SETTINGS_FILE` pointed to a missing `/tmp` path so tests did not consult live settings.

Focused behavior coverage:
- Engine, lane, transport, Substrate, settings, connector, doctor, setup, lead, and readiness regression set: 328 passed, 1 known setup baseline test deselected.
- Final targeted regressions for warm lead prompt installation, SSE option forwarding, and isolated readiness: 5 passed.

Consolidated gates:
- `ruff check omega_prime/` — exit 0.
- `ruff format --check omega_prime/` — exit 0; 591 files already formatted.
- `mypy omega_prime/` — exit 0; no issues in 464 source files.
- `pytest omega_prime/tests -q` — exit 1; 27 failed, 4831 passed, 11 skipped, 15 warnings. The 27 failing test IDs exactly match the recorded baseline in `97-RESEARCH.md`; no new failures.
- `python -m omega_prime.tooling.catalog --check` — exit 0; `docs/tool-catalog.md` current.
- `pytest omega_prime/tests/test_docs.py omega_prime/tests/test_public_repo.py -q` — exit 0; 31 passed after the final documentation edit.

The prompt assembly check was not run because no prompts changed. No live endpoint, credential, live settings file, connector, VPS, or Railway resource was accessed. Current upstream MCP mount/schema compatibility is still unverified; the VPS was unavailable per the supplied context, and no authoritative current server contract was accessible.

No command in test, smoke, or plan introduces an optional turn/session deadline or environment timeout override. Socket/request liveness polling is not an inference duration cap.

## Wave ownership/conflict matrix

| Wave | Plan | Exclusive implementation files | Conflict/dependency rule |
|---:|---|---|---|
| 1 | `97-01` caps | As enumerated in `97-01-PLAN.md` `files_modified` | No overlap with plan 02; preserve no duration cap and only existing resource caps. |
| 1 | `97-02` transport | As enumerated in `97-02-PLAN.md` `files_modified` | Independent of plan 01; publish transport interface before plan 03. |
| 2 | `97-03` arguments/leases/memory | As enumerated in `97-03-PLAN.md` `files_modified` | Depends on `97-02`; single owner per shared file; owns full client/lead changes in its wave. |
| 3 | `97-04` config/settings/diagnostics | As enumerated in `97-04-PLAN.md` `files_modified` | Depends on `97-03`; shared files are serialized after plan 03. Preserve runtime construction and error behavior from plan 03. |
| 4 | `97-05` docs/integration | As enumerated in `97-05-PLAN.md` `files_modified` | Depends on `97-01` and `97-04`; must also document final 97-02/97-03 behavior. No source or test ownership. |

All plan implementation ownership resolves to `bot-00-omega-prime` via `**`; there is no same-wave shared implementation file. The current planning worker's research/validation artifacts do not confer source ownership. The parent is the sole integration and verification owner.

## Receipt and unverified claims

The execution receipt is `.receipts/bot-00-omega-prime/phase97-execution.json`. It records local commands and gate outcomes and explicitly leaves current upstream server compatibility, live endpoint behavior, and future-agent rule adherence unverified. This is a phase execution record, not a milestone audit/completion artifact.

Unverified: current upstream MCP mount/schema/version/response compatibility because authoritative evidence was not accessible; live endpoint/auth/connector connectivity because the VPS is unavailable and live checks are operator-owned; rule adherence by future agents.
