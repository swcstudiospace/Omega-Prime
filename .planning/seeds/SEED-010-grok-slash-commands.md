---
id: SEED-010
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

# SEED-010: Native slash commands for the Grok Bot

## Why This Matters

There is no command dispatcher anywhere; magic keywords are notice-only in the Python loop, which the Grok Bot never runs, and the MCP host serves no prompts or resources although the SDK supports them. Native slash commands must be dispatchable from the Grok Bot surface (MCP prompts/resources and the turn path).

## When to Surface

**Trigger:** when planning the Omega milestone (v10) after v9 closure and the verified prior-branch push.

This seed will surface during `/gsd:new-milestone` when the milestone scope matches.

## Scope Estimate

**Medium** — estimated from the read-only research recorded in the breadcrumbs.

## Breadcrumbs

- `omega_prime/agent/magic_keywords.py`
- `omega_prime/agent/conversation_loop.py:164-168`
- `omega_prime/mcp_server.py:246-253`
- `omega_prime/grokbot/`

## Notes

v13 (2026-10-09) shipped the turn-path half: a user message that is only `/omega-...` runs `omega_command` and does not call the model. MCP prompts and resources are not served. This seed stays dormant until that half exists.

Captured from the user's orchestration request in this session. The user's integration decisions are recorded in `/root/.omp/agent/sessions/-src-repos-Omes-Bot/2026-10-07T08-07-02-266Z_01a11566-d17a-71fc-9303-d0456a5234f3/local/omega-integration-decisions.json`. Research is source-only, not runtime proof.
