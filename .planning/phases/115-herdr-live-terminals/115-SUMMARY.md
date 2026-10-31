# Phase 115 Summary — Herdr live terminals + websocket Command Center

**Task:** 00035 · **Branch:** `feat/herdr-ws` · **Plans:** 115-01, 115-02 (wave 1), 115-03, 115-04 (wave 2) — all executed locally, one atomic commit per task, no push/PR.
**Contract:** `115-UPLIFT.xml` (byte-identical to `/sandbox/home/refs/uplift.xml`), `115-CONTEXT.md` protocol defaults 1–12.

## Delivered

- **115-01 — source of truth (wave 1):** `status_file` validates and round-trips `last_heartbeat_at`/`pid`/`stale`; `heartbeat()` is the single locked liveness write path; `stale.py` computes `pid_dead`/`pane_gone`/`pane_exited`/`no_heartbeat` (20 min default, injectable clock/pid liveness/pane lister) with a stable `since`; the poller applies `stale` inside each active task row (protocol default 1) without touching `normalize_tasks`; `reconcile.py` is a dry-run-by-default CLI (`--apply` writes only through `locked_tasks` + `_write_locked`, never deletes rows, honors the 15-minute writer-protection window and `--force`). Details: `115-01-SUMMARY.md`.
- **115-02 — token-gated terminal websocket (wave 1):** `terminal_ws.py` with `redact_ansi_frame` (visible-text redaction, escape runs verbatim) and `HerdrBridge` (injectable async runner: `herdr pane read --source visible --format ansi`, `send-text`, `send-keys`); `/ws/tasks/{task_id}/terminal` enforces auth-first `{"type":"auth","token","mode"}` (4401), task-id validation like the SSE route (4404), a 4-connection cap (4429), read-only enforcement at server flag, session mode and CLI flag; pane ids come only from the task row. `app.py` mints one per-session token (`secrets.token_urlsafe(32)`, injectable for tests), serves it via same-origin `GET /api/session` (no-store), and `_DashboardGuards` now admits only `/ws/` routes with an allowed loopback Host plus a required allowed Origin, closing 4403 pre-accept; CSP `connect-src` gains the two ws:// sources. Details: `115-02-SUMMARY.md`.
- **115-03 — live events websocket (wave 2):** `events.py` computes the five locked delta types between successive snapshots (`compute_deltas`, pure, deterministic order) and serves `/ws/events` (snapshot first, 15 s heartbeat, 16-connection cap closing 4429 pre-accept, no token, same Host+Origin admission). `DashboardPoller.nudge(source)` plus the `TasksFileWatcher` stat-poll (0.5 s, injectable) wake the poller from the app lifespan so tasks.json writes land as deltas in under ~1 s; `__main__.py` threads its tasks path into `create_app`. Details: `115-03-SUMMARY.md`.
- **115-04 — frontend (wave 2):** `useDashboard` prefers `/ws/events` (snapshot + immutable typed deltas, heartbeat ignored, cannot-apply → `/api/snapshot`) with 1 s→30 s full-jitter backoff and SSE fallback after three failures; `TerminalPanel` is an interactive bundled-xterm terminal (`@xterm/xterm@5.5.0` exact pin, no CDN) over the token-gated websocket with attach/detach, read-only gating (toggle + session flag), status badges and the byte-identical SSE text view as the degraded path (with a 30 s websocket retry); Tasks rows and the TaskDetail heading show STALE badges with the reason; `static/cc` rebuilt and committed. Details: `115-04-SUMMARY.md`.

## Acceptance criteria → evidence

| AC | Claim | Evidence (all local, fakes for Herdr; no network in tests) |
|---|---|---|
| **a1** | Typing into a task's terminal on 127.0.0.1:7441 reaches the Herdr pane (fake in tests: exact text/keys argv) | `test_dashboard_terminal_ws.py` rw end-to-end asserts runner argv `["herdr","pane","send-text","w2:p4","make check\n"]` and `["herdr","pane","send-keys","w2:p4","enter","c-c"]` (115-02). Frontend: `TerminalPanel.test.tsx` proves xterm `onData` becomes exactly `{"type":"input","text":"ls"}` / `{"type":"keys","keys":["enter"]}` on the socket (115-04). |
| **a2** | Without the token, with a foreign Origin, or with a non-loopback Host the websocket is refused | 4401 auth tests (missing/wrong token, timeout) and 4403 guard tests through `create_app` (115-02); `/ws/events` 4403 tests for foreign/missing Origin and bad Host with a localhost-origin success (115-03). |
| **a3** | Read-only flag/mode never forwards input | Client-mode `ro`, server-flag and CLI-flag tests keep frames flowing while send argv never appears (115-02); frontend `TerminalPanel.test.tsx` proves zero sends while the toggle or session readonly is active (115-04). |
| **a4** | `/ws/events` delivers snapshot then a task_finished delta within 2 s of a tasks.json write | `test_dashboard_events_ws.py`: collector-driven proof (elapsed < 2.0 s asserted) and the discriminating watcher proof (30 s collector interval + 0.05 s watcher, delta < 2 s — only watcher→nudge can deliver) (115-03). |
| **a5** | Stale detector flags dead pid, gone/exited pane, no-heartbeat; fresh rows alone | `test_dashboard_stale.py` (precedence, inclusive boundary, suppression when the pane lister is unavailable, stable `since`) with poller integration in `test_dashboard_poller_fixes.py`/`test_dashboard_snapshot_contract.py` (115-01). |
| **a6** | Reconciler dry-run reports, `--apply` writes via locked helpers, never deletes rows | `test_dashboard_reconcile.py` (8 tests: dry-run report, apply through locked helpers, orphan attach by workspace label, writer-protection window + `--force`, no deletions) (115-01). |
| **a7** | Frontend tests cover backoff/fallback and the terminal panel; static/cc rebuilt; existing dashboard tests still pass | 50 frontend tests incl. jittered-backoff/SSE-fallback (`useDashboard.test.tsx`) and the mocked xterm panel suite (`TerminalPanel.test.tsx`); `CC_CHECK_BUILD=1` bundle test + `test_dashboard_command_center_static.py` pass on the committed rebuilt bundle; all pre-existing python dashboard suites pass unchanged. |

## Verification (phase gate)

| Command | Result |
|---|---|
| `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_dashboard_*.py` | **275 passed, 1 skipped** (skip is the pre-existing setuptools marker). Stable across repeated runs. |
| `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/ -k dashboard` | Exit 2: 49 collection `ModuleNotFoundError` errors (apscheduler, mcp, …) in files outside this phase — **pre-existing**, reproduced identically on the pre-phase base `b8c8b6f` during wave 1 (recorded in the wave-1 receipt); dashboard-scoped glob is the equivalent used above. |
| `cd omega_prime/dashboard/frontend && npm ci --no-audit --no-fund && npx vitest run && npm run build` | install clean; **50 passed, 1 skipped**; build succeeds (~0.3 s). |
| `CC_CHECK_BUILD=1 npx vitest run src/test/build-assets.test.ts` | 1 passed (hashed assets, four woff2 fonts, licenses, no maps, no external refs). |
| `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_dashboard_command_center_static.py` | 5 passed. |
| `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_docs.py omega_prime/tests/test_public_repo.py` | 31 passed (AGENTS.md docs checks). |

## Diffs and constraints honored

Only the four plans' declared files changed; `normalize_tasks`' signature and call kwargs untouched; `/api/snapshot`, `/api/stream`, the GET-only HTTP guard (405/403) and the non-ws CSP are byte-identical to pre-phase state; no route path contains `send`/`input`; port 7421 appears only inside pre-existing tests (new servers and tests use 127.0.0.1:7441); the attach token is asserted only via fixed injected values or property checks and never printed or committed; no snapshot content, delta payload or credentialed argv is logged; `node_modules`, `tsconfig.tsbuildinfo` and caches are untracked and unstaged (explicit-path staging only).

## Blocked / remaining limits (none blocking)

- Live-host verification against a real `herdr` on the box (typing into a real pane, `pane_output_changed` behaviour, the 7421 server swap) is outer-driver work — Herdr is not available inside the sandbox by design; every Herdr touchpoint is behind injectable runners proven with fakes.
- The literal `-k dashboard` collection abort is pre-existing sandbox module incompleteness, not phase work; the dashboard-scoped suite is the faithful equivalent (275 passed).
- No push, no PR, no merge: commits exist locally on `feat/herdr-ws` for the outer driver to bundle.
