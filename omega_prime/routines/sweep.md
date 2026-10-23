<routine name="sweep" version="1">
<summary>Run `omega_prime.routines.sweep.sweep_mentions` via `/omega-sweep` or `python -m omega_prime.routines.sweep`. Drafts are queued. This routine does not publish.</summary>
Needs `X_API_TOKEN` or `OMEGA_PRIME_SWEEP_MENTIONS` (a JSON file of posts). Drafts land in `$OMEGA_PRIME_STATE_DIR/sweeps/`. There is no publish step and no flag that posts.

Grok Bot routine a new install can create:
- name: Omega Prime sweep
- prompt: Call the omega_command tool with command `/omega-sweep`. Report queued drafts only. Never publish a reply.
- trigger: when a new X mention arrives (the only source `/omega-sweep` reads). Event-driven only; no timer or cron schedule.
</routine>
