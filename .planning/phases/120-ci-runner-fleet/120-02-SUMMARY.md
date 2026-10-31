---
phase: 120-ci-runner-fleet
plan: "02"
subsystem: infra
tags: [github-actions, self-hosted-runners, autoscaler, asgi, github-app, jit-runners]
requires:
  - phase: 120-01
    provides: Scaler core, settings, webhook boundary, DelayedEvents and EventPublisher ports
provides:
  - framework-free ASGI controller (POST /github/webhook, POST /agent/events, GET /healthz) that answers 202 before any downstream I/O
  - GitHub adapter: lazy RS256 App auth with single-flight installation-token refresh (5 min margin), explicit PAT_FILE mode, JIT generation, paginated startup listing, idle-checked delete, bounded rate-limit retry
  - Desktop wake client: first HTTPS POST is the launch; 502/503/connect retried within 150 s (request time included), max 16 attempts, jittered backoff capped at 15 s; 429 never retried
  - Controller: serialized scaler worker, bounded admission (503 without commit), one-shot startup reconcile, generation-safe idle reap (GitHub idle check before agent stop), redacted publishing
  - CLI `python -m omega_prime.ci_runners controller|agent`; uvicorn is the only new optional extra (`ci-runners`)
affects: [120-03, 120-04]
actuals:
  tokens: ~60000
  tasks: 3
  commits: 1
tech-stack:
  added: ["uvicorn>=0.30 (optional extra ci-runners, imported only to serve)"]
  patterns: ["sync Transport seam + asyncio.to_thread", "JIT held only in-process for the launch and dropped in finally", "decisions → tracked tasks → result events back into the fold"]
key-files:
  created:
    - omega_prime/ci_runners/{http,github,desktop,controller,app,__main__}.py
    - omega_prime/tests/ci_runners/{_fakes,_rig,test_http,test_github,test_desktop,test_controller,test_app,test_cli}.py
  modified:
    - pyproject.toml (optional-dependencies ci-runners = uvicorn)
key-decisions:
  - "Executed inline as the Desk Lead subagent (gsd-runtime rule 5b)."
  - "Reap order: GitHub `delete_runner_if_idle` (GET then DELETE) before the agent stop; if the runner turned busy, the child is kept and the in_progress webhook re-adopts it."
  - "A partial startup snapshot never reaps: GitHub failure → agent rows treated as busy observations; agent failure → GitHub rows mirrored, idle ones age out via normal idle deadlines."
  - "Terminal wake failure asks the agent inventory: present → launched; confirmed absent → rollback; inventory error → ambiguous, kept, never deleted."
  - "App-auth failures raise; PAT is never auto-selected. Missing `cryptography` raises CryptoUnavailable with an install hint."
requirements-completed: [D-120-01, D-120-02, D-120-03, D-120-04, D-120-05, D-120-06, D-120-07, D-120-09]
coverage:
  - id: D1
    description: "Webhook is acked 202 while generate-jitconfig is blocked; after release the runner becomes IDLE"
    requirement: D-120-03
    verification:
      - kind: unit
        ref: "omega_prime/tests/ci_runners/test_app.py#test_webhook_ack_does_not_wait_for_github"
        status: pass
    human_judgment: false
  - id: D2
    description: "App token single-flight + refresh margin; no PAT fallback; optional crypto lazily imported"
    requirement: D-120-09
    verification:
      - kind: unit
        ref: "omega_prime/tests/ci_runners/test_github.py"
        status: pass
    human_judgment: false
  - id: D3
    description: "Wake retry budget (150 s incl. request time, ≤16 attempts, 15 s cap), identical body, 429 not retried"
    requirement: D-120-05
    verification:
      - kind: unit
        ref: "omega_prime/tests/ci_runners/test_desktop.py"
        status: pass
    human_judgment: false
  - id: D4
    description: "Reconcile, 429 rollback, wake-failure identity, idle reap order, busy guard, callbacks, saturation"
    requirement: D-120-04
    verification:
      - kind: unit
        ref: "omega_prime/tests/ci_runners/test_controller.py"
        status: pass
    human_judgment: false
duration: ~3h
completed: 2026-10-23
status: complete
---

# Phase 120 Plan 02: Controller Summary

**Framework-free ASGI controller with GitHub App/JIT and Desktop-wake adapters wired to the 120-01 scaler: 186 hermetic tests in `tests/ci_runners`, 99% branch coverage on `omega_prime.ci_runners`.**

## Accomplishments
- `http.py`: a tiny sync `Transport` seam (stdlib urllib, finite timeout, 4 MiB read cap, redirects returned rather than followed so a bearer token never follows a 302). `HttpRequest` hides headers and body from `repr`. `send()` runs on a worker thread.
- `github.py`: `AppTokenSource` (RS256 JWT, `iat-60`/`exp+540`, `asyncio.Lock` single-flight, cached until 300 s before expiry) and `PatTokenSource`. `GitHubClient` covers org- or repo-scoped JIT, paginated pool listing (≤20 pages), `get_runner` and `delete_runner_if_idle`. Rate limits are retried at most 3 attempts inside 60 s; a wait that does not fit fails fast. Errors carry only op/status/code, never response bodies.
- `desktop.py`: `launch` returns ACCEPTED/REPLAYED/CAPACITY/CONFLICT/REJECTED/WAKE_FAILED. Per-request timeouts are clamped to the remaining budget. Also `inventory`, `find` and `stop`.
- `controller.py`: one worker serializes `Scaler.handle`. Effects run as tracked tasks whose results come back as events. The JIT lives only in `_jits` for the duration of a launch. Every publish passes the known secrets (JITs, GitHub tokens/JWTs, webhook secret, desktop token) to the redactor.
- `app.py`: the routes listed above. Delivery IDs are committed only after admission succeeds, so a 503 redelivery is processed. The agent callback is bearer-checked first and capped at 4 KiB.
- `__main__.py`: `controller` (default 127.0.0.1:8788) and `agent` (0.0.0.0:8787). Settings errors exit 2 naming the variable, never a value.

## Verification (actual runs, 2026-10-23 AEDT)
- `pytest omega_prime/tests/ci_runners` → 186 passed.
- `coverage run --branch` → package total 99% (scaler 99%, controller 96%, github 98%, desktop 99%, app 99%).
- `ruff check` + `ruff format --check` → clean; `mypy omega_prime/ci_runners omega_prime/tests/ci_runners` → no issues in 30 files.
- `polyglot-check --changed` → `polyglot-check: 0 error(s), 0 warning(s) in 14/14 file(s) checked [pyright]`. The first daemon run was stale and reported unresolved new modules. A `--no-daemon` run found 2 real errors (header typing in `http._lower`, and the signature of the test's `log_message` override); both were fixed at source.
- `env_inventory --check` → clean (no new env reads; the CLI passes the env mapping to settings).
- Full suite: 5881 passed, 17 failed. These are the same 17 environmental failures (`test_prime_kernel_*`, `test_grokbot_oneclick`) recorded in 120-01.

## Deviations
- New `http.py` + `test_http.py` (the shared transport seam, not named in the plan's file list).
- Test fakes live in new `_fakes.py`/`_rig.py`; the 120-01 `conftest.py` is unmodified, as the plan required.
- `python -m omega_prime.ci_runners agent` exits 2 ("the runner agent is not part of this build") until 120-03 adds `agent/app.py`.

## Gaps / holds
- D-120-08 (docker jobs stay on VPS) and D-120-10 (paid Railway cutover + VPS install) are not code in this plan; D-120-10 remains **HELD for Ove**.
- Known limitations (event-driven by design): after a rejected launch, remaining demand waits for the next signal; an agent 429 holds capacity until an exit or completion.
