---
id: SEED-011
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

# SEED-011: Omega access to Railway RAGFlow, Hindsight, GreptimeDB, TimescaleDB and DragonflyDB

## Why This Matters

The five services live in Railway private networks (Ultrathink: Greptime/Timescale/Dragonfly; AgentSubstrate: Hindsight/RAGFlow) reached over Tailscale forwarders. User decisions: keep substrate mediation for the three databases (store-lock test stays), add direct API clients for Hindsight and RAGFlow plus read-only health probes for all five, default to tailnet forwarders with per-service endpoint config, and make no Railway resource changes.

## When to Surface

**Trigger:** when planning the Omega milestone (v10) after v9 closure and the verified prior-branch push.

This seed will surface during `/gsd:new-milestone` when the milestone scope matches.

## Scope Estimate

**Medium** — estimated from the read-only research recorded in the breadcrumbs.

## Breadcrumbs

- `omega_prime/skills/railway-tailscale/SKILL.md`
- `omega_prime/memory/hindsight_service.py`
- `omega_prime/tools/infra.py:306-309`
- `/root/.omp/agent/sessions/-src-repos-Omes-Bot/2026-10-07T08-07-02-266Z_01a11566-d17a-71fc-9303-d0456a5234f3/local/omega-research-ScoutSubstrate.json`
- `/root/.omp/agent/sessions/-src-repos-Omes-Bot/2026-10-07T08-07-02-266Z_01a11566-d17a-71fc-9303-d0456a5234f3/local/omega-integration-decisions.json`

## Notes

Captured from the user's orchestration request in this session. The user's integration decisions are recorded in `/root/.omp/agent/sessions/-src-repos-Omes-Bot/2026-10-07T08-07-02-266Z_01a11566-d17a-71fc-9303-d0456a5234f3/local/omega-integration-decisions.json`. Research is source-only, not runtime proof.
