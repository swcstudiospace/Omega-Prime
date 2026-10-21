---
name: gotxcot-uplift
description: Use when Programming Lead receives a plain operator ask and must run the dense double-uplift pipeline — first XML uplift, 5–8 GoT nodes, 4–8 CoT steps per node, Notion/Linear materialization, second uplift with live URLs — before trackplan-dispatch.
bots: [bot-00-omega-prime]
---
<skill name="gotxcot-uplift" version="1">
# gotxcot-uplift

**Owner:** Programming Lead (bot-00). LEAD orchestrates this skill. Specialists execute tickets. This skill never invents product-code ownership and never edits specialist paths.

**Local guidance:** This repository does not contain a verified copy of the Programming Desk pipeline policy or the referenced `vendor/ultrathink-policy` files; they are not operative authorities here. Follow the local uplift procedure below and the existing [XML root and density guidance](xml-roots.md).
**Next skill, only after this one finishes:** [trackplan-dispatch](../trackplan-dispatch/SKILL.md).

**Status:** Procedure for the desk pack. Live Notion/Linear/Cloud Agent E2E is **not** verified by authoring this skill.

---

## When to use / when not

**Use when** the operator drops a real build, fix, research, or change ask on LEAD and the work should be tracked densely before anyone writes code.

**Do not use when:**

- The message is a trivial ack (`ok`, `lgtm`, `thanks`) or already a finished second-uplift XML.
- Trackers for this `graphId` already exist — go to second uplift only if URLs are missing from the XML, otherwise go to `trackplan-dispatch`.
- You are a specialist seat. Execute the ticket. Do not re-run GoT.
- You are about to edit `services/`, `web/`, `android/`, `ios/`, or `infra/`. Stop. That is not this skill.

```
Plain operator ask?
│
├─ trivial / already dispatched ──▶ stop or trackplan-dispatch
│
└─ real task ──▶ this skill, in order:
      1 first uplift (long XML)
      2 GoT 5–8 nodes
      3 CoT one node at a time, 4–8 steps each
      4 kickoff materialization (Notion + Linear dense)
      5 second uplift with live URLs
      6 hand off to trackplan-dispatch (do not implement)
```

---

## 1. First uplift

Rewrite the plain ask into nested XML. Return **only XML** in the working packet (you may talk to the operator outside the packet). The first character of the spec is `<` and the last is `>`.

Pick exactly one root: `BUILD_PROMPT`, `FIX_PROMPT`, `RESEARCH_PROMPT`, `CHANGE_PROMPT`, or `UPLIFTED_PROMPT`.

Required children, all SCREAMING_SNAKE, all filled with operational text:

- `ORIGINAL` — operator text **verbatim**. Not a paraphrase. Not prior conversation.
- `SYSTEM_ROLE`
- `CONTEXT` or `APP_CONTEXT`
- `SCOPE`
- `CONSTRAINTS` — always include “do not invent repository facts”
- `ACCEPTANCE_CRITERIA` — observable
- `OUT_OF_SCOPE`

Add when relevant, and do not add empty stubs: `PLATFORM_CONSTRAINTS`, `DESIGN_SYSTEM_CONTINUITY`, `SECURITY_AND_VALIDATION`, `GRACEFUL_DEGRADATION`, `STATES`, `WORKFLOW`, `ASSUMPTIONS`, `AMBIGUITIES`, plus **named nested sections** for the surfaces in the ask (pages, modals, APIs, jobs). Domain-shaped tags, not `<item>` soup.

Shape hint (replace names with the ask’s domain):

```xml
<BUILD_PROMPT>
  <ORIGINAL>...</ORIGINAL>
  <SYSTEM_ROLE>...</SYSTEM_ROLE>
  <APP_CONTEXT>...</APP_CONTEXT>
  <SCOPE>...</SCOPE>
  <CONSTRAINTS>...</CONSTRAINTS>
  <NAMED_SURFACE>
    <SUBSECTION>...</SUBSECTION>
    <STATES>...</STATES>
  </NAMED_SURFACE>
  <SECURITY_AND_VALIDATION>...</SECURITY_AND_VALIDATION>
  <GRACEFUL_DEGRADATION>...</GRACEFUL_DEGRADATION>
  <ACCEPTANCE_CRITERIA>...</ACCEPTANCE_CRITERIA>
  <OUT_OF_SCOPE>...</OUT_OF_SCOPE>
  <ASSUMPTIONS>...</ASSUMPTIONS>
  <AMBIGUITIES>...</AMBIGUITIES>
</BUILD_PROMPT>
```

**Fail the pass** if the result is a short paraphrase, a flat 10-field template, or motivational filler. Expand implicit senior-engineer requirements (empty/error/loading, validation, security, accessibility, retries). Do **not** invent file paths, versions, package names, or API routes that the operator did not state.

Background conversation may recover decisions the operator already made. It must not replace `ORIGINAL`.

---

## 2. Graph of Thought — 5 to 8 nodes

Build a DAG of **major work units**. Count is a function of complexity **inside 5–8**.

| Rule | Detail |
|---|---|
| Minimum | **5** for a real build/fix/change. Fewer is a failed generation. |
| Maximum | **8**. Clamp. Do not keep a 9th node. |
| First | `id=n1`, kind `understand`, `depends_on []` |
| Last | kind `synthesize`, depends on unresolved threads |
| Ids | `n1`, `n2`, … in order. Edges only to earlier ids. No cycles. |
| Independence | Add an edge only when the node truly needs the predecessor. A graph, not a forced chain. |
| Critique | At least one `critique` node whose answer can become HITL. |
| Specificity | Questions name this task. |
| Not in the graph | Linear rows, PR mechanics, Greptile, or which seat types the code. Ownership comes after. |

Kinds allowed: `understand`, `decompose`, `generate`, `compare`, `critique`, `aggregate`, `refine`, `synthesize`.

If graph generation fails, use the five-node fallback `Understand → Decompose → Approaches → Risks → Plan`, mark the graph source `fallback`, and keep all required density constraints. No verified local copy of the former vendor constants is available.

---

## 3. Chain of Thought — one node at a time, 4–8 steps

**Default: sequential.** Fill node `n1`, then `n2`, and so on in topological order. Do not fan out all nodes in one burst unless you have confirmed the runtime can parallelize safely (rate limits, dependency levels, failure isolation). Upstream `runThink` defaults `concurrency` to 1. Keep that.

For **each** node produce:

```xml
<node>
  <rationale>
    1. Discrete step.
    2. Discrete step.
    3. Discrete step.
    4. Discrete step.
  </rationale>
  <conclusion>
    Actionable answer. synthesize nodes include a WORKFLOW of file-disjoint units.
    critique nodes end with "Open questions:" or "Open questions: none".
  </conclusion>
</node>
```

| Rule | Detail |
|---|---|
| Step count | **4–8**. Never 1. Never 9+. |
| Shape | Numbered, one step per line, one or two sentences, a finding or an action. |
| Tracker | Each step → one Linear **sub-issue** and one Notion **Sub-Issue**. |
| Scope | Reason about this node. Use predecessor conclusions. Do not restate the whole graph. |
| Missing facts | State a working assumption and continue. Blocking gaps wait for HITL (≤4). |
| Length | Rationale ≤ 2000 characters. Conclusion ≤ 1200, or ≤ 3000 for `synthesize`. |

HITL: at most **4** blocking clarifications, deduplicated. Non-blocking items take their default and are stated in `<CLARIFICATIONS>` later.

---

## 4. Kickoff materialization checklist

Follow the kickoff materialization steps below. No verified local copy of the former vendor checklist is available.

1. Build `graphId` (`ut-<base36>-<uuid8>`) once. Re-entry updates; it does not duplicate.
2. Notion **Task** on Agent Task Graph. `Agent` = **`cursor-cloud`** if Cursor will execute (Lane A).
3. For each node: Linear **issue** on Spectrum Web Co + Notion **Issue** under the Task.
4. For each step: Linear **sub-issue** under that issue + Notion **Sub-Issue** under that Issue.
5. Resolve HITL (ask blocking questions together, or record defaults).
6. Keep every URL and every failure pair. Do not drop remaining steps after one error.

Dense means you finish with roughly `nodes + nodes×steps` Linear rows (for example 6 nodes × 5 steps = 6 issues + 30 sub-issues). If Linear returns `USAGE_LIMIT_EXCEEDED`, stop creating, record the gap, and escalate. Compact mode is an explicit Lead decision after that, not the next automatic step.

---

## 5. Second uplift

Rewrite and **extend** the first-uplift XML. Do not replace it with a summary.

Inject `<ISSUES>` **inside** the root. Inject `<CLARIFICATIONS>` in the same pass. Preserve `ORIGINAL` verbatim.

```xml
<BUILD_PROMPT>
  <!-- entire first uplift, plus graph conclusions where they help the implementer -->
  <CLARIFICATIONS>
    <ITEM id="c1" blocking="false">Assumed default: …</ITEM>
  </CLARIFICATIONS>
  <ISSUES>
    <TASK graphId="ut-…" notionUrl="https://www.notion.so/…">…</TASK>
    <ISSUE nodeId="n1" notionUrl="https://www.notion.so/…" linearId="SPE-1" linearUrl="https://linear.app/…">[n1] …</ISSUE>
    <SUBISSUE nodeId="n1" step="1" notionUrl="https://www.notion.so/…" linearId="SPE-2" linearUrl="https://linear.app/…">[n1] Step 1: …</SUBISSUE>
    <!-- one ISSUE per node (5–8), one SUBISSUE per step (4–8 each) -->
  </ISSUES>
  <TRACKER_GAPS>
    <!-- only if a create failed; otherwise omit -->
  </TRACKER_GAPS>
</BUILD_PROMPT>
```

**Fail the pass** if:

- URLs are placeholders (`https://…`, `SPE-…` with no real id) when kickoff returned real ones.
- Any created Issue or Sub-Issue is missing and not listed under `TRACKER_GAPS`.
- The document got shorter than the first uplift.
- `<ISSUES>` is a markdown table instead of XML.

The second uplift is the prompt `trackplan-dispatch` must send. A work-packet header may sit in front of it. The XML stays the body.

---

## 6. Hand off — do not implement

Open `skills/trackplan-dispatch` with:

| Input | Source |
|---|---|
| `graphId` | kickoff |
| second-uplift XML | this skill, stage 5 |
| Linear ids + Notion URLs | `<ISSUES>` |
| `repo` | operator or ticket |
| `runtime` | `cursor-cloud` (default) or `hermes` |

Map nodes to seats only when you write specialist tickets (Lane C): `ownership.yaml`, one owner per path, contract-first if a node crosses seats. You still do not write the product code.

Write a Lead receipt under `.receipts/bot-00-programming-lead/` for the orchestration you actually performed (uplift source, node count, step counts, tracker gaps, dispatch pointer). Do not invent specialist test commands. Do not tell the operator the feature is done.

---

## Anti-patterns

| Anti-pattern | Why it fails the desk |
|---|---|
| 3 or 4 GoT nodes “to save time” | Below `MIN_NODES`. Real builds use 5–8. |
| One CoT paragraph per node | Steps must be plural, 4–8, each a sub-issue. |
| Parallel CoT by default | Rate-limit default is sequential. |
| Silent compact Linear | Dense is default. Compact needs an explicit decision after quota failure. |
| First uplift only, then dispatch | The runtime must see live URLs from the second uplift. |
| LEAD edits `services/` or clients | Ownership violation. Orchestrate. |
| Invented Notion/Linear URLs | PD-1. Gaps go in `TRACKER_GAPS` and the receipt. |
| Claiming E2E verified | This skill does not prove Cloud Agent, Greptile, or Hermes. |

---

## References

| Path | Role |
|---|---|
| [XML root and density guidance](xml-roots.md) | Local root picker and density checks |
| [Trackplan dispatch](../trackplan-dispatch/SKILL.md) | Dispatch after stage 5 |
| [Ownership](../../../omega_prime/ownership.yaml) | Who may implement |

The former Programming Desk pipeline and operating-model documents, and `vendor/ultrathink-policy` materials, are not present as verified local sources and are not required authorities.
</skill>
