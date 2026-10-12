---
id: SEED-006
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

# SEED-006: NVIDIA OpenShell sandboxed execution and verification

## Why This Matters

OpenShell (Apache-2.0; installed here as /usr/bin/openshell 0.1.2) provides policy-enforced sandboxes. Omega Prime ships only a hand-maintained sandbox-policy.yaml that nothing generates or tests; execute/verification paths do not run inside a sandbox, and sandbox writable paths do not match Omega Prime home/root defaults. Integration should run verification inside OpenShell sandboxes with a policy generated from the seat policy and provider-based credentials.

## When to Surface

**Trigger:** when planning the Omega milestone (v10) after v9 closure and the verified prior-branch push.

This seed will surface during `/gsd:new-milestone` when the milestone scope matches.

## Scope Estimate

**Medium** — estimated from the read-only research recorded in the breadcrumbs.

## Breadcrumbs

- `omega_prime/hosting/openshell/sandbox-policy.yaml`
- `omega_prime/policy/policy.py`
- `omega_prime/credentials/broker.py`
- `https://github.com/NVIDIA/OpenShell`
- `/root/.omp/agent/sessions/-src-repos-Omes-Bot/2026-10-07T08-07-02-266Z_01a11566-d17a-71fc-9303-d0456a5234f3/local/omega-research-ScoutOmesSurface.json`

## Notes

Captured from the user's orchestration request in this session. The user's integration decisions are recorded in `/root/.omp/agent/sessions/-src-repos-Omes-Bot/2026-10-07T08-07-02-266Z_01a11566-d17a-71fc-9303-d0456a5234f3/local/omega-integration-decisions.json`. Research is source-only, not runtime proof.
