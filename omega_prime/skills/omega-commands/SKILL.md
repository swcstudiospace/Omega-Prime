---
name: omega-commands
description: When to call omega_command. A user message that is only /omega-... runs that slash command instead of the underlying tools.
bots: [bot-00-omega-prime]
---
<skill name="omega-commands" version="1">
# Omega commands

## L1 — Summary

Grok Bot reaches Omega Prime's sequences through one tool, `omega_command`.
Pass the slash text. Do not reimplement the sequence by calling the
underlying tools yourself.

Besides the curated commands, every checked-in skill and routine has a
generated command. `/omega-<skill>` loads that skill's instructions.
`/omega-<routine>` runs that routine's workflow where one exists and
otherwise loads its instructions. When a generated name matches a curated
command or another generated name, it takes a `-skill` or `-routine`
suffix. `/omega-help` lists every name.

**Decision tree:**

```
User message is only /omega-... (curated, /omega-<skill> or /omega-<routine>)?
├─▶ Call omega_command with that exact text, arguments included. Stop.
│   /omega-code-review     → code-review skill instructions
│   /omega-python-skill    → python skill instructions (suffixed)
│   /omega-python          → curated check: ruff and compileall
│   /omega-onboard-routine → onboard routine through its workflow
│
User asks to onboard, set up, or take a first run?
├─▶ /omega-onboard
│
User asks which connectors are missing?
├─▶ /omega-connectors. Never ask them to paste a token.
│
User asks whether the Python package is clean?
├─▶ /omega-python (ruff and compileall). Full suites are /omega-gates.
│
User asks for desk status?
├─▶ /omega-desk. Doctor alone is /omega-doctor.
│
User asks what the bot can run?
└─▶ /omega-help
```

## L2 — Do not

- Do not paste or request an API token, key, or private key in chat.
- Do not call `lead_doctor`, `lead_roster_status`, `memory`, or
  `run_terminal` yourself to imitate `/omega-onboard` or `/omega-python`.
- Do not treat a sentence that merely mentions `/omega-help` as the command.
  Prose goes to you; the bare slash message goes to `omega_command`.
- Do not treat native `/python` as `/omega-python`. A host may surface a
  skill as `/<skill-name>`; native `/python` shows the python skill's
  instructions and runs nothing. `/omega-python` runs ruff and compileall
  through `omega_command`, and `/omega-python-skill` loads the same
  instructions through `omega_command`.
- Do not guess a near name. `/omega-desk-lead` is the desk-lead routine,
  not `/omega-desk`. An unknown name returns `unknown_command`; offer
  `/omega-help`.
</skill>
