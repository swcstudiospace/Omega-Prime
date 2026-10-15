# Verify the install

This prompt confirms that the omega-prime server answers and lists the roster it serves, after the install prompt has finished.

Command: [`/omega-roster`](../../../plugin/commands/omega-roster.md)

Evidence level: local proof for the command's own tool result. A live Bot run is UNPROVEN until the [user UAT](../../../../../docs/grok-bot-native.md#user-uat) rows are recorded.

Send this as the whole message:

```text
/omega-roster
```

The Bot calls the `omega_command` tool of the `omega-prime` server with that text, as the [prompting contract](../../../SETUP.md#prompting-contract) states.

## What this does not prove

- It does not prove that the Bot attached the omega-prime server. That is user UAT row 1.
- The roster is what the host serves now. It is not a live model answer, and a listed tool is not proof that the tool ran.
- It does not prove that each tool works. The per-tool proof is in the readiness report described in [local validation](../../../../../docs/local-validation.md).

Live Grok Bot import is UNPROVEN until the user UAT rows are recorded.
