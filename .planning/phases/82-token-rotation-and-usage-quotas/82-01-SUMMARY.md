---
plan: 82-01
status: complete
phase: 82
---

# 82-01 Summary: Token rotation

## Tasks completed

- Task 1: `omega_prime/grokbot/tokens.py` — store entries gain `rotated_from`/`rotated_to` lineage (reads tolerate absent fields; pre-plan files load unchanged); `rotate_token()` mints a same-label/scope replacement under the existing flock+atomic path, stamps lineage (first successor wins, no forks), clamps old expiry to mint+overlap (never extends natural expiry); `rotate` CLI (`--label|--id`, `--overlap-hours` default 1, `--dry-run`, `--due-only` due = unrevoked/unsuperseded/with-expiry/remainder ≤ window incl. self-heal of stalled schedulers; nothing-due exits 0); plaintext printed once to stdout only; audit `token_rotated` hash-only event (label_hash/entry hashes/overlap_hours) into the sibling audit NDJSON — failure warns, never blocks.
- Task 2: `rotation` config node (`enabled` default False, `overlap_hours` default 24) with `OMEGA_PRIME_ROTATION_*` env overrides (invalid hours warns + falls back); `--due-only` gates on the flag (manual `--label/--id` rotates regardless — operator intent, documented); cron files untouched (scheduling seam = `rotate --due-only` via the existing heartbeat_set prompt; runbook section 8 in `docs/setup.md` covers dry-run-first, sizing, break-glass revoke, rollback via ENABLED=0).
- Task 3: `/metrics` series `omega_token_rotations_total`, `omega_token_expiry_total`, `omega_token_active{label_hash}` in `grokbot/metrics.py` (sha256 label hashing; stale-hash zeroing; expiry dedupe) wired in `grokbot/remote.py` best-effort at serve time; runbook documents the series + `audit verify` step.

## Deviations and bug fix

- Real bug caught by the plan's tests: the successor link was stamped only when the old entry was unexpired, breaking lineage for already-expired entries and leaving them forever-due. Fixed: `rotated_to` is stamped unconditionally; the horizon still only shortens expiry.
- `revoke_token` carries lineage through (otherwise revoke would wipe it); re-rotation keeps the original `rotated_to` link.
- Plan verify grep fixed by parent: expected token prefix is `omk_` (security.py default), not the plan's stale `op_`.

## Verification

13 rotation tests pass (lineage across N rotations, both-sides-of-window with injected clock, revoke-during-window immediate kill, corrupted store fails closed, plaintext-once, due-only/dry-run zero mutation, audit chain verifies to GENESIS, metrics no double-count); live CLI drill: new → rotate → list shows both lineage links; token-prefixed suites 139 passed; ruff clean.
