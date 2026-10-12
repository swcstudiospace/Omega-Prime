---
phase: 77-maintained-docs-and-knowledge-base
plan: 01
subsystem: tooling
tags: [greptile, mcp, provenance, rollback]
requires:
  - phase: 76-clean-ci-cd
    provides: retained independent gates and truthful refresh skip boundary
provides:
  - Canonical namespace refresh with complete candidate validation and stable ordering
  - Recoverable same-filesystem publication and zero-write unchanged observations
  - Exact untrusted archival bodies with visible notices and actual clock/count provenance
affects: [77-02, 77-06, milestone-audit]
actuals:
  tasks: 3
  commits: 5
tech-stack:
  added: []
  patterns: [existing stdlib MCP client and private atomic filesystem helpers]
key-files:
  created: []
  modified: [omega_prime/greptile/kb_sync.py, omega_prime/tests/test_kb_sync.py]
requirements-completed: [KB-02]
requirements-addressed: [KB-01, KB-02, KB-03]
coverage:
  - id: refresh-preservation
    description: Validated refresh and unchanged/failure states preserve the complete owned mirror
    requirement: KB-02
    verification:
      - kind: integration
        ref: local://v15-phase77-owned-client-runtime-proof.json
        status: pass
      - kind: unit
        ref: omega_prime/tests/test_kb_sync.py
        status: pass
    human_judgment: false
  - id: historical-trust-boundary
    description: Exact archival bodies are untrusted data, not authorization or current truth
    requirement: KB-03
    verification: []
    human_judgment: true
    rationale: Repository mirror/guides and phase prohibition review remain dependent-plan acceptance
completed: 2026-10-11
status: complete
---

# 77-01: Complete candidate observation before recoverable publication

Canonical `namespaceId` replaces the old request/lookup alias throughout the existing client and consumers. Every returned path/count/version is validated; index state agrees with the actual path set, index is retrieved first, and stable ordering prevents a reordered listing from manufacturing a change.

## Accomplishments

- Entire before/get/after candidate is admitted before target writes. Empty/no-key/unenrolled/failed/unchanged outcomes remain distinct; the real empty publication has `indexPresent:false` and a null docs version.
- Same-filesystem hard-link backups retain original inodes, bytes, mode and timestamps during handled publication exceptions. No claim of SIGKILL or parallel-writer safety. Failed recovery retains its backup rather than destroying the recoverable edition.
- Previous generated paths are confined before reading/pruning/restoring; unrelated files and the maintained guide are not overwritten. Unowned destination collisions fail.
- Source bodies remain exact, with a visible untrusted historical notice and relative manifest link. Per-document request/fetch times, batch completion, declared chars, observed UTF-8 bytes/digests and complete/retrieved totals are recorded. Only observation clocks are excluded from unchanged equality.
- Diagnostic scanner stays unchanged at the full error/diagnostic boundary before clipping. Ordinary archived prose is not a diagnostic bundle; no client/session/environment credentials enter provenance.
- Every public `sync` operating-system failure is converted through the existing full-input diagnostic gate with displayed raw causes suppressed. Cleanup after successful publication still reports failure while retaining the complete new edition and private old backup; it does not falsely claim rollback.

## Verification actually exercised

- Current full package: `.venv/bin/python -m pytest omega_prime/tests -q` — **2350 passed**,16warnings,157.34s; `artifact://729`, source `acc2aa8659327e11bf22a0357a66b8c86726bda4`. Earlier2175/2177/2344 totals remain historical.
- Current focused KB/catalog/rules/docs consumer union — **200 passed**,1warning,7.98s. Fresh mandatory prior17-file regression — **630 passed**,1warning,92.04s under600-second runner; `local://v15-phase77-final-regression-proof.json`.
- Actual owned HTTP JSON and SSE peers each imported all21 captured primary bodies, reconciled every body/hash/count, consumed canonical namespace/session/auth endpoints, proved shuffled-listing zero-write bytes/mtimes/inodes, late-fetch preservation and second-real-write rollback. Latest peer proof: `local://v15-phase77-remediated-client-runtime.json`; its recorded source snapshot predates the final filesystem wrapper, and **fixture clocks are not live vendor retrieval proof**.
- Actual exported-API filesystem failing-before/passing-after proof: `local://v15-phase77-direct-filesystem-before.json` and `...after.json`. Six current consumer cases cover prior manifest/document read, root preparation, backup creation, cleanup after real rollback and cleanup after real commit; safe type/message/traceback plus truthful prior/new/backup/sentinel state. `local://v15-phase77-filesystem-remediation-binding.json`.
- Current Ruff lint0; formatter normalized only wrapper with400others alreadyformatted; mypy339sourcefiles passed under unchanged repository configuration. Earlier26structural evals and assembly passed at their recorded snapshots; structural evals are not KB proofs.
- Initial zero-alias source/test search belongs to the first-cutover snapshot. Current negative alias-only fixtures deliberately prove refusal; they are not positive legacy callers and are not deleted to satisfy a source-text pin. Production request/admission remains canonical namespaceId only.

## Task Commits

1. **Task 1: Strict production refresh tracer** — `2fb1d8721714736894e0b4b996947cbaaa014dc1` (feat).
2. **Task 2: Migrate current-schema consumer regressions** — `2fb1d8721714736894e0b4b996947cbaaa014dc1` (same atomic contract change; code and broken old consumers must cut over together).
3. **Task 3: Reconcile scheduled refresh workflow** — no source delta: existing single invocation, key skip,30-minute bound and change-only PR behavior already conform; source inspection and zero-write proof retained. No fake commit.
4. **Task 1: Direct opener privacy correction** — `1a2942fcfe0364c0170e83da2d864c0d38c347c4`.
5. **Tasks 1–2: Public diagnostics, reply correlation and admission boundary corrections with meaningful consumers** — `5b5b060e6e488cd6fc0b187c37929364e3a6d04e`.
6. **Tasks 1–2: Residual filesystem privacy and truthful recovery regressions/guidance** — `acc2aa8659327e11bf22a0357a66b8c86726bda4`;4explicit source paths, no planning/userlock staging.
7. **Task 1: Narrow public API docstring to actual recovery states** — `4e4100f3eb51222e490ec2e0ea1c299644160c4f`; docstring-only, actual pydoc surface exit0, no runtime statement or new test changed. Independent WR-FT-01 closure recorded in final REVIEW.

No planning files, unrelated uv.lock, submodule or .swarm changes were staged. No push/publication/deployment/live model/provider/backend mutation.

## Deviations and limits

Parent corrected publication metadata restoration, removed dead unused staging/response variables and request/header wording pins, required real empty/version/index/path consistency and visible archival labeling. These are the locked complete/preserving/trust contract, not new platform machinery. Shared KB-01/KB-03 interfaces are implemented here; their complete repository mirror and guide outcomes belong to77-02. All phase77 requirements stay pending in REQUIREMENTS until the final owner and human dispositions accept the phase.

## Next readiness

All dependent source plans and the fresh mounted-tool complete mirror are now implemented/committed. Final independent security/review/Nyquist refresh and canonical goal verification remain required. Seven unclassified assumptions and19unique/28physical descriptor-less prohibitions remain human-unverified; phase76 decisions do not waive them. No phase advancement or source publication follows implementation alone.
