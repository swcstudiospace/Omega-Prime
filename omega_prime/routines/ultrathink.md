<routine name="ultrathink" version="1">
<summary>Load `skills/ultrathink/SKILL.md`. Run `omega_prime.routines.ultrathink_turn.resolve_turn_plan` via `/omega-ultrathink-routine` or `python -m omega_prime.routines.ultrathink_turn`.</summary>
Looks for `last-plan.json` under `OMEGA_PRIME_STATE_DIR/ultrathink` first, then the host plan directories (`ULTRATHINK_STATE_DIR`, `GROK_HOME`, `HERMES_HOME`, `GROK_PLUGIN_DATA`, `PI_CODING_AGENT_DIR`). A missing plan is `found: false`, not an error.

Grok Bot routine a new install can create:
- name: Omega Prime ultrathink
- prompt: Call the omega_command tool with command `/omega-ultrathink-routine`. If a plan is found, work that plan. If none is found, say so and stop.
- trigger: when an operator message contains `ultrathink`. Event-driven only; no timer or cron schedule.
</routine>
