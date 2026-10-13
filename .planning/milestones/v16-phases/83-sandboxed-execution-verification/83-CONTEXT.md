# Phase 83: Sandboxed execution verification - Context

**Gathered:** 2026-10-13
**Status:** Ready for planning
**Mode:** Auto-generated (discuss skipped via workflow.skip_discuss)

<domain>
## Phase Boundary

Generate the OpenShell sandbox policy from the seat policy and run verification/execute paths inside sandboxes with receipts bound to executed commands. Sliced scope (per plan-05): sandbox policy generation + gates-in-sandbox first; binding quality receipts to recorded executed command + exit. Out of scope: running untrusted third-party code, OpenShell installation (Apache-2.0 binary expected at /usr/bin/openshell 0.1.2 when present; absent = explicitly degraded, never silent), replacing the existing quality gate runner.

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
All implementation choices are at Claude's discretion — discuss phase was skipped per user setting.

### Recorded decisions binding this phase
- Sandbox availability degrades explicitly: `sandbox.enabled=true` without a working openshell → degraded receipts naming the cause, never a silent pass (plan-05 SLICE constraint).
- Generated policy replaces the hand-maintained `omega_prime/hosting/openshell/sandbox-policy.yaml` (regenerate, don't hand-edit — Dockerfile precedent).
- Writable paths derive from Omega home/root defaults (SEED-006 mismatch fix).

</decisions>

<code_context>
## Existing Code Insights

Verified evidence (`.planning/recon-evidence.md` R7): hand-maintained `omega_prime/hosting/openshell/sandbox-policy.yaml` that nothing generates or tests; execute/verification paths do not run sandboxed; writable paths don't match Omega home/root defaults (SEED-006); verification receipts are structure-checked, not bound to executed commands (`omega_prime/tools/quality.py:148-155,240-241` — SEED-012 slice); seat policy machinery in `policy/policy.py` (per SEED-006 breadcrumbs). Plan: `.planning/updates/plan-05.md` (U5; `sandbox.enabled/policy/scope` config).

</code_context>

<specifics>
## Specifics

- Gates-in-sandbox: the quality gate runner executes recorded commands; when sandbox scope covers them and openshell is present, the same command runs inside the sandbox with the generated policy; sandbox-off behaves byte-identically to today.
- Receipt binding: receipts must cite the exact argv, exit status, and (when sandboxed) the sandbox policy digest — model-written receipts no longer satisfy gate status.
- No network in generated policy except what the seat policy already allows; policy generation is deterministic and tested (snapshot test).

</specifics>

<deferred>
## Deferred Items

- Full SEED-012 (gate runner beyond the Omega repo, Greptile binding), sandboxed execute-path coverage beyond verification gates.

</deferred>
