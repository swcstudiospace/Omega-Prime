# Phase 83 Plan 02: Gates in Sandbox + Receipt Binding Summary

**One-liner:** Fail-closed OpenShell runner routing verification gates
through sandboxes with argv/exit/policy-digest receipt binding and
explicit degraded mode.

## Frontmatter

- phase: 83, plan: 83-02, subsystem: sandbox-execution
- requirements: [SAND-02]
- status: complete
- key-files:
  - created: omega_prime/sandbox/runner.py,
    omega_prime/tests/test_sandbox_runner.py,
    omega_prime/tests/test_quality_gates_sandboxed.py
  - modified: omega_prime/tools/quality.py, omega_prime/receipts.py,
    docs/tool-host.md

## Tasks

### Task 1: Fail-closed runner + injectable transport — done
- `omega_prime/sandbox/runner.py`: real probe (`which` + `--version`,
  runnable-not-version) for the default transport; injected transports
  fully emulate presence via their `--version` verdict (stub tests never
  touch ambient PATH); create argv
  `openshell sandbox create --name <id> --policy <policy>
  --no-auto-providers -- <argv...>`; combined-output tail -2500;
  every result records argv/argv_sha256/exit/policy_sha256.
  Creation/policy/probe failures raise `SandboxUnavailable` naming
  binary-missing / create-failed / policy-unreadable — no unsandboxed
  fallback exists; no `shell=True` anywhere.
- 10 runner tests: stub success, create-failure no-execution proof
  (marker file absent), emulated + real absent-binary, unreadable
  policy, metacharacter argv integrity, argv shape, live probe skip.

### Task 2: Gates-in-sandbox routing + degraded receipts — done
- `QualityContext` gains `sandbox` (config-node shape) and
  `sandbox_transport`; `_sandbox_cfg` enables only on explicit
  `enabled is True` with scope in {gates, gates+execute} (bogus scope
  disables). Flag-off executes today's exact path and shape
  (`{"exit_code","output"}` only — no additive fields).
- Sandboxed entries add `argv`, `argv_sha256`, `exit_code`, `sandbox`
  block; successes append to the command log with `policy_sha256`;
  enabled-but-absent entries carry `sandbox_unavailable: <cause>`,
  exit 127, `ok False`, argv never executed.
- 13 routing tests: off-parity (4 variants), stub routing + digest
  match + log record, gates+execute scope, gate-failure binding,
  egress-denial observability, metacharacters, absent/create-failure
  degradation, tamper rejection, approve cross-check, ghost rejection.

### Task 3: Receipt tamper-evidence + docs — done
- `receipts.py`: `command_digest` canonical hash; `append_execution`
  accepts additive `policy_sha256` (persisted only for sandboxed runs —
  flag-off records byte-identical); `validate_receipt` gains
  `expected_policy_sha256` and verifies cited `argv_sha256` against the
  cited cmd plus cited `sandbox_policy_sha256` against the expected
  digest and the captured record — mismatches raise naming the index.
  Receipts without sandbox fields validate exactly as before.
- `receipt_approve` passes the expected digest derived from the ctx
  sandbox policy (C83: swapped policy digest rejects approval).
- docs/tool-host.md finalized: flag matrix with env names, degraded
  contract, receipt field reference with tamper-reject guarantee;
  create-command usage unchanged.

## Deviations from Plan

- **C83 binding:** `policy_sha256` persisted in execution records and
  wired into the `receipt_approve` call site (argv-tamper check alone
  cannot catch a swapped policy digest).
- **83-02 Task-2 binding:** additive receipt fields appear only on
  sandboxed runs; flag-off entries keep today's exact key set.
- **Probe split (correctness fix):** first cut consulted ambient PATH
  before the injected transport, so stub-present tests failed with PATH
  scrubbed. Restructured: default transport → real probe; injected
  transport → emulated probe. Hermetic both ways.
- **Transport fallback fix:** quality initially fell back to its own
  `_default_run` (swallows FileNotFoundError), mislabeling a missing
  binary as unrunnable; now falls back to the runner default so the
  cause reads `binary-missing`.

## Verification (exercised)

- New files: 38 passed (binary present), 37 passed + 1 skip (PATH
  scrubbed, no openshell).
- Degraded proof (binary hidden): `ok False`,
  `sandbox_unavailable: binary-missing`, no side-effect file, empty log.
- Tamper proof and parity proof covered in-test (forged digest /
  altered cmd / swapped policy all reject; off-shape exact).
- Existing suites left for parent run per assignment constraints.

## Known Stubs

None.

## Deferred (out of scope per plan-05 slice order)

Execute-path sandboxing beyond verification gates (unchanged here).
