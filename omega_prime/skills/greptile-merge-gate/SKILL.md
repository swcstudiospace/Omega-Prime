---
name: greptile-merge-gate
description: Use when QUALITY (or LEAD on QUALITY's behalf) must enforce the Greptile merge gate on a draft PR before anyone claims the change may merge. Does not weaken G-1..G-6.
bots: [bot-00-omega-prime]
---
<skill name="greptile-merge-gate" version="1">
# greptile-merge-gate

**Owner:** QUALITY (bot-06). LEAD may trigger the review after a draft PR opens. Only QUALITY records the merge-claim verdict. This skill does not weaken G-1…G-6 or PD-1…PD-6.

**Status:** Policy encoded from `https://github.com/swcstudiospace/programming-desk/blob/main/docs/github-sot-orchestration.md` §4. **E2E not verified** — no live Greptile trigger was run while authoring this pack. If MCP `get_me` returns needsAuth, say so and stop. Do not mark the gate passed.

---

## When to use

A draft PR exists for desk work (Cloud Agent, Hermes, or a specialist) and someone is about to claim it may merge, or QUALITY is reviewing a completion claim.

Do not use to skip review, waive comments without a receipt, or treat a queued trigger as a finished analysis.

## After the PR opens

1. Resolve the repo tuple (`name`, `remote`, `defaultBranch`) with the Greptile repository list.
2. Call `trigger_code_review` with that tuple and `prNumber`.
3. A successful trigger means the review was **queued**, not that analysis finished.
4. Poll `list_code_reviews` / `get_code_review` until status is terminal: `COMPLETED`, `FAILED`, or `SKIPPED`.
5. Fetch comments via `get_merge_request` and/or `list_merge_request_comments`. Filter to Greptile and the `addressed` flag.

## Merge-claim rule

| Condition | Merge claim |
|---|---|
| Greptile comments with `addressed=false` | **Block** the completion / merge claim |
| Addressed in code with a follow-up commit, or explicitly waived | Allowed only with a **waiver receipt** |
| Greptile `FAILED`, `SKIPPED`, or unavailable | Do not silently skip. Record `unverified` or a blocker. LEAD escalates. |

**Waiver receipt** (under `.receipts/` or a PR comment linked from the receipt): who waived, which comment ids, why, and that QUALITY acknowledged. Waiver is reviewable. Ignoring Greptile is not.

## Checklist (QUALITY)

- [ ] Review triggered for this PR number, not a different repo
- [ ] Terminal status recorded (`COMPLETED` / `FAILED` / `SKIPPED`)
- [ ] Every unaddressed comment either blocks or has a waiver receipt
- [ ] G-1…G-6 still required. Greptile does not replace them
- [ ] Verdict names the PR URL and the Greptile status
- [ ] No self-approval of QUALITY's own gate changes

## What this skill does not do

- It does not rewrite product code. Findings go back to the owning seat.
- It does not claim the full intake → merge loop is verified.
- It does not authorize `--no-verify`, skipped tests, or a green receipt over a failed Greptile run.
</skill>
