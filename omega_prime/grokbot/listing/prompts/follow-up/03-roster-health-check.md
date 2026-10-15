# Health check

This prompt runs the host's own health check. It reports one green flag for each of the prompt, skills, memory, tools, roster and substrate checks. It does not install, register or repair anything, but it writes its outcome to the roster's doctor record.

Command: [`/omega-doctor`](../../../plugin/commands/omega-doctor.md)

Evidence level: local proof for the command's own tool result. A live Bot run is UNPROVEN until the [user UAT](../../../../../docs/grok-bot-native.md#user-uat) rows are recorded.

Send this as the whole message:

```text
/omega-doctor
```

The Bot calls the `omega_command` tool of the `omega-prime` server with that text, as the [prompting contract](../../../SETUP.md#prompting-contract) states.

## What this does not prove

- A green check describes the host's own state. It does not prove that a live Bot is attached. That is user UAT row 1.
- It does not prove that provider keys, connectors or Prime capability families are configured. Prime families stay off until they are enabled.
- A green check is not proof that a tool ran. The per-tool proof is in the readiness report described in [local validation](../../../../../docs/local-validation.md).

Live Grok Bot import is UNPROVEN until the user UAT rows are recorded.
