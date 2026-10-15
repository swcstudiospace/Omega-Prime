# Requirements: v18 Operator posture

Accepted 2026-10-14 when the operator said to continue building the phases. The seven were the set recorded in STATE.md. Phase 77.1 live UAT stays user-owned.

- [x] **OPS-01**: A purple_cycle cron kind can be scheduled, listed, cleared, and ticked. Invalid interval, apply mode, and unknown steps write no job. Plan and run never dispatch findings.
- [x] **OPS-02**: durable_status adds cycle and sync_backlog. Missing or unreadable inputs are empty blocks and do not raise. Existing keys stay.
- [x] **OPS-03**: /omega-redteam-conflicts lists grant conflicts, basenames only, capped. Bad arguments and a detector failure write nothing and are not an empty success.
- [x] **OPS-04**: Findings export as finding/1 JSONL with the coverage export file policy. /omega-redteam-findings --jsonl prints that text and writes no file.
- [x] **OPS-05**: A second live purple cycle is rejected with cycle-locked. A deadline stops later steps and records partial, not success. Plan mode stays pure.
- [x] **OPS-06**: /omega-redteam-readiness reports the five readiness fields and never starts an engagement.
- [x] **OPS-07**: /omega-audit verifies the audit chain. A missing log is empty, not success. A broken chain returns no record bodies.
