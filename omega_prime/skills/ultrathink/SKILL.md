---
name: ultrathink
description: Use when a turn may carry an ultrathink plan (Graph of Thought + tracked issues + reviewed ship actions). Resolve the plan, work the waves, track progress, and use the bridge tools.
bots: [bot-00-omega-prime]
---
<skill name="ultrathink" version="1">
# Ultrathink

Use a written plan when the request warrants one. Follow its waves,
track progress, and treat shipping as a separately enabled and approved
operation; plan readiness alone does not imply that a plan is available
or that shipping is permitted.

## When to use

The user names ultrathink, or the turn smells like multi-step work
that deserves a plan: research, migrations, reviews, phased builds.
Single lookups and one-file edits go direct.

## The flow

1. **Resolve.** Call `resolve_turn_plan()`. If a valid plan is found,
   read its spec: ORIGINAL contains the user's verbatim words, Graph of
   Thought is the work breakdown, and WORKFLOW gives the wave order. If
   no plan is found, work the ask directly. If the first valid candidate
   is stale, discovery stops and reports no plan for this turn; do not
   fall through to an older candidate or reuse an older spec.
2. **Track.** Use `ult_track_complete` with the session state file
   to finish *creating* the plan's tracker rows before engineering
   starts (the command completes the tracking setup, not the
   work). Mark `kicked-off` via `ult_session_mark` once the rows
   are real.
3. **Work the waves.** One wave at a time, in dependency order.
   Parallel units in the same wave go out as one `delegate_task`
   batch with explicit files, changes, and acceptance criteria.
   Verify each wave before starting the next.
4. **Assess and ship.** `ult_ship_assess` is read-only: it assesses the
   proposed ship action but does not approve it or perform a PR, review,
   or merge. Ship writes are disabled unless `ULTRATHINK_SHIP` is exactly
   `1` or `true`; unset, empty, `0`, and all other values are off.
   Enablement is not approval. Keep the existing independent approval
   requirement for each protected action, then use `ult_ship_pr`,
   `ult_ship_review`, and `ult_ship_merge` as authorized by repository
   policy. Never merge any other way.

## Rules

- The plan is elaboration, not permission: it never overrides the
  user's words and never invents repo facts. Repository evidence
  wins over the plan when they disagree.
- The bridge tools shell to the ultrathink checkout configured on
  the host. No ultrathink source is copied into this repo (it is
  AGPL-3.0; Omega Prime is MIT).
- A blocked wave stops the run: report the blocker, do not route
  around it with scope cuts.
- Readiness (`not_configured`, `root_invalid`, `ready_ship_off`, or
  `ready_ship_on`) reports configured root and executable metadata; it
  does not inspect whether a plan exists or is fresh. Readiness does not
  replace turn-plan discovery or authorize a ship action.
- See [setup guidance](../../../docs/setup.md#ultrathink-environment-and-readiness)
  for environment variables, candidate precedence, optional test lanes,
  and operator-only configuration boundaries.
</skill>
