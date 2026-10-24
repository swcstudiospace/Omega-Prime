---
phase: 121
plan: 01
status: complete (code); go-live held for Ove (spend + DNS)
backend: inline (executor subagent)
decisions_auto_accepted: [D-121-01, D-121-02, D-121-03, D-121-04, D-121-05, D-121-06, D-121-07, D-121-08, D-121-09, D-121-10]
---

# 121-01 Summary: multi-agent serverless runner pool

## What changed

- `settings.py`: `AgentEndpoint` and `ControllerSettings.agents`. Agent 1 is the legacy `OMEGA_CI_DESKTOP_URL` / `_TOKEN_FILE` plus the new `_CAPACITY`. Agents 2..5 are `OMEGA_CI_DESKTOP_<n>_{URL,TOKEN_FILE,CAPACITY}`, which must be contiguous, https only, unique, and each needs a token file. Each agent's cap is 1..10. The pool hard ceiling is 50, and `OMEGA_CI_MAX_RUNNERS` defaults to the sum of agent capacities and must not exceed it.
- `desktop.py`: `DesktopClient.wake()` sends `GET /healthz` within the existing wake budget and returns a `WakeProbe` (ready / draining / status). The `RunnerAgent` protocol is added.
- `pool.py` (new): `DesktopPool` handles least-loaded placement (healthy agents first, then load/capacity, then index) and reserves the owner before any await. The first runner placed on an empty agent gets a wake probe first; on a timeout or a draining agent it falls back to the next agent. A 429 holds the agent as full. A definitive failure marks the agent unhealthy with a 60 s cooldown that doubles to a 600 s cap, measured by clock comparison with no timers. An ambiguous launch is never placed twice. Inventory is a per-agent union, and ghost reconcile counts a failed agent's owned names as present. Events: `agent_woke`, `agent_unhealthy`, `agent_healthy`. With one agent the pool is a pass-through (identical behaviour to Phase 120).
- `controller.py` / `app.py`: every agent call goes through the pool, `retain()` prunes owners as runners finish, and the callback auth accepts any agent's token in constant time.
- Docs: `docs/ci-runners.md` (swarm section with every env var and value, scale-up/scale-down, measured cost), the generated `docs/configuration.md`, `deploy/ci-runner-desktop/README.md` (adding agents 2 and 3), and `deploy/ci-runner-controller/env.example`.

## Serverless (Ove's steering at 12:58 AEDT)

There is no idle polling anywhere: the pool has no timers, and health is learned only from launch/stop/inventory results that real jobs trigger. An agent with no runners gets no requests, so Railway sleeps it after about 10 min without outbound traffic. Tests assert that an idle pool sends zero requests and that an agent is re-probed after it empties. The runner agent itself needs no change (D-121-08).

## Verification (box, 2026-10-24 AEDT)

- `pytest omega_prime/tests/ci_runners -q` → 610 passed, 2 skipped (25 new pool tests plus settings/app/desktop additions)
- `ruff check`, `ruff format --check`, and `mypy omega_prime/` (652 files) → clean; `polyglot-check --changed` → 0 errors
- `env_inventory --check` → only the known main-red `GITHUB_REF: MissingPurpose` (jobhook.py:270); the new rows are rendered into docs/configuration.md
- Full suite: see the PR body (the local env lacks `hypothesis`, so test_envelope.py is skipped locally)

## Held for Ove

Creating Railway services `ubuntu-desktop-2/3` (paid; billing is PAST_DUE), the Hostinger DNS CNAMEs for runner2/runner3.ego.engineer, the Railway custom domains, and the live controller env change. None of these were run.
