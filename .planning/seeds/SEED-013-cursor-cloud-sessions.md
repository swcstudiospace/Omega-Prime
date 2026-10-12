---
id: SEED-013
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

# SEED-013: Cursor Cloud Sessions with Ultrathink pre-injection uplift and post-session review

## Why This Matters

Cursor Cloud dispatch exists only as skill prose (trackplan-dispatch Lane A) with no adapter on either side. User decision: Omega uplifts and prepares the spec automatically, each launch requires human approval, and run monitoring plus post-session code review (Omega verification and review) run automatically. Cursor Cloud Agents API v1 (beta) / v0 with Basic-auth API key; no key is configured here.

## When to Surface

**Trigger:** when planning the Omega milestone (v10) after v9 closure and the verified prior-branch push.

This seed will surface during `/gsd:new-milestone` when the milestone scope matches.

## Scope Estimate

**Medium** — estimated from the read-only research recorded in the breadcrumbs.

## Breadcrumbs

- `omega_prime/skills/trackplan-dispatch/SKILL.md`
- `omega_prime/skills/gotxcot-uplift/SKILL.md`
- `https://cursor.com/docs/cloud-agent/api/endpoints`
- `/root/.omp/agent/sessions/-src-repos-Omes-Bot/2026-10-07T08-07-02-266Z_01a11566-d17a-71fc-9303-d0456a5234f3/local/omega-integration-decisions.json`

## Notes

Captured from the user's orchestration request in this session. The user's integration decisions are recorded in `/root/.omp/agent/sessions/-src-repos-Omes-Bot/2026-10-07T08-07-02-266Z_01a11566-d17a-71fc-9303-d0456a5234f3/local/omega-integration-decisions.json`. Research is source-only, not runtime proof.
