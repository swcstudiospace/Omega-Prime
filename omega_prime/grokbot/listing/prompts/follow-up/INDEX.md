# Follow-up prompts

Five prompts to run after step 4 of the install prompt works. Each file sends one `/omega-*` command as the whole message, as the [prompting contract](../../../SETUP.md#prompting-contract) states.

| Order | Prompt | Sends | Use it when |
| --- | --- | --- | --- |
| 1 | [Verify the install](01-verify-install.md) | `/omega-roster` | the install has finished and you want the roster the host serves |
| 2 | [Tour the commands](02-help-tour.md) | `/omega-help` | you want the list of `/omega-*` names |
| 3 | [Health check](03-roster-health-check.md) | `/omega-doctor` | you want the host's own health checks |
| 4 | [First real task](04-first-real-task-code-review.md) | `/omega-code-review` | you have a file, diff or branch to review |
| 5 | [Troubleshoot an earlier step](05-troubleshooting.md) | `/omega-doctor` | an earlier step failed, and you want the first red check |

Live Grok Bot import is UNPROVEN. See the [user UAT](../../../../../docs/grok-bot-native.md#user-uat).

A new prompt file must be added to this table, and a removed one must be removed from it. The listing test fails on either mismatch.
