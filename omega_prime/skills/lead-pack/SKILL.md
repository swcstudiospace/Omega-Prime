---
name: lead-pack
description: "Run the desk Lead flow inside Omega Prime: claim intake, write tickets, dispatch to specialists, consolidate receipts into a report."
bots: [bot-00-omega-prime]
---
<skill name="lead-pack" version="1">
# Lead pack

Run the desk Lead flow inside Omega Prime: claim intake, write tickets, dispatch to
specialists, consolidate receipts into a report.

## When to use

An operator ask arrives as intake (or backlog orders exist) and needs
assignable tickets with verified outcomes. One pass handles a bounded batch;
repeat passes drain the queue.

## Ticket shape

`{ticket_id, intake_id, ask, status}` where status is `dispatched`, `done`,
or `blocked`. `ticket_id` is `t-<intake_id>`. Blocked tickets keep a `reason`.

## Dispatch rules

- Dispatch through the injected dispatcher only; without one, tickets go
  `blocked` with reason `no dispatcher`. Never invent specialist work.
- A dispatch without a receipt dict blocks the ticket; the order returns to
  `open` so a later pass retries it.
- Ack `done` only when the receipt validates; anything else returns the
  order to `open`.

## Receipt requirements

Every consolidated receipt passes `lead_receipt_check`: commands with exit
codes, claims citing evidence indexes, and an explicit `unverified` list.
Invalid receipts are listed in the report's `invalid` section — never
dropped, never counted as done.

## Tools

`lead_intake_next`, `lead_intake_ack`, `lead_brief`, `lead_memory_retain`,
`lead_memory_recall`, `lead_ownership_resolve`, `lead_receipt_check`,
`lead_event_emit`, `lead_doctor`, `lead_render_prompt`, `lead_docs_search`,
`lead_graph_register`, `lead_graph_state`, `lead_bus_start`, `lead_bus_wait`,
`lead_roster_status`.
</skill>
