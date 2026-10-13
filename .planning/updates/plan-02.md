# U2 — Protected Release Pipeline → Phase 80

Issue: SPE-10594 · Milestone v16 · Evidence: `local://v16-recon-findings.md` (HEAD df14512)

## TITLE_AND_SUMMARY

Make image publication a real, exercised, protected release path. Create the missing
`grokbot-image-publish` protected GitHub environment so the publish job stops being
DEAD-CLOSED, exercise a `workflow_dispatch` publish end-to-end with checksum/image-id binding,
and define the staged rollout path with health checks plus a one-step rollback built on the
existing deploy.py staging-first promotion evidence.

Definitions:
- **Protected environment**: a GitHub Actions environment with protection rules (required
  reviewers); jobs naming it wait for reviewer approval before running.
- **Checksum/image-id binding**: the publish job records the built image digest and a
  `sha256sums.txt` of its artifacts, and the same digest string is what deploy.py promotion
  evidence must show — one identifier chains build → approval → promote.
- **Staged rollout**: promote to the staging target first, gate on health checks, then promote
  the identical digest to the live target; never build twice.

## EVIDENCE

- R2: grokbot-image.yml publish job is DEAD-CLOSED — it requires protected environment
  `grokbot-image-publish`, which does not exist (workflow header lines 1-20); the build side
  (buildx + live verify + SBOM + blocking trivy CRITICAL) exists.
- R2: deploy.py implements a staging-first promotion gate with approval/digest evidence
  (deploy.py:1570-1576+), a hardening checker whose check() flags inline-secrets
  (deploy.py:1069-1108), non-root uid 10001, read-only rootfs, no-new-privileges, /healthz.
- R2: NO CD to any host and no integration/canary environment exist today; image publication
  has never been exercised.
- R5: verify.py:557-567 asserts unauthenticated /metrics 401s — the health/verify surface the
  image exposes is already test-covered.

## VALUE_PROPOSITION

- The release path goes from "exists in YAML, never run" to "run once with evidence, repeatable
  on demand" — the exact gap R2 records.
- Digest binding means the artifact a reviewer approves is provably the artifact promoted;
  no rebuild-between-approval-and-promote window.
- One-step rollback using recorded promotion evidence removes the "rebuild and hope" fallback.

## ARCHITECTURE_SKETCH

- **Environment**: repo environment `grokbot-image-publish`, protection rule
  `required_reviewers` (the user as sole reviewer; no timers), deployment branch policy limited
  to `main`.
- **Publish job additions (grokbot-image.yml)**: after the existing build/verify/SBOM/trivy
  stages, the publish step emits —
  1. image digest (`sha256:...`) as a job output,
  2. `sha256sums.txt` (image tarball + SBOM) as a workflow artifact,
  3. a job-summary line `image <digest> <- run <run_id> <- commit <sha>>` (the binding chain).
- **Promotion evidence**: the promotion step calls the existing deploy.py staging-first gate
  (deploy.py:1570-1576+) with the digest from step 1; deploy.py check() re-runs its hardening
  checks (deploy.py:1069-1108) against the rendered artifacts before any promote is accepted.
- **Staged path**: workflow_dispatch input `rollout: staged|direct` (default `staged`).
  `staged` = promote digest to the staging target, run health checks
  (`/healthz` and `/readyz` probes as rendered by deploy.py's k8s renderer, verified
  in-tree), then promote the same digest onward after explicit reviewer approval on
  the protected environment. `direct` is retained but never default.
- **Rollback artifact**: each successful promotion writes an evidence file recording
  `{promoted_digest, previous_digest, approval, timestamp}`. One-step rollback = re-run the
  promote step with `previous_digest` from that file; no rebuild.
- Health-check runner: `python -m omega_prime.grokbot.deploy healthcheck --target <t>`
  (thin wrapper over the rendered probe paths) — fails non-zero on first failed probe.

## IMPLEMENTATION_STEPS

1. **Create the protected environment.** Add repo environment `grokbot-image-publish` with a
   `required_reviewers` protection rule and branch policy `main` (repo settings, or
   `gh api` equivalent).
   **Verify:** `gh api repos/{owner}/{repo}/environments/grokbot-image-publish --jq '.protection_rules[].type'` — output contains `required_reviewers`.
2. **Digest/checksum binding in the publish job.** Extend grokbot-image.yml publish job to emit
   digest output, `sha256sums.txt` artifact, and the binding job-summary line.
   **Verify:** dispatch the workflow on a branch; the run summary shows `image sha256:<digest> <- run <id> <- commit <sha>` and `sha256sums.txt` downloads; `sha256sum` of the downloaded image artifact matches the listed checksum.
3. **Promotion evidence wiring.** Make the promote step pass the publish-job digest into the
   deploy.py staging-first gate and extend the evidence record with `previous_digest`.
   **Verify:** `python -m pytest tests/test_grokbot_deploy.py -k "promot or evidence" -q` — passes, including the case where the supplied digest does not match the rendered artifact (promotion refused).
4. **Health-check wrapper.** Implement `deploy healthcheck --target` over the rendered
   `/healthz` and `/readyz` probe paths with fail-fast semantics.
   **Verify:** `python -m pytest tests/test_grokbot_deploy.py -k healthcheck -q`; then against a locally started rendered target: healthy target exits 0, stopped target exits non-zero.
5. **Staged rollout path.** Implement the `rollout` dispatch input: staged promotes to staging,
   runs `healthcheck`, and requires protected-environment approval before the same digest is
   promoted onward; direct skips staging only when the input says so.
   **Verify:** `python -m pytest tests/test_grokbot_deploy.py -k rollout -q`; workflow_dispatch with `rollout: staged` on a test branch shows the job waiting on the `grokbot-image-publish` environment until approval.
6. **Rollback runbook.** Document in docs/deploy.md: locate the newest promotion evidence file,
   re-run the promote step with its `previous_digest`, re-run `healthcheck`; record the rollback
   as a new evidence entry.
   **Verify:** `grep -n "Rollback" docs/deploy.md` finds the section; a rehearsed dry-run (staging target) demonstrates promote → rollback → healthy, with two evidence files on disk.
7. **First real publish exercise.** Run the full workflow_dispatch publish on `main` with user
   approval on the protected environment; publish the image; attach SBOM.
   **Verify:** the GHCR package exists at the digested tag; run summary shows the binding chain; trivy gate was green (R2 build stages all passed in the same run).

## DEPENDENCIES

- None inside milestone v16. plan-01 is not a dependency: /redteam is excluded from the image
  context (deploy.py:274, R2), so the published image carries no sidecar state.
- plan-03's nightly readiness runs consume the image only optionally (they run from source);
  no ordering constraint.
- External: GHCR write permissions for GITHUB_TOKEN in grokbot-image.yml; the user as required
  reviewer (approval is a human step by design, R2 DEAD-CLOSED rationale).

## RISKS_AND_MITIGATIONS

- *Publish exercised once then left to rot.* The nightly/periodic option: reuse the
  workflow_dispatch path in a quarterly scheduled reminder issue — kept out of scope of
  automation (R2: no host CD by planning decision), but the runbook documents the drill.
- *Digest mismatch between build and promote (rebuilt image).* The gate refuses any digest not
  matching the publish job output (step 3 verification covers the refusal path).
- *Environment protection misconfiguration (accidental self-approval).* Reviewer set = the
  user only; branch policy = main; workflow cannot modify environment protection at runtime.
- *Healthcheck false positives on slow starts.* Probe semantics follow the rendered
  liveness/readiness periods from deploy.py's renderer; wrapper retries within the rendered
  `periodSeconds` budget before failing.
- *No host CD means "rollout" is still evidence-plus-manual-step.* True by design (R2); the
  plan's value is the protected path, binding, and one-step rollback — not automation onto a
  host.

## ROLLOUT_AND_ROLLBACK

- Config flag (repo-level Actions variable, matching the OMEGA_PRIME_* naming style used
  in-repo): `OMEGA_PRIME_IMAGE_ROLLOUT` ∈ {`staged`, `direct`}, default `staged`; the publish
  workflow reads it and refuses `direct` unless explicitly overridden at dispatch.
- Health gate: promotion past staging requires the `healthcheck` wrapper exit 0 against the
  staging target; a failed gate leaves the live target untouched.
- Revert step: read `previous_digest` from the newest promotion evidence file and re-run the
  promote step pinned to it, then re-run `healthcheck` — one step, no rebuild. If the new
  image never promoted past staging, live needs no action; delete the staged target and the
  revert is complete.
- The protected environment itself is the persistent kill-switch for the release path: removing
  reviewer approval (or the environment) re-closes the pipeline DEAD-CLOSED exactly as found
  (R2).

## SUCCESS_METRICS

- Publish job runs to completion at least once with user approval through
  `grokbot-image-publish` (R2 DEAD-CLOSED state closed).
- Every promotion in the evidence trail shows one digest chained: build output = approved =
  promoted = healthchecked.
- Rehearsed rollback restores the previous digest in a single promote step using recorded
  evidence — measured in the step-6 drill.
- `direct` rollout count = 0 in the evidence trail (staged is the enforced default).

## GSD_PHASE_MAPPING

- Milestone v16, **Phase 80** — "Protected release pipeline".
- Phase directory under the v16 phases layout (`.planning/<v16-phases>/80-<slug>/`), v14/v15
  `NN-<slug>` convention.
- Execution order: steps 1-4 are a first task wave (environment + binding + gates);
  steps 5-7 form the second wave (staged path, runbook, first exercise) and are the phase gate.
- Phase verification: SUCCESS_METRICS become VERIFICATION.md checks; "first real publish
  exercised with approval" is a hard acceptance criterion.
