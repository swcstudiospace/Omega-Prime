# Phase 121 — GoTxCoT uplift (task 00064)

First uplift written 2026-10-24 13:05 AEDT before discuss/plan. Second uplift (tracker URLs) is in the `<tracking>` block below.

```xml
<uplift task="00064" phase="121" slug="ci-runner-swarm">
  <ORIGINAL at="2026-10-24T12:53+11:00">/gsd-autonomous ultrathink orchestration workflowz Please introduce a Linux Desktop Pro Container Swarm for the Pool. So we can double the CI's. We can't have 9 jobs in queue. So still serverless but yeah let's ramp it up to 3 Linux machines so thats 24 CI's in the pool which should speed up our coding</ORIGINAL>
  <ORIGINAL at="2026-10-24T12:58+11:00">remember to mirror Serverless so they can scale down</ORIGINAL>
  <goal>The Omega CI controller places ephemeral runners across N Railway "Ubuntu Desktop" runner agents (start N=3, 8 slots each, pool 24). Every agent stays serverless: it sleeps when idle and is woken on demand by the launch path only.</goal>
  <constraints>
    <c>Every job still runs on [self-hosted, linux, x64, omega-ci].</c>
    <c>Backward compatible: a controller.env with only OMEGA_CI_DESKTOP_URL/TOKEN_FILE behaves exactly as today.</c>
    <c>No timers or health polling while idle. Health comes from launch, wake and inventory answers only.</c>
    <c>HARD STOP on spend: no Railway service, domain or live controller env change. Cost shown first (billing is PAST_DUE).</c>
    <c>Tests use no real sockets. Docs list every env var and value up front.</c>
  </constraints>
  <got>
    <node id="g1" kind="settings">Parse N agents: legacy OMEGA_CI_DESKTOP_* is agent 1; OMEGA_CI_DESKTOP_2..9_{URL,TOKEN_FILE,CAPACITY}. Pool max = sum of capacities.</node>
    <node id="g2" kind="placement">Least-loaded (load/capacity) healthy agent with room; ties go to the lowest index.</node>
    <node id="g3" kind="wake">A possibly-asleep agent (no live runners on it) gets GET /healthz with the wake budget before the launch. Not ready or draining → fall back to the next agent.</node>
    <node id="g4" kind="failover">429 marks the agent full until a slot frees there. A definitive failure (wake failed and runner absent, 409, 4xx/5xx) marks it unhealthy with a backoff cooldown. Ambiguous results never fail over (single-use JIT).</node>
    <node id="g5" kind="reconcile">The ghost-slot check runs per agent. Runners owned by an agent whose inventory failed count as present. The check returns None only when every agent failed.</node>
    <node id="g6" kind="callbacks">The exit callback accepts any configured agent token.</node>
    <node id="g7" kind="scale-down">Unchanged: idle grace reaps runners, and with no children and no polling the agent goes quiet, so Railway sleeps it after about 10 min without outbound traffic.</node>
    <node id="g8" kind="ops">Cost model (idle and busy), DNS on Hostinger, and a go-live runbook split into what Desk Lead can do and what only Ove can do.</node>
  </got>
  <cot node="g2">
    <s>Compute load per agent from the pool's owner map, pruned to the scaler ledger after every step.</s>
    <s>Filter: capacity left, not full-flagged. Prefer healthy agents, then unhealthy ones as a last resort.</s>
    <s>Sort by (load/capacity, index).</s>
    <s>Try the agents in order until one accepts.</s>
  </cot>
  <cot node="g3">
    <s>Wake only when that agent owns zero live runners. An agent with live runners is awake.</s>
    <s>Use DesktopClient.wake(): GET /healthz, retrying connect and 502/503 within the wake budget.</s>
    <s>200 and not draining → place. Otherwise mark unhealthy and try the next agent.</s>
    <s>Never call it from anywhere but the launch path.</s>
  </cot>
  <cot node="g5">
    <s>Fan out GET /v1/runners to every agent concurrently.</s>
    <s>Union the names that answered and record owners.</s>
    <s>Add owned names for the agents that failed (conservative).</s>
    <s>Return None when every agent failed, which matches the single-agent behaviour.</s>
  </cot>
  <cot node="g1">
    <s>With one agent, capacity defaults to OMEGA_CI_MAX_RUNNERS (today's 8).</s>
    <s>With more than one agent, OMEGA_CI_MAX_RUNNERS defaults to the sum and must not exceed it. Per-agent capacity is 1..10 because of the pids.max 1000 shared per container.</s>
    <s>HARD_MAX_RUNNERS is raised from 10 to 50 (5 agents × 10; D-121-10). Per-agent HARD_MAX stays 10.</s>
    <s>Duplicate URLs are rejected.</s>
  </cot>
</uplift>
```

<second_uplift at="2026-10-24 ~13:20 AEDT">
  <trackers>
    <notion task="https://app.notion.com/p/403bc1a0c7ae81568c93e53fee8b0580" sub="https://app.notion.com/p/403bc1a0c7ae81d59353e73c26e94d62"/>
    <linear status="blocked">free issue limit exceeded for workspace; no issue created</linear>
  </trackers>
  <execution plan="121-01" backend="inline (executor subagent; agentOS dispatch is not available to a subagent, so rule 4a is recorded as inline)"/>
</second_uplift>

