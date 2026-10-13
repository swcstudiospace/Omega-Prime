# Phase 83 Plan 01: Sandbox Policy Generation Summary

**One-liner:** Deterministic seat-to-OpenShell policy renderer with generated-file cutover, snapshot golden, sandbox config node, and CI drift guard.

## Frontmatter

- phase: 83, plan: 83-01, subsystem: sandbox-policy
- requirements: [SAND-01]
- status: complete
- key-files:
  - created: omega_prime/sandbox/__init__.py, omega_prime/sandbox/policy_gen.py,
    omega_prime/tests/test_sandbox_policy_gen.py,
    omega_prime/tests/data/sandbox-policy-golden.yaml
  - modified: omega_prime/hosting/openshell/sandbox-policy.yaml (regenerated),
    omega_prime/config.py, omega_prime/tests/test_prime_config.py,
    .github/workflows/ci.yml, docs/tool-host.md

## Tasks

### Task 1: Sandbox policy generator module + CLI — done
- `omega_prime/sandbox/policy_gen.py`: reads seat JSON **only** via
  `SeatPolicy.load`, rejects unknown top-level seat keys loudly, maps
  `paths.read_only` globs alongside the fixed system set, derives
  read-write from `Path.home()/".omega-prime"` + repo-layout-derived
  work-root state + `/sandbox,/tmp,/dev/null`, emits default-deny
  network (allowlist only from seat `network.hosts`), deterministic
  YAML emitter mirroring the deploy.py precedent, CLI with `--out -`,
  plus `digest_bytes`/`digest_file` helpers for 83-02 receipt binding.

### Task 2: Cutover + snapshot golden — done
- Committed policy regenerated via the Task-1 CLI (zero hand-edits);
  generator emits the full header (generator name, regenerate command,
  source seat, create usage) so `--out - | diff - committed` is empty.
- Golden snapshot committed; tests assert generator==golden,
  committed==generator (full bytes and body-below-header), determinism,
  and shipped semantics (workdir, best_effort, empty allowlist).

### Task 3: Config node + CI drift guard + docs — done
- `sandbox` node default-off (`enabled:false`,
  `policy:omega_prime/hosting/openshell/sandbox-policy.yaml`,
  `scope:gates`), `OMEGA_PRIME_SANDBOX_*` env overrides, bogus scope
  falls back to `gates` loudly; config tests extended (6 new cases).
- CI `sandbox-policy-drift` job (regenerate + `diff -u` with pipefail,
  diff artifact attached on failure; stdlib-only, no binary/network).
- docs/tool-host.md: generation, flag matrix, receipt-binding forward
  pointer, degraded contract.

## Deviations from Plan

- **C83 binding (header):** generator emits the same header comment the
  committed file carries, so shell diff-verify and snapshot test agree.
- **Intentional deltas vs old hand-written bytes** (derivation only):
  read-only gains the 4 seat globs (list switches flow→block style past
  76 cols); read-write gains home scratch + work-root state;
  `best_effort` is now JSON-quoted (same YAML scalar).
- **CI edit repair:** an incremental patch duplicated one `diff` line
  and broke YAML parsing mid-task; repaired and re-validated with
  `yaml.safe_load` (jobs include `sandbox-policy-drift`). No behavior
  impact.

## Verification (exercised)

- `pytest omega_prime/tests/test_sandbox_policy_gen.py` — 15 passed.
- Regen twice + `diff` — empty; `--out - | diff - committed` — empty.
- `load_config` defaults/env/bogus-scope checks via `python -c` — ok.
- Existing suites (`test_policy`, `test_prime_config`,
  `test_quality`, receipts) left for parent run per assignment
  constraints (only new test files executed here).

## Known Stubs

None.
