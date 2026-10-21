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

Skills are `SKILL.md` prompt files the agent reads when relevant; routines
are flows the agent runs (engagement sweeps, nightly learning, ultrathink
turns). Name one to invoke it, or let the bot pick: it never claims a skill
that is not a file on disk. The template carries pointers, not bodies —
both live in this checkout.

## Tools and approvals

The roster (`omega_prime/contracts/tool-rosters/omega-prime.yaml`) is the
truth: the bot claims only tools on it. The default host serves the roster
intersection `setup_check` reports; the Prime families and the Red Team
family are config-gated and default off. `delegate_task` is served; without
a provider env it returns `not_configured: provider`. Approval depends on the
tool's actual registration, not whether its name sounds like a read or write.
Tools marked `requires_approval=True` wait for an approved grant and cannot
self-approve. Local `write_file`, `patch_file`, `edit_file` and `run_terminal`
are not gated by that flag; they run with host-user authority. Use an owned,
policy-constrained workspace and sandbox the host. The generated
[catalog](tool-catalog.md) records the actual per-tool approval flag.

How approval works on a running host: a gated tool answers
`{"error": "approval required", "tool": "<name>"}` unless the host was
started with `--approve TOOL:APPROVER` for it (repeatable; a malformed
value, or an approver equal to the bot's own id, exits 2). An approval
covers every later call to that tool until the process exits; restart to
revoke. The approver name is recorded, not authenticated. Alternatively an
admin token approves a gated tool without a restart via
`POST /admin/approvals` (scope `admin`; not mounted when authentication is
off). See `docs/tool-host.md` and `docs/grok-bot-native.md`.

A call that fails validation — `unknown_field`, `bad_type`, `bad_value`,
`unsupported_schema_version` — is rejected before any effect; nothing is
written. A gated tool answers `approval required` before its arguments are
checked. Approved handlers do not turn missing required inputs into
success: the typed error names the gap.

## Config and state roots

Omega Prime runs with zero configuration; every Prime capability family is
off by default. Opt in per family via `omega-prime.json` in the directory
you pass as `--root`, or via `OMEGA_PRIME_PRIME_<FAMILY>_ENABLED=1` in the
host's environment (`true`, `yes`, and `on` also enable; any other value,
including an empty one, turns the family off and overrides the file). The
environment wins over the file. The host reads both at startup, so restart
it after any change.

- `--root DIR` — repository checkout holding `omega_prime/` and
  `omega-prime.json` (default: cwd).
- `--work-root DIR` — directory the desk tools act on (default:
  `OMEGA_PRIME_WORK_ROOT`, else `--root`).
- `--home DIR` — working directory of `execute_code` and `mcp_call`
  (default: `~`).
- `OMEGA_PRIME_STATE_DIR` — the state root. The agent's own data (memory,
  history and the `intake.json` work-order queue) lives in its `agent/`
  subfolder; `OMEGA_PRIME_AGENT_STATE_DIR` names that folder directly. See
  `docs/setup.md#agent-data-folder`.

Secrets are supplied privately in the host environment or a `0600` token
file — never in chat, `argv`, examples, or saved output. See
`docs/setup.md#4-secrets-go-in-the-private-environment-never-in-examples`.

## Receipts

A completion claim cites its evidence: the commands run and their
exit codes. Ask "show the receipt" any time to see what was run,
what passed, and what stayed unverified.

Locally, the receipts are the commands in `docs/setup.md#5-verify-locally-with-no-credentials`:
`setup_check` (roster/template/assembly/registry checks),
`grokbot.readiness --json` (per-tool `exercised` / `feature_disabled` /
`blocked` / `failed` rows with named prerequisites), and
`grokbot.doctor --json` (host/transport readiness). A readiness row of
`blocked` or `feature_disabled` is an honest unexecuted prerequisite, not a
pass; only `exercised` with a verified effect counts as run.

## Claim limits

- Controller and source prompts are not handler execution. A workflow that
  ran under an explicitly labeled `ScriptedModel` double, or a check that
  inspected prepared-seat bytes, proves local composition for that
  disposable seat — not live provider behavior.
- This page claims no live Grok attachment, account, or provider call. The
  smoke prompt in `docs/setup.md` is a seat-side check, not proof of
  attachment.
