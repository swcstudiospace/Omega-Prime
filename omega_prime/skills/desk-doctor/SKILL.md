---
name: desk-doctor
description: "Run and read the desk integrity report (lead_doctor check): prompt SHA, skills, memory bank, tool roster, connector, roster registration."
bots: [bot-00-omega-prime]
gates: [G-7]
---
<skill name="desk-doctor" version="1">
# Desk Doctor

## Served tools

Use only tools available in the Omega Prime registry. The external Desk Gateway is optional. In Solo mode, `not_configured: solo` is terminal and never authorizes an external transport. Use an external Desk only when Solo is off and Desk is explicitly configured, and only after a served tool returns `not_configured`.

## Omega Prime adaptation

Omega Prime runs `lead_doctor` through its local tool registry. It checks the local bot configuration available to that tool; it is not a probe of external network services. Desk-specific checks below apply only to an explicitly configured Desk.

## When this applies (L1)

`/desk doctor` is `lead_doctor` with `action: check`. It reports the configured integrity checks for the current bot. Run it after local setup or a relevant prompt/roster change. Do not infer network health or external service availability from a local integrity report.
Detailed Desk rows later in this skill apply only when Desk is explicitly configured; they are not
required for Solo integrity.

**Doctor reports; it does not fix a red integrity row.** Use only actions exposed by the local
`lead_doctor` tool. Do not edit a receipt or change a gate result to make it green.

```
Running `lead_doctor`?
│
├─ Result green ──▶ Record the result tail in the current receipt if one is open.
├─ Result red ──▶ Report the row verbatim; follow only a repair action the tool exposes.
└─ A gate row red ──▶ Blocker for LEAD, not a repair. Fix the cause under the owning skill.
```

A red row is information. Two red rows are two pieces of information. The only wrong response is
to make them green by editing what they measure.

---

## Method (L2)

### §1 Running it

Call `lead_doctor` with:

| Input | Value | Why it matters |
|---|---|---|
| `action` | `check` | Requests the integrity report |
| `prompt_sha256` | SHA-256 of the installed prompt, when required by the local tool | Lets the configured check compare the supplied prompt hash |
| `installed_skills` | Names shown by the local skill composer, when required | Lets the configured check compare installed skills; for legacy Desk bootstrap see [desk-bootstrap](../desk-bootstrap/SKILL.md) |

Compute the hash on the Bot computer (`sha256sum SYSTEM_PROMPT.xml`). Do not type a hash from
memory or from a previous run; the point is to detect a file that changed.

### §2 Reading the report

| Result | What to do |
|---|---|
| A named local check is green or red | Report the result as returned; do not infer checks the tool did not perform |
| A service-dependent check is unavailable or skipped | Record it as unverified; it is not evidence of health or absence |
| A Desk connector or registration check appears | Treat it as Desk-specific; it is not a default Solo requirement |

The row text names the check that failed. Report it verbatim. A paraphrase ("memory is broken")
loses the detail the owning seat needs and costs a round trip.

### §3 Repair

If `lead_doctor` exposes a repair action for a failed local check, use only that documented action
and then re-run the check. Do not assume it can repair a service, connector, or external system.
If no repair action is available, report the failed row and blocker to LEAD.

### §4 Where the result goes

When a receipt is open, include the doctor result tail in its evidence. A doctor run is evidence
about the checks it performed; it never substitutes for the task's own verification commands (G-2).

---

## Worked example

Report the exact `lead_doctor` result in the receipt. Do not infer network health from a local
integrity check or treat an unavailable check as green.

No invented example output: record only the checks and result actually returned by the tool.

---

## Failure modes

| Mode | Symptom | Fix |
|---|---|---|
| Incorrect hash or omitted local input | Prompt or skills check cannot be interpreted | Use the current local value required by the tool |
| Hand-editing evidence | Report becomes unreliable | Do not edit the prompt, receipt, or gate to change the result |
| A check is unavailable | Result is unknown, not green | Record it as unverified and report the blocker |
| Desk connector, cross-seat, or pack row | Desk-specific check appears | Apply only to an explicitly configured Desk |
| Doctor as task evidence | Receipt cites a doctor run for "tests pass" | Doctor proves only the checks it performed, not the work |

---

## Gate mapping

| Rule | Gate or directive | Why |
|---|---|---|
| Integrity report records a result, not a task verification | G-2 | The task's own verification evidence remains required |
| Doctor never edits a receipt or gate | G-2 | A report must not alter the evidence it describes |
| Doctor output tail goes in the receipt when one is open | G-2 | The check result is part of what the reviewer needs |
</skill>
