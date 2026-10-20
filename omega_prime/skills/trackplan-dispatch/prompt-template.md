# Cloud Agent / Hermes prompt template (trackplan-dispatch)

Fill every `{{…}}`. The body of the spec is the **second-uplift XML**, including `<ISSUES>` with live Notion and Linear URLs. Do not paste a summary in place of `{{second_uplift_xml}}`.

Density the packet must reflect: **5–8 GoT nodes** (one Linear issue and one Notion Issue each) and **4–8 CoT steps per node** (one Linear sub-issue and one Notion Sub-Issue each). Sequential CoT was the default fill. If `<TRACKER_GAPS>` lists failed creates, keep that element and do not invent URLs.

Attach plan/session JSON via Cloud Agent `files` when it exists. Hand off **problem + outcome**, not line-by-line prescriptions.

---

## Title (Cloud Agent)

```
SPE-{{primary_linear_id}} / {{graph_id}} {{short_goal}}
```

---

## Prompt body

```markdown
# Implement TrackPlan work as a draft GitHub PR

You are the implementing agent. LEAD orchestrates; you write code only in paths allowed by the repository's [ownership rules](../../../omega_prime/ownership.yaml). Open a **draft** PR. Do not claim merge. Expect Greptile after the PR opens.

The specification below is the second uplift. It already contains `<ISSUES>` with live tracker URLs. Do not create a parallel Linear/Notion tree. Do not shrink the XML.

## Work packet
- graph_id: {{graph_id}}
- density: {{node_count}} GoT nodes (target 5–8); {{steps_per_node}} CoT sub-issues per node (target 4–8)
- linear: {{linear_parents_and_subs_with_urls}}
- notion: {{notion_task_issue_subissue_urls}}
- runtime: {{cloud|hermes}}
- owner: {{bot-0N-seat | LEAD-orchestrated Cloud/Hermes}}
- repo: {{https://github.com/org/repo}}
- branch: bot-{{nn}}-{{seat}}/{{task_id}}
- starting_ref: {{main|sha|omit}}
- goal: {{uplifted_outcome}}
- paths_in_scope: {{globs_or_dirs}}
- out_of_scope: {{explicit_exclusions}}
- success_criteria:
  - {{criterion_1}}
  - {{criterion_2}}
- ownership: Obey ownership.yaml. One owner per path. No unowned paths. No cross-seat edits without contract-first. LEAD does not write product code — if you are Cloud/Hermes covering multiple seats, still respect path owners and stop with blockers on conflicts.
- receipt_path: .receipts/{{bot_id}}/{{task_id}}.json
- report_back: draft PR URL, receipt path, unverified list, blockers
- greptile: After draft PR opens, QUALITY/LEAD will trigger Greptile. Address comments or record a waiver receipt. **Do not claim merge.**

## Second-uplift specification
{{second_uplift_xml}}

## Constraints
- GitHub is source of truth (branch + draft PR + receipt). Do not treat Linear/Notion alone as done.
- Prefer small, reviewable commits.
- No secrets in the repo, the PR body, or the XML.
- If acceptance cannot be verified, list it under receipt `unverified` with the reason.
- Node and sub-issue URLs inside `<ISSUES>` are the tracker map. Update those rows only through the desk sync path; do not open duplicates.
```

`{{second_uplift_xml}}` must be the full document: `ORIGINAL`, `SCOPE`, `ACCEPTANCE_CRITERIA`, named surfaces, `<CLARIFICATIONS>` when any exist, and `<ISSUES>` with one `TASK`, one `ISSUE` per node, and one `SUBISSUE` per step.

---

## Lane B addendum (Hermes)

When `runtime=hermes`, prepend:

```markdown
## Runtime
You are Hermes on the VPS lane. Durable output is still a GitHub branch + draft PR + `.receipts/`. Do not leave results only on the VPS. The second-uplift XML is the spec, same as Lane A.
```

Pass this markdown as the `goal` of `lead_bus_start` (`runtime` set for the job), then poll with `lead_bus_wait`. Only when Solo is off, Desk is explicitly configured, and the served bus tool returns `not_configured` may a connected Desk gateway `agent_bus_start_job` be used. `not_configured: solo` is a terminal local blocker: do not route the prompt through agent_bus, Hermes, or SSH. `handoff_to_hermes` is available only as an explicitly configured Hermes fallback. Do not reinstall stdio `user-hermes` or recreate `hermes-mcp-bridge.mjs`.
