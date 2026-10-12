---
id: SEED-003
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

# SEED-003: Native Agent Swarm connectivity through a process-edge bridge

## Why This Matters

agent-swarm (no LICENSE file) exposes a Python TaskStore, signed swarm.v1 envelopes and orch_plan/orch_status/swarm_run scripts, but no MCP server; Omega Prime has zero swarm references. A minimal native connection reuses the omp python-bridge pattern: controller role via orch_plan/orch_status and a worker entry for swarm_run --runtime grok. Signing material must never reach Omega or its model context.

## When to Surface

**Trigger:** when planning the Omega milestone (v10) after v9 closure and the verified prior-branch push.

This seed will surface during `/gsd:new-milestone` when the milestone scope matches.

## Scope Estimate

**Medium** — estimated from the read-only research recorded in the breadcrumbs.

## Breadcrumbs

- `/root/src/repos/agent-swarm/swarm/taskstore.py`
- `/root/src/repos/agent-swarm/scripts/orch_status.py`
- `/root/src/repos/agent-swarm/omp/src/bridge.ts`
- `/root/.omp/agent/sessions/-src-repos-Omes-Bot/2026-10-07T08-07-02-266Z_01a11566-d17a-71fc-9303-d0456a5234f3/local/omega-research-ScoutSwarm.json`

## Notes

Captured from the user's orchestration request in this session. The user's integration decisions are recorded in `/root/.omp/agent/sessions/-src-repos-Omes-Bot/2026-10-07T08-07-02-266Z_01a11566-d17a-71fc-9303-d0456a5234f3/local/omega-integration-decisions.json`. Research is source-only, not runtime proof.
