# Red team module

An optional, default-off tool family that mirrors the T3MP3ST loopback
gateway. When enabled, the Omega Prime tool registry gains eleven
`tempest_*` tools that proxy the upstream gateway's HTTP API, plus a
vendored copy of T3MP3ST's declarative Grok Bot pack. Omega Prime ships
no gateway: the tools talk to a T3MP3ST sidecar you start and control.
The upstream and what was and was not vendored is in `VENDOR.md`.

This page documents the contract. It does not claim live results — see
[Status](#status).

## The gate

The family is off until `omega-prime.json` turns it on. The top-level
`redteam` key defaults to:

```json
{
  "redteam": {
    "enabled": false,
    "gateway_url": "http://127.0.0.1:3333",
    "bearer_token_file": ""
  }
}
```

Environment beats the file, as with the other Prime families:

```bash
OMEGA_PRIME_REDTEAM_ENABLED=1
OMEGA_PRIME_REDTEAM_GATEWAY_URL=http://127.0.0.1:3333
```

With the family off, none of the eleven tools register and the host
behaves as if the module did not exist. Restart the host after a change;
see the troubleshooting table in [Driving the bot](driving-the-bot.md).

## Tools

| Tool | Effect | Gateway calls | Approval |
| --- | --- | --- | --- |
| `tempest_status` | Read mission status and gateway/LLM provider state. | `GET /api/mission/status`, `GET /api/llm/status` | no |
| `tempest_mission_start` | Start a mission. | `POST /api/mission/start` (`name?`, `targets?`, `operators?`) | **required** |
| `tempest_mission_stop` | Stop the running mission. | `POST /api/mission/stop` | **required** |
| `tempest_report` | Export the engagement report for a mission. | `GET /api/mission/report` or `GET /api/mission/{missionId}/report` | no |
| `tempest_operators_list` | List spawned operators. | `GET /api/operators/list` | no |
| `tempest_operator_task` | Task one operator. | `POST /api/operators/{operatorId}/task` (`taskName`, `taskDescription`) | **required** |
| `tempest_findings` | Read findings, optionally for one mission. | `GET /api/findings[?missionId=]` | no |
| `tempest_providers` | Read LLM provider and OAuth/subscription-session state. | `GET /api/llm/status`, `GET /api/oauth/status` | no |
| `tempest_purple_synthesize` | Merge findings with detections into a purple synthesis. | `POST /api/teams/purple/synthesize` (`findings?`, `detections?`) | no |
| `tempest_hats_list` | Read hat prompts and tool grants. | `GET /api/teams/tool-grants`, `GET /api/operators/prompts` | no |
| `tempest_hat_dispatch` | Spawn a hat operator and task it. | `POST /api/operators/spawn` (`archetype`), then `POST /api/operators/{id}/task` | **required** |

The four approval-required tools are the state-changing ones: mission
start and stop, direct operator tasking, and hat dispatch. They are
gated like any other tool in the host; see [Tool host](tool-host.md)
for the gate mechanics and the `--approve` flag.

## Gateway contract

The tools speak the upstream loopback HTTP API, not MCP:

- Default `gateway_url` is `http://127.0.0.1:3333`. Loopback means the
  host `127.0.0.1` or `localhost` — nothing else counts.
- A non-loopback gateway URL is refused unless a bearer token is
  configured. The token comes from `T3MP3ST_BEARER_TOKEN`, from the
  file named by `T3MP3ST_BEARER_TOKEN_FILE` (the upstream environment
  contract), or from the file named by `redteam.bearer_token_file` in
  `omega-prime.json`.
- The sidecar is T3MP3ST itself, checked out as the tracked root
  `redteam/` submodule (remote `https://github.com/swcstudiospace/purple-team.git`,
  commit `831a6b0ee09f12c0f069f51d62b2f0945d505980` per `VENDOR.md`) and
  started separately — Omega Prime does not start, supervise, install, or
  auto-launch it. See [Backend checkout and start](#backend-checkout-and-start).
## Backend checkout and start

The TypeScript backend is separate upstream source reached through the
`redteam/` gitlink. The Python package, the registry adapter
(`omega_prime/tools/redteam.py`), and the vendored 14-skill / five-seat
pack (`omega_prime/redteam/pack/`) are ordinary parent content; the
backend is not ported and never starts on its own.

An ordinary `git clone` of Omega Prime leaves the submodule path
empty. Either clone recursively or init afterwards (targeted init
suffices when only the backend is wanted):

```bash
git clone --recurse-submodules https://github.com/swcstudiospace/omes-bot.git omega-prime
# or, inside an existing checkout:
git submodule update --init redteam
```

The optional upstream setup follows its pinned package contract
(Node `>= 22.19.0`), authorized-use only:

```bash
npm --prefix redteam ci
npm --prefix redteam run server
```

No secrets are read from ambient examples: the gateway bearer token
reaches the tools only through the token sources named above, never
through checked-in values. Point `redteam.gateway_url` at the sidecar
you started and keep the family default-off until an authorized
engagement needs it.

## Vendored skill pack

`omega_prime/redteam/pack/` holds T3MP3ST's declarative Grok Bot pack
copied verbatim: the seat templates (COMMANDER, RED, BLACK, BLUE,
WHITE), the red-team roster, and 14 saved skills. The skills are the
upstream descriptions, one line each:

| Skill | Use |
| --- | --- |
| `tempest-bootstrap` | `/tempest-help` or `/tempest-config`, or a seat's first session: registers the seat id and checks gateway reachability. |
| `tempest-run` | `/tempest-run` with an objective: acknowledges with a run id, streams progress, and closes with a completion summary. |
| `tempest-mission` | Starting, parking, resuming, pausing, or stopping a mission: owns mission ids and the parked/resumable lifecycle. |
| `tempest-operators` | `/tempest-agents` or tasking a seat: lists operators and dispatches work to one operator id. |
| `tempest-status` | `/tempest-status` or `/tempest-providers`: reads mission status plus provider and subscription-session state. |
| `tempest-report` | `/tempest-report`: exports the engagement report for a mission id. |
| `tempest-memory` | Recalling past work or reviewing learned entries: reads the memory capsule and accepts or rejects proposals. |
| `black-specter` | `/black-specter` with a red plan: critiques the plan's covert tradecraft, cover, and noise budget from the adversary's eyes. |
| `black-wraith` | `/black-wraith` with exfil paths: simulates the paths as analysis and checks them against the noise budget. |
| `blue-sentinel` | `/blue-sentinel` with findings: maps each finding to telemetry and writes one detector recommendation per gap. |
| `blue-warden` | `/blue-warden` with findings: audits hardening and returns a verification checklist plus misconfiguration call-outs. |
| `white-arbiter` | `/white-arbiter` with an action: returns the ROE verdict on that action and resolves any red versus blue dispute about it. |
| `white-auditor` | `/white-auditor` with evidence: scores each entry by provenance and aggregates the evidence-strength verdict. |
| `dark-purple` | `/dark-purple` with a mission scope: runs the full loop of red findings to black pressure to blue detection to white verdict to report. |

The upstream skills drive Grok Bot seats against the gateway; in Omega
Prime they are reference material for what the mirrored tools and hats
mean. The tool family itself is registered through the standard
registry — the same surface as every other rostered tool, cataloged in
[Tool catalog](tool-catalog.md).

## Status

No live T3MP3ST gateway has been exercised from this repo. The tool
family is implemented against the upstream loopback HTTP contract and
the vendored pack is a verbatim copy; actual behavior — missions,
operators, findings, reports — depends entirely on a T3MP3ST sidecar
that this repo does not ship or start. If the sidecar is down, the
tools fail with connection errors; nothing else in Omega Prime changes.

Grok's own recursive-clone behavior is not attested by these local
operator instructions: a Grok Bot seat cannot be assumed to populate
the `redteam/` (or `prime-agent/`) submodule on its own. No
deployed or live model / mission proof is established here — no
claim is made about a running sidecar, a live provider, or a real
mission outcome.

## Authorized use only

The pack and the `tempest_*` tools exist for red-team and purple-team
work against systems you own or have explicit written permission to
test. Nothing in this module authorizes testing anyone else's systems,
and the approval gates on the state-changing tools are part of that
contract, not an obstacle to it.
