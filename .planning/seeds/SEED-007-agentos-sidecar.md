---
id: SEED-007
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

# SEED-007: rivet.dev AgentOS optional TypeScript sidecar and MCP over Streamable HTTP

## Why This Matters

AgentOS is a TypeScript library (V8/WASM VMs); CPython cannot run inside it. User decision: ship an optional Node sidecar hosting AgentOS VMs that Omega calls over HTTP for sandboxed JS/TS execution and type-checks, and serve Omega MCP over Streamable HTTP so AgentOS actors can call Omega tools. Today the host is stdio-only and AgentOS exists only as NOTES.md.

## When to Surface

**Trigger:** when planning the Omega milestone (v10) after v9 closure and the verified prior-branch push.

This seed will surface during `/gsd:new-milestone` when the milestone scope matches.

## Scope Estimate

**Medium** — estimated from the read-only research recorded in the breadcrumbs.

## Breadcrumbs

- `omega_prime/hosting/agentos/NOTES.md`
- `omega_prime/mcp_server.py:256-258`
- `https://github.com/rivet-dev/agentos`
- `https://rivet.dev/agentos/docs/`

## Notes

Captured from the user's orchestration request in this session. The user's integration decisions are recorded in `/root/.omp/agent/sessions/-src-repos-Omes-Bot/2026-10-07T08-07-02-266Z_01a11566-d17a-71fc-9303-d0456a5234f3/local/omega-integration-decisions.json`. Research is source-only, not runtime proof.
