# U1 — Purple-Team Sidecar Hardening & Refresh Pipeline → Phase 79

Issue: SPE-10594 · Milestone v16 · Evidence: `local://v16-recon-findings.md` (HEAD df14512)

## TITLE_AND_SUMMARY

Harden the `redteam` submodule pipeline and ship the purple-team sidecar in a BLOCKED state.
Two halves:

1. **Refresh pipeline (CI-side).** Dependabot watches the `gitsubmodule` ecosystem; a CI job
   proves the submodule pin, VENDOR.md, the provenance docstrings, the vendored pack, and the
   adapter's tool surface all agree with upstream at the pin; the Node deps of the redteam
   backend get a CI-only supply-chain audit; docs/red-team.md documents the bump runbook.
2. **Authorization & state layer (runtime-side).** A signed rules-of-engagement (RoE) artifact
   schema, fail-closed BLOCKED startup without a signed artifact, a kill switch, an explicit
   IDLE/RUNNING/DEGRADED/BLOCKED/ERROR state machine, a cases-as-data simulation library, and a
   findings pipeline with dedupe.

**SHIPS BLOCKED per user decision 2026-10-13: no live engagement until the user signs a RoE
artifact.** Default config (`redteam.enabled: False`) is unchanged; every new control defaults
to its most restrictive state.

## EVIDENCE

- R6: submodule `redteam` pinned `831a6b0` (= v1.2~1), upstream main `06c11a5` has tags v1.2
  AND v1.3 → pin ~2 releases behind; local checkout clean/detached.
- R6: gating at omega_prime/config.py:52-56 (default `redteam.enabled: False`),
  config.py:126-129 (`redteam_enabled()`), config.py:100-115 (env `OMEGA_PRIME_REDTEAM_ENABLED`);
  11 tempest_* tools register via tools/redteam.py (mcp_server.py:747-776).
- R6 gaps (a)-(g): no checkout-vs-pin check; no Dependabot `gitsubmodule` ecosystem (only
  pip/actions/docker in .github/dependabot.yml); no Node-dep audit (supply-chain.yml is
  pip-only); no documented bump procedure (VENDOR.md:84-89 + two provenance docstrings
  hand-edited); vendored pack ↔ upstream equivalence unchecked; adapter hand-mirrors upstream
  redteam/src/mcp-server.ts (silent drift); NO RoE artifact, kill switch, state machine,
  cases-as-data library, detection-validation criteria, findings pipeline/dedupe, drift→DEGRADED.
- R1: `redteam` submodule is NEVER bootstrapped; test_grokbot_bootstrap.py:756-757 asserts
  redteam absent from args and dir empty.
- R2: deploy.py:274 excludes /redteam from the Docker context — the sidecar never ships inside
  the Grok Bot image; supply-chain.yml is the weekly-schedule workflow precedent.

## VALUE_PROPOSITION

- Upstream upgrades stop being silent risk: pin drift, pack divergence, and tool-surface drift
  are detected in CI instead of at first use.
- The authorization posture becomes enforceable rather than procedural: the sidecar physically
  cannot leave BLOCKED without a signed RoE, and any operator can stop it instantly.
- Simulation and findings are data, so engagement readiness is measurable and auditable without
  ever running a live engagement.

## ARCHITECTURE_SKETCH

- `omega_prime/redteam/provenance.py` (new, stdlib-only): subcommands
  `check` (submodule HEAD ≡ VENDOR.md pin ≡ provenance docstrings),
  `pack-diff` (vendored pack at omega_prime/redteam/pack/ vs submodule checkout at pin,
  byte-equivalence for markdown/JSON), `surface` (parse upstream redteam/src/mcp-server.ts tool
  names, compare against the adapter's `REDTEAM_TOOL_NAMES` constant in tools/redteam.py).
  Any mismatch exits non-zero and names the disagreeing pair.
- `.github/dependabot.yml`: add `- package-ecosystem: gitsubmodule` entry (directory `/`).
- `.github/workflows/supply-chain.yml`: new `redteam-node-audit` job — checkout with
  submodules, `npm ci --ignore-scripts`, `npm audit --omit=dev --audit-level=high` inside the
  redteam submodule only; CI-only, never at runtime.
- `omega_prime/redteam/roe.py` (new): RoE artifact schema — JSON document with required keys
  `schema_version`, `operator`, `authorized_targets` (explicit host/tool scope list),
  `effective_from`, `effective_until`, `case_classes` (subset of the six simulation classes),
  plus a detached signature over the canonical JSON. Validation fails closed: absent, expired,
  unsigned, or schema-mismatched ⇒ no engagement authorization.
- State machine (in tools/redteam.py, exposed as a gauge in metrics.py):
  `IDLE` (enabled, authorized, no run) → `RUNNING` (simulation run active) →
  `DEGRADED` (consistency triangle or drift check failed at runtime) →
  `BLOCKED` (kill switch set, RoE missing/invalid, or `redteam.enabled` false) →
  `ERROR` (gateway unreachable or protocol fault). Transitions are one-way into BLOCKED/ERROR
  only via the stated causes; recovery re-evaluates from IDLE.
- Kill switch: `redteam.kill` config node (default `false`), env `OMEGA_PRIME_REDTEAM_KILL`.
  Evaluated before every other redteam control; `true` ⇒ BLOCKED, tempest_* tools never
  register, any in-flight run aborts at the next gateway call boundary.
- Cases-as-data library `omega_prime/redteam/simulation.py` + case files under
  `omega_prime/redteam/cases/`: JSON case records with keys `case_id`, `class`, `payload`,
  `expected` (detection expectation). Classes shipped: `prompt-injection`, `jailbreak`,
  `abuse`, `malformed-input`, `auth-bypass`, `cost-abuse`. Cases run only against the loopback
  gateway via the existing adapter — never against any live target.
- Detection-validation: a case PASSES when the gateway/adapter records a detection event for
  its `case_id`; suite validation passes only at 100% detection or with per-case documented
  accepted residual in the case file (`residual_rationale`).
- Findings pipeline `omega_prime/redteam/findings.py`: append-only NDJSON; each finding keyed
  by `dedupe_hash = sha256(canonical(case_id + normalized gateway response))`; duplicate
  content appends an `occurrence_count` increment instead of a new row.

## IMPLEMENTATION_STEPS

1. **Provenance checker.** Add `omega_prime/redteam/provenance.py` with `check`, `pack-diff`,
   `surface` as specified above.
   **Verify:** `python -m omega_prime.redteam.provenance check && python -m omega_prime.redteam.provenance pack-diff && python -m omega_prime.redteam.provenance surface` — all exit 0 at the current pin; then mutate a copy of VENDOR.md to a wrong pin and observe `check` exit non-zero.
2. **CI provenance job + Dependabot gitsubmodule.** Add a `redteam-provenance` job to
   .github/workflows/ci.yml (checkout `submodules: true`) and the `gitsubmodule` entry to
   .github/dependabot.yml.
   **Verify:** `grep -n "gitsubmodule" .github/dependabot.yml` prints the new entry; the CI run on the PR shows `redteam-provenance` green.
3. **Node-dep supply-chain audit.** Add the `redteam-node-audit` job to
   .github/workflows/supply-chain.yml.
   **Verify:** trigger supply-chain.yml via workflow_dispatch; job runs `npm audit --omit=dev --audit-level=high` in the redteam submodule and is green (or fails listing real advisories at the current pin — triage into the documented ignore block with rationale, mirroring the existing PYSEC ignore style of supply-chain.yml).
4. **Bump runbook.** Document in docs/red-team.md: update pin → run provenance check/pack-diff/
   surface → update VENDOR.md:84-89 + the two provenance docstrings → open PR with CI proof.
   **Verify:** `grep -n "Bumping the redteam submodule" docs/red-team.md` finds the section; a dry-run of steps 1-3 against upstream v1.3 completes without undocumented steps.
5. **RoE schema + fail-closed BLOCKED startup.** Add omega_prime/redteam/roe.py; when
   `redteam.enabled` is true and no valid signed RoE resolves, register zero tempest_* tools and
   report state BLOCKED at startup.
   **Verify:** `OMEGA_PRIME_REDTEAM_ENABLED=1 python -m pytest tests/test_redteam_roe.py -q` — asserts zero tempest_* registrations and state BLOCKED without an artifact; with a fixture-signed artifact the fixture run reaches IDLE.
6. **Kill switch.** Add `redteam.kill` node + `OMEGA_PRIME_REDTEAM_KILL` env mapping in
   omega_prime/config.py (same `_env_overrides` pattern as `redteam.enabled`,
   config.py:100-115); evaluate first in the registration path.
   **Verify:** `OMEGA_PRIME_REDTEAM_KILL=1 OMEGA_PRIME_REDTEAM_ENABLED=1 python -m pytest tests/test_redteam_kill_switch.py -q` — enabled+RoE present still yields BLOCKED and zero registrations.
7. **State machine.** Implement IDLE/RUNNING/DEGRADED/BLOCKED/ERROR in tools/redteam.py with a
   metrics gauge; drift-check failure ⇒ DEGRADED.
   **Verify:** `python -m pytest tests/test_redteam_states.py -q` — covers every transition incl. drift→DEGRADED; observable at runtime: the state gauge appears in /metrics output.
8. **Cases-as-data library.** Add simulation.py + case files for the six classes; runs target
   the loopback gateway only.
   **Verify:** `python -m omega_prime.redteam.simulation run --suite smoke` exits 0 and prints per-class case counts; `git grep -L loopback omega_prime/redteam/cases/` returns nothing (every case file declares its loopback-only transport).
9. **Detection-validation pass criteria.** Implement the 100%-detection-or-documented-residual
   rule and the `validate` subcommand.
   **Verify:** `python -m omega_prime.redteam.simulation validate --suite smoke` exits 0 on the shipped fixtures; deleting one case's detection expectation from a copy makes `validate` exit non-zero naming the case.
10. **Findings pipeline with dedupe.** Add findings.py (NDJSON, dedupe_hash, occurrence_count).
    **Verify:** `python -m pytest tests/test_redteam_findings.py -q` — replaying one case twice yields exactly one row with `occurrence_count: 2`; the file passes the existing audit-chain style integrity read.
11. **Ship BLOCKED.** Merge with `redteam.enabled` default False (config.py:52-56) and no RoE
    artifact committed; state at startup is BLOCKED for every deployment.
    **Verify:** fresh clone + default config: `python -m omega_prime.redteam.provenance check` exits 0 and a default-config startup registers zero tempest_* tools (observable via the tool roster in the startup log).

## DEPENDENCIES

- None inside milestone v16. plan-02 is NOT a dependency: the sidecar is excluded from the
  image (deploy.py:274, R2) and ships BLOCKED regardless of release pipeline state.
- External: upstream purple-team.git (R6 pin facts); GitHub Actions runners with git submodule
  checkout (already used by CI jobs such as rust-parity, R2).
- plan-06 shares no code with this plan (its judge/eval stack is separate from simulation).

## RISKS_AND_MITIGATIONS

- *Upstream tool-surface refactor breaks `surface` check parsing.* Parse only the tool-name
  declarations, tolerate formatting; on parse failure fail the job with the raw excerpt, never
  silently pass (R6f is exactly this silent-drift failure mode).
- *npm audit noise at the pin.* Triage into a documented, dated ignore block in
  supply-chain.yml (precedent: the documented oauthlib PYSEC-2026-4114 ignore, R2); audit-level
  set to `high` to keep signal.
- *RoE signature validation complexity.* Detached signature over canonical JSON with a
  documented verification path; verification keys are config-supplied, never fetched at runtime.
- *Simulation cases leak into live use.* Cases carry loopback-only transport declarations
  (step 8 check); the adapter is a pure HTTP proxy to the loopback gateway (R6) and tempest_
  mission_start already requires approval (docs/tool-catalog.md:1246-1249, R6).
- *BLOCKED state confuses operators.* Startup log states the exact blocking cause (kill switch
  / RoE missing / RoE expired / disabled) — no generic error.

## ROLLOUT_AND_ROLLBACK

- Config flag: `redteam.roe_required` (node key, default `true` — fail closed) and
  `redteam.roe_file` (path to the signed artifact, default empty ⇒ BLOCKED when enabled), both
  following the omega_prime/config.py node-key style with `OMEGA_PRIME_REDTEAM_ROE_REQUIRED` /
  `OMEGA_PRIME_REDTEAM_ROE_FILE` env overrides (config.py:100-115 pattern).
- Kill switch: `OMEGA_PRIME_REDTEAM_KILL=1` (or `redteam.kill: true` in omega-prime.json) stops
  everything instantly and takes precedence over all other redteam keys.
- Revert step: set `redteam.enabled: false` (existing key, config.py:52-56) or
  `OMEGA_PRIME_REDTEAM_ENABLED=0` — the family unregisters exactly as today (R6). New keys
  default to restrictive values, so deleting any new config entry re-blocks; no migration is
  needed to roll back the runtime half. CI jobs revert by reverting the workflow files.
- Live engagement remains impossible until the user signs a RoE artifact (user decision
  2026-10-13); signing and installing an artifact is a separate, explicit act.

## SUCCESS_METRICS

- CI proves pin ≡ VENDOR.md ≡ docstrings, pack equivalence, and tool-surface agreement on every
  push/PR (0 unchecked drift vectors from R6 a, e, f).
- Dependabot opens gitsubmodule PRs when upstream moves (R6b closed).
- Node-dep audit runs weekly with documented disposition for every finding (R6c closed).
- Bump procedure executable by following docs/red-team.md alone (R6d closed).
- With `redteam.enabled=true` and no signed RoE: startup state is BLOCKED, zero tempest_* tools
  registered — verified in CI (R6g authorization gap closed).
- Six case classes executable via `simulation run`; findings dedupe holds under replay.

## GSD_PHASE_MAPPING

- Milestone v16, **Phase 79** — "Red-team sidecar hardening & refresh pipeline".
- Phase directory under the v16 phases layout (`.planning/<v16-phases>/79-<slug>/`), following
  the v14/v15 `NN-<slug>` convention (.planning tree, R7 for the archived-v15 state).
- Execution order inside the phase: steps 1-4 (CI pipeline) are independent of steps 5-10
  (authorization/state layer) and can run as two plan tasks; step 11 is the phase gate.
- Phase verification: SUCCESS_METRICS above become the VERIFICATION.md checks; the BLOCKED ship
  state is a hard acceptance criterion, not a follow-up.
