# Architecture

How Omega Prime is put together: one process, one loop, one registry, and the
planes around them. Tool membership follows the roster contract
(`omega_prime/contracts/tool-rosters/omega-prime.yaml`) and the generated
[tool catalog](tool-catalog.md); skills are `SKILL.md` files under
`omega_prime/skills/` and routines are prompt flows. For current suite/eval
results, run the commands under Verification and require a clean result.

## The shape

```text
prompt ──▶ loop ──▶ registry ──▶ tools ──▶ receipts
             │          │
          skills     policy + broker + journal
             │          │
        memory ◀── substrate surface ──▶ Hindsight / RAGflow
```

Everything runs in one Python process (`omega_prime/`). There is no sidecar, no
Node agent process, no Rust service beside it.

## The loop

`omega_prime/agent/conversation_loop.py` runs one turn: install the system prompt
once, take the user row, then iterate model call → tool round or text reply
until something finishes the turn. The Omp harness rides the same loop as
options on the `Agent` (pause gates, output budgets, plan mode, extensions).
Magic keywords (`ultrathink`, `orchestrate`, `workflowz`) inject notices for
the turn when they appear as standalone prose.

## The registry

`omega_prime/tools/registry.py` maps 165 tool names to handlers across 29 families
(coding, growth, delegate, omega command, platform, IDE, connectors, seven desk packs, app packs,
ultrathink, substrate, cron, learning, durable, the seven config-gated Prime families: RLM, harness,
goals, heartbeat, autonomous, messaging, kernel, and the default-off Red Team family). Dispatch always
returns JSON; unknown names are
errors, never exceptions. Three gates wrap every call, in order: the seat
policy (allowed tools/hosts/paths), the approval log (mutating tools wait
for a person), and the audit + tracer (one verdict and span per dispatch).

The roster is the contract: `omega_prime/contracts/tool-rosters/omega-prime.yaml` must
list exactly the registered names, and the policy must allow every one.
The suite enforces both, and the [tool catalog](tool-catalog.md) is
generated from the live registry so docs cannot drift.

## Memory and skills

Two file-backed stores (`MEMORY.md`, `USER.md`) plus bank-tagged episodic
recall serve every call shape: Hermes learn/recall, Omp mnemopi, and the
Hindsight `retain`/`recall` pair. Skills are `SKILL.md` files under
`omega_prime/skills/`; routines are prompt flows (`sweep`, `nightly`,
`ultrathink_turn`) plus the desk lead routines. The curator folds nightly
learnings back into memory.

## Destination Transport & Browser Confinement

Egress and browser execution follow strict operational boundaries:

- Destination transport (`omega_prime/providers/destination.py`) manages seat policy
  enforcement, DNS resolution, numeric dialing, and TLS validation.
- Composed preview and review orchestration (`omega_prime/tools/webpack.py`) uses
  registered `RootOperation` scopes to prevent SSRF and unmediated DNS rebinding.
- Browser egress confinement (`omega_prime/tools/browser_egress.py`) manages sandbox
  accounting, socket ledger proofs, and terminal close receipts.
- Vision inspection occurs only after complete session close and verified clean
  drain receipts.

## The Grok Bot shell

The same agent, addressed from Grok: `omega_prime/grokbot/templates/OMEGA_PRIME.md` is
the Add-Bot template (name, description, empty skill/routine sections —
bodies live in the repo), `grokbot/SETUP.md` is the four-step install, and
`omega_prime/scripts/assemble-prompts.sh` builds `prompts-assembled/OMEGA_PRIME.xml`
from the seat XML plus shared directives. `--check` fails CI on drift.

## The substrate surface

On wired installs the turn opens with a shared brief from substrate-mcp
and reports session/prompt/tool/file events with graph provenance; memory
writes go through the shared plane; episodes come from the shared
`ultrathink` Hindsight bank with local fallback; docs answer from RAGflow.
See [Substrate surface](substrate.md). GreptimeDB, TimescaleDB, and
DragonflyDB stay behind the store lock — Omega Prime holds no clients for them,
and a test fails the build if one appears.

## Verification

| Gate | Command | Current |
| --- | --- | --- |
| Suite | `pytest omega_prime/tests -q` | must pass (exit 0) |
| Evals | `omega_prime.evals.runner omega_prime/evals/cases` | must pass (exit 0) |
| Assembly | `assemble-prompts.sh --check` | clean |
| Setup | `omega_prime.setup_check --root .` | clean |
| Docs | catalog `--check` + link tests | clean |

A completion claim without a command and an exit code is not a claim.
See [Verification receipts](user-guide.md) and CONTRIBUTING.md.
