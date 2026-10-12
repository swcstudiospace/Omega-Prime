---
phase: 77-maintained-docs-and-knowledge-base
plan: 06
subsystem: docs
tags: [navigation, canonical-docs, local-proof]
requires:
  - phase: 77-01..05 and77-07
    provides: preserving client, complete mirror and source-grounded guides
provides:
  - Coherent repository/root contribution and canonical landing guidance
  - Once-only canonical navigation and honest Unreleased entry
affects: [78, milestone-audit, source-delivery]
actuals:
  tasks: 3
  commits: 1
tech-stack:
  added: []
key-files:
  created: []
  modified: [README.md, CONTRIBUTING.md, docs/README.md, docs/SUMMARY.md, CHANGELOG.md]
requirements-completed: [DOC-01, DOC-05, KB-03, DOC-02, DOC-03, DOC-04]
coverage:
  - id: canonical-root-navigation
    description: Current URLs, prerequisites, source/live limits, canonical pages and assets resolve
    requirement: DOC-05
    verification:
      - kind: integration
        ref: local://v15-phase77-canonical-links-proof.json
        status: pass
      - kind: automated_ui
        ref: local://v15-phase77-root-nav-surface-proof.json
        status: pass
    human_judgment: true
    rationale: Source-faithfulness and phase assumption/prohibition review remain human judgments; no remotepublication proof
completed: 2026-10-11
status: complete
---

# 77-06: Canonical root claims and navigation match the actual union

## Accomplishments and exercised proof

README andCONTRIBUTING now use the actualomes-botURL/recursiveclone, formal>=3.11/recommended3.12/retainedCI3.12–14 distinctions, pinnedPrime/fullsuitepreq and optionalNode/native/serviceboundaries. RemovedAddBot-product/liveattachment language, blanketupstreamimport/toolchainclaims, ignoredgitlinktext, fixed inventoryprosecounts and stalehistoricalplanningrange. CurrentruntimeguidesareseparatefromADRsandthehistoricalmirror.

Thecanonical landingpointsreadersbyinstall/use/host/extend/release/historyaudiences; SUMMARYlistsall21canonicalpagesexactlyonce, notthe21archivedproviderpages. Unreleasedcontainsone truthfulphase77entry; no inventedversion, image size or live/deployedoperation.

- Existingnavigation/catalog/ruleconsumers:4passed/1warning; actualcatalog --check0.
- ActualMarkdownItlinkconsumer:25canonical/rootfiles,170internal links/assets,0issues;14remotelinksnotfetched. `local://v15-phase77-canonical-links-proof.json`.
- RealownedChrome/Mermaid surface:landingvisuallyinspected,21unique renderednavigation targets dereferencedstatus200withheadings; `local://v15-phase77-root-nav-surface-proof.json`. Standardresponsiveimage rule addedonlytothethrowawaypreviewfixture; notclaimedasGitBookCSS/configuration.
- ExactcurrentREADMEoneclick --work-root --dry-run exited0; maintainedisolatedreadiness exited0withvalidatedreport; `local://v15-phase77-root-cli-proof.json`. No owned/sourcehandler effects inferred from mere discovery.
- Fullcurrentcode2177passed/16warnings/160.27s(`artifact://662`),Ruff/mypy339passed;26structuralevals/assemblypassedatsamebehavioralunion. Structuralteststotalsarenotexternalproof.

## Task Commits

1. **Task 1: Coherent root and contribution claims** — `256baac892ddcd0d2572d52354e9d47a55107122` (docs).
2. **Task 2: Once-only canonical landing/navigation** — `256baac892ddcd0d2572d52354e9d47a55107122` (same rootintegration).
3. **Task 3: Honest Unreleased changelog entry** — `256baac892ddcd0d2572d52354e9d47a55107122` (same rootintegration).

Fiveexplicitownedsourcepaths only, no planning/uv.lock/submodule/.swarm/unrelatedstaging; .gitbook.yaml remainsunchangedbecauseit alreadyselectsdocsrootREADME/SUMMARY. No push/PR/merge/tag/imagepublication/deployment/Grok/model/provider/backendoperation is claimed.

## Final phase acceptance remains pending

Allseven sourceplans are implemented/summarized, but this is not canonicalphaseacceptance. Independentsecurity/Nyquist/code/evalcoverage gates and whole-goalverification mustrun;7unclassified assumptions and19unique descriptor-lessprohibitionsstillneedactualhumanreview. No waiver fromphase76answers and no phase78advancebeforethisgate.
