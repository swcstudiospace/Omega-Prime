---
name: tool-packs
description: Load and unload application tool packs for one ticket; the 20-tool ceiling; pack authoring and consumer acknowledgement.
bots: [bot-00-omega-prime]
gates: [G-4, G-7]
---
<skill name="tool-packs" version="1">
# Tool Packs

## Served tools

Use only tools available in the Omega Prime registry. The external Desk Gateway is optional. In Solo mode, `not_configured: solo` is a terminal local outcome and does not activate `agent_bus_*` or another external transport. Use an external Desk tool only when Solo is off and Desk is explicitly configured, and only when the served tool returns `not_configured`.

## Omega Prime adaptation

Omega Prime loads packs with `app_tools_load {app, task_id}` (approval
required) and calls the pack through the `app_*` backends
(`app_api_smoke`, `app_push_test`, …) — no connector refresh, no
per-seat tool lists. The 20-live-tools ceiling still holds and
unloading still closes the ticket's pack. Read `app_tools_load`
below as `app_tools_load`.

## When this applies (L1)

A tool pack is a contract-defined bundle of at most five application-specific tools
(`contracts/tool-packs/<app>.yaml`) that a seat loads for the life of one ticket and unloads when
the ticket closes. `app_tools_load {app, task_id}` turns a pack on;
`app_tools_load {app, task_id, unload: true}` turns it off. **Live tools per seat never
exceed 20** (15 roster + 5 pack), and the gateway refuses the call that would cross the line.

Packs today: `kanbanos`, `desklanes`, `clippyos`, each for seats `web`, `android`, `ios`. The
load tool sits on the ANDROID and IOS rosters (`packs_allowed: true`); WEB is a declared consumer
but its roster does not carry `app_tools_load`, so WEB reaches a pack only through the pack
connector fallback in §3.

```
Ticket needs an app's tools (smoke, flags, push test, crash reports, product data)?
│
├─ Pack loaded for another ticket?      YES ──▶ Unload it first; the ceiling is per seat. §2
├─ app_tools_load {app, task_id}   ──▶ Tools appear on the next tools/list. §3
│     Not visible? Refresh the connector. Still not? Enable /mcp/<seat>/packs/<app>. §3
├─ Use the tools under skills/desk-gateway rules (kinds, reasons, deadline). §4
└─ Ticket closes ──▶ app_tools_load {app, task_id, unload: true}. §5

Need a tool no pack has? ──▶ Contract change, QUALITY, consumers ack. Never a browser workaround. §6
```

A pack is scoped to a `task_id` so the audit log can say which ticket used which product surface.
A pack loaded "for later" is a pack with no ticket, and doctor reports it.

---

## Method (L2)

### §1 What is in a pack

| Pack | Tools | Seats |
|---|---|---|
| `kanbanos` | `kanbanos_api_smoke`, `kanbanos_supabase_query`, `kanbanos_push_test`, `kanbanos_feature_flags`, `kanbanos_crash_reports` | web, android, ios |
| `desklanes` | `desklanes_api_smoke`, `desklanes_scoreboard_get`, `desklanes_push_test`, `desklanes_store_listing_get`, `desklanes_crash_reports` | web, android, ios |
| `clippyos` | `clippyos_api_smoke`, `clippyos_render_job_status`, `clippyos_push_test`, `clippyos_crash_reports` | web, android, ios |

Every pack tool has the same shape as a roster tool: `kind`, `gates`, a JSON schema with
`additionalProperties: false`, and a `packs.*` backend in the gateway. `*_supabase_query` is
SELECT-only with a row cap; `*_push_test` reaches registered test devices only. The contract file
is authoritative when this table drifts.

### §2 Loading

1. Confirm no other pack is loaded: your `tools/list` count should be 10–15. If it is above 15,
   another pack is live; unload it (§5) or the load is refused for crossing 20.
2. `app_tools_load {app: "kanbanos", task_id: "<the ticket id>"}`. Use the real ticket id
   (Linear id or graph slug); the gateway writes it to `tool_pack_state` and the audit event.
3. The result names the tools added. Only one pack at a time: 15 + 5 = 20, so a second pack has
   nowhere to go.

`app_tools_load` is a `write` tool and fails closed: an error means nothing was loaded.
The `reason` will say either that the pack's `seats:` list does not include you, or that the
load would take you past 20 live tools. Neither is a retry.

### §3 Seeing the tools

After a load the gateway emits MCP `notifications/tools/list_changed`. Whether Grok Bot's client
acts on it is unverified (plan §13), so:

1. Call any tool; the next `tools/list` should include the pack.
2. If not, refresh the connector in Grok Bot (disable and re-enable `desk-<seat>`, or re-open the
   Bot).
3. If the tools still do not appear, use the fallback that needs no client support: every pack
   is also served at `https://desk.swcstudio.space/mcp/<seat>/packs/<app>`. Enable that pack
   connector from Marketplace → Your plugins for the life of the ticket, and disable it when the
   ticket closes. The same seat token authorises it; the same 20-tool ceiling applies because the
   gateway counts both surfaces.

WEB uses step 3 directly, since its roster carries no load tool.

### §4 Using pack tools

Pack tools are gateway tools: reads fail open with a `reason`, writes fail closed, 20 s per call,
every call audited, no secrets in arguments (`skills/desk-gateway`). Two pack-specific rules:

- A pack tool's result is evidence for the ticket that loaded it. A smoke result recorded under
  another `task_id` is a receipt claim with the wrong provenance.
- `*_push_test` and `*_supabase_query` are scoped by the gateway (test devices, SELECT). Do not
  ask them for more; the refusal is the design.

### §5 Unloading

When the ticket closes (PR opened and receipt written, or the ticket is handed back):

```
app_tools_load {app: "kanbanos", task_id: "<same ticket id>", unload: true}
```

Then confirm `tools/list` is back to 10–15. A pack left loaded keeps the seat above 15 with no
open ticket, which `lead_doctor check` reports on the Tools row and which blocks the next load.
If the fallback connector was used, disable it from Marketplace → Your plugins as well.

### §6 Authoring a pack

Packs are contracts. The rules:

| Rule | Consequence of breaking it |
|---|---|
| File is `contracts/tool-packs/<app>.yaml`; owner QUALITY; `contract_surface: true` | Anywhere else and G-1 sees an unowned path or a seat editing a contract |
| At most five tools; names prefixed `<app>_` | Six tools breaks the 20 ceiling; an unprefixed name collides with a roster tool |
| Every tool has `kind`, `gates`, a schema with `additionalProperties: false`, a `packs.*` backend | G-7 fails a tool without a schema; an open schema lets arguments through the audit hash unexamined |
| g5/g6 tools carry `rollback_plan`/`approval_id` in `required` | Otherwise the gateway cannot enforce PD-5 on them |
| `seats:` lists the consuming seats; those seats ack the change | G-4: a pack change with no ack is a change nobody agreed to consume |
| No secrets, hosts or project ids in the file | The contract is public to every seat and every template recipient (G-3) |
| SYSTEMS implements the backend after the contract merges | Code before contract means the contract describes what was built, not what was agreed |

Propose a new pack or a change with `sys_contract_propose` (SYSTEMS) or a PR to QUALITY; the
consuming seats answer with `qua_contract_ack`. Do not add a tool by editing the gateway alone.

---

## Worked example

**Good — ANDROID smoke-tests KanbanOS staging for SPE-214.**

```
tools/list → 15
app_tools_load {app: "kanbanos", task_id: "SPE-214"} → added 5 tools
tools/list → 20 (after connector refresh)
kanbanos_api_smoke {environment: staging}  → 200, v2.3.1, 140 ms
kanbanos_feature_flags {environment: staging} → {…}
kanbanos_crash_reports {platform: android, since_hours: 48} → 3 groups
… receipt written, PR opened …
app_tools_load {app: "kanbanos", task_id: "SPE-214", unload: true}
tools/list → 15
```

**Bad — same seat, next week.**

```
app_tools_load {app: "desklanes", task_id: "SPE-231"} → error, reason: 20 tools already live
Seat: "gateway bug", opens the Desk Lanes admin in the browser instead
```

The kanbanos pack from SPE-214 was never unloaded. The refusal is correct, doctor has been
reporting Tools red for a week, and the browser read is un-audited and unverifiable. Unload the
old pack, load the new one, and the same work takes two calls.

---

## Failure modes

| Mode | Symptom | Fix |
|---|---|---|
| Pack never unloaded | Tools row red; next load refused for the 20 ceiling | Unload with the original `task_id` |
| Two packs at once | Second load refused for the 20 ceiling | One pack per seat; unload first |
| Wrong `task_id` | Audit ties the smoke to the wrong ticket | Use the ticket's real id on load and unload |
| Tools not visible | Load succeeded, `tools/list` unchanged | Refresh the connector; else enable `/mcp/<seat>/packs/<app>` |
| Fallback connector left enabled | Count stays high after unload | Disable it in Marketplace → Your plugins |
| WEB calls `app_tools_load` | Not on WEB's `tools/list` | WEB's roster has no load tool; use the pack connector |
| Browser instead of pack | Un-audited product read | Load the pack; if no pack has the tool, §6 |
| Sixth tool in a pack | G-7 fails the contract | Split the app into two packs or drop a tool |
| Backend before contract | Tool exists on the gateway, absent from the contract | Contract PR first; consumers ack; then code |

---

## Gate mapping

| Rule | Gate or directive | Why |
|---|---|---|
| Pack changes need consumer acks | G-4 | `contracts/tool-packs/**` consumers are WEB, ANDROID, IOS |
| ≤ 5 tools per pack; ≤ 20 live; schemas on every tool | G-7 | The same checks doctor runs on the Tools row |
| g5/g6 pack tools require approval and rollback fields | G-5, G-6, PD-5 | A product rollout tool is still a rollout tool |
| No secrets or hosts in a pack contract | G-3 | Contracts are read by every seat and template recipient |
| Pack results are evidence under the loading ticket | G-2 | Provenance is part of what a receipt proves |
</skill>
