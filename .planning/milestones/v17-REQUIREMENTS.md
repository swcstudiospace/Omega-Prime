# Requirements: v17 XML Prompt Standard

Approved 2026-10-14. User instruction: "ensure all of our Prompts in this
application are wrapped in XML Tags like our Programming Desk"
(`~/src/repos/programming-desk`: `prompts/*.xml` seat prompts +
`prompts/_shared/core-directives.xml`, assembled by
`scripts/assemble-prompts.sh`, UPLIFTED_PROMPT/BUILD_PROMPT dispatch XML).
Execution: autonomous GSD phase loop under `/gsd-autonomous`.

## Standard

- [x] **PROM-01**: An XML prompt standard is defined and enforced: every
  first-party prompt the application authors is wrapped in XML tags, checked
  by `python -m omega_prime.tooling.prompt_format --check` in CI and by
  `omega_prime/tests/test_prompt_format.py`.
- [x] **PROM-02**: The Grok Bot system prompt stays the assembled
  `prompts-assembled/OMEGA_PRIME.xml` (seat XML + shared core directives,
  PD shape) and names the prompt-format convention in a `<prompt_format>`
  section; doctor/readiness stay green on the new hash.

## Runtime-built prompts

- [x] **PROM-03**: The in-process system prompt prefix is wrapped in
  `<omega_prime_system>`; mid-turn steer rows in `<steer>`; user/tool text
  cannot break out of its wrapper with a typed closing tag.
- [x] **PROM-04**: Compaction output is wrapped in `<context_summary>` and
  elided useless tool results read `<elided reason="useless-tool-result"/>`;
  the system prompt's bytes stay unchanged and transcript invariants hold.
- [x] **PROM-05**: Memory blocks render as `<memory_notes>` / `<user_profile>`;
  goal and autonomous continuations render as `<goal_continuation>` /
  `<autonomous_continuation>`. The pre-v10 parity fixture records this one
  deliberate text change in its provenance; all other recorded bytes are
  untouched.

## Static prompt documents

- [x] **PROM-06**: All 27 checked-in `skills/*/SKILL.md` keep their YAML
  frontmatter and wrap the body in `<skill name="…" version="1">`; skills
  learned at runtime (curator) are written in the same envelope.
- [x] **PROM-07**: All 7 `routines/*.md` wrap in `<routine name="…" version="1">`
  with a `<summary>`; generated command names/summaries are unchanged.
- [x] **PROM-08**: All 58 generated `grokbot/plugin/commands/*.md` carry a
  `<command>`/`<instruction>` body; the operator paste prompt in
  `omega_prime/grokbot/SETUP.md` §1 (and its quote in `docs/marketing.md`)
  is an XML `<task>`, with the declared stdio server command verbatim.

## Out of scope

- `omega_prime/redteam/pack/` — vendored byte-verbatim from upstream
  (provenance `pack-diff` gate); wrapping it is an upstream change.
- `grokbot/templates/OMEGA_PRIME.md` — external Grok Bot import format;
  carries no prompt body by gate.
- Tool description strings (function-calling metadata, PD has the same),
  MCP prompts/resources primitives (SEED-010 — separate v17 candidate,
  not this milestone), eval case data, and one-line user smoke prompts
  (magic-keyword prose matching must see them unwrapped).
- Phase 77.1 live Grok Bot import UAT — user-owned, stays `human_needed`.
