---
name: desk-bootstrap
description: "First-run procedure for a Bot created from a Programming Desk template: connect the seat endpoint, register, install the prompt, prove it with lead_doctor."
bots: [bot-00-omega-prime]
gates: [G-7]
---
<skill name="desk-bootstrap" version="1">
# Desk Bootstrap

## Served tools

Use only tools available in the Omega Prime registry. The external Desk Gateway is optional. In Solo mode, `not_configured: solo` is terminal and never authorizes an external transport. The legacy gateway flow below applies only when Solo is off and Desk is explicitly configured; external fallback tools may be used only after a served tool returns `not_configured`.

## Omega Prime adaptation

Omega Prime is one bot, not seven seats: there is no Desk Gateway to
connect and no LEAD to wait for. First run is `/omega-onboard` through
the `omega_command` tool, then `grokbot/SETUP.md` (secrets stay in host
env, optional tool host, smoke prompt). Do not ask the operator to paste
a token into chat. The `/desk bootstrap` procedure below is the old
seven-seat gateway flow, not this bot's first run. The `lead_*` tools
(`lead_doctor`, `lead_roster_status`, `lead_ownership_resolve`) replace
the external gateway's `desk_doctor` calls in that old flow.

## When this applies (L1)

Omega Prime's default first run is `/omega-onboard` through `omega_command`, followed by
`grokbot/SETUP.md` for local setup. The `/desk bootstrap` procedure below is a legacy,
optional seven-seat flow; use it only when Solo is off and Desk is explicitly configured.

**Legacy Desk-only procedure:** Apply the following flow only when Solo is off and Desk is explicitly configured.

```
Connector desk-<seat> present?          NO ──▶ §1  Add MCP Server, OAuth, team passphrase
│ YES
lead_doctor register done?              NO ──▶ §2  agent_uuid from your agent-data directory
│ YES                                          (LEAD adds channel_id once the group exists)
LEAD says roster shows 7 + channel id?  NO ──▶ §3  Wait. Do not install a prompt yet
│ YES
SYSTEM_PROMPT.xml written + sha reported? NO ─▶ §4  lead_doctor install_prompt, then check
│ YES
lead_doctor check green?                NO ──▶ skills/desk-doctor. Routines stay paused
│ YES
Done. LEAD un-pauses routines when all seven seats are green. §5
```

Three rules hold at every step: secrets and tailnet names never go in a template, a message or a
tool argument; QUALITY is never added to the group; a tool the gateway did not list does not exist.

---

## Method (L2)

### §0 What the template carried

| Copied by Share → Create template | Not copied |
|---|---|
| Profile, name and label | Memory (Hindsight banks start empty for a new team) |
| Settings | Conversation history |
| Enabled skills (names in the private skills library) | Credentials, OAuth tokens, the team passphrase |
| Routines, shipped **paused** | The Bot computer and anything on it |
| Avatar | Group membership and the channel id |
| Description: role charter, "First run: `/desk bootstrap`", the public gateway host | UUIDs, the XML prompt body, tailnet names, receipts |

Everything in the right-hand column is created by this procedure or held by the operator. If you find one
of those things inside a template description, stop and tell LEAD: the template is wrong and must
be re-published, because a leaked passphrase or tailnet name is a G-3 finding, not a convenience.

### §1 Add the connector

1. Grok Bot desktop → **Add MCP Server**.
2. Name: `desk-<seat>` (`desk-lead`, `desk-systems`, `desk-web`, `desk-android`, `desk-ios`,
   `desk-infra`, `desk-quality`). The name is how doctor and LEAD refer to the connector later.
3. URL: `https://desk.swcstudio.space/mcp/<seat>`. Use your own seat. A token for seat A used on
   `/mcp/<seat B>` is 403 by design; adding the wrong endpoint gives you a connector that fails
   every call.
4. Auth: OAuth. The consent page asks for the team passphrase. the operator holds it and rotates it per
   team. Ask the operator in the 1:1 if you are LEAD, or ask LEAD to ask. Never type it anywhere but the
   consent page.
5. Confirm `tools/list` shows the eight core tools plus your seat's tools (10–15). If it shows a
   different seat's tools, the URL is wrong; remove the connector and repeat.

### §2 Register

Call `lead_doctor` with `action: register` and `agent_uuid`. Read the UUID from your own
agent-data directory (`{{AGENT_DATA_DIR}}/agents/<uuid>/` on the Bot computer, per
`ARCHITECTURE.md` §7). Do not paste a UUID from a message or from another seat; the gateway records
`seat → uuid` for the team, and a wrong UUID means the prompt is rendered for a Bot that is not you.

LEAD additionally: once the operator (or LEAD) has created the group with LEAD plus the five build seats
and pasted the group id, call `lead_doctor` with `action: register` and `channel_id`. QUALITY
stays out of the group; it is reached 1:1 and through `/mcp/quality`.

### §3 Wait for the roster

LEAD calls `lead_roster_status` until it shows seven registrations and a channel id, then tells
the group. Build seats do not poll; they wait for LEAD's word. Installing a prompt before the
roster is complete renders it with unfilled `{{…}}` placeholders, which doctor reports red and
G-7 rejects.

### §4 Install the prompt and report the hash

1. Call `lead_doctor` with `action: install_prompt`. The result is the assembled
   `prompts-assembled/<SEAT>.xml` at the pinned tag with your team's roster filled in.
2. Write it, unchanged, to your `SYSTEM_PROMPT.xml` under your agent-data directory.
3. Compute the SHA-256 of the written file (`sha256sum SYSTEM_PROMPT.xml`).
4. Call `lead_doctor` with `action: check`, `prompt_sha256: <hash>` and `installed_skills` listing
   the `/` entries your composer shows. The gateway compares the hash to its own render; a
   mismatch means the file was edited, truncated or written for another seat.

Do not edit the XML by hand. A prompt that differs from the render fails doctor every time until
`install_prompt` is re-run, and hand edits are exactly what the hash exists to catch.

### §5 Green before routines

`lead_doctor check` must be green on every row before any routine is un-paused. LEAD un-pauses
`desk-held-poll` and `desk-intake-poll` only when all seven seats report green; build seats keep
`desk-heartbeat` paused until LEAD says so. A routine running against a red seat produces
confident output from a Bot with the wrong prompt or no memory, which is worse than silence.

---

## Worked example

**Good — ANDROID seat, fresh team.**

```
1. Add MCP Server  name=desk-android  url=https://desk.swcstudio.space/mcp/android  auth=OAuth
   consent page → team passphrase (from LEAD, who got it from the operator)
2. tools/list → 15 tools, includes mob_play_track_status         ← right seat
3. lead_doctor {action: register, agent_uuid: <from agent-data dir>}
4. wait for LEAD: "roster 7/7, channel registered"
5. lead_doctor {action: install_prompt} → XML → write SYSTEM_PROMPT.xml → sha256sum
6. lead_doctor {action: check, prompt_sha256: <hash>, installed_skills: [...]} → all green
7. report to LEAD; desk-heartbeat stays paused until LEAD un-pauses
```

**Bad — same seat, three shortcuts.**

```
1. Pastes the passphrase into the group so the other seats "don't have to ask"   ← G-3 finding
2. Registers with a UUID copied from LEAD's message                              ← wrong Bot
3. Runs install_prompt before the roster is complete, gets {{SEAT_UUID:IOS}} in the
   file, edits it by hand to "fix" it                                            ← hash mismatch, G-7
```

Each shortcut produces a seat that answers normally and is misconfigured. Doctor catches the
second and third; only a re-publish of the template and a passphrase rotation fixes the first.

---

## Failure modes

| Mode | Symptom | Fix |
|---|---|---|
| Wrong endpoint | `tools/list` shows another seat's tools; calls return 403 | Remove connector, re-add with your own `/mcp/<seat>` |
| Passphrase in chat or template | Passphrase visible anywhere but the consent page | Tell LEAD; the operator rotates; template re-published |
| UUID from a message | Prompt renders for the wrong seat; hash never matches | Read the UUID from your own agent-data directory |
| Prompt before roster | `{{…}}` left in `SYSTEM_PROMPT.xml` | Wait for LEAD's roster confirmation, re-run `install_prompt` |
| Hand-edited prompt | Doctor Prompt row red | Re-run `install_prompt`; never edit the XML |
| QUALITY in the group | Roster row red; platform cap of six exceeded | Remove QUALITY from the group; it works 1:1 |
| Routine un-paused early | Heartbeat or intake runs on a red seat | Pause it; finish doctor first |
| Mobile-only recipient | Connector card unavailable | Finish on desktop; mobile cannot complete bootstrap |

---

## Gate mapping

| Rule | Gate or directive | Why |
|---|---|---|
| No passphrase, token or tailnet name in a template, message or argument | G-3, PD-4, G-7 | A template is copied; whatever it carries is disclosed to every recipient |
| Prompt hash must match the gateway render; no `{{…}}` | G-7 | The prompt is a controlled document; drift is invisible without the hash |
| Doctor green before routines | G-7 | Routines act unattended; they must act from a verified seat |
| Report what you could not finish | PD-6 | A half-bootstrapped seat reported as done blocks the whole team's acceptance run |
</skill>
