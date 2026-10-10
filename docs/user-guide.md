# User guide

How to get the best out of Omega Prime: the magic words, ultrathink
turns, skills, tools, and the approval model.

## Magic words

Three standalone lowercase words change how Omega Prime works a turn
(they are ignored inside code and markup):

- `ultrathink` — think hard before answering. Best paired with
  the ask: "`ultrathink` how should we split this migration?"
- `orchestrate` — fan substantial work out to subagents, verify
  between phases, and don't yield until everything is done.
- `workflowz` — run the work as a batched multi-subagent
  workflow: scope the list, dispatch one batch per phase, verify,
  integrate.

## Ultrathink turns

Say `ultrathink` plus a multi-step ask and the turn runs the
ultrathink flow: resolve any written plan, work its waves in
order, track progress, and ship finished work through a reviewed
PR. The plan never overrides your words — it organizes them.

## Skills and routines

Omega Prime carries 27 skills (platform guides, review and debugging
playbooks, security handling, desk flows, slash commands, ultrathink) and 7
routines (lead dispatch, nightly learning, sweep, ultrathink turns,
onboard, python check, connectors). Name one to invoke it, or let the bot pick:
it never claims a skill that isn't a file on disk.

## Tools and approvals

The roster lists 165 tools across coding, growth, platform, IDE,
messaging, seven domain packs (lead, systems, web, mobile,
infra, quality, app packs), substrate plus ultrathink bridge
tools, the seven Prime families (RLM, harness, goals, heartbeat,
autonomous, agent messaging, kernel), and the Red Team family
(tempest_* proxies for the local T3MP3ST loopback gateway) — those are
config-gated and default off, so a default install serves the roster
intersection `setup_check`
reports (117 today). `delegate_task` is served; without a provider env it
returns `not_configured: provider`. Most reads run free; writes,
deploys, publishes, and merges
need your approval first — the bot asks, you approve, then it runs.
Nothing approval-gated ever self-approves.

## Receipts

A completion claim cites its evidence: the commands run and their
exit codes. Ask "show the receipt" any time to see what was run,
what passed, and what stayed unverified.
