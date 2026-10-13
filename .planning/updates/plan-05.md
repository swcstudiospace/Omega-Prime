# U5 — OpenShell Sandboxed Execution + Verification-Bound Receipts → Phase 83

Issue: SPE-10594 · Milestone v16 · Evidence: `local://v16-recon-findings.md` (HEAD df14512)

## TITLE_AND_SUMMARY

Close SEED-006 and SEED-012 together: generate the OpenShell sandbox policy from the seat
policy instead of hand-maintaining it, run the verification and execute paths inside OpenShell
sandboxes, and bind quality receipts to the commands that were actually executed.

Definitions:
- **OpenShell sandbox**: an nvidia/openshell sandbox created from a policy YAML — filesystem
  and network permissions declared up front, network default-deny; the existing
  omega_prime/hosting/openshell/sandbox-policy.yaml documents this usage (verified in-tree).
- **Seat policy**: the JSON document parsed by `SeatPolicy.load` (omega_prime/policy/policy.py)
  with `paths.read_only` (glob list) and `network.hosts` (exact-match allowlist, no implied
  subdomains); "no policy attached anywhere means today's behavior".
- **Verification-bound receipt**: a quality receipt (tools/quality.py) that records the
  executed command strings and their SHA-256 hashes, so a receipt cannot be presented for work
  whose commands were never run or ran under different permissions.
- **Slice order (fixed)**: policy generation + gates-in-sandbox first; execute-path sandboxing
  and receipt binding follow in this plan.

## EVIDENCE

- R7 / SEED-006: hand-maintained, untested sandbox-policy.yaml; nothing in omega_prime runs
  sandboxed.
- R7 / SEED-012: verification receipts are not bound to executed commands
  (tools/quality.py:148-155, 240-241).
- R2: deploy.py already proves the deterministic-renderer + hardening-checker pattern
  (targets docker/compose/systemd/k8s; hardening check() at deploy.py:1069-1108) — the policy
  generator reuses this shape.
- R1: the tool host is a Python MCP server (stdio default), so sandboxing is per-execution
  context, not per-image.

## VALUE_PROPOSITION

- The sandbox policy stops drifting from the seat policy: one source of truth, generated
  output, and a test proving the mapping.
- Gates (verification) and execution gain an enforced permission boundary that does not depend
  on the agent's own judgment (the seat-policy design goal, omega_prime/policy/policy.py).
- Receipts become evidence again: a receipt that names its commands and hashes can be
  re-verified, which is the whole point of a receipt.

## ARCHITECTURE_SKETCH

- `omega_prime/sandbox/policy_gen.py` (new): reads a seat-policy JSON via `SeatPolicy.load`
  and emits an OpenShell sandbox policy YAML:
  - `paths.read_only` globs → read-only filesystem grants;
  - `network.hosts` → the (usually empty) network allowlist; default-deny preserved
    (the existing hand-written policy is already default-deny, verified in-tree);
  - output is deterministic (same seat policy → byte-identical YAML), following the
    deploy.py deterministic-renderer precedent (R2).
  CLI: `python -m omega_prime.sandbox.policy_gen --seat <seat.json> --out <policy.yaml>`.
  The hand-maintained omega_prime/hosting/openshell/sandbox-policy.yaml becomes generated
  output checked into the repo, regenerated in CI to prove no drift.
- `omega_prime/sandbox/runner.py` (new): runs a command list inside a sandbox
  (`openshell sandbox create --name <id> --policy <generated> --no-auto-providers`, per the
  in-tree usage comment) and captures exit + output; sandbox-creation failure ⇒ the guarded
  path fails closed with an error, never falls back to unsandboxed execution.
- Gates-in-sandbox: the verification path (tools/quality.py verification entry points) runs
  its gate commands through runner.py when `sandbox.enabled` is true; `execute` paths follow
  in the second slice with the same runner and the same fail-closed rule.
- Receipt binding: tools/quality.py receipts (tools/quality.py:148-155, 240-241) gain
  `executed_commands: [{cmd, sha256}]` and `sandbox_policy_sha256`; a receipt written by a
  sandboxed run is tamper-evident against its recorded commands.
- Drift guard: a CI job regenerates the policy from the canonical seat policy and diffs
  against the committed file.

## IMPLEMENTATION_STEPS

1. **Policy generator.** Implement policy_gen.py with the SeatPolicy → OpenShell mapping.
   **Verify:** `python -m pytest tests/test_sandbox_policy_gen.py -q` — covers read_only globs, empty and non-empty host allowlists, and default-deny output; `python -m omega_prime.sandbox.policy_gen --seat <fixture> --out /tmp/p.yaml && python -m omega_prime.sandbox.policy_gen --seat <fixture> --out /tmp/p2.yaml && diff /tmp/p.yaml /tmp/p2.yaml` — empty (determinism).
2. **Cutover the hand-maintained file.** Regenerate
   omega_prime/hosting/openshell/sandbox-policy.yaml from the canonical seat policy; keep the
   file's header comment pointing at the generator.
   **Verify:** `python -m omega_prime.sandbox.policy_gen --seat <canonical seat policy> --out - | diff - omega_prime/hosting/openshell/sandbox-policy.yaml` — empty diff; the OpenShell create command from the header still works verbatim.
3. **CI drift guard.** Add the regeneration-diff job to .github/workflows/ci.yml.
   **Verify:** CI run on the PR shows the drift job green; hand-editing the committed YAML in a scratch branch turns it red with the diff attached.
4. **Sandbox runner.** Implement runner.py with fail-closed semantics (no unsandboxed
   fallback).
   **Verify:** `python -m pytest tests/test_sandbox_runner.py -q` — sandbox present: command runs inside and output is captured; sandbox creation forced to fail: guarded call raises/errors and records `sandbox_unavailable` (observable in the error payload), never executes the command outside.
5. **Gates in sandbox (slice 1).** Route the verification path's gate commands through
   runner.py under `sandbox.enabled`.
   **Verify:** `python -m pytest tests/test_quality_gates_sandboxed.py -q` — with the flag off, gate behavior is byte-identical to today (existing tests pass unchanged); with it on, the gate's commands run under the generated policy and a gate that tries network egress is denied by the default-deny policy (observable denial in the gate result).
6. **Execute paths in sandbox (slice 2).** Apply the same runner to the execute path.
   **Verify:** `python -m pytest tests/test_execute_sandboxed.py -q` — same off/on matrix as step 5; a seeded command that writes outside its grant fails with the sandbox denial, and no partial write escapes (checked by fixture).
7. **Receipt binding.** Extend receipts with `executed_commands` (+sha256) and
   `sandbox_policy_sha256` at tools/quality.py:148-155, 240-241.
   **Verify:** `python -m pytest tests/test_quality_receipt_binding.py -q` — a receipt from a sandboxed gate run lists exactly the commands the run executed (cross-checked against runner's capture); altering a recorded command in the receipt file makes the receipt verifier reject it.
8. **Docs.** Document the generator, the flag matrix, and receipt fields in docs/tool-host.md.
   **Verify:** `grep -n "sandbox policy" docs/tool-host.md` finds the section; the documented generate command matches step 2's verified invocation.

## DEPENDENCIES

- None inside milestone v16 on plan-01..04/06/07. Heritage: SEED-006 (sandbox) and SEED-012
  (receipt binding) — this plan closes both (R7).
- plan-03 soft dependency: sandbox denials are tool-level errors; if they should surface in
  SLO/denial metrics, they arrive via the existing `omega_tool_denials_total{code}` series —
  no ordering constraint.
- External: `openshell` binary availability on hosts where sandboxing is enabled; gate hosts
  without it keep `sandbox.enabled` false (default).

## RISKS_AND_MITIGATIONS

- *Policy mapping loses a seat-policy nuance.* The mapping is table-driven and tested per
  field (step 1); unknown seat-policy keys make generation fail loudly rather than drop them.
- *Sandbox latency on hot paths.* Sandboxing applies to gates and execute paths only, not the
  request path; the flag matrix keeps non-sandboxed parity tests green.
- *OpenShell unavailable in CI.* CI exercises the generator, drift guard, and runner's
  fail-closed path with a stub transport; full in-sandbox runs are verified in the maintainer
  environment and recorded in phase VERIFICATION (mirrors how PyRIT stays hermetic, R3).
- *Receipts grow large with many commands.* Records are one line per executed command with a
  hash — bounded by gates actually running a handful of commands; no sampled/faked entries.
- *False confidence from a stale generated policy.* The CI drift guard (step 3) fails any
  hand-edit, so the committed policy is always the generated one.

## ROLLOUT_AND_ROLLBACK

- Config flag: `sandbox.enabled` (node key, default `false`) and `sandbox.policy` (path to the
  generated policy, default the repo file), plus `sandbox.scope` ∈ {`gates`, `gates+execute`}
  (default `gates` — the fixed slice order), in the omega_prime/config.py node-key style with
  env overrides `OMEGA_PRIME_SANDBOX_ENABLED` / `OMEGA_PRIME_SANDBOX_POLICY` /
  `OMEGA_PRIME_SANDBOX_SCOPE` (config.py:100-115 `OMEGA_PRIME_*` pattern).
- Rollout: generate + commit policy (no behavior change) → enable `gates` in one deployment →
  widen to `gates+execute` after denial-free observation.
- Revert step: set `OMEGA_PRIME_SANDBOX_ENABLED=0` (or delete the node key) — gates and
  execute paths run exactly as today; the generated policy file remains but is only consumed
  when the flag is on. Receipts written while sandboxed keep their recorded commands and
  remain verifiable after rollback (the fields are additive).

## SUCCESS_METRICS

- Committed sandbox policy is provably generated: CI drift guard green on every push; zero
  hand-edits post-cutover (SEED-006 closed).
- With the flag on, zero gate/execute commands run outside a sandbox — the fail-closed runner
  test proves the no-fallback rule (SEED-012's enforcement half).
- Every sandboxed receipt binds its exact executed commands + policy hash; receipt
  verification rejects any tampering in the step-7 test.
- Non-sandboxed default behavior unchanged: the full existing suite passes with the flag off
  (parity is a gate, not a hope).

## GSD_PHASE_MAPPING

- Milestone v16, **Phase 83** — "OpenShell sandboxed execution + verification-bound
  receipts".
- Phase directory under the v16 phases layout (`.planning/<v16-phases>/83-<slug>/`), v14/v15
  `NN-<slug>` convention.
- Execution order: steps 1-3 (generator + cutover + drift guard) are the first task wave;
  steps 4-6 (runner + gating, in slice order) the second; steps 7-8 the last. Wave 2 depends
  on wave 1; wave 3 depends on step 4 only.
- Phase verification: SUCCESS_METRICS become VERIFICATION.md checks; "flag off = byte-identical
  behavior" is a hard acceptance criterion for the slice rollout.
