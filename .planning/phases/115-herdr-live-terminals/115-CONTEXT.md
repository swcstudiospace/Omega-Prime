# Phase 115: Herdr live terminals + websocket Command Center — Context

**Gathered:** 2026-10-21
**Status:** Planning complete; four plans staged (two waves), none implemented.
**Branch:** `feat/herdr-ws` (on `4016ef8` herdr-default-launch, per ROADMAP Phase 115).
**Task:** 00035 (graph `ut-mvi8h35-5d2a91c7`; see <tracker>).
**Contract:** `.planning/phases/115-herdr-live-terminals/115-UPLIFT.xml` — byte-identical to `/sandbox/home/refs/uplift.xml` (verified with `diff -q`). Companions: `/sandbox/home/refs/got.json` (== `115-GOT.json`), `/sandbox/home/refs/herdr-api.json`, `/sandbox/home/refs/README-REFS.md`.

<goal>
## Goal

Interactive per-task Herdr terminal (xterm.js over a token-gated loopback websocket, read-only switch), live `/ws/events` typed deltas replacing snapshot polling with backoff + SSE fallback, and tasks.json kept current through heartbeats, a stale detector and a reconciler CLI — all Herdr I/O behind injectable runners with fakes in tests, no live Herdr in the sandbox.
</goal>

<locked_decisions>
## Locked decisions from the uplift — every default auto-accepted

Ove ran `/gsd-autonomous` for this task; every scope item, constraint, assumption and clarification below was auto-accepted and is locked. Executors implement, they do not reopen these.

### SCOPE (auto-accepted)

- **INTERACTIVE_TERMINAL** *(auto-accepted)*: each task detail page embeds an interactive terminal (bundled `@xterm/xterm`, no CDN) attached to the task's Herdr pane over WebSocket `/ws/tasks/{task_id}/terminal`. Server streams ANSI frames of the pane's visible screen on `pane_output_changed` (or a bounded poll fallback) and forwards keystrokes to `pane.send_input` (text) / `send_keys` (named keys). Read-only: server flag `--terminal-readonly` forces every connection read-only; a client may attach read-only; read-only connections never forward input.
- **ATTACH_AUTH** *(auto-accepted)*: per-server-session random token (`secrets.token_urlsafe(32)`) minted at startup, in memory only, served to the same-origin page via `GET /api/session` (no-store; loopback Host check plus `Sec-Fetch-Site: same-origin|none` when present). WebSocket handshakes carry the token (first message), an Origin of `http://127.0.0.1:PORT` or `http://localhost:PORT`, and a loopback Host; otherwise close 4401/4403. Never log the token.
- **LIVE_EVENTS** *(auto-accepted)*: WebSocket `/ws/events` sends `{type:"snapshot"}` on connect, then typed deltas from diffing successive snapshots — `task_changed`, `task_finished`, `waiting_added`, `waiting_cleared`, `stale_changed` — plus a heartbeat every 15 s. A tasks.json mtime watcher (stat poll 0.5 s, no inotify) wakes the poller so updates land in under ~1 s. Frontend `useDashboard` prefers `/ws/events` with exponential backoff reconnect (1 s doubling to 30 s, full jitter), falls back to `/api/stream` SSE after 3 consecutive failures, retries the websocket later. `/api/snapshot`, `/api/stream` and the SSE terminal keep working.
- **SOURCE_OF_TRUTH** *(auto-accepted)*: `status_file.heartbeat(path, task_id, *, state=None, note=None, pid=None)` writes `last_heartbeat_at` (+ optional pid) through the locked helpers; validation accepts optional `last_heartbeat_at` (ISO string) and `pid` (int) and keeps rejecting junk. `stale.py` computes per-task staleness for working/blocked rows: `pid_dead`, `pane_gone`, `pane_exited`, `no_heartbeat` (no heartbeat or activity for N minutes, default 20, `--stale-minutes`). The snapshot carries `stale {reason, since}` per task; the UI shows a STALE badge with the reason. `reconcile.py` CLI rebuilds liveness from `herdr pane list` + `pane process-info` and `/proc`: reports rows whose pane/pid is dead, attaches orphan panes whose workspace label matches a task id, dry-run by default, `--apply` writes through `locked_tasks` + `_write_locked`, never deletes rows, and never touches rows updated in the last 15 minutes by another writer unless `--force`.

### CONSTRAINTS (auto-accepted)

1. *(auto-accepted)* Do not invent repository facts: read the code before changing it.
2. *(auto-accepted)* Server binds loopback only; websocket routes enforce the same Host allow-list as `_DashboardGuards` plus an explicit Origin allow-list (browsers do not apply CORS to websockets).
3. *(auto-accepted)* Keep `_DashboardGuards` GET-only for HTTP; websockets are the only write path and only with the token.
4. *(auto-accepted)* Redact frames with the existing terminal/redact rules adapted for ANSI (redact visible text, keep escape sequences intact).
5. *(auto-accepted)* No network in tests; Herdr, gh, podman are faked. Tests use Starlette TestClient websocket support; served checks only on `127.0.0.1:7441`, never 7421.
6. *(auto-accepted)* Never weaken, skip or delete existing tests. Never print or commit secrets or credentialed argv. Commit titles `00035 - ...`, atomic per task. No push, no PR from inside the sandbox.

### SECURITY_AND_VALIDATION (auto-accepted)

- Token compare with `secrets.compare_digest`; cap terminal websockets (4) and event websockets (16); input frames max 4 KiB, rate-limited; task ids validated like the SSE route; pane ids must match `^w[0-9A-Za-z]+:p[0-9A-Za-z]+$` and must come from the task row, never from the client.

### GRACEFUL_DEGRADATION (auto-accepted)

- No `herdr_pane` → terminal shows "no session" with the task's last note; pane closed → status `pane_closed` and stop; herdr unavailable → status `unavailable` with retry; websocket blocked → SSE fallback; tasks.json invalid → existing source-health error path.

### ASSUMPTIONS (defaults, auto-accepted)

- Token via same-origin `/api/session` *(auto-accepted)*.
- Read-only default off *(auto-accepted)*.
- Stale N = 20 min *(auto-accepted)*.
- Poll fallback 1 s *(auto-accepted)*.
- Event wake stat-poll 0.5 s *(auto-accepted)*.

### CLARIFICATIONS (auto-accepted, non-blocking)

- **c1** *(auto-accepted)*: interactive mirror via `pane.read` visible ANSI + `send_input` (Herdr has no raw PTY attach over its API); xterm.js renders full-screen frames.
- **c2** *(auto-accepted)*: the engine does not drive omp-sandbox yet (sbx-2..5 unmerged), so migrated code-building tasks use the drive.py pattern.

### OUT_OF_SCOPE (auto-accepted)

Swapping the live 7421 server (needs Ove), deploys, VPS/Railway changes, merging, wiring the engine to omp-sandbox (task 00030).
</locked_decisions>

<protocol_defaults>
## Cross-plan interface defaults (auto-accepted this plan stage)

These make the four plans composable. Each is the boring/safe reading of the uplift; planners and executors treat them as locked.

1. **Staleness shape**: `stale` lives INSIDE each active-task dict in the snapshot — `"stale": {"reason": "...", "since": "<ISO-8601>" | null} | null` — never as a new top-level snapshot key (`test_dashboard_snapshot_contract.py` and `test_dashboard_app.py` lock the top-level key set). Reasons: `pid_dead | pane_gone | pane_exited | no_heartbeat`. Evaluated only for tasks.json rows whose state is `working` or `blocked`; null otherwise (fresh rows, queued/waiting/done/failed). `since` is STABLE ACROSS REBUILDS: the row's newest liveness reference timestamp (`last_heartbeat_at`/`last_activity_at`), null when the row carries none — never the current time — so unchanged evidence produces byte-identical snapshots and the poller digest (`poller.py:215-232`) does not fire `_changed` on every collector cycle (SSE heartbeats stay 15 s; `/ws/events` deltas stay quiet). Persisted `stale` markers in tasks.json (reconcile `--apply`) always carry an ISO `since` (reference timestamp, falling back to the run's `now` only when the row has none).
2. **status_file validation**: task rows accept optional `last_heartbeat_at` (parseable ISO-8601 string), `pid` (positive int; bool rejected), and tolerate a written `stale` object `{reason: str, since: ISO str}`; junk of any of these is still rejected. `_write_locked`/`_validated` round-trip all three.
3. **heartbeat()**: `heartbeat(path, task_id, *, state=None, note=None, pid=None, now=None)` — `locked_tasks(path, write=True)`, locate the row by exact `id`, set `last_heartbeat_at` (injectable clock, default `datetime.now(...)` ISO), optional `state`/`note`/`pid` updates, refresh root `updated_at`, write via `_write_locked`. Unknown task id raises; nothing is logged.
4. **reconcile CLI**: `python3 -m omega_prime.dashboard.reconcile [--tasks PATH] [--apply] [--force] [--stale-minutes N]` — dry-run by default, prints a redacted JSON report; `--apply` writes only through `locked_tasks` + `_write_locked`; never deletes rows; skips rows protected by root `updated_at` younger than 15 min unless `--force`; Herdr CLI access behind an injectable runner (fakes in tests), like `herdr_launch._run`/`terminal.runner`.
5. **Terminal WS protocol**: path `/ws/tasks/{task_id}/terminal`. Client's first message must be `{"type":"auth","token":"...","mode":"rw"|"ro"}` within 5 s. Server close codes: 4401 missing/wrong token or auth timeout, 4403 bad Host/Origin. Input frames `{"type":"input","text":...}` and `{"type":"keys","keys":[...]}`, each ≤ 4 KiB, rate-limited, silently dropped when the connection is read-only (server flag or client mode). Server frames `{"type":"frame","text":"<ANSI>","updated_at":...}` (visible-screen frame, sent only when the frame content changes) and `{"type":"status","status":"no_session"|"pane_closed"|"unavailable"}`. Cap 4 concurrent terminal websockets; poll fallback 0.25–1 s.
6. **WebSocket gating**: `_DashboardGuards` stays GET-only for HTTP; for `scope["type"] == "websocket"` it admits ONLY the registered `/ws/` routes, with Host in the existing allow-list and an Origin header REQUIRED and in `{http://127.0.0.1:PORT, http://localhost:PORT}`; anything else is closed 4403 before any application logic. Non-browser test clients set the Origin header explicitly.
7. **Session endpoint**: `GET /api/session` → `{"token": "...", "readonly": bool}` with `Cache-Control: no-store`; token minted with `secrets.token_urlsafe(32)` in `create_app` (injectable for tests); compared with `secrets.compare_digest`; token value never logged, never in tasks.json, never in a cookie.
8. **CSP**: `connect-src 'self' ws://127.0.0.1:{port} ws://localhost:{port}` — assembled per-app from the `port` given to `create_app`; the rest of `_SECURITY_HEADERS` is unchanged (existing substring assertions keep passing).
9. **Events WS protocol**: `/ws/events`; first message `{"type":"snapshot","snapshot":{...full snapshot...}}`; deltas `{"type":"task_changed","task":{...}}`, `{"type":"task_finished","task":{...}}`, `{"type":"waiting_added","item":{...}}`, `{"type":"waiting_cleared","id":"..."}`, `{"type":"stale_changed","id":"...","stale":{...}|null}`; `{"type":"heartbeat"}` every 15 s; cap 16 concurrent connections; same Host/Origin checks; no token (read-only events).
10. **Poller wake**: a public `DashboardPoller` method (e.g. `nudge(source)`) makes the `tasks_json` collector due immediately; a stat-poll watcher (0.5 s interval, injectable sleep, lives in `events.py`) is started from the app lifespan with the tasks.json path, which `__main__.py` already builds. Update landing target: < 1 s.
11. **Frontend**: `@xterm/xterm` pinned EXACT (no caret; confirm the current version against registry.npmjs.org during execution — expected 5.5.x); WS URLs derive from `location.host`; `useDashboard` keeps the existing SSE + poll fallback chain below the new websocket tier; the SSE text terminal stays inside `TerminalPanel` as the fallback view when the websocket fails.
12. **Ports and secrets in tests/docs**: never port 7421 for new servers in tests or doc examples; served checks use `127.0.0.1:7441`. Existing tests that already send `host: 127.0.0.1:7421` headers are untouched. No token/secret values in logs, tests output, or commits.
</protocol_defaults>

<acceptance>
## Acceptance criteria (a1–a7, from the uplift) → plan mapping

- **a1** (typing into a task's terminal on `127.0.0.1:7441` reaches the Herdr pane; fake in tests: `send_input` called with exact text/keys) → **115-02**.
- **a2** (without the token, with a foreign Origin, or with a non-loopback Host the websocket is refused) → **115-02**.
- **a3** (read-only flag/mode never forwards input) → **115-02**.
- **a4** (`/ws/events` delivers snapshot then a `task_finished` delta within 2 s of a tasks.json write; temp file, fast poll) → **115-03**.
- **a5** (stale detector flags dead pid, gone pane and no-heartbeat cases, leaves fresh rows alone) → **115-01**.
- **a6** (reconciler dry-run reports, `--apply` writes via locked helpers, never deletes rows) → **115-01**.
- **a7** (frontend tests cover backoff/fallback and the terminal panel; `static/cc` rebuilt and committed; existing dashboard tests still pass) → **115-04**.
</acceptance>

<waves>
## Waves and file ownership (file-disjoint inside each wave)

| Plan | Wave | Depends on | Owns (implementation + tests) |
|---|---|---|---|
| 115-01 source of truth | 1 | — | `omega_prime/dashboard/status_file.py`, new `omega_prime/dashboard/stale.py`, new `omega_prime/dashboard/reconcile.py`, `omega_prime/dashboard/model.py`, `omega_prime/dashboard/poller.py`, new `omega_prime/tests/test_dashboard_heartbeat.py`, `test_dashboard_stale.py`, `test_dashboard_reconcile.py` |
| 115-02 terminal websocket | 1 | — | new `omega_prime/dashboard/terminal_ws.py`, `omega_prime/dashboard/app.py`, `omega_prime/dashboard/__main__.py`, new `omega_prime/tests/test_dashboard_terminal_ws.py` |
| 115-03 live events | 2 | 115-01 | new `omega_prime/dashboard/events.py`, `omega_prime/dashboard/app.py` (`/ws/events` route + watcher wiring), `omega_prime/dashboard/poller.py` (wake), `omega_prime/dashboard/__main__.py` (tasks path into app), new `omega_prime/tests/test_dashboard_events_ws.py` |
| 115-04 frontend | 2 | 115-02, 115-03 | `omega_prime/dashboard/frontend/**` (`package.json`, `package-lock.json`, `src/…`), rebuilt `omega_prime/dashboard/static/cc/**` |

Wave-1 plans share no files; wave-2 plans share no files. Cross-wave overlaps (`app.py`, `poller.py`, `__main__.py`) are sequential by dependency, never concurrent. `115-02` must land its route additions in `app.py` without depending on 115-01's model changes (it resolves panes from snapshot rows exactly like the SSE terminal route does today).
</waves>

<files_read>
## Exact files read this planning session

Contract refs: `/sandbox/home/refs/uplift.xml`, `/sandbox/home/refs/got.json`, `/sandbox/home/refs/herdr-api.json`, `/sandbox/home/refs/README-REFS.md`.
Phase artifacts: `.planning/phases/115-herdr-live-terminals/115-UPLIFT.xml` (diff-verified identical), `115-GOT.json`, `115-TRACKING.md`, `.planning/ROADMAP.md` (Phase 115 entry), `omega_prime/ownership.yaml`.
Backend: `omega_prime/dashboard/app.py`, `status_file.py`, `poller.py`, `terminal.py`, `redact.py`, `model.py`, `__main__.py`, `config.py`, `herdr_launch.py`, `collectors/tasks_json.py`.
Tests: `omega_prime/tests/test_dashboard_terminal_api.py` (full), `test_dashboard_app.py` (guards/headers sections), `test_dashboard_snapshot_contract.py` (assertions), `test_dashboard_app_fixes.py` / `test_dashboard_live_fixes.py` (grep for `create_app` locks).
Frontend: `frontend/package.json`, `vite.config.ts`, `src/types.ts`, `src/hooks/useDashboard.ts` (+ test grep), `src/components/TerminalPanel.tsx`, `src/components/TerminalPanel.css` (listing only), `src/components/TaskDetail.tsx` (grep), `src/test/setup.ts`, `src/test/build-assets.test.ts`, `useDashboard.test.tsx` (grep).
Format precedent: `.planning/phases/97-substrate-direct-connect/97-CONTEXT.md`, `97-01-PLAN.md`.
No `omega_prime/dashboard/README*` or `docs/` exists — 115-04's "update dashboard docs/README if present" resolves to a no-op unless one is created before execution.
</files_read>

<existing_tests>
## Existing tests that must keep passing (never weakened, skipped or deleted)

All 25 files in `omega_prime/tests/test_dashboard_*.py`: `test_dashboard_app.py`, `test_dashboard_app_fixes.py`, `test_dashboard_command_center_static.py`, `test_dashboard_file_collectors.py`, `test_dashboard_github_cache.py`, `test_dashboard_github_command_center.py`, `test_dashboard_github_greptile_polish.py`, `test_dashboard_github_merges_polish.py`, `test_dashboard_herdr_launch.py`, `test_dashboard_integration.py`, `test_dashboard_jobs_model_fixes.py`, `test_dashboard_live_fixes.py`, `test_dashboard_model.py`, `test_dashboard_model_buckets.py`, `test_dashboard_poller_fixes.py`, `test_dashboard_process_collectors.py`, `test_dashboard_redaction.py`, `test_dashboard_remote_collectors.py`, `test_dashboard_sessions_fixes.py`, `test_dashboard_snapshot_contract.py`, `test_dashboard_task_ids.py`, `test_dashboard_task_ids_cli.py`, `test_dashboard_terminal.py`, `test_dashboard_terminal_api.py`, `test_dashboard_wiring_fixes.py`. Plus the frontend suites under `frontend/src/**/*.test.tsx` and `CC_CHECK_BUILD=1 npx vitest run` after the 115-04 rebuild.

Hard locks planners must design around:

- `test_dashboard_snapshot_contract.py` / `test_dashboard_app.py::test_static_snapshot_and_health_headers`: top-level snapshot keys are exactly `{generated_at, sources, active, pr_gate, waiting_on_ove, finished, idle_sessions}` — hence decision 1 (`stale` inside task rows). CSP assertions are substrings (`default-src 'self'`, `font-src 'self'`) — adding `ws://` sources to `connect-src` is safe.
- `test_dashboard_app.py::test_method_and_host_guards`: every HTTP method ≠ GET on any path is 405; wrong Host 403. Websocket scopes must not loosen this.
- `test_dashboard_terminal_api.py::test_terminal_has_no_input_route_or_subcommand`: no route path may contain `send` or `input` — `/ws/tasks/{task_id}/terminal` passes; do not name input-related routes differently.
- `test_dashboard_app_fixes.py:144`: `create_app(poller, port=7421, host="::1")` — `create_app` keeps `poller` positional and all new parameters keyword-only with defaults.
- `test_dashboard_command_center_static.py` + `frontend/src/test/build-assets.test.ts`: the committed `static/cc` bundle must stay consistent (hashed local assets, 4 woff2 fonts, no external runtime refs, no source maps) after 115-04 rebuilds it.
</existing_tests>

<source_map>
## Source map (symbols verified on `feat/herdr-ws` @ `f5cf797`)

- `omega_prime/dashboard/app.py` — `_SECURITY_HEADERS` `:24-28` (CSP `connect-src 'self'`); `_SSEResponse` `:31-141` (raw-ASGI SSE; disconnect handling); `_DashboardGuards` `:144-207` (GET-only; `allowed_hosts = {127.0.0.1:port, localhost:port, host:port}`; non-http scopes pass straight through `:161-164` — today websockets bypass the guard entirely); `create_app(poller, *, port=7421, host="127.0.0.1", heartbeat_s=15.0, terminal_service=None)` `:222-393`; routes `:377-391` incl. `/api/tasks/{task_id:path}/terminal` `:327-349` (task lookup across `active`/`idle_sessions`/`finished`, `resolve_task_pane`, 429 on no slot); `/api/stream` `:352-375` (SSE snapshot + `poller.wait_for_update(heartbeat_s)`, 15 s heartbeats); lifespan starts/stops poller `:236-242`.
- `omega_prime/dashboard/status_file.py` — `_STATES` `:22`, `_PANE` `:24`; `_validated` `:105-223` (task rows: id/title/owner/state/started_at/last_activity_at/progress/note/links/herdr_pane/seq — no heartbeat/pid/stale today); `locked_tasks(path, *, write)` `:226-253` (flock + `_validated` on read); `_write_locked` `:256-276` (re-validates + redacts, atomic replace); `write_tasks` `:279-312`; `read_tasks` `:315-334` → `TasksFileResult(status, data, error, age_s)`.
- `omega_prime/dashboard/poller.py` — `CollectorSpec(source, fn, interval_s, timeout_s)` `:32-38`; `DashboardPoller(specs, *, quiet_minutes=15.0, window_hours=24.0, tick_s=0.25, clock=None, task_ids_path=None)` `:45-69`; `run_forever` `:89-102` (tick 0.25 s scheduling via `_next_due`); `_build_snapshot` `:177-232` (`normalize_tasks` → `number_snapshot` → `redact_value` → sha256 content hash; sets `_changed`); `snapshot()`/`snapshot_hash()`/`sources_status()`; `wait_for_update(timeout)` `:246-256`; `refresh_now()` `:258-263`.
- `omega_prime/dashboard/terminal.py` — `_ANSI` regex `:24`; secret regexes `:25-28`; `resolve_task_pane` `:31-39` (dict `herdr_pane.pane_id` or owner kind `herdr-pane`); `sanitize_terminal_text` `:42-57` (strips ANSI then redacts — the pattern 115-02 adapts to ANSI-preserving); `PaneClosedError` `:60`; `_read_pane` argv runner `:64-99` (`herdr pane read <pane> --source recent --lines 200 --format text`); `TerminalService(runner=, clock=, sleep=)` `:102-157` with `asyncio.Semaphore(4)` `reserve()`, SSE `stream()`, `_event` JSON framing.
- `omega_prime/dashboard/redact.py` — `redact_value` recursively drops/redacts denied keys (`token, key, secret, password, pat, auth, cookie, bearer` substring match) and env-named secrets; `redact_line`. Consequence: never name new fields with those substrings; `stale`, `reason`, `since`, `pid`, `last_heartbeat_at` are safe.
- `omega_prime/dashboard/model.py` — `Task` dataclass `:78-118` (has `last_activity_at`, `herdr_pane`; `to_dict` redacted); `FinishedItem` `:165-198`; `Snapshot` `:204-…`; `normalize_tasks` `:247-…`; tasks.json rows → active `Task`s at `:638-690` (state map queued→idle, working→working, blocked→blocked…; done/failed → `FinishedItem`); waiting/finished extras `:691-712`.
- `omega_prime/dashboard/collectors/tasks_json.py` — mtime_ns+size cache `:20-56`; `extra={"tasks","waiting_on_ove","finished","identity_registry","next_seq","age_s"}`; source status `missing`/`unreadable`/`ok`.
- `omega_prime/dashboard/__main__.py` — `build_default_collectors` `:38-133` (`tasks_json` spec at LOCAL_SOURCES=3.0 s, `tasks_path=home/.omega-prime/dashboard/tasks.json` `:103-108`); `main()` `:176-315`: subcommands `next-id|assign-id|assign-ids`, flags `--host` (default 127.0.0.1, `_loopback` enforced), `--port` (default env `OMEGA_PRIME_DASHBOARD_PORT` or 7421), `--once`, `--stop`; `build_poller` passes `task_ids_path=tasks_path`; `create_app(poller, port=args.port, host=args.host)` `:293`; `_serve` uvicorn `:169-173`.
- `omega_prime/dashboard/herdr_launch.py` — `workspace_label(identity)` `:41-42` (lowercase `[a-z0-9_-]` ≤32 — the label reconciler matches orphan panes by); `record_pane`/`launch`/CLI `main` `:45-148`; sync `_run` runner pattern + `_write_locked`/`locked_tasks` usage.
- `omega_prime/dashboard/config.py` — `DashboardConfig(window_hours, quiet_minutes, fleet_enabled)` from `~/.omega-prime/settings.json` allowlist.
- Frontend — `useDashboard.ts`: `POLL_MS=5000`, `RECONNECT_MS=30000`; fetch `/api/snapshot` → EventSource `/api/stream` (`snapshot` event) → poll fallback; `live` flag; `refresh()`. `TerminalPanel.tsx`: SSE `terminal` event, statuses `live|no_session|pane_closed|unavailable`, keyed remount per task, copy/pause actions. `types.ts`: `Task`, `HerdrPane {pane_id, workspace?}`, `Snapshot` (add `stale?: {reason: string, since: string} | null` to `Task` in 115-04). `TaskDetail.tsx` `:39-44`: `state-pill`, detail grid, `<TerminalPanel taskId={taskId}/>`. `vite.config.ts`: base `/cc/`, `outDir ../static/cc` `emptyOutDir`, vitest jsdom + `src/test/setup.ts`. `package.json`: exact pins, no carets; scripts `test`/`build` (`tsc -b && vite build`).
- Herdr API (refs): `pane.read {pane_id, source: visible|recent, format: text|ansi, strip_ansi, lines}`; `pane.send_input {pane_id, text?, keys?}`; `pane.send_keys {pane_id, keys}`; `pane.send_text {pane_id, text}`; `events.subscribe {subscriptions:[{type: pane_output_changed|pane_exited|pane_closed, pane_id?}]}`; CLI: `herdr pane read <pane> --source visible --format ansi`, `herdr pane send-text <pane> <text>`, `herdr pane send-keys <pane> <key>...`, `herdr pane list`, `herdr pane process-info --pane <pane>`; JSON `{"id":..,"result":{...}}` on stdout, errors JSON on stderr exit 1. Herdr 0.9.5 runs on the host only — inside this sandbox it is faked everywhere.
</source_map>

<pitfalls>
## Pitfalls for planners and executors

- The SSE endpoints cannot be asserted through buffered TestClient bodies — existing SSE tests drive raw ASGI (`_wire` in `test_dashboard_terminal_api.py`, `drive()` in `test_dashboard_app.py`). Websocket tests DO use `TestClient.websocket_connect` and must send the `origin` header explicitly (decision 6).
- `_DashboardGuards` currently forwards every non-http scope untouched (`app.py:161-164`); wave-1 must add websocket admission there (or in a dedicated websocket guard wrapper) without changing HTTP behavior — `test_method_and_host_guards` pins 405/403.
- Every write through `locked_tasks` is re-validated by `_write_locked` → `_validated`; the heartbeat/reconcile writers must emit only fields `_validated` accepts (decision 2 lands first inside 115-01).
- `redact_value` drops keys containing `token|key|secret|password|pat|auth|cookie|bearer` — never name new persisted/snapshot fields with those substrings.
- `tasks_json` collector caches on `(st_mtime_ns, st_size)`; the wake path relies on a real stat change — tests that rewrite tasks.json must change content (size/mtime), and fast-poll tests inject small intervals rather than sleeping long.
- `test_terminal_has_no_input_route_or_subcommand` forbids `send`/`input` substrings in ANY route path — name websocket routes exactly `/ws/tasks/{task_id}/terminal` and `/ws/events`.
- The attach token is a secret: never in logs, tasks.json, test output, commit messages, or error strings; compare with `secrets.compare_digest`; tests may assert on a FIXED injected token, never print a minted one.
- `static/cc` is committed output: 115-04 must rebuild (`npm run build`) and commit the new hashed assets; `node_modules/`, `tsconfig.tsbuildinfo`, and caches are never committed (they are not gitignored — add paths explicitly, never `git add -A` in `frontend/`).
- Never use port 7421 for new servers in tests or doc examples; served smoke checks bind `127.0.0.1:7441`.
- `create_app` default port stays 7421 (live server compatibility) — only tests/docs avoid it for NEW servers.
- Starlette `WebSocketRoute` + `TestClient.websocket_connect`: rejection before `accept` surfaces as `WebSocketDisconnect`; 4401/4403 must be sent as websocket close codes (accept-then-close with code, or `websocket.close` before accept per Starlette version — pin behavior in tests via the disconnect code).
- `heartbeat()` and reconcile writes must not log credentialed argv; the injectable Herdr runner receives argv lists but tests use fakes only.
</pitfalls>

<out_of_scope>
## Out of scope (auto-accepted)

Swapping the live 7421 server (needs Ove), deploys, VPS/Railway changes, merging, wiring the engine to omp-sandbox (task 00030). Planning stage adds no product code; the four PLAN files and this context are the only new artifacts, plus the receipt.
</out_of_scope>

<tracker>
## Tracker

Task 00035 — Notion `https://www.notion.so/400bc1a0c7ae81919484d2057452d58d`; Linear issues SPE-18415..SPE-18420 (nodes n1–n6), sub-issues SPE-18421..SPE-18449; dashboard row `gb:00035-herdr-ws`. Full detail in `115-TRACKING.md`. Commits cite `Refs: 00035` plus the Linear id of the node they implement.
</tracker>

<baseline>
## Baseline (this planning session)

- `python3 -m pytest -q -p no:cacheprovider omega_prime/tests/test_dashboard_terminal_api.py` → 9 passed, exit 0 (validates the prescribed verify-command shape).
- `diff -q /sandbox/home/refs/uplift.xml .planning/phases/115-herdr-live-terminals/115-UPLIFT.xml` → identical, exit 0.
- `git status --porcelain -b` → clean on `feat/herdr-ws` @ `f5cf797`.
- Full dashboard suite and frontend suite are execution-time gates (see each PLAN's `<verify>`); not run at plan stage.
</baseline>

<no_implementation>
## No implementation claim

This file records planning only. No runtime source, tests, docs, or live settings changed; no gates beyond the baseline above were run; no Herdr, gh, or podman binary was contacted (unavailable in-sandbox by design). Implementation starts only in the execute stage for Phase 115.
</no_implementation>
