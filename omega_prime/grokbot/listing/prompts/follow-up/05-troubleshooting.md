# Troubleshoot an earlier step

Use this prompt when an earlier `/omega-*` step failed or looked wrong. It runs the host's health check and triages its first red check. `/omega-doctor` is the closest existing command, because it reports a green flag for each check. No single command diagnoses a failure end to end, so this prompt stops at the first red check.

Command: [`/omega-doctor`](../../../plugin/commands/omega-doctor.md)

Evidence level: local proof for the command's own tool result. A live Bot run is UNPROVEN until the [user UAT](../../../../../docs/grok-bot-native.md#user-uat) rows are recorded.

Send this as the whole message:

```text
/omega-doctor

The command loads and returns the doctor checks. Read the checks in order. Find the first whose green is false (skip prompt and skills when no doctor_context was supplied by the caller). Report that check name and detail and stop.
```

The Bot calls the `omega_command` tool of the `omega-prime` server with that text, as the [prompting contract](../../../SETUP.md#prompting-contract) states.

The copied message supplies no `doctor_context`, so the `prompt` and `skills` flags stay false until the caller supplies the observed `prompt_sha256` and `installed_skills` values.

## What this does not prove

- The red check names where to look. It does not name the root cause.
- A green result does not prove that the earlier step now passes. Run that step again after the fix.
- The check does not install, register or repair anything, and it grants no approval if the host asks for one. It writes its outcome to the roster's doctor record.

Live Grok Bot import is UNPROVEN until the user UAT rows are recorded.
