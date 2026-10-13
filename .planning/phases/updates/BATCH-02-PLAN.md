# Batch 02 Plan: Purple-Team SOTA features (scored, ship order locked)

**Rubric:** impact / enterprise-readiness / rollback-safety / effort-fit / alignment (1–5 each, /25).
**Batch branch:** `batch-02-purpleteam` → PR to main when green. Specs: `PLAN-G1..G7.md` (this directory).

| Ship | Feature | Impact | Ent | Rollback | Effort | Align | /25 | Spec |
|------|---------|--------|-----|----------|--------|-------|-----|------|
| 1 | G1 Pin-bump automation | 5 | 5 | 4 | 3 | 5 | **22** | PLAN-G1.md |
| 2 | G5 Transitions timeline | 4 | 4 | 5 | 5 | 4 | **22** | PLAN-G5.md |
| 3 | G6 Coverage matrix | 4 | 4 | 5 | 5 | 4 | **22** | PLAN-G6.md |
| 4 | G4 Triage approvals from chat | 4 | 5 | 4 | 4 | 5 | **22** | PLAN-G4.md |
| 5 | G2 RoE in-chat signing UX | 5 | 4 | 4 | 4 | 4 | **21** | PLAN-G2.md |
| 6 | G3 Weekly durable digest | 3 | 4 | 5 | 5 | 4 | **21** | PLAN-G3.md |
| 7 | G7 Upstream release watcher | 3 | 4 | 5 | 5 | 4 | **21** | PLAN-G7.md |

**Rationale highlights:** G1 first — it automates the runbook that clears the CRITICAL `proxy-addr` advisory properly (the redteam-node-audit gate stays honestly red until an upstream bump lands). G5+G6 pair in one owner (same file). G2+G4 pair (same command surface). Large-effort cap: G1 only.

**Dropped from batch 01 report's candidate list (with reasons):** none — all 7 proposed items map 1:1 to G1–G7.

**Waves:** A = G1 ∥ G2+G4. B = G3+G5+G6 (one owner, shared file). C = G7 (heartbeat/durable shared with B, serialized after).
