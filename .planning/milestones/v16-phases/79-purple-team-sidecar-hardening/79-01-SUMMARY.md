---
plan: 79-01
status: complete
phase: 79
---

# 79-01 Summary: Sidecar harness core

## Tasks completed

- Task 1: `sidecar/state.py` state machine — IDLE/RUNNING/DEGRADED/BLOCKED/ERROR with RFC3339-timestamped transitions appended under `sidecar/reports/` (`OMEGA_PRIME_SIDECAR_REPORTS_DIR` override for hermetic tests); legal-transition map with logged rejections; `sidecar/VERSION`, `sidecar/contract/bot-interface.json` (11 REDTEAM_TOOL_NAMES + loopback gateway).
- Task 2: `sidecar/roe.py` (schema `roe.schema.json`, load/validate, detached HMAC-SHA256 `sign_artifact`; JSON-only artifact with `roe.yaml.template` sign/convert steps), `sidecar/harness.py` (exit 3 BLOCKED without present+unexpired artifact; cause category on stdout; transition logged).
- Task 3: config wiring `redteam.kill/roe_required/roe_file/roe_key` + env overrides in `omega_prime/config.py`; guarded registration in `omega_prime/mcp_server.py` (zero tools unless IDLE; kill evaluated first, also inside `tools/redteam.py:_call` for in-flight abort); `omega_prime/metrics.py` sidecar gauge wired into served `/metrics` in `omega_prime/grokbot/remote.py` (parent follow-up applied).

## Deviations

- Added `OMEGA_PRIME_SIDECAR_REPORTS_DIR` (test isolation; env wins, else `sidecar/reports/`).
- Added 4th config key `redteam.roe_key` (HMAC check unwirable otherwise; empty default fails closed; never logged).
- IDLE/BLOCKED/ERROR self-reaffirmation allowed (fresh evaluations); all other self-loops illegal.
- Default-off path stays silent (no audit row) to keep default behavior byte-identical.

## Verification

`test_sidecar_state.py`, `test_sidecar_roe.py`, `test_sidecar_guardrails.py`, `test_redteam.py` green (49→64 tests after fixes); `harness.py --check` exit 3 BLOCKED; full suite 2642 passed / 0 failed (see 79-VERIFICATION.md). Commit 699db86 + parent fixes in the phase commit.
