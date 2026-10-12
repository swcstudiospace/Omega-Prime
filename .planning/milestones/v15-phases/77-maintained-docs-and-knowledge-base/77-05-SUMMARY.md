---
phase: 77-maintained-docs-and-knowledge-base
plan: 05
subsystem: docs
tags: [canonical-docs, source-faithfulness, native-gsd]
requires:
  - phase: 76-clean-ci-cd
    provides: exercised local image/release boundary and retained source gates
provides: ["Current integration, substrate, host and migration contracts", "Source-specific failure, credential, storage and transport prerequisites"]
affects: [77-06, 78, milestone-audit]
actuals:
  tasks: 2
  commits: 1
tech-stack:
  added: []
  patterns: [existing documentation layout and existing consumer boundaries]
key-files:
  created: []
  modified: ["docs/connectors.md", "docs/substrate.md", "docs/tool-host.md", "docs/grok-bot-native.md", "docs/migration.md"]
requirements-completed: ["DOC-01", "DOC-04", "DOC-02"]
coverage:
  - id: 77-05-source-guidance
    description: Current integration, substrate, host and migration contracts
    verification:
      - kind: integration
        ref: local://v15-phase77-canonical-cli-proof.json
        status: pass
    human_judgment: true
    rationale: Human source-faithfulness and phase prohibition/edge review remains required; local proof does not establish live accounts or remote publication.
completed: 2026-10-11
status: complete
---

# 77-05: Current integration, substrate, host and migration contracts

## Accomplishments and exercised evidence

Five existing guides reconciled against actual source. Substrate failure behavior is operation-specific; explicit token/broker/anonymous auth and write/read visibility are distinct. Hindsight default points to actual sourceURL. Host/native/migration source bounds distinguish inventory, optional libraries/services, local preparation and unproven live operations. Bounded usage reviewer found no additional blocker; full2175package consumers passed. Proposed live substrate.probes command was deliberately not executed, not reported as proof.

Evidence: `local://v15-phase77-canonical-cli-proof.json and artifact://624`. Full package2175passed/16warnings/158.21s(`artifact://624`); Ruff401formatted/mypy339sourcefiles/26structuralevals/assemblypassed. These are local/source proofs, not hostedCI or external-service operation.

## Task Commits

1. **Task 1: Current integration, substrate, host and migration contracts** — `df743524d5277e88c58910552b1dd4dea949252d` (one green bounded source commit).
2. **Task 2: Source-specific failure, credential, storage and transport prerequisites** — `df743524d5277e88c58910552b1dd4dea949252d` (same coherent documentation/consumer contract; no fake separate commit).

## Source scope and limits

Modified only ["docs/connectors.md", "docs/substrate.md", "docs/tool-host.md", "docs/grok-bot-native.md", "docs/migration.md"]. No root/sharednav/changelog/sourceoutsideplan/submodule/upstream/.swarm/userUVlock or planning files staged. The applicable generated/config projections are intentionally unchanged when already conformant. No push/PR/merge/tag/imagepublication/deployment/liveGrok/model/provider/backend/missionproof is claimed.

`requirements-completed` records this plan's finished contributions, not phase acceptance. The whole-phase requirements in REQUIREMENTS remainpending until77-02/06 integration, independentphasegates and the canonical owner/human dispositions pass. All7unclassifiedflags and19unique descriptor-lessprohibitions stay unverified by human; phase76answers do not waive them.

## Next readiness

Source surfaces are ready for77-02historicalguide and77-06root/navigation/changelog integration. Existingpatternsretained; no new runtime/provider/judgeframework.
