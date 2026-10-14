# Architecture

One Python process. One agent. Hermes is moved and adapted into `agent/`. Omp's harness, edit pipeline, language servers, modes, and memory clients are ported into that same agent. The TypeScript tests in the oh-my-pi checkout are the spec for that behavior. Neither checkout is imported at runtime.

The Grok shell stays beside the agent:

| Path | Role |
| --- | --- |
| `prompts/_shared/core-directives.xml` | Receipts, secrets, destructive-op approval. Prepended to the seat. |
| `prompts/bot-00-omega-prime.xml` | The seat. Points at skills, the tool roster, and the prompt folder. |
| `prompts-assembled/OMEGA_PRIME.xml` | What a host loads. Produced by `scripts/assemble-prompts.sh`. |
| `contracts/tool-rosters/omega-prime.yaml` | Tools the model may be offered. Matches the registry, not a wish list. |
| `grokbot/templates/OMEGA_PRIME.md` | Name, description, enabled skills, routines. No prompt body, no tokens. |
| `grokbot/rosters/default.json` | Values substituted into `{{DEFAULT_BRANCH}}`, `{{BOT_ID}}`, `{{ROSTER_VERSION}}`. |
| `skills/<name>/SKILL.md` | A skill the template is allowed to name. Frontmatter, then the body inside `<skill name="…" version="1">`. |
| `routines/<name>.md` | A routine the template is allowed to name. `<routine name="…" version="1">` with a `<summary>`. |
| `tooling/prompt_format.py` | The XML prompt standard gate: every authored prompt wrapped, checked in CI. |
| `ownership.yaml` | One owner per path. Last match wins. One bot today. |

Temporal is not how the two agents are merged. It can sit under cron and delegation later, after those exist.

## Domain packs (v5)

The seven Programming Desk seats were absorbed as registry families in
`tools/`: `lead.py`, `systems.py`, `webpack.py`, `mobile.py`,
`infra.py`, `quality.py`, `packs.py`. Each family ports its desk
module behind injected seams (clients, runners, stores); tests use
fakes only. Write-kind tools need approval; the roster, policy, and
composition tests gain names only.

| Path | Role |
| --- | --- |
| `contracts/tool-packs/*.yaml` | App pack surfaces (clippyos, desklanes, kanbanos). |
| `evals/cases/golden.json` | Approved-behaviour cases per pack, plus loop cases. |
| `evals/cases/redteam.json` | Refusal cases per pack (approval, policy). |
| `tests/test_receipts_e2e.py` | Receipts loop over real hermetic commands. |
| `receipts.py` | Claim/command/exit-code checks; no self-approval. |
