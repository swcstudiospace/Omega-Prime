# Event-driven SDLC loop (00043)

Replaces paused Grok Bot **cron/polling routines** with **event wakes** that update the Command Center via locked `tasks.json` writes (`/ws/events`).

> Listeners are wired by the parent agent with `UpdateRoutine` after Ove answers HITL. This package stages **handlers + docs** only — no cron creation here.

## Routine → event mapping

| Paused routine | Capability | Trigger events (GitHub / Origin) | Handler | Command Center effect |
|---|---|---|---|---|
| Org CI/CD sweep (5m) | Merge when Greptile 5/5 + CI green + mergeable | `check_run.completed`, `check_suite.completed`, `pull_request.synchronize`, `pull_request_review.submitted`, Greptile `issue_comment` | `on_gate_signal` | Update PR-linked `gb:*` note; optional merge under standing gate (non-draft, non-auto-deploy) |
| Per-PR Greptile loops | Re-review / fix loop | `issue_comment.created` (Greptile), `pull_request_review` | `on_review_signal` | Note score/threads; spawn fix work only when score &lt; 5 |
| Blocked-task follow-up (30m) | Chase red CI / blocked / waiting-on-Ove | `check_run` failure, PR `mergeable_state` dirty/blocked, task state via CC | `on_blocked_signal` | Set `state=blocked`, note with check URL |
| Fleet deploy (15m) | Railway deploy 729f319 | **HITL** until Railway CLI login; then optional deploy-success event | `on_fleet_signal` (stub) | Stay `blocked` until Ove pings / login restored |
| Session digest (hourly) | Summarize to Ove | **No pure event** — HITL: drop / on-demand / weekday morning exception | n/a | n/a until Ove chooses |

## Standing gates (unchanged)

- Greptile **5/5 on current head**
- CI fully green
- GitHub mergeable / clean
- Merge commits only
- **Hold** drafts and anything that auto-deploys to production
- Ming alone merges/deploys/restarts connector / changes Railway / touches VPS

## Handler package

`omega_prime/sdlc_events/`:

- `types.py` — typed envelopes (`DeliveryId`, `RepoRef`, `PrRef`, `EventKind`)
- `gate.py` — merge-gate predicate (pure)
- `idempotency.py` — delivery-keyed store
- `handlers.py` — `on_gate_signal`, `on_review_signal`, `on_blocked_signal`
- `task_store.py` — adapter over locked `tasks.json` helpers
- `replay.py` — CLI dry-run from fixtures

## Listener wiring notes (for parent)

Prefer one Grok Bot routine with:

```json
{ "type": "group", "listeners": [
  { "type": "github", "repos": ["swcstudiospace/Omega-Prime"], "events": [
    "check_run.completed", "check_suite.completed",
    "pull_request.synchronize", "pull_request.ready_for_review",
    "pull_request_review.submitted", "issue_comment.created"
  ]},
  { "type": "origin", "repos": ["swcstudiospace/Omega-Prime"], "events": [
    "pr-checks-passed", "pr-checks-failed", "pr-ready-for-merge"
  ]}
]}
```

HITL may drop Origin. **Do not** add a cron schedule to this routine.

## HITL (blocking)

1. Merge posture on gate pass: auto (standing) / ping-only / ping-then-ack
2. Digest: drop / on-demand / weekday morning cron exception
3. Listeners: GitHub only / GitHub+Origin
4. Fleet: Ove ping one-shot / listen deploy SUCCESS only after login

## Graph

`ut-tnau6h-870e8435` — Linear SPE-19091…SPE-19097 — Notion Task https://app.notion.com/p/401bc1a0c7ae817a8766f4a9edda9f20
