# omega_prime.redteam — vendored T3MP3ST Grok Bot pack

This package is a verbatim vendored copy of the T3MP3ST declarative **Grok Bot
pack** (upstream: [swcstudiospace/purple-team](https://github.com/swcstudiospace/purple-team),
commit `831a6b0`, MIT, © 2026 SWC Studio Space). It contains **declarative
artifacts only** — markdown and JSON; no executable upstream code ships here.
See `__init__.py` for full provenance and `pack/LICENSE` for the MIT notice.

## What the pack is

- **5 seat templates** in `pack/templates/`: `COMMANDER`, `RED`, `BLACK`,
  `BLUE`, `WHITE` — the role documents that configure each seat of a red team
  engagement.
- **A 5-seat roster** in `pack/rosters/redteam.json` pointing every seat at the
  T3MP3ST loopback gateway at `http://127.0.0.1:3333`.
- **14 saved skills** in `pack/skills/` (plus `GRAMMAR.md` and `index.json`)
  that drive the roster's slash commands — status, mission lifecycle, reports,
  operators, findings, providers, purple synthesis, and hat dispatch.

## How it relates to Omega Prime

The registry-side `tempest_*` tools (`tempest_status`, `tempest_mission_start`,
`tempest_mission_stop`, `tempest_report`, `tempest_operators_list`,
`tempest_operator_task`, `tempest_findings`, `tempest_providers`,
`tempest_purple_synthesize`, `tempest_hats_list`, `tempest_hat_dispatch`) are
**not** in this package — they live in `omega_prime/tools/redteam.py` and are
registered only when the `redteam` family flag is enabled (top-level
`redteam.enabled` in Omega Prime config, default **off**). This package is the
declarative content those tools' gateway ecosystem consumes: import it, load a
seat template or skill, and drive the loopback gateway the tools talk to.

## Auth model

The gateway is trusted on **loopback only** (`127.0.0.1` / `localhost`).
Requests to the loopback gateway rely on that local trust with no bearer
credential. Any **non-loopback** gateway URL is refused unless a bearer token
is configured (Omega Prime reads it from `redteam.bearer_token_file` or the
upstream `T3MP3ST_BEARER_TOKEN` / `T3MP3ST_BEARER_TOKEN_FILE` variables).

## Authorized use only

Red team tooling is for **authorized engagements only**: operate against
systems **you own or have written permission to test**. Nothing in this pack
authorizes testing against third-party systems.

## Known upstream staleness

The vendored `pack/README.md` says "**seven** saved skills" (lines 5 and 18).
That wording is stale upstream: `pack/skills/index.json` and
`pack/marketplace/plugin.json` both list **14** skills, which is the correct
count in this copy. The upstream README is kept byte-verbatim as a Grok import
artifact, so the outdated wording is preserved as-is.
