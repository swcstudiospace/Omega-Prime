---
plan: 79-02
status: complete
phase: 79
---

# 79-02 Summary: Simulation library, detection validation, findings pipeline

## Tasks completed

- Task 1: `sidecar/case.schema.json` + six versioned case files (one per class: prompt-injection, jailbreak, abuse, malformed-input, auth-bypass, cost-abuse), loopback-only transport declared, inert payloads; strict minimal-YAML loader rejecting hostile constructs (tabs, anchors, tags, block scalars, dup keys, deep nesting).
- Task 2: `sidecar/simulation.py` — drift-check first; IDLE→RUNNING→IDLE transitions via the 79-01 state machine; per-case observed-vs-expected detection comparison (`residual_rationale` excuses exactly its own case); `last-run.json` record; `run`/`validate` CLI (exit 0/1/2); validate fails closed naming the remedy when no run record exists.
- Task 3: `sidecar/findings.py` — sha256 content-hash dedupe (replay bumps `occurrence_count`, preserves `ts`; whitespace-only differences dedupe), atomic writes, offline `work-items.json` export, `check_drift` (VERSION + adapter surface vs contract; adapter ast-parsed, never imported; unreadable inputs fail closed as drift) → DEGRADED naming the disagreeing pair.

## Deviations

- `sidecar/harness.py` intentionally unmodified: plan's file list excludes it; simulation reuses `sidecar.state.transition` directly.

## Verification

`test_sidecar_cases.py` (schema + 6 classes + hostile-YAML), `test_sidecar_simulation.py` (fault injection, DEGRADED edges, no-socket-by-construction), `test_sidecar_findings.py` (dedupe/export/drift) — all green; `run --suite smoke` then `validate --suite smoke` exit 0; included in the 2642-passed full run.
