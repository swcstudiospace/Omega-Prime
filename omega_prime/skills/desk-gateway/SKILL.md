---
name: desk-gateway
description: "How to use the per-seat gateway tools: rosters as contracts, fail-open reads and fail-closed writes, gate tags, deadlines, 403 and not_configured handling."
bots: [bot-00-omega-prime]
gates: [G-5, G-6, G-7]
---
<skill name="desk-gateway" version="1">
# Desk Gateway

## Served tools

Use the Omega Prime tools named below (`lead_*`, `app_*`, `web_*`, `mob_*`, `infra_*`, `sys_*`, `qua_*`). An external Desk Gateway is optional and is not part of a default install. Its `desk_*` and `agent_bus_*` tools are a fallback only when that gateway is connected and a served tool returns `not_configured`. If neither answers, stop and record the blocker. Do not invent a tool name.

## Omega Prime adaptation

Omega Prime has no Desk Gateway: tools come from the in-process registry
(or the Omega Prime MCP tool host at `python -m omega_prime.mcp_server`), and
the roster is `contracts/tool-rosters/omega-prime.yaml`. Read "gateway"
below as that registry, "seat" as the single Omega Prime bot, and
refusals as blockers for the operator, not retry loops.

## When this applies (L1)

Every `desk_*` tool you hold comes from the Desk Gateway at `https://desk.swcstudio.space/mcp/<seat>`.
The gateway serves your roster and nothing else: **a tool the gateway did not list does not
exist**, and a call it refused is a blocker for LEAD, not a retry loop. Rosters are contracts
under `contracts/tool-rosters/` (QUALITY); the eight core tools are in `_core.yaml` and your seat
file adds the rest.

```
Calling a gateway tool?
│
├─ Is it in your tools/list right now?      NO ──▶ It does not exist for you. Ask LEAD. §1
├─ kind: read?  Empty result + reason ──▶ Fail-open. Record the reason; do not treat empty as "none". §2
├─ kind: write? Error ──▶ Fail-closed. Nothing happened. One retry only if the reason says so. §2
├─ gates: [g5]? ──▶ External gateway: rollback_plan + approval_id on the call (PD-5). Served tool: those fields stay on the receipt. §3
├─ gates: [g6]? ──▶ External gateway: approval_id on the call. Served tool: receipt only. §3
├─ Took > 20 s? ──▶ The call was cut. Bound the inputs and split the work. §4
├─ 403? ──▶ Wrong seat endpoint. Stop. Tell LEAD. §5
├─ not_configured? ──▶ Upstream credential not provided. `unverified`, no browser workaround. §5
└─ Argument contains a token, password or key? ──▶ Remove it. Every call is audited. §6
```

The gateway exists so that Bots hold no credentials and every action is on the event log. Both
properties only hold if the seat treats refusals as answers and arguments as public.

---

## Method (L2)

### §1 The roster is the contract

`tools/list` on your endpoint returns your seat's roster: the eight core tools plus 2–7 seat
tools, 10–15 in total, or up to 20 with one tool pack loaded (`skills/tool-packs`). Each tool
carries `kind` (`read` or `write`), `gates` (`g5`, `g6`, or none) and a JSON schema. The schema
is enforced: `additionalProperties: false` means an extra argument is a validation error, not
an ignored hint.

If a task needs a tool that is not on your roster, the answer is a contract change through
QUALITY (G-4), or a handoff to the seat that has it. Working around a missing tool with the
browser or the shell puts an un-audited action on a plane the gateway was built to fence.

### §2 Failure semantics by kind

| Kind | On upstream failure | What you do |
|---|---|---|
| `read` | Fails **open**: empty result plus a `reason` string | Record the reason. An empty result with a reason is "unknown", never "none". A claim built on it goes in `unverified` |
| `write` | Fails **closed**: an error, and the mutation did not happen | Do not assume partial effect. Retry once only if the reason is transient (the deadline was hit, the upstream was unavailable); otherwise report the blocker |
| `write` + `g5`/`g6` | Fails closed and refuses without the gate fields | A refusal is a blocker for LEAD. Never re-issue the call with invented `approval_id` |

The gateway never returns an upstream stack trace. If the `reason` is unhelpful, say so in the
receipt; do not guess at the cause.

### §3 Gate tags (PD-5)

| Tag | Required inputs | Where they come from |
|---|---|---|
| `g5` | `rollback_plan`, `approval_id` | The plan is yours and must satisfy G-5 (command, recovery time, data implications). The approval id is a recorded human approval, obtained the way `core-directives` routes it: build seats ask in the group or via LEAD; LEAD surfaces it to the operator |
| `g6` | `approval_id` | Same approval path. The id is echoed into the audit event, which is how the receipt and the event log agree |

An `approval_id` is a reference to an approval that exists. Typing a plausible string to satisfy
the schema is a PD-5 breach with an audit trail attached to it. If you do not have one, you do
not make the call.

On an external gateway, `g5` and `g6` fields are tool arguments. On the served Omega Prime
registry they are not. `infra_railway_redeploy` accepts only `project`, `service`, and
`prior_deployment_id`. Put `approval_id` and `rollback_plan` on the receipt. The registry
rejects any other argument (`additionalProperties: false`). The host approval log still gates
that tool; the id is not an argument.

Tools carrying these tags on an external gateway today: `web_vercel_promote` (g5),
`web_vercel_rollback` (g5), `infra_railway_redeploy` (g5), `mob_play_staged_rollout` (g5 g6),
`mob_play_halt_rollout` (g6), `mob_appstore_phased_release` (g5 g6),
`mob_appstore_pause_release` (g6). Your roster file is authoritative if this list drifts.

### §4 The 20-second deadline

Every call is cut at 20 seconds, under the MCP client's own deadline, so the client sees a clean
error rather than a hang. Consequences:

- Bound inputs that have a bound: `limit`, `lines`, `since_hours`, `timeout_sec` (for
  `lead_bus_wait`, poll in slices rather than one long wait).
- Split a large question into several calls; three calls that return beat one that is cut.
- A cut read returns empty with a `reason` naming the deadline. Treat it as §2, not as "the data
  is empty".

### §5 Errors that mean "stop"

| Error | Meaning | Response |
|---|---|---|
| `403` | Your token is for a different seat than the endpoint you called, or the connector was added with the wrong URL | Stop. Tell LEAD with the endpoint and tool name. Do not retry, do not try another endpoint. `/desk doctor` shows the Connector row |
| `not_configured` | The operator has not provided the upstream credential for that tool (Play, App Store Connect, Vercel, Railway, Greptile) | Record it in `unverified` with the tool name. Do not perform the action through the browser or a personal login; the gateway path exists so that the action is audited and the credential is not yours to hold |
| Refusal naming gate fields | A g5/g6 call without its approval or rollback inputs | Blocker for LEAD. Obtain the approval; do not invent it |
| Unknown tool | The tool is not on your roster (or a pack was unloaded) | §1. Check `tools/list`; do not guess a name |

### §6 Audit and arguments

Every call, successful or not, is written to the event log (Greptime `agent_events`) with seat,
tool, `graph_id`, `task_id`, outcome, duration and a hash of the redacted arguments. Pass
`graph_id` and `task_id` wherever the schema allows them, so the event can be tied to the ticket.

Arguments are redacted at the gateway, but redaction is a backstop, not permission. A token in
an argument is a token that left the Bot computer, was hashed into an audit row and may have
reached an upstream before redaction rules matched it. If a tool needs a secret, the gateway
already holds it; you never supply one (PD-4).

---

## Worked example

**Good — INFRA redeploys a Railway service.**

```
infra_railway_status {project: {{RAILWAY_PROJECT}}}                    → prior deployment id dpl_7f…
approval obtained via LEAD ↔ the operator 1:1; id {{APPROVAL_ID}}
infra_railway_redeploy {project: {{RAILWAY_PROJECT}}, service: {{RAILWAY_SERVICE}},
  prior_deployment_id: "dpl_7f…"}
→ ok, new deployment id
Receipt: approvals=[{{APPROVAL_ID}}], rollback_plan="Redeploy dpl_7f… from the dashboard; under 2 min; no schema change", command tail recorded
```

**Bad — ANDROID gets `not_configured` from `mob_play_track_status`.**

```
→ {error: not_configured, reason: "play developer api credential not provided"}
Seat opens the Play Console in the browser with a personal account, reads the track, reports
"rollout at 20%".
```

The number may be right and the receipt cannot prove it: the read is un-audited, the credential
was never the seat's to use, and the claim is unverifiable by re-running the receipt. Correct
response: `unverified: ["Play track state not read — mob_play_track_status not_configured"]`,
and tell LEAD so INFRA can add the credential to the gateway env.

**Bad — WEB gets 403 from `web_vercel_deployments` and tries `/mcp/systems`.**

The second endpoint is also 403 and now two audit rows show a seat probing another seat's
surface. One 403 is a misconfiguration; the response is `skills/desk-bootstrap` §1, not a search.

---

## Failure modes

| Mode | Symptom | Fix |
|---|---|---|
| Guessed tool name | Unknown-tool error | Read `tools/list`; the roster file names them exactly |
| Empty read taken as "none" | Claim built on a fail-open result | Check for `reason`; move the claim to `unverified` |
| Retry loop on a refusal | Same refusal N times in the audit log | One call, one blocker to LEAD |
| Invented `approval_id` | Schema passes, audit shows an id that matches no approval | PD-5 breach; obtain a real approval |
| Long single call | Empty result, reason names the 20 s deadline | Bound `limit`/`lines`/`timeout_sec`; split |
| 403 explored | Multiple endpoints probed | Stop at the first 403; tell LEAD |
| Browser workaround | Action done outside the gateway after `not_configured` | Record in `unverified`; ask INFRA for the credential |
| Secret in an argument | Redacted args hash, token exposed in transit | Never supply a secret; the gateway holds them |
| Extra argument | Validation error | Schemas are `additionalProperties: false` |

---

## Gate mapping

| Rule | Gate or directive | Why |
|---|---|---|
| `rollback_plan` on an external g5 call, and on the receipt for a served tool | G-5 | The external gateway refuses without it. A served tool rejects it as an argument; the receipt still carries the plan |
| `approval_id` on an external g5/g6 call, and on the receipt for a served tool | G-6, PD-5 | Approval is recorded in two places that must agree. It is not an argument of `infra_railway_redeploy` |
| No secrets in arguments | G-3, PD-4 | Arguments are hashed into an audit row and may transit upstreams |
| Roster changes go through contract review | G-4 | Every seat is a consumer of `contracts/tool-rosters/**` |
| Roster count 10–15 (≤ 20 with a pack); g5/g6 tools carry their fields | G-7 | The same band doctor checks at runtime |
| `not_configured` and fail-open reads land in `unverified` | G-2, PD-1, PD-6 | A result you did not get is not a result you can claim |
</skill>
