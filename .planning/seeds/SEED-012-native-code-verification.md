---
id: SEED-012
status: dormant
planted: 2026-10-07
planted_during: v9 SOTA-upgrade phase 46/48 gap closure
trigger_when: when planning the Omega milestone (v10) after v9 closure and the verified prior-branch push
scope: Large
audit_acknowledged:
  milestone: v15
  at: 2026-10-12
  status: dormant
---

# SEED-012: Native code verification bound to executed commands

## Why This Matters

Verification today is partial: receipts are structure-checked and model-written, not tied to executed commands; the gate runner only checks the Omega Prime repo with whatever python3 is on PATH; Greptile is unconfigured. Omega must verify target-repo code by actually running gates (sandboxed where available) and binding receipts to the recorded runs.

## When to Surface

**Trigger:** when planning the Omega milestone (v10) after v9 closure and the verified prior-branch push.

This seed will surface during `/gsd:new-milestone` when the milestone scope matches.

## Scope Estimate

**Large** — estimated from the read-only research recorded in the breadcrumbs.

## Breadcrumbs

- `omega_prime/tools/quality.py:148-155`
- `omega_prime/tools/quality.py:240-241`
- `/root/.omp/agent/sessions/-src-repos-Omes-Bot/2026-10-07T08-07-02-266Z_01a11566-d17a-71fc-9303-d0456a5234f3/local/omega-research-ScoutDesk.json`
- `/root/.omp/agent/sessions/-src-repos-Omes-Bot/2026-10-07T08-07-02-266Z_01a11566-d17a-71fc-9303-d0456a5234f3/local/omega-research-ScoutOmesSurface.json`

## Notes

Captured from the user's orchestration request in this session. The user's integration decisions are recorded in `/root/.omp/agent/sessions/-src-repos-Omes-Bot/2026-10-07T08-07-02-266Z_01a11566-d17a-71fc-9303-d0456a5234f3/local/omega-integration-decisions.json`. Research is source-only, not runtime proof.
