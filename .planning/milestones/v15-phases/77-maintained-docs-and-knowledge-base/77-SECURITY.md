---
phase: "77"
slug: maintained-docs-and-knowledge-base
status: verified
threats_open: 0
asvs_level: 1
block_on: high
register_authored_at_plan_time: true
created: "2026-10-11"
updated: "2026-10-12"
source_revision: 1f0adf62a1668d3f3a0d3149987543cdd5f5b5cd
---

# Phase 77 — Security

Latest affected-source independent `gsd-security-auditor` verdict at `1f0adf62a1668d3f3a0d3149987543cdd5f5b5cd`: **SECURED,16closed/0open**, ASVS1/block-high. All16dispositions remainmitigate, no acceptedrisk/transfer. Subsequent3208b9b is docs-only removal of a residual canonical caller duration promise, not a new behavior run. Initial13/3, intermediate15/1 and acc2aa8fullprivacy closures remainhistorical. Canonical whole-phase and fresh human acceptance remainpending.

## Trust Boundaries

| Boundary | Data and required distinction |
| --- | --- |
| Authenticated KB provider → archive | Fully identifiable, complete stable publication; literal bodies are untrusted data, never execution or authority. |
| Untrusted errors/labels → API/CLI diagnostics | Full-input unchanged detector before clipping/publication and no raw displayed causes. |
| Prior filesystem → publication/recovery | Owned safe paths, preserved complete edition/recoverable backups; not crash/concurrent-writer guarantees. |
| Canonical guidance → operator decisions | Source-specific approval/auth/prerequisite/license/live-proof boundaries, not implied authorization. |
| Registry definitions → generated catalog | Actual registered metadata, not handler execution; retained behavioral consumers, no copied pins. |

## Threat Register

| Threat ID | Category | Component | Severity | Disposition | Mitigation | Status |
| --- | --- | --- | --- | --- | --- | --- |
| T-77-01 | Tampering | sync listing/document validation | high | mitigate | Strict counts/version/truncation checks; whole-candidate failure before publication (Task 1); verify by migrated adverse consumers | closed |
| T-77-02 | Information Disclosure | error/diagnostic paths | high | mitigate | Whole-input unchanged assert_no_secrets before clip/echo; gated API/CLI errors with displayed cause suppression; direct filesystem/read/parser/validation/publication/status consumers | closed |
| T-77-03 | Denial of Service | refresh equality/churn | medium | mitigate | Clock-excluded comparison with zero-write unchanged outcome (Task 1, workflow gate Task 3); single-writer limit explicit, no kill/parallel guarantee claimed | closed |
| T-77-04 | Tampering | mirror publication | high | mitigate | Whole-candidate validation plus set-level preservation; late-fetch/write failure restores the entire prior set (Task 1, 77-01 client); verify by parent byte reconciliation and fault proofs | closed |
| T-77-05 | Tampering | mirror output paths | high | mitigate | Unsafe-path guard with output-root containment and symlink safety; refuse before publication (Task 1); verify by adverse path consumers | closed |
| T-77-06 | Elevation of Privilege | historical body directives | high | mitigate | Bodies archived literally as untrusted data; guide separation with no tenancy/command/approval selection from content (Task 2); verify by parent review of request records and sentinels | closed |
| T-77-07 | Spoofing | architecture/usage claims | high | mitigate | Current source/receipt grounding and registration-specific requires_approval distinction; ungated coding/write/terminal tools retain host-user authority and sandbox warning | closed |
| T-77-08 | Tampering | canonical docs versus historical synthesis | high | mitigate | Historical text never promoted to current truth; synthesis cited as evidence only (Tasks 1-2); verify by parent provenance review | closed |
| T-77-09 | Information Disclosure | setup/usage examples | high | mitigate | Secrets private-only; no credential in argv/examples/receipts (Tasks 1-2); verify by parent route walk and reviewer check | closed |
| T-77-10 | Spoofing | capability/attachment claims | high | mitigate | Parent-exercised local routes only; unexecuted provider/live ops optional/blocked; FAQ distinguishes configured requested standards from actual per-PR remote review | closed |
| T-77-11 | Spoofing | optional-capability prose | high | mitigate | Actual-prereq labeling; unexecuted ops marked optional/blocked, never live (Tasks 1-2); verify by parent proof review | closed |
| T-77-12 | Tampering | synthesis-as-architecture copying | high | mitigate | Link-don't-copy rule; historical text never current truth (Task 1); verify by parent provenance review | closed |
| T-77-13 | Tampering | navigation/roots | high | mitigate | Once-only SUMMARY entries, resolved links/assets, mirror-outside-GitBook rule (Task 2); verify by parent rendered-link walk plus retained nav consumer | closed |
| T-77-14 | Spoofing | release/count/model claims | medium | mitigate | Unsupported claims removed; changelog entry invention-free (Tasks 1, 3); verify by parent source-diff review | closed |
| T-77-15 | Spoofing | generated catalog output | high | mitigate | No hand-edits; parent regeneration with --check consumer; inventory never posed as exercised handlers (Task 1); verify by --check lane | closed |
| T-77-16 | Tampering | source-word pins masking drift | medium | mitigate | Parent removes literal/ordering/copied-default pins while retaining the real drift consumer and nav contract (Task 2); verify by consumer lane plus pin-absence review | closed |

## Findings and independent dispositions

| Finding | Prior failure | Current independent closure |
| --- | --- | --- |
| SEC77-01 / T-77-02 | Direct response-read/validation/raw causes/status privacy; intermediate audit additionally found raw prior-read/root/backup/cleanup errors escaping sync | Existing whole-input constructor/transport/status guard, fromNone wrapped errors and outer exported sync OSError boundary. Actual before/after and six filesystem consumers prove safe exposed message/formatted traceback with truthful prior/new/backup effects. |
| SEC77-02 / T-77-07 | Blanket mutating/write approval claims contradicted unflagged coding registrations | Corrected SECURITY/user-guide require registration-specific requires_approval; ungated coding/write/terminal authority and sandbox warning retained. Independently closed at intermediate15/1 audit. |
| SEC77-03 / T-77-10 | FAQ asserted every-PR remote review without proof | Corrected FAQ distinguishes local requested standards from authorized remote connection/trigger evidence. Independently closed at intermediate15/1 audit. |

- Complete initial report: `local://v15-phase77-security-audit-initial.json` —13closed/3open at256baac.
- Complete intermediate report: `local://v15-phase77-security-rereview-before-filesystem.json` —15closed/1highopen at75744eb.
- Complete final structured verdict: `local://v15-phase77-final-security-verdict.json`; independent source inspection, all16declaredIDs, no new register expansion, no auditor-run checks or writes.
- Actual parent direct-FS failing-before/passing-after: `local://v15-phase77-direct-filesystem-before.json` / `...after.json`. Current six consumer cases also cover prior document read, root/backup setup and cleanup after real rollback versus real commit.
- Current parent full2350passed/16warnings/157.34s (`artifact://729`), focused200passed/1warning/7.98s, mandatoryprior630passed/1warning/92.04s; `local://v15-phase77-filesystem-remediation-binding.json` and `...final-regression-proof.json`.
- Cleanup failure after successful publication reports failure with the **new complete edition still in place** and private old backup retained. Cleanup after successful rollback leaves prior state restored; failed partial recovery is not “fully restored.” Single-writer handled exceptions only, not crash/concurrent publication or atomic visibility.
- `from None` suppresses displayed exception chains, not raw in-process `__context__`. No guarantee of arbitrary credential recognition or Python-memory erasure. Literal archived bodies remain untrusted data, not diagnostic bundles or content-granted authority.
- Final auditor noted the overbroad public sync docstring; parent corrected it in docs-only `4e4100f3eb51222e490ec2e0ea1c299644160c4f`, actual pydoc surface exit0, independently closed WR-FT-01. No runtime statement changed; the exercised privacy/state lane remains bound toacc2aa8, not relabeled as a new run.
- Latest affected-source register verification: `local://v15-phase77-stdout-security-verdict.json`. T-77-07/09/10 independently retraced source-root/workspace, protocol channel/credential/report and setup/live-claim boundaries; all other prior closures retained. No new threat scan, accepted risk, project check or auditor file write.
- Actual stdio invalid JSON-RPC frames were observed before correction; SDK tolerated them, and probeexit1 was a harness camelCase error, not launcherexit1. Parent corrected protocol emission, not a warning-suppression workaround. Actual three initialized SDK owned-file routes had no parse errors; two strict current wire consumers reject nonprotocol frames and read independent contents.
- Current behavior at1f0adf6: full2352passed/16warnings/176.35s before test-only isolation flag; current2strictcases passed after flag, fresh mandatory17-fileprior632passed/1warning/113.41s, Ruff0/401format/mypy3390. Default-doctor smoke is separately observed and does not claim all of its I/O was temporary; permanent strict consumers skip only that install-root audit probe.
- `local://v15-phase77-interface-correction-binding.json`, `...stdio-prior-regression.json`, `...final-caller-doc-binding.json`. Final26file177internallink audit0issues/14remoteunfetched; no final-sourceimage/remoteclone/hostedjob/publication/liveprovider/backendmission proof.

## Accepted Risks Log

No accepted risks. All16dispositions remainmitigate; no user risk waiver, no transfer. Phase76answers do not waive phase77's7unclassified assumptions or19unique descriptor-less prohibitions.

## Security Audit Trail

| Audit Date | Total | Closed | Open blocking | Run by | Snapshot |
| --- | --- | --- | --- | --- | --- |
| 2026-10-11 | 16 | 13 | 3 | gsd-security-auditor | 256baac |

## Sign-Off

- [x] All16threats have prospective dispositions and unique identifiers.
- [x] No accepted risk or transfer invented.
- [x] `threats_open: 0` independently confirmed after all behavioral repairs/proof.
- [x] `status: verified` records the declared-threat lane only.

**Security verification:** SECURED. **Phase approval:** Pending canonical owner and fresh human dispositions for7assumptions/19uniqueprohibitions. No next-phase, source-publication, deployment, live-operation or signedSwarm authorization follows this report.

## Security Audit 2026-10-12

| Metric | Count |
|---|---|
| Threats found | 16 |
| Closed | 16 |
| Open | 0 |
