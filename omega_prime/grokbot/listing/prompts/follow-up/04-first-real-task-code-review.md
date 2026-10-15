# First real task

This prompt prepares a first real review. It shows the exact command text to send so the Bot can load the review instructions and checks. Name a file, diff or branch in place of the placeholder before you send it.

The Bot loads the instructions from the skill and returns checks. The actual review against those instructions is performed by the Bot as a follow-on action.
Command: [`/omega-code-review`](../../../plugin/commands/omega-code-review.md)

Evidence level: local proof for the command's own tool result. A live Bot run is UNPROVEN until the [user UAT](../../../../../docs/grok-bot-native.md#user-uat) rows are recorded.

Replace the placeholder, then send exactly this one command as the whole message:

```text
/omega-code-review <file, diff or branch named in the review request>
```

The command loads the review instructions and returns checks. The Bot performs the review as a separate step using the returned data.

The Bot calls the `omega_command` tool of the `omega-prime` server with that text, as the [prompting contract](../../../SETUP.md#prompting-contract) states.

## What this does not prove

- The review is the Bot's judgement against the review skill. It is not a test run.
- The reviewed code is not executed, so this result does not prove that the code is correct.
- It does not prove any Greptile, CI or pull-request check.

Live Grok Bot import is UNPROVEN until the user UAT rows are recorded.
