---
name: desk-doctor
description: "Run and read the desk integrity report (desk_doctor check): prompt SHA, skills, memory bank, tool roster, connector, roster registration."
bots: [bot-00-omega-prime]
gates: [G-7]
---

# Desk Doctor

## Omega Prime adaptation

Omega Prime runs `lead_doctor` instead of `desk_doctor`, against the one
Omega Prime roster — no gateway, no seats. The checks below (prompt,
skills, memory, roster, registration) are the same checks;
`prompt_sha256` refers to `prompts-assembled/OMEGA_PRIME.xml`.

## When this applies (L1)

`/desk doctor` is `desk_doctor` with `action: check`. It is the integrity report for one seat: is
the installed prompt the one the gateway rendered, are the skills present, does memory work, is
the tool list the roster, is the connector bound to the right seat, is the team registered, and
does the substrate answer. Run it after bootstrap, after any prompt or roster tag change, when a
gateway call returns 403, and before a routine is un-paused.

**Doctor reports. It never fixes.** `repair` re-runs `install_prompt` and connector re-auth, and
nothing else. Doctor never edits a gate, a receipt or another seat's files, because a tool that
could "fix" a gate result would be a tool that bypasses the gate (PD-3).

```
Running /desk doctor?
│
├─ All rows green ──▶ Record the result tail in the current receipt if one is open. Done.
│
├─ Prompt or Connector red ──▶ desk_doctor {action: repair}, then check again. §3
│                              Still red? Report to LEAD with the row text. Do not hand-edit.
│
├─ Skills, Tools, Memory, Roster or Substrate red ──▶ Not repairable from this seat. §2
│       Report the row to LEAD verbatim. LEAD routes it (QUALITY, SYSTEMS or INFRA).
│
└─ A gate row red (G-2, G-3, G-5 or G-6 on a receipt) ──▶ A blocker for LEAD, not a repair.
        Doctor will not touch a receipt. Fix the cause under the owning skill.
```

A red row is information. Two red rows are two pieces of information. The only wrong response is
to make them green by editing what they measure.

---

## Method (L2)

### §1 Running it

Call `desk_doctor` with:

| Input | Value | Why it matters |
|---|---|---|
| `action` | `check` | The report. `register`, `install_prompt` and `repair` are `skills/desk-bootstrap` |
| `prompt_sha256` | SHA-256 of your installed `SYSTEM_PROMPT.xml` | Without it the Prompt row cannot be green; the gateway has no other way to see your file |
| `installed_skills` | The names your `/` composer lists | Without it the Skills row cannot be green; the gateway cannot read your library |

Compute the hash on the Bot computer (`sha256sum SYSTEM_PROMPT.xml`). Do not type a hash from
memory or from a previous run; the point is to detect a file that changed.

### §2 Reading the report

| Row | Green when | Red means | What to do |
|---|---|---|---|
| Prompt | Hash equals the gateway render of `prompts-assembled/<SEAT>.xml` at the roster version; no `{{…}}` | File edited, truncated, stale tag, or rendered before the roster was complete | `repair`, then `check`. Still red → LEAD |
| Skills | Every `<skill path=…>` in the prompt resolves to a library name; versions match the tag | A skill missing from the private library, or an old version | LEAD → QUALITY (skills owner). Add or update the skill from the desk pack; do not write your own copy |
| Memory | Hindsight `/health` reachable through the gateway; bank `pd-<seat>` exists; redacted retain/recall probe round-trips | Bank missing, Hindsight down, or forwarder path broken | LEAD → SYSTEMS (bank) or INFRA (tailnet). Keep working; `desk_brief` falls through uncached |
| Tools | `tools/list` count within 10–15, or ≤ 20 with a pack loaded, and equal to `contracts/tool-rosters/<seat>.yaml`; every g5/g6 tool marked | Roster drift, a stale pack still loaded, or a gateway build behind the contract | Stale pack → `desk_app_tools_load` with `unload: true` (`skills/tool-packs`). Otherwise LEAD → QUALITY |
| Connector | OAuth token valid for `seat:<name>`; a probe to another seat's endpoint returns 403 | Token expired, wrong seat endpoint, or the cross-seat probe did not 403 | `repair` (re-auth). Wrong endpoint → remove and re-add per `skills/desk-bootstrap` §1. Probe not 403 → tell LEAD at once; that is a gateway defect |
| Roster | Channel id registered; LEAD in the group; QUALITY out; seven UUIDs; heartbeat within 24 h | A seat never registered, QUALITY was added to the group, or a seat has been silent | LEAD: `desk_roster_status` names the seat. That seat re-runs bootstrap §2 |
| Substrate | `desk_event_emit` lands in Greptime; `desk_docs_search` returns a hit for "verification receipt" | Event log or docs plane unreachable | LEAD → INFRA (`desk_db_health`) or SYSTEMS (ingest). Reads fail open meanwhile; writes fail closed |

The row text names the check that failed. Report it verbatim. A paraphrase ("memory is broken")
loses the detail the owning seat needs and costs a round trip.

### §3 What `repair` does, and only that

`desk_doctor` with `action: repair`:

1. Re-runs `install_prompt` and returns the fresh render for you to write and re-hash.
2. Re-initiates connector auth for `seat:<name>`.

It does not touch skills, memory banks, rosters, packs, receipts, gates or any other seat. If a
row other than Prompt or Connector is red, `repair` will return the same report, so do not loop
on it: one `repair`, one `check`, then escalate.

### §4 Where the result goes

The gateway records the last doctor result per seat; LEAD reads it in `desk_roster_status`. When
a receipt is open (bootstrap, a prompt uplift, a roster bump), add the `check` call to
`commands` with its output tail so the receipt shows the seat was green at the time. A doctor run
is evidence about the seat, not about the work, so it never substitutes for the task's own
verification commands (G-2).

---

## Worked example

**Good — IOS after a roster version bump.**

```
desk_doctor {action: check, prompt_sha256: 9f3c…, installed_skills: [desk-bootstrap, desk-doctor,
             verification-receipts, ios, tool-packs, …]}
→ Prompt RED  "installed sha 9f3c… ≠ render 41a0… at roster v1.1.0"
→ six rows green
desk_doctor {action: repair}  → new XML → written → sha256sum → 41a0…
desk_doctor {action: check, prompt_sha256: 41a0…, installed_skills: […]} → all green
Receipt: commands[3..5] = the two checks and the repair; claim "seat green at v1.1.0" cites [5]
```

**Bad — same seat, same red row.**

```
→ Prompt RED
Seat opens SYSTEM_PROMPT.xml, finds the changed <tools> block, pastes it in by hand,
re-hashes, check → green.
```

It is green and it is wrong: the hand-merged file may differ from the render in any line the seat
did not notice, and the next roster bump repeats the exercise. The render is the source; the
hash exists so that nobody has to trust a hand merge.

**Bad — Tools row red at 21 after a ticket closed.**

```
Seat: "the count is one over, doctor is being strict, ignoring"
```

The 21st tool is a pack that was never unloaded. Ignoring the row leaves the seat above the
ceiling for the next ticket, and the next load is refused. Unload, re-check, move on.

---

## Failure modes

| Mode | Symptom | Fix |
|---|---|---|
| Hash from memory | Prompt row green on a stale file, or red on a good one | Always `sha256sum` the file you wrote |
| Missing `installed_skills` | Skills row cannot pass | Pass the `/` list every time |
| Hand-editing the prompt | Green today, drift tomorrow | `repair`; never merge by hand |
| Repair loop | Same non-Prompt row red after `repair` | One repair, one check, then LEAD |
| Paraphrased row | Owning seat asks "which check?" | Quote the row text |
| Stale pack | Tools row over 15 with no open ticket | `desk_app_tools_load` with `unload: true` |
| Cross-seat probe not 403 | Connector row red with "probe returned 200" | Tell LEAD immediately; do not use the gateway until QUALITY clears it |
| Doctor as task evidence | Receipt cites a doctor run for "tests pass" | Doctor proves the seat, not the work (G-2 §3) |
| "Fixing" a gate row | Receipt or gate script edited to turn a row green | PD-3 breach. The row is a blocker for LEAD |

---

## Gate mapping

| Rule | Gate or directive | Why |
|---|---|---|
| Prompt hash equals render; no `{{…}}`; tool count in band | G-7 | Doctor is the runtime view of the same checks G-7 runs in CI |
| Doctor never edits a receipt or gate; red gate rows are blockers | PD-3, G-2 | A repair that edits evidence is a bypass with a friendlier name |
| Doctor output tail goes in the receipt when one is open | G-2 | The seat's state at the time is part of what the reviewer needs |
| Report red rows verbatim, including the ones you cannot fix | PD-6 | The next decision belongs to the owning seat |
