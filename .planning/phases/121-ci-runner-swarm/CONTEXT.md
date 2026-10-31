# Phase 121: CI runner swarm (task 00064) — Context

**Gathered:** 2026-10-24 (AEDT) · **Mode:** /gsd-autonomous smart_discuss, grey areas auto-accepted (gsd-runtime rule 5b)

## Domain
The controller in `omega_prime/ci_runners` drives one Railway runner agent today (`runner.ego.engineer`, capacity 8). This phase makes it schedule across N agents (3 × 8 = 24) and keeps every agent serverless. Creating the Railway services, adding the domains and editing DNS and the live controller env are all out of scope. They are held for Ove's spend go.

## Decisions (all `auto-accepted`)
| # | Grey area | Recommended answer taken |
|---|---|---|
| D-121-01 | Env shape for N agents | The legacy `OMEGA_CI_DESKTOP_URL` / `_TOKEN_FILE` stays agent 1. Agents 2..5 use `OMEGA_CI_DESKTOP_<n>_URL`, `_TOKEN_FILE` and `_CAPACITY`. Agent 1's capacity is `OMEGA_CI_DESKTOP_CAPACITY`. No JSON blobs, so the existing env-file conventions are kept. |
| D-121-02 | Pool size | `OMEGA_CI_MAX_RUNNERS` defaults to the sum of agent capacities. If set, it is a global cap and must be ≤ the sum. Single agent: capacity defaults to `OMEGA_CI_MAX_RUNNERS`, so behaviour is byte-for-byte today's. |
| D-121-03 | Placement | Least-loaded by load/capacity, ties to the lowest index (spread). Spreading keeps each container under its shared pids.max 1000. Extra awake containers cost only their idle baseline (about 0.16 GB). |
| D-121-04 | Wake | Wake on demand only. An agent with no live runners from this controller is treated as possibly asleep: GET `/healthz` within the wake budget before the launch. Not ready or draining → next agent. No idle polling, ever. |
| D-121-05 | Failover | 429 marks the agent full until a runner on it is released. A definitive failure marks it unhealthy with a cooldown of 60 s doubling to 600 s, measured by clock comparison with no timers. Unhealthy agents are still tried last, so the pool never deadlocks. Ambiguous launches never fail over. |
| D-121-06 | Ghost slots | Checked per agent. A failed agent's owned runners count as present. The check returns None only if every agent failed. |
| D-121-07 | Exit callbacks | Each agent has its own token. `/agent/events` accepts any configured agent token. |
| D-121-08 | Scale-down | No code change on the agent. The idle reap empties it, and Railway sleeps it after about 10 min with no outbound traffic. The 22 h zero-egress window on 23 Oct confirms the agent is quiet. A 5-minute ~385 B/590 B exchange seen 24 Oct 01:41–05:00 AEDT is noted as a watch item. |
| D-121-10 | Agent ceiling (decided while executing) | MAX_AGENTS = 5, so the pool hard ceiling is 50 and each agent keeps a cap of 10. The env-inventory gate needs every setting name to be a literal read with a documented purpose, so the agent slots are listed explicitly instead of read dynamically. 5 slots cover the 3-agent target with headroom. More slots are a 3-line change. |
| D-121-09 | Runner labels | Unchanged: `[self-hosted, linux, x64, omega-ci]` on every agent, all in runner group omega-ci (id 4). |

## Deferred
- Creating `Ubuntu Desktop 2/3`, the custom domains `runner2/runner3.ego.engineer`, the Hostinger DNS CNAMEs, and the controller.env + secret files. All held for Ove (spend, and billing PAST_DUE).
- Moving the controller to the GitHub App rate limit (00062 follow-up).
