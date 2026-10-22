# Phase 112 Plan 02: omp-sandbox Engine Backend Summary

**Status:** complete
**Phase:** 112-sbx3-engine-backend / **Plan:** 02
**Requirements:** W112-02, W112-03, W112-04
**Node:** task 00030 node n3 (steps 1, 2, 3, 5; tests are 112-03's step 6)
**Branch:** feat/sbx-3-engine-backend (no push, no PR, no remote)

One-liner: a stdlib-only Anda stdio client (`omega_prime/engine/sandbox_omp.py`) drives GSD stages
inside the omp-sandbox wrapper, selected only by `OMEGA_PRIME_OMP_BACKEND=omp-sandbox`, admitted
through the `OMP_SANDBOX_WORKSPACE_ROOTS` allowlist before any spawn, routed through
`plan_router.select` (zai → glm-5.3 plan / glm-5.3-flash execute), mapped into the frozen
`EngineResult` with exported commits under `stats["omp_sandbox"]`, every wrapper failure surfacing
as `sandbox_unavailable` through `run()`'s existing phase-111 wiring.

## Per-task commits

| Task | Commit | Subject |
|------|--------|---------|
| T1 | 478eaea | 00013 - engine: Anda stdio client sandbox_omp.py (W112-02) |
| T2 | f25565f | 00013 - engine: omp-sandbox backend selection, workspace allowlist, probe and _run branch (W112-02) |
| T3 | 4932c07 | 00013 - engine: engine_plan pass-through on delegate_via_engine and ultrathink_engine (W112-03) |
| T4 | a21b853 | 00013 - engine: one EngineResult contract for the omp-sandbox backend (W112-04) |

Only the three plan-owned files were ever staged:
`omega_prime/engine/sandbox_omp.py` (new), `omega_prime/engine/omp_driver.py`,
`omega_prime/engine/attach.py`. The sibling 112-01 commits interleaved in the log
(da27928, 25af193) touch `omp-sandbox/**` only.

## Verification (executor environment, this sandbox)

- `python3 -m py_compile omega_prime/engine/sandbox_omp.py omega_prime/engine/omp_driver.py omega_prime/engine/attach.py` — clean, exit 0.
- Three engine suites, run UNPREFIXED from the repo root (the plan's `PYTHONPATH=.` prefix breaks
  pytest in this sandbox — "No module named pytest" — per the task brief's environment facts):

```
=========================== short test summary info ============================
FAILED omega_prime/tests/test_engine_stage.py::test_mcp_desk_parent_reads_the_stage_from_its_env - ModuleNotFoundError: No module named 'mcp'
1 failed, 68 passed in 4.08s
```

Identical to the pre-change baseline: the one pre-existing `mcp`-import failure (optional dep
absent, documented in 112-CONTEXT) and the same 68 green. Zero new failures, zero skips added,
no test touched.

- Import-order safety (sandbox_omp ⇄ omp_driver lazy import): both
  `import sandbox_omp; import omp_driver` and the reverse succeed.
- `omega_prime/engine/sandbox.py`: byte-identical (0 diff lines vs the plan's base).
- `EngineResult`: no field changes (0 diff hunks); no `TierUnavailable` class anywhere under
  `omega_prime/engine/`; no second block writer (`raise_block` appears nowhere in sandbox_omp.py;
  run()'s phase-111 wiring is consumed as-is).
- Functional smoke of the new branch (throwaway /tmp harness with a fake Anda wrapper speaking the
  client.rs protocol; 23/23 checks): probe honest without bwrap (`(True, "ok")` with no bwrap on
  PATH); full run routes to the backend with `sandbox="omp-sandbox"`, plan/provider/model mirrored
  from the Choice (zai / zai / glm-5.3); `session.json` carries the bwrap key set;
  `commands == ["up", "run_gsd_stage", "sandbox_status", "export_commits", "down"]`;
  `stats["omp_sandbox"]` has exactly the documented keys; wrapper events + final down-summary
  `job_end` land in events.jsonl through the redacting `append_event`; worktree outside the roots
  refused with `path_refused` BEFORE any wrapper spawn (spawn log absent); tier failure
  (ok:false `anda_error`) → `sandbox_unavailable`; failed stage → `ok=False, reason="engine"` with
  the record's error in detail; interrupt mid-stage → abort sent, `stage_done(aborted)` awaited,
  `reason="cancelled"` with `started=True`; unset/other `OMEGA_PRIME_OMP_BACKEND` values keep the
  bwrap gates (`(False, "sandbox_unavailable")` from the bwrap check, never the backend).

## Documented stats keys

`stats["omp_sandbox"] = {"stage_id", "tier", "status", "model_requested", "export"}`

- `stage_id`: the wrapper's stageId from `run_gsd_stage`.
- `tier`: the `up` result's `tier` (or None).
- `status`: the final `stage_done` status string.
- `model_requested`: the `run_gsd_stage` result's `model` (what the engine asked for; e.g.
  `zai/glm-5.3` plan, `zai/glm-5.3-flash` execute).
- `export`: the `export_commits` result dict `{head, base, ref, commits, dirty, bundle, bytes,
  via}` (as returned by the wrapper), or None when the stage did not complete.

## Decisions and contract deviations

1. **Keyword name `engine_plan` (documented contract deviation, pinned by the plan).** The shared
   contract says both attach entry points gain `plan: str | None = None`, but
   `ultrathink_engine`'s first positional `plan` already is the ultrathink spec mapping — a second
   `plan` parameter is a Python SyntaxError. Both `delegate_via_engine` and `ultrathink_engine`
   therefore gain keyword-only `engine_plan: str | None = None`, the established in-tree name for
   the requested coding plan (route_turn threads `agent.engine_plan` into `run(plan=...)`,
   attach.py:174). Threaded verbatim into `run(plan=engine_plan)`; existing callers and defaults
   untouched; 112-03 pins the same name.
2. **Bounded-wait constants.** Sized in the spirit of `_READY_TIMEOUT`/`_COMMAND_TIMEOUT` (15s
   each) but with head-room for what the bounds actually cover: `_WRAPPER_READY_TIMEOUT = 30.0`
   (handshake) and `_TOOL_TIMEOUT = 300.0` (per-tool replies; `up` boots broker + tier + VM +
   GSD-Core and the TS core's own internal bounds run to 300s). The stage wait itself has no
   deadline, per plan.
3. **session.json truthfulness.** The post-run write carries the bwrap success key set with
   `"ok": <stage ok>` — True on a completed stage, False when a failed/aborted stage returns
   `ok=False` instead of raising (the bwrap path only ever writes this file on success; the
   backend writes it on both return paths so the file always matches the returned result).

## Known stubs

None. Every code path in the three files is wired; no placeholders, no TODOs, no mocks. The
Rust anda wrapper is not built in this environment by design (112-03's fake wrapper exercises the
client through `OMEGA_PRIME_OMP_SANDBOX_ANDA`); nothing here depends on a stub.

## Out of scope / not done here (by plan)

- No behavioral tests (112-03 owns `omega_prime/tests/**`, wave 2).
- No omp-sandbox changes (112-01's tree), no dashboard changes, no new dependencies (stdlib +
  in-tree engine modules only).
- `OMEGA_PRIME_OMP_ALLOW_ZAI` / `ZAI_API_KEY` values are never printed, logged or stored;
  presence/truthiness only (via `plan_router`, unchanged).
