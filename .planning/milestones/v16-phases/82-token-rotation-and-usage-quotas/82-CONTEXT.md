# Phase 82: Token rotation and usage quotas - Context

**Gathered:** 2026-10-13
**Status:** Ready for planning
**Mode:** Auto-generated (discuss skipped via workflow.skip_discuss)

<domain>
## Phase Boundary

Automate host-token rotation with overlap windows and introduce soft-then-hard per-caller usage quotas with reporting. Scope: rotation subcommand(s) minting replacements with a configurable overlap window (dual-valid grace), cron-heartbeat scheduling, audit-chain rotation events, expiry surfaced in /metrics; per-caller quota enforcement in report-only → soft (warnings) → hard (caps) modes with usage reporting via /metrics series. Out of scope: sharing credentials between surfaces, automated rotation of third-party provider keys, changing FileTokenStore fail-closed semantics, hard-capping anything in report-only mode.

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
All implementation choices are at Claude's discretion — discuss phase was skipped per user setting.

### Recorded decisions binding this phase
- Quota modes ship report-only FIRST (default), then soft, then hard — user default q3 (`.planning/decisions.md` row 4).
- Caller identity comes from the host token identity (plan-07 dependency on plan-04 key versioning).
- No third-party key rotation; host tokens only (recon R4 scope).

</decisions>

<code_context>
## Existing Code Insights

Verified evidence (`.planning/recon-evidence.md` R4 + R5): `omega_prime/tokens.py` (create_token plaintext-once, SHA-256-only 0600 store, FileTokenStore live-reload fail-closed, CLI new/list/revoke with --ttl-days → expires_at; rotation = manual revoke+mint today; no overlap windows, no key versioning, no rotation scheduler); cron heartbeat exists (cron/heartbeat.py via JobStore/APScheduler — v11/v15 evidence); metrics interceptors count calls but enforce nothing (R8); audit hash chain in `grokbot/audit.py` accepts appends. Plans: `.planning/updates/plan-04.md` (U4 rotation.enabled/overlap_hours) + `plan-07.md` (U7 quotas.mode report|soft|hard + quotas.document).

</code_context>

<specifics>
## Specifics

- Overlap window semantics: old token stays valid until window end, then dies — never two independent trusts; revocation still immediate.
- Quota identity must degrade safely for unauthenticated/local transports (report-only records, hard mode is documented as token-identity-scoped).
- The 11-name REDTEAM_TOOL_NAMES pin and roster consistency triangle stay untouched (no new tool names; this phase adds no roster entries).

</specifics>

<deferred>
## Deferred Items

- Provider-key rotation, multi-tenant billing, quota persistence beyond the token store (operator backends).

</deferred>
