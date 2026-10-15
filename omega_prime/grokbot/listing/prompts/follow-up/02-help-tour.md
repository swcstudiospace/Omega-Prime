# Tour the commands

This prompt lists the `/omega-*` names the host serves, so the operator can see what to send next.

Command: [`/omega-help`](../../../plugin/commands/omega-help.md)

Evidence level: local proof for the command's own tool result. A live Bot run is UNPROVEN until the [user UAT](../../../../../docs/grok-bot-native.md#user-uat) rows are recorded.

Send this as the whole message:

```text
/omega-help
```

The Bot calls the `omega_command` tool of the `omega-prime` server with that text, as the [prompting contract](../../../SETUP.md#prompting-contract) states.

## What this does not prove

- The names come from the checked-in command set. This result does not prove that each listed command runs.
- It does not prove that the Bot's own `/` menu shows these names natively. That is user UAT row 4.
- The list is what this host serves. A name missing from it is not proof that the command was removed from the repository.

Live Grok Bot import is UNPROVEN until the user UAT rows are recorded.
