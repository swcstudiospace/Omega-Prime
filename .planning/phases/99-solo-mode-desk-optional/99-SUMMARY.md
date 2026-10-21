# Phase 99 — Solo mode, Desk optional

## Outcome

Implemented solo-first runtime routing for bot-00 while preserving explicit Desk configuration when `OMEGA_PRIME_SOLO=0`. The Desk remains optional; this phase did not remove Desk code or alter other seats' defaults.

## Changes

- Runtime construction resolves solo mode once. Solo docs search and event emission use the shared substrate client; unsupported bus operations return `not_configured: solo`. Desk docs, bus, and notify are enabled only when solo is off and their configuration is available. Removed the localhost bus fallback, including blocked-binding handling; doctor reports solo state and active Desk planes.
- Desk gateway skill loads on demand in prompt sources and regenerated assembled prompt. Rewrote Desk skill references to verified in-repo material or explicit absence notes; corrected dead references.
- Documented optional/legacy Desk variables and solo behavior. Added routing, outage, Desk-compatibility, and link-resolution tests. Added changelog entry after targeted proof.

## Verification

- Focused Phase 99 and adjacent contract tests: 176 passed.
- `ruff check omega_prime/`, `ruff format --check omega_prime/`, `mypy omega_prime/`, and prompt assembly check passed.
- Full suite: 4,782 passed, 11 skipped, 27 failed. All 27 match the committed baseline failures tied to missing/empty `prime-agent` and/or missing `rlm`; details and evidence are in [99-VERIFICATION.md](99-VERIFICATION.md).

## Integration seam and remaining operator work

Phase 97 owns the `SubstrateClient.docs_search` wire-argument and SSE transport change. This phase calls the Python API without changing its wire argument. The current checkout still has the pre-Phase-97 seam; rebase after Phases 97/98 integrate and verify docs search dispatches using Phase 97's `question` contract. No connector restart, deployment, live configuration change, push, PR, or merge was performed. The v20 milestone remains in progress; no audit, milestone completion, or cleanup was run.
