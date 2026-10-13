---
status: passed
phase: 79
requirement_coverage:
  - id: SIDE-01
    status: passed
    human_judgment: false
  - id: SIDE-02
    status: passed
    human_judgment: false
  - id: SIDE-03
    status: passed
    human_judgment: false
---

# Phase 79 Summary: Purple-team sidecar hardening

## What was built

The T3MP3ST red-team sidecar now survives constant upstream upgrades and is engagement-gated by design:

1. **Harness core (79-01, commit 699db86)** — `sidecar/` at repo root, stdlib-only: `state.py` (IDLE/RUNNING/DEGRADED/BLOCKED/ERROR with RFC3339-timestamped transitions to `sidecar/reports/`, env-overridable reports dir), `roe.py` (rules-of-engagement schema `sidecar/roe.schema.json`, load/validate, detached HMAC-SHA256 signing via `sign_artifact`), `harness.py` (exit 3 BLOCKED without a present, unexpired artifact; cause category on stdout; kill-switch precedence), `roe.yaml.template` (human shape + sign/convert steps), `sidecar/VERSION`, `sidecar/contract/bot-interface.json` (the 11 `REDTEAM_TOOL_NAMES` + loopback gateway contract). Config keys `redteam.kill` / `redteam.roe_required` (default true) / `redteam.roe_file` / `redteam.roe_key` with `OMEGA_PRIME_REDTEAM_*` env overrides; guarded registration in `omega_prime/mcp_server.py` (zero red-team tools unless IDLE; kill evaluated first, also inside `_call` for in-flight abort); `omega_prime/metrics.py` sidecar-state gauge wired as a serve-time snapshot into the served `/metrics` registry in `omega_prime/grokbot/remote.py`.
2. **Simulation library + findings (79-02)** — `sidecar/case.schema.json` plus six versioned case files, one per technique class (prompt-injection, jailbreak, abuse, malformed-input, auth-bypass, cost-abuse), each declaring loopback-only transport and inert payloads; strict minimal-YAML case loader that fails closed on hostile YAML constructs; `simulation.py` (run/validate: drift-check first, IDLE→RUNNING→IDLE transitions, per-case observed-vs-expected detection comparison, `last-run.json` record; a pass requires observed detection to equal the expectation, `residual_rationale` excuses exactly its own case); `findings.py` (sha256 content-hash dedupe with replay occurrence counts, atomic writes, offline `work-items.json` export, `check_drift` VERSION/contract/adapter-surface comparison → DEGRADED with the disagreeing pair named, adapter read via ast — never imported).
3. **Freshness automation (79-03)** — `sidecar/provenance.py` (check/pack-diff/surface: gitlink ≡ `VENDOR.md` red-team section ≡ `__init__.py` docstring; vendored pack byte-diff vs upstream at the pin; upstream `mcp-server.ts` surface vs `REDTEAM_TOOL_NAMES`); `gitsubmodule` Dependabot entry; `redteam-provenance` CI job (submodules, contents:read, fail-closed); `redteam-node-audit` job in `supply-chain.yml` (npm audit, dated TRIAGED block mirroring the PYSEC-2026-4114 precedent, fails on untriaged high+); 6-step bump runbook in `docs/red-team.md`.

## Tasks completed

- 79-01: state machine, RoE schema/validator/sign, fail-closed harness entry, kill switch + config wiring, VERSION + interface contract
- 79-02: case schema + six-class library, simulation runner with detection validation, findings pipeline + drift→DEGRADED
- 79-03: provenance checker, Dependabot + two CI jobs, bump runbook + packaging assertions

## Decisions and deviations

- **Ships BLOCKED** (user decision 2026-10-13): no live engagement paths exist; harness exits 3 BLOCKED at the default config; `roe_required=true` is the fail-closed default.
- RoE artifact is **JSON-only** (stdlib has no YAML parser); `roe.yaml.template` documents the sign/convert flow. Validation order: schema → signature → window → targets.
- Config gained a 4th key `redteam.roe_key` beyond the plan's three — the detached-HMAC check is unwirable without a config-supplied key; empty default fails closed; never logged.
- `/metrics` gauge is a **serve-time snapshot** of the registration-time guard state (remote.py is outside 79-01's file scope; live gauge re-evaluation is a recorded follow-up). With the sidecar BLOCKED the value cannot advance mid-serve.
- Docker context excludes `/sidecar` entirely (generator + committed `.dockerignore` in lockstep), subsuming the plan's reports-only intent; bootstrap path has no sidecar references (asserted by tests).
- Checker advisory fixes applied: CI YAML verify now hard-fails on unparseable YAML and greps permission scopes (79-03); GAP-14 resource-caps/identity and GAP-15 credential TTL/egress are **deferred to the RoE-unblock phase** (unenforceable while BLOCKED) in `.planning/sidecar-audit.md`; provenance checker location recorded in the runbook.
- `test_all_prime_flags_serve_the_roster` updated to the phase-79 contract: red-team enabled without a signed RoE serves roster-minus-tempest (BLOCKED); a new `test_signed_roe_completes_the_roster` proves the signed-RoE path completes the 165-name roster. Completeness invariant preserved, not weakened.

## Verification results (parent-run)

- Full suite: see `79-VERIFICATION.md` (final run recorded there; interim runs 2640 passed / 1 failed → the one failure was the superseded roster invariant above, now split into two passing tests).
- Sidecar surface: tests across `test_sidecar_state.py`, `test_sidecar_roe.py`, `test_sidecar_guardrails.py`, `test_sidecar_cases.py`, `test_sidecar_simulation.py`, `test_sidecar_findings.py`, `test_sidecar_provenance.py`, `test_sidecar_packaging.py` + `test_redteam.py` all pass (111+ in the sidecar subset); `ruff check` + `format --check` clean; `mypy --explicit-package-bases sidecar/*.py` clean (CI mypy scope stays `omega_prime/`).
- Behavioral proofs: `sidecar/harness.py --check` → exit 3 `BLOCKED (disabled): redteam.enabled is false (default off)`; `provenance.py check|pack-diff|surface` → exit 0 at pin `831a6b0` (25 pack files byte-identical, 11 surface names agree); `simulation.py run` then `validate --suite smoke` → exit 0; the three edited workflow files parse as YAML and grant no write permissions.
- One real defect found by the new gate during bring-up: the provenance checker initially read VENDOR.md's first `Commit:` pin (the Hermes pin) instead of the red-team section's — fixed with section-scoped extraction plus a regression test.

## Known issues and follow-ups

- GAP-14 (resource caps, service identity) and GAP-15 (dedicated credential TTL/rotation, egress allowlist) are deferred to the RoE-unblock phase.
- Live gauge re-evaluation mid-serve; audit-row emission on the default-off path intentionally absent (byte-identical default behavior).
- Dependabot/CI jobs are authored but the hosted run is unproven until the next push (no hosted workflow execution in this phase).

## Commit

- 699db86 `feat(sidecar): 79-01 harness core — RoE fail-closed BLOCKED startup, kill switch, logged state machine (Refs SPE-10596)`
- (this commit) 79-02 + 79-03 + summary/verification artifacts
