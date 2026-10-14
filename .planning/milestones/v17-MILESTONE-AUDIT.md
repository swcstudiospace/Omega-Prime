---
status: passed
scores:
  requirements: 8/8
  phases: 4/4
---

# Milestone Audit: v17 XML Prompt Standard

**Audit date:** 2026-10-14
**Verdict:** PASS — PROM-01 through PROM-08 are met. Phases 85–88 are executed. Phase 77.1 live import UAT stays user-owned and is out of scope.

## Delivered

| Phase | What landed |
|-------|-------------|
| 85 | `omega_prime.tooling.prompt_format --check` in the CI docs job. Seat prompt names the convention in `<prompt_format>`. Assembled prompt regenerated. |
| 86 | Runtime prompts use `<omega_prime_system>`, `<steer>`, `<context_summary>`, `<elided>`, `<memory_notes>`, `<user_profile>`, `<goal_continuation>`, and `<autonomous_continuation>`. Interpolated text is XML-escaped. |
| 87 | 27 skill bodies, 7 routines, and the generated command files are wrapped. SETUP.md section 1 is an XML `<task>`. |
| 88 | Local gates and readiness are green. Live Grok Bot import remains the user's 77.1 check. |

## Out of scope, unchanged

- `omega_prime/redteam/pack/` stays byte-identical to upstream (`pack-diff` passed).
- `grokbot/templates/OMEGA_PRIME.md`, eval cases, and tool description strings are not application prompts.
- Phase 77.1 stays `human_needed`.

## Review

Two review rounds found closing-tag gaps. The fixes escape `&`, `<`, and `>`, including the compression role, and treat a closing tag with whitespace before `>` as the close. The prompt-format gate and generated-command load both require that close to be the exact final tag.

## Gate

Full pytest after that fix: 4607 passed, 3 skipped, 16 warnings, 300.91s.
