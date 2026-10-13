---
plan: 80-02
status: complete
phase: 80
---

# 80-02 Summary: Staged rollout + rollback

## Tasks completed

- Task 1: `omega_prime/grokbot/deploy.py` — `OMEGA_PRIME_IMAGE_ROLLOUT` mode (staged default; direct requires env=direct AND --direct), `healthcheck` subcommand (port resolved from rendered files; /healthz then /readyz; attempts from failureThreshold/retries clamped to 3; exit 0/1/2), `promote` subcommand (staging receipt or `.digest` required; approval record auto-adopted; digest-verbatim match; promotion-evidence-<UTC>.json with promoted/previous digest chain); CLI + `__all__`/docstring updated; tests appended (staged refusal, success+chain, mismatch, direct triple-refusal, dead-port healthcheck, warn-then-clear).
- Task 2: `scripts/release-evidence-drill.py` — hermetic 4-stage drill (render → warn-then-clear → staging+digest+approval clears findings → promote with chained evidence), PASS/FAIL per stage.
- Task 3: `scripts/image-rollback-drill.sh` (local-docker-only save/load round-trips, sha256 records, two promotes then rollback to previous_digest read from newest evidence JSON, stub health server re-check, trap cleanup) + `docs/deploy.md` "Image-digest rollback (local exercise)" runbook appended inside the existing rollback section (80-01's subsection untouched).

## Deviations

- Two author-side edit mistakes self-corrected during development (overbroad range deletes; unanchored digest regex; retries clamp to max 3 so k8s startupProbe thresholds cannot inflate healthcheck attempts) — each re-verified via py_compile + behavioral smoke.

## Verification (parent-run)

- `pytest test_grokbot_deploy.py test_grokbot_image_gates.py` → 193 passed.
- Evidence drill: 4/4 PASS, evidence keys `['approval', 'previous_digest', 'promoted_digest', 'rollout', 'timestamp']`.
- Rollback drill on live Docker: DRILL OK — previous digest `sha256:30a02d41…` re-promoted as new evidence, healthcheck exit 0 after rollback; no push/ssh/remote-registry paths (grep-gated).
- `bash -n` clean; ruff clean after parent format pass; mypy clean on deploy.py.
