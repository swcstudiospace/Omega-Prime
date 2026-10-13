---
status: passed
phase: 83
verified_at: 2026-10-13
covered_files:
  - omega_prime/sandbox/__init__.py
  - omega_prime/sandbox/policy_gen.py
  - omega_prime/sandbox/runner.py
  - omega_prime/hosting/openshell/sandbox-policy.yaml
  - omega_prime/tools/quality.py
  - omega_prime/receipts.py
  - omega_prime/config.py
  - .github/workflows/ci.yml
covered_digest: v3:sha256:2412c3ffc9149bed3ef26db563d558e34cd9fb18278880951f7e2c406a4c0bc8
---

# Phase 83 Verification

## Requirement outcomes

| Req | Outcome | Evidence |
|-----|---------|----------|
| SAND-01 | ✓ VERIFIED | Policy is generated from `SeatPolicy.load` (unknown keys rejected loudly), emitted deterministically (regen-twice and regen-vs-committed diffs empty; full header emitted so CLI diff and snapshot test agree), cutover committed with a golden snapshot (`test_sandbox_policy_gen.py`, 15 tests); writable paths derive from Omega home (`~/.omega-prime`) and work-root state; network default-deny with only seat-policy hosts; CI `sandbox-policy-drift` job re-generates and diffs; `sandbox` config node default-off with env overrides and loud bogus-scope handling. Intentional byte deltas vs the hand-written file are derivation-only (+4 seat read_only globs, +home scratch, +work-root state, quoted best_effort). |
| SAND-02 | ✓ VERIFIED | Gates run inside the sandbox when scope covers the gate and the binary is present (`SandboxRunner`, real probe, no fallback, no shell=True); `sandbox.enabled` off ⇒ byte-identical flag-off receipts (exact key set) and unchanged gate behavior; enabled-but-absent binary ⇒ degraded receipt `ok=false` with named cause (`sandbox_unavailable: binary-missing`), argv never executed, empty log; receipts persist `command_digest` + `policy_sha256` (sandboxed records only) and `validate_receipt(expected_policy_sha256=…)` + the wired `receipt_approve` call site reject a swapped policy digest naming the command index (C83 warning closed). |

## Automated runs (parent, 2026-10-13)

- New suites: 38 passed with the binary present; 37 passed + 1 explicit live-probe skip with a scrubbed PATH (hermeticity proven both ways).
- Touched suites: prime_config, policy, quality, receipts e2e/grokbot/desk, sandbox×3 → 102 passed.
- CI drift invocation: regen to /tmp and `diff` against the committed policy → identical.
- ruff clean after parent pass (also repaired a swallowed `ToolRegistry` import in quality.py that ruff caught as F821); full suite close-of-phase: **2762 passed, 0 failed, 16 warnings** in 238.14s.

## Known limits

- Execute-path sandboxing remains the SEED-012 follow-up (ROADMAP SC2 wording carries the deferral; decision log 2026-10-13).
- Openshell-present behavior is probe-gated in tests; no live sandbox run is claimed where the binary is absent (this host).
