<routine name="desk-lead" version="1">
<summary>Load `skills/lead-pack/SKILL.md`. Run `omega_prime.routines.desk_lead.run_lead_pass` via `/omega-desk-lead` or `python -m omega_prime.routines.desk_lead`.</summary>
Claims intake from `$OMEGA_PRIME_STATE_DIR/intake.json`. Without a child model the pass still runs and reports each ticket blocked. It does not invent a done receipt.

Grok Bot routine a new install can create:
- name: Omega Prime desk-lead
- prompt: Call the omega_command tool with command `/omega-desk-lead`. Report tickets, blocked reasons, and receipts. Do not mark blocked work done.
- schedule: weekdays at 09:15 local (`15 9 * * 1-5`)
</routine>
