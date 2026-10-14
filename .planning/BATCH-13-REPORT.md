# Batch 13 Report: Purple-Team SOTA features (5 shipped)

**Shipped:** 2026-10-14 · **On main:** `93e95ea` · **Suite:** 3569 passed / 0 failed / 3 skipped (ruff + `mypy omega_prime/` clean, 411 files)

## The 5 features

| # | Feature | Value | Revert |
|---|---------|-------|--------|
| M8 | Webhook HMAC signing | HMAC-SHA256 `X-Omega-Signature` + timestamp headers over `{ts}.{payload}`; rotation via `X-Omega-Signature-Previous`; unsigned (byte-identical) when no secret | revert webhook tracker signing |
| M9 | Pin-bump `--rollback` | Re-pins to the previous version (report record → git history fallback), RoE-gated (active grant required when configured), revert PR unconditional, dry-run plan | revert `--rollback` flag |
| M10 | Engagement report `--delta` | Three-way trend (older → previous → current) with deterministic arrows for runs/pass-rate/coverage/findings/risk score; composes with --compare | revert `--delta` flag |
| M11 | Per-grant tracker allowlists | RoE `covers` key scopes which trackers a grant permits; uncovered tracker skipped with structured reason; no grants = all allowed (backward compatible) | revert grant_trackers + tick gate |
| M12 | Coverage `--export-md` | Embeddable markdown fragment (matrix + risk score + gate line, no h1/snapshot) for PR comments; canonical output unchanged | revert `--export-md` flag |

## Verification

- Full suite: **3569 passed / 0 failed** on main (`93e95ea`); ruff + `mypy omega_prime/` (411 files) clean.
- New tests: ~65 across 4 extended test files (261 findings-sync+roe-lifecycle combined, 146 pin-bump, 137 engagement-report).

## Blocked items (documented, not silent)

- Item ① (absorb upstream v1.5): requires the upstream purple-team release to ship first.
- Item ② (live-gateway e2e with real detection): requires running T3MP3ST + signed RoE — user-owned.

## Batch 14 proposal (paused — awaiting go-ahead)

1. Purple-team repo: absorb upstream v1.5 when it ships (release_watch + pin-bump chain)
2. Live-gateway e2e with real detection validation (requires T3MP3ST + RoE)
3. Findings sync: generic webhook payload templates (Jinja-free deterministic `{{field}}` substitution)
4. Pin-bump: `--explain <advisory>` — human-readable one-pager per advisory (what/why/fix/rollback)
5. Engagement report: `--recipient-file <path>` — render the email block into a standalone file for mail tooling
6. RoE lifecycle: grant usage ledger — append-only record of which grants gated which dispatches (audit evidence)
7. Coverage-matrix: deterministic diffable scorecard (machine-readable pass/fail per class for CI annotations)
