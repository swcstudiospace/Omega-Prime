---
name: trackplan-dispatch
description: Use when the second-uplift XML already contains live Notion and Linear URLs (5–8 GoT nodes, 4–8 CoT sub-issues per node) and Programming Lead must launch Cursor Cloud Agent or VPS Hermes to implement the work as a GitHub PR.
bots: [bot-00-omega-prime]
---
<skill name="trackplan-dispatch" version="1">
# trackplan-dispatch

**Owner:** Programming Lead (bot-00). LEAD orchestrates; specialists own product paths via `ownership.yaml`. This skill never invents product-code ownership.

**Desk defaults:** Linear density **Dense** (one issue per GoT node, one sub-issue per CoT step). Node target **5–8**. Step target **4–8 per node**. CoT fill that produced those steps was sequential by default. Notion collection **🧩 Agent Task Graph** (`collection://be3418f0-d2d8-411b-8677-fa8a95ee63be`). Linear team **Spectrum Web Co**. Companion docs: `docs/gotxcot-cloud-pipeline.md`, `docs/github-sot-orchestration.md`.

**Prerequisite skill:** `skills/gotxcot-uplift` (first uplift → GoT → per-node CoT → kickoff → **second uplift**). This skill consumes the second uplift. It does not create it.

**Status:** Skill written for the desk pack. E2E launch → PR → Greptile → QUALITY **not verified**. Lane B preferred path is connected `user-hermes-agent` (HTTP) via `agent_bus_*`. Local stdio `user-hermes` was intentionally uninstalled on the desk host on 2026-09-24; do not rebuild `hermes-mcp-bridge.mjs`. `handoff_to_hermes` is Hermes-only fallback.

---

## When to use / when not

**Use after:**

- Dense kickoff finished (`skills/gotxcot-uplift` stage 4, or upstream `ultrathink-kickoff` adapted to desk counts).
- The **second uplift** has been written: nested XML whose root contains `<ISSUES>` with live Notion and Linear URLs for the Task, every Issue (5–8), and every Sub-Issue (4–8 per node).
- You have a target GitHub repo URL and a chosen `runtime`.

**Do not use for:**

- The first uplift, GoT, CoT, or HITL — that is `skills/gotxcot-uplift`.
- Creating Notion or Linear rows — kickoff owns creation. This skill only embeds the IDs already created.
- Dispatching a short paraphrase, a markdown issue list, or a first-uplift XML that has no URLs.
- Claiming merge, QUALITY pass, or Greptile clean.
- Specialist path edits. Lane C is `SendToAgent` per `ownership.yaml`.
- Re-running kickoff “to be safe” when the Graph ID already exists.

```
Second uplift done (XML includes live <ISSUES> URLs)?
│
├─ NO  ──▶ skills/gotxcot-uplift. Do not dispatch a URL-less spec.
│
└─ YES ──▶ This skill.
           Choose runtime → Lane A (cursor-cloud) or Lane B (hermes).
           Build GitHub SoT work packet around the second-uplift XML.
           Launch → write Lead receipt.
           Do NOT poll Cloud Agent. Do NOT claim merge. Do NOT edit product code.
```

---

## Inputs required

| Input | Required | Notes |
|---|---|---|
| `graphId` | **Yes** | `ut-<base36>-<uuid8>` |
| **Second-uplift XML** | **Yes** | Root contains `<ISSUES>` with live URLs. This is the spec. A summary is not a substitute. |
| Session / plan path | Optional | Attach when it exists. The XML is still required. |
| `repo` | **Yes** | Full remote URL, e.g. `https://github.com/org/repo` |
| `runtime` | **Yes** | `cursor-cloud` (Lane A, default) or `hermes` (Lane B) |
| `starting_ref` | Optional | Branch or SHA. Else the repo default. |
| Node / issue subset | Optional | Restrict launch to specific `nodeId`s. Default = whole graph. |
| Notion `Agent` | Lane A | Confirm Task `Agent` = `cursor-cloud` before launch. |
| Density check | **Yes** | 5–8 `<ISSUE>` elements, 4–8 `<SUBISSUE>` elements per node, unless `<TRACKER_GAPS>` explains a real create failure. |
| Acceptance criteria | **Yes** | From the uplift XML, not invented at dispatch time. |

If the XML has fewer than 5 issues and no recorded fallback/gap, send it back to `gotxcot-uplift`. Do not “just dispatch”.

---

## Lane selection

Matches `docs/github-sot-orchestration.md`:

| `runtime` | Lane | Action |
|---|---|---|
| `cursor-cloud` / `cloud` / omitted | **A — Cursor Cloud Agent** | Default. Notion `Agent` = `cursor-cloud`. |
| `hermes` | **B — VPS Hermes** | Prefer connected `user-hermes-agent` `agent_bus_*`. Do not reinstall stdio `user-hermes`. **Never** invent an SSH coding handoff. |
| Clear path ownership only | **C — Specialist** | Out of scope here → `SendToAgent`. The ticket still cites the second-uplift `<ISSUES>` URLs for the nodes that seat owns. |

Lanes may compose (A/B for a node, C for a path slice). Lane B does **not** bypass G-1…G-6.

**SSH rule:** SSH to the VPS is INFRA-owned ops, not the default code handoff.

---

## GitHub SoT work packet

The agent prompt **must** contain the fields below **and** the full second-uplift XML. Mirror the fields into the PR body / `.receipts/` when the implementer opens the PR.

| Field | Purpose |
|---|---|
| `repo` | Full GitHub URL |
| `graph_id` | TrackPlan Graph ID |
| `branch` | `bot-<nn>-<seat>/<task_id>` — include a Linear id or graph slug in `task_id` |
| `starting_ref` | Optional base |
| `linear` | Parent issue + sub-issue ids and URLs (from `<ISSUES>`, not from memory) |
| `notion` | Task / Issue / Sub-Issue URLs |
| `runtime` | `cloud` or `hermes` |
| `owner` | Acting seat when known; else “LEAD-orchestrated Cloud/Hermes — respect ownership.yaml” |
| `goal` | From the uplift, not a new goal |
| `paths_in_scope` / `out_of_scope` | From the ticket or ownership inference — **do not invent ownership** |
| `acceptance` / `success_criteria` | From `ACCEPTANCE_CRITERIA` |
| `ownership.yaml` constraint | Only owned paths; cross-seat → contract-first; LEAD does not write product code |
| `receipt_path` | `.receipts/<bot-id>/<task_id>.json` |
| `report_back` | PR URL, receipt, `unverified`, blockers |
| **Greptile expect** | After the draft PR opens, QUALITY/LEAD trigger Greptile. Implementer addresses comments. **Do not claim merge.** |
| **Density** | State the node count (5–8) and that each node has 4–8 sub-issues in `<ISSUES>` |

Copy-paste scaffold: `skills/trackplan-dispatch/prompt-template.md`.

---

## Lane A — Cursor Cloud Agent

### Preconditions

1. Idempotency check passed (below).
2. Notion Task `Agent` = `cursor-cloud` (update if kickoff left `claude-code`).
3. Prompt = work packet + **entire second-uplift XML**. Do not truncate `<ISSUES>` or drop named surface sections.
4. Attach plan/session JSON via `files` when it exists.

### Launch

Use the Cursor Cloud Agent launch surface available to LEAD:

| Arg | Value |
|---|---|
| `repo` / `repo_url` | Full URL |
| `prompt` | Work packet + full second-uplift XML. Hand off problem and outcome. Do not add line-by-line prescriptions that contradict the XML. |
| `title` | `SPE-<primary> / <graphId> <short goal>` |
| `files` | Optional plan/session JSON |
| `images` | Only if the operator attached UI or repro images |

### After launch

1. Record the agent id and URL in the Lead receipt.
2. Acknowledge briefly. **Do not poll** to completion. Lead is revived when the run finishes.
3. On revival: note branch and draft PR URL if present. List `unverified`.
4. Greptile, QUALITY, and merge are not this skill. Point QUALITY at the PR (`skills/greptile-merge-gate`). Do not claim merge from tracker state.

### Ownership reminder (include in the prompt)

> LEAD orchestrates. You implement. Respect `ownership.yaml`: one owner per path. If the change spans seats, stop and report blockers rather than editing foreign paths. Write a verification receipt under `.receipts/`. Open a **draft** PR with the work-packet fields in the body. The `<ISSUES>` block is the tracker map — do not open a second set of Linear issues.

---

## Lane B — VPS Hermes

### Prefer connected HTTP MCP

| MCP server | Observed state (desk host, 2026-09-24) | Use |
|---|---|---|
| `user-hermes-agent` | **Connected** (HTTP). `agent_bus_health` returned `status: ok` | Preferred Lane B control. Call `agent_bus_*` |
| `user-hermes` (stdio) | **Uninstalled** — removed on purpose. Do not rebuild | Superseded by Agent Bus on `user-hermes-agent`. Missing `hermes-mcp-bridge.mjs` is not a cue to recreate the script |

**If `agent_bus_*` is missing, unauthorized, or returns an error: STOP.** Record the blocker. Do **not**:

- Rebuild `/workspace/hermes-mcp-bridge.mjs` or reinstall stdio `user-hermes`.
- Invent an SSH coding handoff (unless the operator explicitly routes INFRA for ops).
- Paste the packet only into chat as the durable record.
- Claim Hermes picked up the job without a tool result or a GitHub draft PR.
- Call `handoff_to_hermes` for a goal that already has an Agent Bus job.

### Live tools

Preferred path on `user-hermes-agent`:

| Tool | Role | Arguments |
|---|---|---|
| `agent_bus_health` | Loopback health of local vps-agent-bus. Returns status and available runtimes | none |
| `agent_bus_start_job` | Start a job (`POST /v1/jobs`). Returns `jobId`, status, and `wsUrl`. MCP does not open the WebSocket | required `runtime`, `goal`; optional `provider`, `idempotency_key` |
| `agent_bus_get_job` | One job snapshot (`GET /v1/jobs/{id}`) | required `job_id` |
| `agent_bus_wait_job` | Poll until `completed`, `failed`, `error`, or timeout | required `job_id`; timeout default 180s (max 600) |

Put the GitHub work packet and the second-uplift XML in `goal` (and keep the same text in the draft PR). `agent_bus_start_job` can note that a public `wss` URL needs `BUS_TOKEN`. The token is not part of the tool result. Do not write it into the prompt, the PR, or a receipt.

**`handoff_to_hermes`** is the Hermes-only durable EXECUTE fallback. Prefer `agent_bus_*` for live multi-runtime jobs. Use this tool only for one durable Hermes EXECUTE. Do not fire multiple handoffs in parallel for one goal. Poll `get_task`. Do not re-submit.

Live schema:

| Field | Required | Shape |
|---|---|---|
| `goal` | yes | string |
| `messages` | yes | array of objects. Each message is a free-form object, typically role/content conversation turns compacted for Hermes |
| `title` | no | string or null |
| `priority` | no | string, default `normal` |
| `labels` | no | array of strings, or null |
| `tenant_id` | no | string or null |
| `idempotency_key` | no | string or null |

The older design-intent shape (`repo`, `branch`, `graph_id`, `work_packet_markdown`, `prompt` as separate arguments) is not the live schema. Fold those fields into `goal` and into `messages` only when this fallback is the call you are making.

Hermes durable output is the same as Lane A: **branch + draft PR on GitHub** + `.receipts/`. The VPS is a computer, not a source of truth.

---

## Idempotency

Do **not** double-launch the same `graphId` + node subset without an explicit operator request.

Before launch:

1. Look for a running Cloud Agent whose title or prompt cites this `graphId` or primary Linear id. If you cannot list agents, write `unverified` — do not assume none exist.
2. Search open GitHub PRs for the `graph_id` or Linear ids.
3. If the Notion Task already has a `PR URL` or a recorded agent id, confirm with the operator before a second launch.
4. Re-entrant kickoff may update tracker rows. Dispatch is not automatically re-entrant.

---

## Verification receipt (Lead)

Write `.receipts/bot-00-programming-lead/<task_id>.json`.

```json
{
  "task_id": "dispatch-<graphId>-<nodeOrAll>",
  "bot": "bot-00-programming-lead",
  "started_at": "<iso8601>",
  "completed_at": "<iso8601>",
  "graph_id": "ut-…",
  "runtime": "cursor-cloud | hermes",
  "lane": "A | B",
  "repo": "https://github.com/…",
  "node_count": 6,
  "subissue_counts": {"n1": 5, "n2": 4},
  "linear_ids": ["SPE-…"],
  "notion_urls": ["…"],
  "commands": [
    { "cmd": "CloudAgent action=launch … | agent_bus_start_job … | STOP blocker", "exit_code": 0, "output_tail": "agent id / job id / blocker" }
  ],
  "claims": [
    { "claim": "Dispatched Lane A/B for graphId (or reported blocker)", "evidence_command_index": 0 }
  ],
  "agent_id": "<cloud agent id or hermes job id or null>",
  "pr_url": null,
  "unverified": [
    "E2E PR open not confirmed at dispatch time",
    "Greptile not run (QUALITY)",
    "Lane B job result absent when this dispatch did not call agent_bus_start_job"
  ],
  "blockers": []
}
```

Honest `unverified` / `blockers` are success. Do not fabricate PR URLs, Greptile passes, or node counts that the XML does not contain.

---

## Honest gaps

| Gap | Impact |
|---|---|
| E2E not verified | Intake → second uplift → launch → PR → Greptile → QUALITY → sync is **not** proven |
| Lane B E2E | `user-hermes-agent` is connected and `agent_bus_*` is the live path. This skill text does not prove a Hermes job id plus a draft PR |
| `user-hermes` stdio | Uninstalled on the desk host 2026-09-24. Do not rebuild `hermes-mcp-bridge.mjs`. Agent Bus supersedes that bridge |
| `handoff_to_hermes` | Hermes-only durable EXECUTE fallback. Required `goal` (string) and `messages` (array of free-form objects, typically role/content turns). Optional `title`, `priority` (default `normal`), `labels`, `tenant_id`, `idempotency_key`. Prefer `agent_bus_*` for live multi-runtime. One handoff per goal; poll `get_task`; do not re-submit |
| Cloud Agent dry-run | Launch only when the operator intends real work |
| PR → Notion/Linear sync | Still to build. Do not invent tracker rows |
| VPS engine constants | May still be MIN_NODES=3 until patched. Desk dispatch still requires 5–8 `<ISSUE>` nodes in the XML |

---

## References

| Path | Role |
|---|---|
| `skills/gotxcot-uplift/SKILL.md` | Produces the second-uplift XML this skill requires |
| `docs/gotxcot-cloud-pipeline.md` | Dense policy, two uplifts |
| `docs/github-sot-orchestration.md` | Lanes, SoT, Greptile, Hermes |
| `docs/desk-operating-model.md` | Lane C ticket shape |
| `ownership.yaml` | Path → specialist |
| `skills/verification-receipts/SKILL.md` | G-2 receipt rules |
| `skills/trackplan-dispatch/prompt-template.md` | Prompt scaffold |
| `skills/greptile-merge-gate/SKILL.md` | After the draft PR exists |
| `vendor/ultrathink-policy/constants.ts` | 5–8 nodes, 4–8 steps |
</skill>
