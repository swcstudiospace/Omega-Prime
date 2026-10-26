# Phase 104 - Context

**Requirement:** TD-01 through TD-08 (milestones/v21-REQUIREMENTS.md, from the
uplift's acceptance criteria a1–a8).

**Spec:** `.planning/phases/104-task-dashboard/104-UPLIFT.xml` is the
authoritative uplift (scope, constraints, the 11 DATA_SOURCES, the Task model,
the tasks.json contract, the API, refresh loop, security rules, graceful
degradation and the GOT workflow graph U4 → (U1 ∥ U2 ∥ U3 ∥ U5) → U6 → U7).
Where this file and the uplift disagree, the uplift wins unless a decision
below overrides it.

**Evidence (read this session, 2026-10-21):** Worktree
`/workspace/omes-bot-wt/dash` on `feat/task-dashboard` at `44b28df`, clean,
based on current `main`. `.venv` already has starlette and uvicorn;
`omega_prime/grokbot/approvals_api.py` already uses starlette.
`omega_prime/credentials/redact.py` provides `redact_text`/`redact_value`
(sk-, xai-, gh*_, AKIA, Bearer, KEY=value patterns). `omega_prime/engine/`
has `jobs.py`, `fleet_client.py`, `watch.py`, `omp_driver.py`. Tests live in
`omega_prime/tests/` (run with
`PYTHONPATH=/workspace/omes-bot-wt/dash /workspace/omes-bot/.venv/bin/python
-m pytest omega_prime/tests -q`; the venv's editable install points at the
main checkout, so PYTHONPATH is required). Known baseline failures on main:
`test_desk_wiring`, `test_grokbot_oneclick`, `test_omega_commands` doctor
tests, `test_prime_kernel_*`. CI gates exactly: `ruff check omega_prime/`;
`ruff format --check omega_prime/`; `mypy omega_prime/`;
`pytest omega_prime/tests -q`.

**Decisions (operator, 2026-10-21 01:51 AEDT — locked):**

1. **Location:** omes-bot package `omega_prime/dashboard/` on this branch,
   `feat/task-dashboard` (uplift q1 resolved). The fallback standalone repo
   is rejected.
2. **Fleet collector:** implemented but OFF by default; enabled only via a
   non-secret setting (uplift q3 resolved as "off until enabled").
3. **Omega jobs `final_text`:** show the first line, redacted, capped at 120
   characters (uplift q4 resolved).
4. **Runtime home:** a Herdr workspace "dashboard" on `127.0.0.1:7421`. The
   operator starts the server (nohup or Herdr pane); the repo ships
   `scripts/dashboard-start.sh` but nothing auto-starts and this session never
   launches a long-running server (uplift q2 resolved).
5. **Planning-only session:** U7's scope here is `docs/dashboard.md` +
   `scripts/dashboard-start.sh` + integration tests. The live box run
   (a1–a7 observation), the rebase/PR and the Greptile 5/5 + merge-yes gate
   are operator steps after the phase — do NOT plan tasks that push, open a
   PR, merge, deploy, or touch Railway, the VPS, `/etc`, the connector
   checkout `/workspace/omes-bot`, other worktrees (p1..p7),
   `/workspace/omp-sandbox`, `/workspace/agent-swarm` or Herdr config.
6. **Ambiguity defaults accepted:** port 7421 (checked free, configurable via
   `OMEGA_PRIME_DASHBOARD_PORT`); Greptile score parsed from "Confidence
   score: n/5" in the review/comment body, else "not reviewed"; 24h window
   and 15-minute quiet threshold as defaults settable through the non-secret
   settings allowlist; tasks.json is the only Grok Bot bridge.

**Execution constraints (session rules — locked):**

- Python: `/workspace/omes-bot/.venv/bin/python` with
  `PYTHONPATH=/workspace/omes-bot-wt/dash`. No pip installs. starlette and
  uvicorn come from the venv; stdlib otherwise; no npm, no frontend framework.
- File-disjoint from the wiring branches: touch only
  `omega_prime/dashboard/**`, its tests under `omega_prime/tests/`,
  `docs/dashboard.md`, `scripts/dashboard-start.sh`, and at most one additive
  line in `engine/cli.py` if the `omega dashboard` subcommand is added.
- Read-only dashboard: GET routes only; no state changes to the box, Herdr,
  omp, podman, GitHub or the fleet; action buttons are out of scope.
- Loopback only: refuse `0.0.0.0`, `::` and any non-loopback host with a
  non-zero exit; Host-header guard; strict CSP; textContent only in the UI.
- Tests use `tmp_path` and starlette's TestClient; never write
  `~/.omega-prime/settings.json`, `~/.omega-prime/dashboard/tasks.json` or
  any live config; never start a long-running server in tests; never read or
  print secret values; never read `/proc/*/environ` or auth/token files.
- No time caps introduced anywhere.

**Rejected:** action buttons (revive/kill pane, cancel fleet job, merge/approve
PR, restart sandbox); any public URL, tunnel, auth system, Railway/VPS deploy
or wiring into the grokbot server/Dockerfile; changing Grok Bot to expose its
tasks; editing wiring branches, omp-sandbox, agent-swarm or Herdr config;
Linear/Notion ticket creation; historical analytics, cost charts or token
dashboards beyond per-card totals; new packages of any kind.

**Assumptions (from the uplift, carried forward):** `~/.omega-prime/dashboard/
tasks.json` is written by Omega Prime on each chat update (the only bridge for
Grok Bot tasks/routines); port 7421 was free at 01:45 AEDT; the omp session
jsonl schema seen in the herdr-smoke session holds for RPC sessions and the
collector tolerates unknown entry types; the settings allowlist from wiring
phase 1 exists for non-secret settings.
