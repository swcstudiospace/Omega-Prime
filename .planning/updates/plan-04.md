# U4 — Automated Token Rotation → Phase 82

Issue: SPE-10594 · Milestone v16 · Evidence: `local://v16-recon-findings.md` (HEAD df14512)

## TITLE_AND_SUMMARY

Automate access-token rotation for the MCP tool host. A new `rotate` subcommand on the existing
tokens CLI mints a replacement token while the old one stays valid for a configurable overlap
window; scheduling rides the existing cron heartbeat; expiry and denial counters land in
/metrics; every rotation event is written to the append-only audit chain.

Definitions:
- **Rotation**: replacing a live bearer token with a new one without an authentication outage.
- **Overlap window**: the period after minting the replacement during which both tokens
  authenticate; the old token is revoked when the window closes.
- **Key versioning**: each token-store entry carries `rotated_from` (hash of the predecessor
  entry) and `expires_at`, so the lineage of a caller's tokens is explicit and auditable.
- **Cron heartbeat**: the existing heartbeat job family (prime.heartbeat capability,
  omega_prime/config.py PRIME_FAMILIES) that schedules recurring prompts/jobs; rotation adds a
  heartbeat job rather than introducing a new scheduler.

## EVIDENCE

- R4: tokens.py implements plaintext-once minting, SHA-256-only storage in a 0600 store,
  atomic writes, fail-closed live-reload FileTokenStore, and a manual CLI
  new/list/revoke --ttl-days (tokens.py:256-285, 345-426, 495-523).
- R4: revocation and TTL expiry exist operationally, but rotation is MANUAL CLI only — no
  automated rotation job, no overlap windows, no key versioning, no rotation alerts.
- R5: metrics.py already exposes `omega_tool_denials_total{code}`; the audit chain (audit.py)
  is an append-only NDJSON hash chain with verify() back to GENESIS_HASH; AuditInterceptor
  hashes args, never values.
- R8: nothing in omega_prime/ enforces quotas today — rotation is an identity
  primitive plan-07 builds on, not a quota mechanism.

## VALUE_PROPOSITION

- Credential lifetime stops depending on someone remembering to run the CLI; a leaked token's
  maximum exposure becomes the overlap window plus TTL instead of "until noticed".
- The audit chain gains machine-verifiable rotation lineage, so "which token was live when"
  is answerable from the chain rather than from shell history.
- plan-07 (per-user quotas) needs stable caller identity across rotations — this plan provides
  the versioned lineage it consumes.

## ARCHITECTURE_SKETCH

- `tokens.py` CLI gains `rotate`: `rotate --label <label> [--overlap-hours N]`. Semantics:
  1. load the store (fail-closed FileTokenStore as today, R4),
  2. mint a replacement entry with the same `label`/scope and a new `expires_at`
     (default TTL rules unchanged),
  3. stamp the old entry `rotated_to=<new entry hash>` and keep it authenticating until
     `mint_time + overlap_hours`, then it is revoked by the same expiry path that already
     handles `--ttl-days` (R4),
  4. append one audit-chain event `{kind: "token_rotated", label_hash, old_entry_hash,
     new_entry_hash, overlap_hours}` — hashes only, never token material (R4 plaintext-once
     and R5 hash-args conventions).
- Store entry shape: existing fields + `rotated_from`/`rotated_to`/`expires_at` (key
  versioning). Old entries keep authenticating inside the overlap window through the existing
  live-reload store — no second credential path.
- Scheduler: a heartbeat job (existing cron heartbeat family) that runs
  `rotate --due-only`: rotate when `expires_at - now <= overlap_hours` for any entry.
  Job creation is one-time setup documented in docs/setup.md; the job is a no-op unless
  `rotation.enabled` is true.
- Metrics (metrics.py registry, R5 shapes): `omega_token_rotations_total` (counter),
  `omega_token_expiry_total` (counter), `omega_token_active{label_hash}` (gauge). Denial
  visibility reuses the existing `omega_tool_denials_total{code}`.
- Rotation alerts are NOT added here (no alerting substrate exists yet, R5); the counters are
  the contract plan-03's SLO/alert layer reads.

## IMPLEMENTATION_STEPS

1. **Store entry versioning.** Extend token-store entries with `rotated_from`, `rotated_to`,
   `expires_at`; keep SHA-256-only, 0600, atomic-write semantics (tokens.py:256-285, 345-426).
   **Verify:** `python -m pytest tests/test_tokens.py -q` — existing tests pass unchanged and new entries round-trip the three fields; a hand-corrupted store still fails closed exactly as today.
2. **`rotate` subcommand.** Implement mint-replacement + overlap + revocation-at-window-close
   on the CLI (tokens.py:495-523 region).
   **Verify:** in a temp store: `python -m omega_prime.tokens rotate --label ci --overlap-hours 1` prints the new token once (plaintext-once), then `list` shows both entries with `rotated_to/rotated_from` linked; forcing the overlap horizon into the past revokes the old entry on the next `list`.
3. **`--due-only` mode.** Add the dry-run-able due check used by the heartbeat job.
   **Verify:** `python -m omega_prime.tokens rotate --due-only --dry-run` on a store with an entry 30 minutes from expiry reports it due; a fresh entry reports none due; exit codes 0/1 distinguish them.
4. **Heartbeat scheduling.** Document + register the heartbeat job that runs
   `rotate --due-only` on the cron heartbeat; gated by `rotation.enabled`.
   **Verify:** with `rotation.enabled` false the heartbeat job logs one skip line and mutates nothing (observable in the heartbeat job output); flipping the flag true and running the job performs the step-3 verified rotation.
5. **Audit-chain events.** Append `token_rotated` events with hash-only fields.
   **Verify:** perform one rotation in a temp root, then `python -m omega_prime.audit verify` (audit.py CLI verify, R5) — chain verifies back to GENESIS_HASH and the newest event is `token_rotated` with no token material in the record (`grep -c` of the token prefix on the audit file = 0).
6. **Metrics.** Add `omega_token_rotations_total`, `omega_token_expiry_total`,
   `omega_token_active` to metrics.py.
   **Verify:** `python -m pytest tests/test_metrics.py -k token -q`; then serve with one rotated store and scrape /metrics — the counters appear and `omega_token_active` matches the store's live-entry count.
7. **Runbook.** Document first-time enablement (heartbeat job creation, flag, overlap sizing)
   in docs/setup.md.
   **Verify:** `grep -n "token rotation" docs/setup.md` finds the section; following it in a fresh checkout produces a working scheduled rotation with no undocumented steps.

## DEPENDENCIES

- plan-07 (U7, Phase 82) consumes this plan's key versioning for caller identity across
  rotations; plan-07 must not land before step 1-2 exist. This plan does not depend on plan-07.
- plan-03: the counters added in step 6 flow into /metrics, OTLP export, and any future alert
  rules via the existing registry (R5) — soft dependency, no ordering constraint.
- None on plan-01/02/05/06. The manual CLI and TTL behavior are unchanged (R4), so rollback
  risk to existing auth is minimal.
- External: the cron heartbeat family must be enabled (`prime.heartbeat.enabled`) for
  scheduled rotation in a given deployment; without it, `rotate` stays a manual command.

## RISKS_AND_MITIGATIONS

- *Both tokens leaked in the overlap window.* Window is configurable and defaults small (1h);
  `revoke` remains immediate and manual (R4) as the break-glass path.
- *Heartbeat job runs on a stale checkout / wrong root.* The job invokes the installed module
  with an explicit `--store` path (same explicit-path convention as the rest of the repo);
  dry-run mode makes the first scheduled execution a no-op until blessed.
- *Audit-chain bloat from churning tokens.* One small event per rotation with hash-only
  fields; the chain already rotates files .1/.2 with verification (R5).
- *Double-rotation race (two heartbeat runs overlap).* Rotation takes the store's existing
  atomic-write + fail-closed reload path (R4); the second run observes the fresh entry as
  not-due and exits 0 without minting.
- *Silent rotation failure.* `--due-only` exit 1 and the skipped/failed heartbeat line are the
  observable; counters make a stalled rotation visible in /metrics (step 6).

## ROLLOUT_AND_ROLLBACK

- Config flag: `rotation.enabled` (node key, default `false`) and `rotation.overlap_hours`
  (default 24 for scheduled use; the CLI default stays 1h), in the omega_prime/config.py
  node-key style with env overrides `OMEGA_PRIME_ROTATION_ENABLED` /
  `OMEGA_PRIME_ROTATION_OVERLAP_HOURS` (config.py:100-115 `OMEGA_PRIME_*` pattern).
- Rollout: flag off everywhere by default → enable per deployment → first run is `--dry-run`.
- Revert step: set `OMEGA_PRIME_ROTATION_ENABLED=0` (or the node key false) — the heartbeat
  job skips and nothing rotates; already-minted overlapping tokens are handled by the
  pre-existing revoke CLI (`revoke --label`, R4) which is untouched by this plan. Store entries
  with new fields are read correctly by the old code path's unknown-field tolerance; if not,
  the pre-plan store file is restored from the atomic-write backup the store already keeps
  (R4 atomic writes) — verified in the step-1 test before rollout.

## SUCCESS_METRICS

- Scheduled rotation fires and completes for a long-lived label with zero authentication
  failures inside the overlap window (measured by the deployment's denial counter staying flat
  across the rotation, R5 `omega_tool_denials_total{code}`).
- Every rotation event verifiable in the audit chain with hash-only content.
- Token lineage (`rotated_from`/`rotated_to`) reconstructable for any label via `list` — no
  gaps after N rotations.
- A stalled scheduler (heartbeat disabled) is visible within one scrape via
  `omega_token_rotations_total` going stale while entries approach expiry.

## GSD_PHASE_MAPPING

- Milestone v16, **Phase 82** — "Automated token rotation" (shares Phase 82 with plan-07;
  this plan is the first half — identity + rotation — plan-07 the second — quotas + reporting).
- Phase directory under the v16 phases layout (`.planning/<v16-phases>/82-<slug>/`), v14/v15
  `NN-<slug>` convention; plan-07 references the same phase directory with its own plan file.
- Execution order: steps 1-3 one task (store + CLI); steps 4-5 second (scheduling + audit);
  steps 6-7 third (metrics + runbook). plan-07 unblocks after step 2.
- Phase verification: SUCCESS_METRICS become VERIFICATION.md checks; "manual CLI unchanged"
  (R4) is a hard regression criterion.
