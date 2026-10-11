# Migrating to v10 (Prime merge)

v10 merges a third upstream runtime — Prime Agent — into Omega Prime and
relicenses the repository. It is designed to be a no-op upgrade for
existing installs.

## If you upgrade and change nothing

- **Prime capabilities remain opt-in.** With every family off, no Prime
  family is offered in the live registry or canonical assembled tool list.
  Six current consumer transcripts match the actual captured pre-v10
  `79ff51af` baseline, including approvals, effects, errors, and stop/call
  accounting (`tests/test_prime_regression.py`). This is bounded behavioral
  evidence, not byte-for-byte installation identity. The unmodified historical
  suite recorded 376 passes and two obsolete source/inventory-pin failures;
  the literal all-tests compatibility criterion remains a decision gate.
- **Your data is unchanged.** Goal state, memory, sessions, and cron jobs
  load as before; Prime goal semantics live in a `prime_goal.json` sidecar
  that does not touch the base `goals.json` schema.

## The license change

The repository moves from MIT to **AGPL-3.0-only**, Copyright (C) 2026
Spectrum Web Co. If you embed Omega Prime in a service, review the AGPL's
network-use terms. Upstream-ported modules keep their MIT attributions
(`VENDOR.md`); the relicensing applies to Omega Prime itself.

## Opting in to Prime capabilities

Set flags in `omega-prime.json` (repo root) or via
`OMEGA_PRIME_PRIME_<FAMILY>_ENABLED=1` env vars — see
[agent-loop.md](agent-loop.md) for the full reference and per-family tool
lists, and [connectors.md](connectors.md) for the typed adapter contracts
(versioned schemas, strict decoding, structured errors).

New rostered tool families when enabled: `rlm_*` (7), `harness_*` (6),
`goal_*` (5), `heartbeat_*` (3), `autonomous_*` (3), `agent_message_send` +
`agent_observe` (2), and the kernel family (`prime.kernel.enabled`, 11):
`prime_cell`, `prime_factory_run`, `prime_factory_status`, `prime_factory_stop`,
`prime_factory_resume`, `prime_factory_graph`, `prime_bash`, `prime_skill_list`,
`prime_crates`, `prime_goal`, `prime_autonomous`. All are in the policy allowlist;
writes require approval (`prime_cell`, `prime_factory_run`, `prime_factory_stop`,
`prime_factory_resume`, `prime_bash`, `prime_goal`, `prime_autonomous`).

Canonical prompt assembly reads no user configuration or environment. Use
`render(..., config=load_config(...))` when embedding the effective configuration,
or repeat `--enable-family FAMILY` on the assembler CLI when opting in; the CLI
then requires `--output PATH` (written, or with `--check` compared) and never
touches the canonical `prompts-assembled/OMEGA_PRIME.xml`. The
policy contract remains an allowlist superset, not permission to invoke absent
tools. Catalog approval flags and required parameters come from actual registrations.

RLM children now have atomic durable session documents, owner-scoped constructor
recovery, interrupted/error states without replay, and durable rename/delete
metadata. List/delete expose no child answer; collect is the answer channel.
Kernel progress is bound to the executing child and obeys parent acceptance
and throttling. Child execution still needs an explicitly supplied runner.
Kernel spawn/create without a real selected/configured model fail before effects;
there is no invented scripted default or automatic native session engine.

Embedding callers: an `RlmHost` (or `register_rlm_tools` with a parent) requires
the explicit parent contract `session_dir`, `session_name`, `delegate_depth`,
`max_depth`, and `max_children`; there is no working-directory default, and
`OmegaPrimeAgent(..., session_dir=...)` supplies the directory. The
`rlm_create_session` tool has no `cwd`. Every Prime tool handler accepts only its
declared parameters plus `schema_version`; any other key, including
session-bound fields such as `sender`, `session`, `scope`, or `stale_after_turns`,
is `unknown_field`. `goal_set` replaces the whole goal. See
[connectors.md](connectors.md).

Enabled goal/autonomous tools now drive logical-turn boundaries in
`OmegaPrimeAgent`; continuation uses the existing caller budget and iteration
cap. Enabled heartbeats re-enter named live sessions through in-process
APScheduler, not a daemon. Use context-manager ownership or call `close()` so
last-owner cleanup settles scheduled work. Existing persisted job and goal
schemas are unchanged; a passed quality gate or a budget limit does not claim
objective completion.

Enabled controls accept normal Anthropic finish reasons and retain an accepted
answer when a later continuation is interrupted. Mixed usage shapes are charged
per call without normalizing away raw provider metadata. An explicit
`autonomous_start` is still required to replace a stopped run.

Malformed heartbeat rows are disarmed; calendar-invalid schedule requests fail
before persistence. Injected schedulers require a fresh runtime after shutdown.
Harness refinements now capture the pre-change entries for rollback; existing
snapshot contents and the fixed base prompt are not rewritten.


## For downstream redistributors

- The pinned Rust checkout remains ignored and read-only. Default-off installs
  remain the Python product (package floor `>=3.11`; see
  [Setup](setup.md) for the exercised and CI interpreters); selecting
  optional native providers or kernel tools requires the bridge build
  described in the [README](../README.md#build-from-source-both-toolchains).
- If you pinned the MIT license for compliance, pin the pre-v10 release;
  v10 and later are AGPL-3.0-only. Upstream MIT notices stay intact
  (`VENDOR.md`): inclusion here relicenses nothing upstream.

## Further reading

Typed adapter contracts live in [Connectors](connectors.md); execution and
security boundaries live in [Architecture](architecture.md); the hand-run
checks live in [Local validation](local-validation.md). The
Greptile-synthesized history is a separately labelled untrusted archive —
see [Greptile](greptile.md) — never current migration truth. Nothing here
asserts a deployment, a live provider attachment, or a signed approval.
