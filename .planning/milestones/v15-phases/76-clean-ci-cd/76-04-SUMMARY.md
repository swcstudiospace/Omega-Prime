---
phase: 76-clean-ci-cd
plan: "04"
subsystem: infra
tags: [docker, conformance, sbom, critical-scan, artifact-identity]
requires:
  - phase: 76-03
    provides: generated root-submodule exclusion and real context proof
provides:
  - Same-image hardened SSE and Streamable HTTP validation with complete admin proof admission
  - Actual SPDX and configured blocking CRITICAL scan evidence
  - Default-off protected manual publisher binding actual per-step outcomes and transferred bytes
affects: [76-05, release-guidance]
actuals:
  tasks: 3
  commits: 1
tech-stack:
  added: []
  patterns: [existing-verifier-and-secret-gate, private-atomic-evidence, same-run-immutable-artifact-identity]
key-files:
  created: [omega_prime/grokbot/image_gates.py, omega_prime/tests/test_grokbot_image_gates.py]
  modified: [.github/workflows/grokbot-image.yml]
key-decisions:
  - Both server containers use supported transport sse; clients select sse versus http
  - Admission requires every expected row once, no skip, coherent counts and observed exit0
  - Publication controls and required check set are not caller-skippable; transfer never rebuilds
  - Missing protected environment is a real future prerequisite, not a fabricated approval
requirements-completed: [CI-01, CI-02, CI-03]
completed: 2026-10-11
status: complete
---

# Phase76 Plan04 — Same bytes, real runtime, guarded publisher

One real generated image passed both hardened transport/admin lifecycles, SPDX generation and the unchanged fixed-CRITICAL scan. The optional publisher is implemented but was not invoked.

## Exercised runtime and supply chain

- Actual build exited0 in193.63s, base digest `sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258`. Retained local image identity `sha256:bd6afe9fa57b7aeb7271b07543ddc7dc41aa27a5c8d5d8a3737140e5d72031fd` is one semantic domain, not an archive checksum or a claimed registry publication digest.
- SSE and Streamable HTTP each17pass/0fail/0skip, call-token admin refusal and genuine grant/revoke transitions,12 persisted audit rows each, observeduid10001/read-only rootfs/cap-dropALL/no-new-privileges and SIGTERMexit0; owned containers removed. The verifier deliberately omits required handler arguments after approval, so this proves admin-state/gate effects, not arbitrary gated handler execution.
- Real Syft1.54.1 produced SPDX2.3 (273 packages). Real Trivy0.75.0 returned0 with configured vuln/CRITICAL/ignore-unfixedtrue/exit1 policy: zero fixed-CRITICAL findings. Vendor-severity/third-party-SBOM warnings retained; not all-severity clean, not secret-scan/license clearance. Tool images pinned to observed immutable digests in `local://v15-native-phase76-image-runtime-verification.json`.
- Actual saved archive checksum `43b466f147c5d01ffe2f3a406a8758fde5c89deaa11d50750076275b703aa405`, private0600. Own image tag deleted before loading that archive; rehydrated exact same image ID without a rebuild/cache-tag fallback. Built helper hash equals committed helper hash `9f678853412346119f43734ee15b78acfbdc2d2a269d98f7969d226aa75dd42c`.
- Current actual image reports admit through production helper; partial/skipped-admin mutations refuse. Actual workflow manual/ref guard rejects automatic trigger,off,wrong-ref and metacharacter input without an injected side effect. Actual workflow diagnostic retention against owned Docker stdout withholds an unknown recognized candidate, preserves safe output and mode0600. No hosted failed-job status proof claimed from that standalone exercise.

## Source/control changes and defects corrected

Initial16 helper tests missed integration defects. Actual actionlint/producer-schema/empty-check/fresh-host probes exposed missing shell closer, nonexistent reachable JSON flag, empty-required-check bypass and artifact-cwd import failure. Corrected undeclared source aliases, full binding, input-as-data, post-scan receipt ordering and cancellable-validation/noncancelling-publication separation. Restored two accidentally cut declarations, removed incidental field/writer echo tests, made duplicate-key/secret fixtures otherwise schema-valid, and compared archive checksum before load. Before/after records retained; no detector rule or scan exception changed.

Publisher source requires explicit dispatch opt-in/ref confirmation, named reviewer-protected environment observation, protected ref, successful validation job plus recorded actual validation/SBOM/scan outcomes, exact checkout/event/ref/run/source/artifact-ID bindings, full guarded strict JSON, archive checksum and loaded image ID before tag/login/push. Same-run artifact API read is publisher-scoped; package-write appears only there. No rebuild. Registry RepoDigest is recorded separately only after a future authorized push. Required-reviewer setup and prohibition of self-review/bypass are operator policy prerequisites, not observed approvals.

## Verification and evidence limits

- Full source union2160 passed/16warnings/151.24s, exit0 (`artifact://402`). Target helper/verifier47passed; whole Ruff clean/mypy339/format401 and actionlint passed.
- Complete receipts and raw SPDX/Trivy/transport JSON: `local://v15-native-phase76-image-runtime-verification.json`, `...image-real-admission.json`, `...manual-ref-guard.json`, `...image-diagnostic-proof.json`, `...image-gates-before.json`, `...image-gates-after.json`.
- Upload/download v7 and artifact-id output confirmed from primary action catalog, not a hosted upload. Actual repository environment inventory returns0. No environment configured, no GitHub workflow execution, image publishing, deployment, cluster operation, live Grok/model/provider/backend call or signedSwarm approval claimed.
- Plan05 and independent ASVS13/13 source closure now completed; all requirements remain Pending until canonical acceptance/planning-flag disposition. Descriptorless prohibitions remain flagged, no fabricated wired-check descriptor/waiver.

## Task Commits

1. **Task 1: Both-transport hardened validation and safe retention** — `2bb3dfd747670ed659cb3c1f47b4e22ca99e4d69`
2. **Task 2: Manual protected same-byte publisher source** — `2bb3dfd747670ed659cb3c1f47b4e22ca99e4d69`
3. **Task 3: Bounded admission/identity/strict-evidence helpers and consumer regressions** — `2bb3dfd747670ed659cb3c1f47b4e22ca99e4d69`

Parent source-only atomic commit after runtime/scan/union proof. Planning artifacts and unrelated lock excluded. Local image was built from settled then-uncommitted source; matching helper bytes are observed, not a false original commit-only build provenance claim.

## Current-source proof after audited correction

The initial image/runtime/SPDX/scan/archive numbers above are historical snapshots. Strict retention correctionc88 changed copied package bytes, so parent rebuilt from committed current input rather than relabelling those receipts. New image ID `sha256:32158495ccf6ce8ed9678d5f480201f67122f42528bf7cc506aaec3b73e89185`, both17pass/0skip/cleanSIGTERM and actual hardening/admin transitions, SPDX273packages, configured fixed-CRITICAL0; fresh archive `58f5c6b6613d86387508ae9981cf6c780a67ac16ecfff48fe67ac9da7b2b07cc` rehydrates the exact ID after deleting its own tag, no rebuild during transfer. Committed CI-script hash385d5bed…9789c equals actual image bytes. Complete source-consistent receipt `local://v15-native-phase76-post-repair-image-verification.json`; earlier raw artifacts retained separately. Current product2161/evals26/assembly, final-doc158, prior-phase254 regressions and source audit13closed are observed, not hosted/live evidence. Canonical owner still pending.
