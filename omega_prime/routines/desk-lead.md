<routine name="desk-lead" version="1">
<summary>Load `skills/lead-pack/SKILL.md`. Run `omega_prime.routines.desk_lead.run_lead_pass` via `/omega-desk-lead` or `python -m omega_prime.routines.desk_lead`.</summary>
Claims intake from `$OMEGA_PRIME_STATE_DIR/agent/intake.json` (the agent data folder; `OMEGA_PRIME_AGENT_STATE_DIR` overrides it). Without a child model the pass still runs and reports each ticket blocked. It does not invent a done receipt.

Grok Bot routine a new install can create:
- name: Omega Prime desk-lead
- prompt: Call the omega_command tool with command `/omega-desk-lead`. Report tickets, blocked reasons, and receipts. Do not mark blocked work done.
- trigger: when a new intake item or ticket arrives (Linear issue created, or a lead intake event), and when a blocked ticket changes state (unblocked, reopened, or its blocker resolved) so open work is picked up again. Event-driven only; no timer or cron schedule.
</routine>
