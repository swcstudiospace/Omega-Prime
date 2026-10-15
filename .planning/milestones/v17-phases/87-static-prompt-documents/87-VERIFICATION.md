---
status: passed
phase: 87
verified_at: 2026-10-14
---

# Phase 87 Verification

## Requirement outcomes

| Req | Outcome | Evidence |
|-----|---------|----------|
| PROM-06 | VERIFIED | Checked-in `SKILL.md` bodies open with `<skill name="{folder}" version="1">`. The curator writes the same envelope and escapes tool output. Load rejects a body whose first skill close is not the exact final tag. |
| PROM-07 | VERIFIED | The seven routines open with `<routine name="{stem}" version="1">` and one non-empty `<summary>`. Generated command names stay 24 curated and 58 total. |
| PROM-08 | VERIFIED | `plugin --check` reports 62 generated files up to date. Each generated command file carries `<command>` and `<instruction>`. `omega_prime/grokbot/SETUP.md` section 1 is an XML `<task>` and `docs/marketing.md` quotes it. |

## Gate

`python -m omega_prime.tooling.plugin --check` exited 0. `test_command_generation.py` covers an earlier `</skill>` and `</skill >` as `source_invalid`.
