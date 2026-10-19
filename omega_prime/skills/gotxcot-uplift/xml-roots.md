# XML roots and density checks

Use with `skills/gotxcot-uplift`. This is a helper, not a second procedure.

## Root picker

| If the ask is… | Root |
|---|---|
| A new capability, page, flow, or endpoint | `BUILD_PROMPT` |
| A bug, regression, or broken behavior | `FIX_PROMPT` |
| An investigation, comparison, or explanation with no code change required yet | `RESEARCH_PROMPT` |
| A refactor, rename, migration, restyle, or behavior tweak of something that exists | `CHANGE_PROMPT` |
| None of those cleanly | `UPLIFTED_PROMPT` |

One root. Do not wrap a `BUILD_PROMPT` in `UPLIFTED_PROMPT`.

## First-uplift checklist

- [ ] `ORIGINAL` equals the operator message, character for character
- [ ] `SCOPE` says what is in this run
- [ ] `OUT_OF_SCOPE` names the adjacent work a specialist might start by accident
- [ ] `ACCEPTANCE_CRITERIA` are observable (command, UI state, API response) — not “works well”
- [ ] `CONSTRAINTS` forbids inventing repo paths and versions
- [ ] Named sections match surfaces in the ask
- [ ] Unknowns are in `ASSUMPTIONS` or `AMBIGUITIES`
- [ ] The document is long enough that a specialist can implement without re-interviewing the operator for the happy path, empty state, and failure state

## Second-uplift checklist

- [ ] Same root as the first pass
- [ ] `ORIGINAL` unchanged
- [ ] First-pass sections still present
- [ ] `<ISSUES>` is inside the root
- [ ] One `<TASK>` with `graphId` and `notionUrl`
- [ ] One `<ISSUE>` per node, 5–8, each with `nodeId`, `notionUrl`, `linearId`, `linearUrl`
- [ ] One `<SUBISSUE>` per step, 4–8 per node, each with `step` plus both URLs
- [ ] No placeholder URLs
- [ ] `<TRACKER_GAPS>` present only for real create failures
- [ ] `<CLARIFICATIONS>` states every default that was assumed

## Node count clamp

```text
if nodes < 5: regenerate (or fallback graph of 5) and mark source
if nodes > 8: clamp to 8
else: keep
```

## Step count clamp

```text
for each node:
  if steps < 4 or steps > 8: regenerate that node's rationale
  else: one sub-issue per step, no collapsing
```
