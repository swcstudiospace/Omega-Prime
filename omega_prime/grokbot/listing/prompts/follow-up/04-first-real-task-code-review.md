# First real task

This prompt starts a first real review. Name a file, diff or branch in place of the placeholder before you send it.

Command: [`/omega-code-review`](../../../plugin/commands/omega-code-review.md)

Evidence level: local proof for the command's own tool result. A live Bot run is UNPROVEN until the [user UAT](../../../../../docs/grok-bot-native.md#user-uat) rows are recorded.

Replace the placeholder, then send this as the whole message:

```text
/omega-code-review <file, diff or branch named in the review request>
```

The Bot calls the `omega_command` tool of the `omega-prime` server with that text, as the [prompting contract](../../../SETUP.md#prompting-contract) states.

The command loads the review instructions and returns checks for the file, diff or branch you named. It does not review the code. The review is a separate step the Bot performs after the command returns.

## What this does not prove

- The review is the Bot's judgement against the review skill. It is not a test run.
- The reviewed code is not executed, so this result does not prove that the code is correct.
- It does not prove any Greptile, CI or pull-request check.

Live Grok Bot import is UNPROVEN until the user UAT rows are recorded.
