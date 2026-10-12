---
id: SEED-004
status: dormant
planted: 2026-10-07
planted_during: v9 SOTA-upgrade phase 46/48 gap closure
trigger_when: when planning the Omega milestone (v10) after v9 closure and the verified prior-branch push
scope: Medium
audit_acknowledged:
  milestone: v15
  at: 2026-10-12
  status: dormant
---

# SEED-004: License-safe Claude Ultrathink connectivity plus native independent uplift

## Why This Matters

claude-ultrathink is AGPL; Omega Prime reaches it only through seven CLI bridge tools and last-plan.json. Gaps: no live planning link (planner host ids exclude grok-bot), ship tools run without --cwd/state-dir, and exit-0 JSON errors are reported as success. Native uplift must stay independently authored (no AGPL copying) while the bridge is fixed at the process edge.

## When to Surface

**Trigger:** when planning the Omega milestone (v10) after v9 closure and the verified prior-branch push.

This seed will surface during `/gsd:new-milestone` when the milestone scope matches.

## Scope Estimate

**Medium** — estimated from the read-only research recorded in the breadcrumbs.

## Breadcrumbs

- `/root/src/repos/claude-ultrathink/ultrathink.discovery.json`
- `/root/src/repos/claude-ultrathink/hooks/engine.ts`
- `omega_prime/skills/gotxcot-uplift/SKILL.md`
- `/root/.omp/agent/sessions/-src-repos-Omes-Bot/2026-10-07T08-07-02-266Z_01a11566-d17a-71fc-9303-d0456a5234f3/local/omega-research-ScoutUltrathink.json`

## Notes

Captured from the user's orchestration request in this session. The user's integration decisions are recorded in `/root/.omp/agent/sessions/-src-repos-Omes-Bot/2026-10-07T08-07-02-266Z_01a11566-d17a-71fc-9303-d0456a5234f3/local/omega-integration-decisions.json`. Research is source-only, not runtime proof.
