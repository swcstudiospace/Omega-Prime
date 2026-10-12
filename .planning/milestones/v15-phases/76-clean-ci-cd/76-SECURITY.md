---
phase: "76"
slug: clean-ci-cd
status: verified
threats_open: 0
asvs_level: 1
register_authored_at_plan_time: true
created: "2026-10-10"
---

# Phase76 — Security

All13 plan-authored IDs retained. Independent native ASVS1/block-high auditor returned SECURED13/13 after the confirmed duplicate-member retention gap was strictly repaired and parent before/after/CLI regression inspected. Parent persists the read-only verdict; no risk accepted, signedSwarm approval, image publication or deployment. Source-mitigation closure is not final phase acceptance or a claim that the pre-repair image validates newly changed package bytes.

## Trust boundaries

| Boundary | Data/control crossing |
|---|---|
| Product/child diagnostic → CI publication | Complete report, observed exits/identity and potentially sensitive failure text |
| CI workflow → privileged runner/package publisher | GitHub event/ref/permissions, source/check/run/artifact identity and approval |
| Generated inputs → built image | Public package resources versus heavy/private root submodule content |
| Validation artifact → publication | Archive checksum/local image ID/source SHA and required success evidence, never rebuilt bytes |
| KB automation → human review | Secret availability/status and synthesized kb/-only PR content, never automatic merge |
| Documentation → operator | Validated identity, authorization, release/rollback and local/live proof claims |

## Authored threat register

| Threat ID | Category | Component | Severity | Disposition | Planned mitigation / required proof | Status |
|---|---|---|---|---|---|---|
| T-76-01 | Information Disclosure | ci.yml retention upload | high | mitigate | Whole-input guarded canonical admission/private failure metadata, no raw upload; strict recursive duplicate rejection; poisoned-label and real165-report before/after/private600 proof. | closed |
| T-76-02 | Tampering | readiness/retention exits | high | mitigate | Exact observed nonzero retained; rejected0→effective1/metadata0, unobserved null. Actual duplicate root/nested0/7 regression and genuine unique report control; ambiguity gap repaired, not waived. | closed |
| T-76-03 | Elevation of Privilege | main CI permissions | medium | mitigate | Explicit least contents-read, concurrency, deadlines, preserved matrices/unmasked stages; independent source review/actionlint. | closed |
| T-76-04 | Tampering | pip-audit scope | high | mitigate | Committed lock and only existing PYSEC-2026-4114 rationale; no new exception or audit bypass. | closed |
| T-76-05 | Information Disclosure | pinned Rust test environment | high | mitigate | Exact pins/toolchain/locked native/parity/license independence and credential unsetting preserved; independent source audit, no fresh upstream run implied. | closed |
| T-76-06 | Repudiation | KB skip versus success | medium | mitigate | Explicit missing-key skipped/not-run, kb-only human-review PR, no auto-merge; actual CLI absence/auth negatives and source review. | closed |
| T-76-07 | Tampering | generated context exclusions | high | mitigate | Generator root redteam exclusion with product adapter/plugin/roster retained; real Docker context proof, controls unchanged by retention repair. | closed |
| T-76-08 | Tampering | generated Dockerfile/ignore | medium | mitigate | Deterministic generator-owned projections, guarded input/foreign writes/private helpers; actual render/check, byte-equality and root-USER refusal. | closed |
| T-76-09 | Information Disclosure | image validation failure retention | high | mitigate | Full-input gate before clipping/no raw fallback; actual302-line candidate outside200tail withheld/private600, safe output retained; unchanged verifier publication guard. | closed |
| T-76-10 | Spoofing | publisher image identity | high | mitigate | Exact source/ref/event/run/immutable-artifact/status binding, strict guarded JSON, archive checksum before load and image ID before tag/push, no rebuild. Actual deleted-tag archive proof at prior image snapshot; fresh current image proof pending. | closed |
| T-76-11 | Elevation of Privilege | publisher authorization | high | mitigate | Default-off manual input/ref-as-data, live named reviewer-presence/protected-ref/current-success gates, isolated package-write/read-only artifact API and separate noncancelling publication. No observed approval; no-self/admin-bypass policy remains an explicit future prerequisite. | closed |
| T-76-12 | Tampering | image scan policy | high | mitigate | Unchanged SPDX upload and vuln/CRITICAL/ignore-unfixedtrue/exit1 with no added exception; actual prior image SPDX/scan0, refreshed post-repair scan pending. | closed |
| T-76-13 | Information Disclosure | release/rollback guidance | high | mitigate | Warning-only markers, prior validated bytes/digest/no rebuild rollback, explicit local/historical/hosted/live boundaries and excluded operations; source/evidence-aligned guide/changelog. | closed |

## Accepted risks

None. All21 requirements retain their mapping; no risk waiver. Image publishing/deployment and unowned Grok/provider/backend operations remain excluded from actual milestone execution.

## Sign-off

- [x] All13 unique plan-authored IDs retained with dispositions.
- [x] Actual source mitigations inspected and parent consumer/container evidence retained at its stated snapshots.
- [x] Independent ASVS1/block-high source audit closed all13 authored threats without accepted risk.
- [x] `threats_open: 0` and `status: verified` persisted from independent verdict; canonical phase acceptance remains separate.

## Independent evidence and qualifications

Auditor: `Native76SecurityAudit`, read-only native GSD, SECURED13/13 after T-76-01/02 re-audit. Initial OPEN verdicts remain historical; no child ran checks or wrote this report. Source scope was HEAD9ec6ab7 plus the parent's inspected strict retention repair/regression; a later commit ID is not inferred.

Actual ambiguity before/after: `local://v15-native-phase76-retention-duplicate-before.json` / `...duplicate-after.json`; new real CLI regression covers root/nested members and observed0/7,15CLI tests18.39s/Ruff0/mypy339. Genuine165 unique report still exits0/verbatim. Original nonzero and unobserved-null behavior preserved.

CI-02 unclassified row remains unresolved/verificationnull; eight descriptorless prohibitions remain flagged-unverified, no fabricated wired machine-check descriptors or human waivers. Reviewer-presence metadata does not establish prevent-self-review or disabled admin bypass. Current protected publication environment is absent; future named-reviewer configuration and real approval including operator-policy restrictions are prerequisites before any separately authorized publication.

Full current source regression, refreshed image input/runtime/SPDX/scan/archive evidence, code review and canonical fingerprint are owner gates, not supplied by this source-mitigation audit. Earlier2160 suite and earlier full image proof must not be relabelled current after package correction. No hosted Actions artifact transfer, approved environment, OCI push, deployment, live provider/Grok/backend or cluster operation was executed.

## Security Audit 2026-10-12

| Metric | Count |
|---|---|
| Threats found | 13 |
| Closed | 13 |
| Open | 0 |

State A re-audit for the stale-verification resume at HEAD `3208b9b` (ASVS 1, block_on high, register authored at plan time, threats_open 0 → workflow short-circuit to Step 6; no auditor spawned, no new-threat scan). `git diff --quiet c88ac89 HEAD` over `.github/`, `.dockerignore`, `Dockerfile`, `ci_receipt.py`, `deploy.py`, `image_gates.py`, `docs/deploy.md` and the three phase-76 test files exited 0; only `CHANGELOG.md` and `test_kb_sync.py` changed (phase 77). Grep-level mitigation anchors re-observed: duplicate-member `object_pairs_hook` (`ci_receipt.py:347`), `_retention_effective` (`:247`); ci.yml permissions/concurrency/timeouts and three-interpreter matrices (`:11,14,21,25,115,119,162,166,206`); single `--ignore-vuln PYSEC-2026-4114` (`supply-chain.yml:37`); credential unset (`rust-parity.yml:101`); kb-refresh skip branch with no merge step; `/redteam` (`deploy.py:274`, `.dockerignore:7`); hardening/scan/publisher gates (`grokbot-image.yml:135-136,442-444,545-558`); phase-76 CHANGELOG entry and missing-key/auth-denial consumers (`test_kb_sync.py:675-705`) retained. `actionlint` over the five workflows exited 0; per-task consumers 401 passed and prior-phase regression 256 passed (exit 0 each, 2026-10-12). No risk accepted, nothing published or deployed.
