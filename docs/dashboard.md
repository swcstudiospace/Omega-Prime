# Omega Prime Task Dashboard

The dashboard is a local, read-only, loopback-only view of what Omega Prime is working on. It does not start automatically; the operator explicitly starts it.

## Start and stop

From this worktree, run:

```sh
cd /workspace/omes-bot-wt/dash
PYTHONPATH=/workspace/omes-bot-wt/dash /workspace/omes-bot/.venv/bin/python -m omega_prime.dashboard --host 127.0.0.1 --port 7421
```

Alternatively run `scripts/dashboard-start.sh`. The operator may run it under `nohup` or in a Herdr workspace named `dashboard` bound to `127.0.0.1:7421`; the script does not daemonize. Open `http://127.0.0.1:7421/` in the box desktop browser. `OMEGA_PRIME_DASHBOARD_PORT` sets the default port when using the module's default-port selection. `--once` prints one snapshot as JSON. `--stop` stops the process recorded at `~/.omega-prime/dashboard/dashboard.pid`.

## Security model

Only GET routes are supported; other methods receive 405. The server refuses `0.0.0.0`, `::`, and non-loopback bind addresses. An exact Host allowlist checks the expected loopback host and port to guard against DNS rebinding. Responses include a restrictive CSP (`default-src 'self'`) and `X-Content-Type-Options: nosniff`; there is no CORS policy. Redaction is applied at collector and output boundaries: denylist-keyed fields, recognized credential patterns, and named environment secrets are redacted. GitHub and fleet credentials resolve through the repository's `CredentialBroker`, with a single-host allowlist checking the destination. Tokens stay in memory only and are never logged. `[REDACTED]` is never truncated away.

The dashboard never reads `/proc/*/environ`, full process arguments beyond the `--mode` value, `auth-broker.token`, pane history, or screen contents. Fleet reads only the configured coordinator URL and broker-resolved token from `coordinator.env`. It does not issue remote writes or provide action buttons.

## API

- `GET /api/snapshot` returns `generated_at`, `sources`, `active`, `pr_gate`, `waiting_on_ove`, and `finished`.
- `GET /api/stream` emits an initial retry directive (`retry: 3000`), then a snapshot event whenever the content hash changes; it sends a heartbeat every 15 seconds without changes.
- `GET /healthz` returns a small liveness response.

The UI updates from the live snapshot; a tasks-file refresh does not require a page reload.

## Refresh and sources

Local sources refresh every 3 seconds; Herdr and Podman every 5 seconds; fleet every 30 seconds; GitHub every 60 seconds. Collectors have independent timeouts, so one slow source cannot stall the others.

| Source ID | Reads | Degradation status and example |
| --- | --- | --- |
| `omp_sessions` | Recent OMP session JSONL | `empty` when no recent sessions; `error` for unreadable data |
| `wiring` | Worktree/runs metadata, phase logs, and completion markers | `empty` when no recent run; `error` for malformed/unavailable input |
| `sandbox` | `PROGRESS.md`, E2E JSONL, and receipts | `empty` when no artifacts; `unavailable` when the root is absent |
| `omega_jobs` | Omega job `session.json` records | `empty` when no recent jobs; `unavailable` when the jobs directory is absent |
| `command_log` | `~/.omega-prime/command-log.jsonl` | `empty` with no records; `unavailable` when missing |
| `tasks_json` | `~/.omega-prime/dashboard/tasks.json` | `missing` until Omega Prime writes it; `unreadable` for invalid files |
| `agentos` | AgentOS routing summary and state-entry count | `unavailable` when its root is absent; `error` for invalid routing data |
| `procs` | Safe process identity fields from procfs | `empty` when no matching process; `error` if procfs cannot be read |
| `herdr` | Read-only pane snapshot command | `unavailable`/`error` when Herdr is down or times out |
| `podman` | Read-only container listing | `unavailable` when the Podman socket/command is missing |
| `github` | Pull requests, comments, and check-runs | `disabled` when not configured; `error` on rate limiting/HTTP failure, retaining cache where available |
| `fleet` | Fleet status/jobs API | `disabled` by default; enabled fleet failures report `error`/`unavailable` |

`ok` means records were collected; `empty` means a successful collection found no records. `error` means collection or parsing failed, `unavailable` means its local dependency is absent, and `disabled` means explicitly inactive. GitHub rate-limit errors retain cached rows with cache age when available.

## Tasks bridge and settings

Omega Prime writes the v1 task bridge at `~/.omega-prime/dashboard/tasks.json` using `omega_prime.dashboard.status_file.write_tasks`. It contains `version: 1` and the `tasks`, `waiting_on_ove`, and `finished` arrays. The writer uses restrictive directory/file permissions (0700/0600); unknown keys are ignored. A missing or malformed file produces a source status/banner, not a dashboard crash.

`~/.omega-prime/settings.json` may contain a `dashboard` object. Only `window_hours` (default 24), `quiet_minutes` (default 15), and `fleet_enabled` (default false) are used. Fleet is OFF until explicitly enabled; when enabled, it performs only `GET /status` and `GET /jobs?limit=20`, keeping its token in memory only. Action buttons do not exist by design; that is an operator decision for later.

## Troubleshooting

- **Cannot bind:** use `127.0.0.1`; public and non-loopback binds are refused.
- **Host rejected:** use the same loopback host and port in the browser URL as the server bind.
- **No task cards:** check source statuses and confirm `tasks.json` exists and validates.
- **Herdr/Podman unavailable:** verify the relevant local command or rootless socket; healthy sources continue independently.
- **GitHub empty/error:** configure the supported GitHub token environment and check rate-limit status/cache age.
- **Need a clean stop:** invoke `--stop`; it uses the dashboard pidfile under `~/.omega-prime/dashboard/`.
