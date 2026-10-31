<routine name="nightly" version="1">
<summary>Run `omega_prime.routines.nightly.nightly_pass` via `/omega-nightly` or `python -m omega_prime.routines.nightly`. The curator earns a skill only when the turn asked to save one.</summary>
Reads turns from `$OMEGA_PRIME_STATE_DIR/nightly-turns.json` (a JSON list, or an object with a `turns` array). Checkpoints reviewed ids in `nightly.json`. Does not publish.

Grok Bot routine a new install can create:
- name: Omega Prime nightly
- prompt: Call the omega_command tool with command `/omega-nightly`. Report the JSON result. Do not publish anything.
- trigger: when a conversation or session ends and appends turns to `nightly-turns.json`. Event-driven only; no timer or cron schedule.
</routine>
