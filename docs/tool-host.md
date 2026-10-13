# Tool host

Run the real Omega Prime tools behind your bot over MCP. Stdio is a local
process. The remote listener (`--transport sse`) serves legacy SSE and
Streamable HTTP on one port, with bearer auth, an approval API, and
`/metrics`. Operators: [Grok Bot native host](grok-bot-native.md). Images
and units: [Deploying the tool host](deploy.md).

## The server

```bash
.venv/bin/python -m omega_prime.mcp_server --root . [--home ~] [--no-roster]
```

`--root` is the repo checkout (default: cwd). Without
`--no-roster` the server exposes the shipped roster's default set — the
intersection `setup_check` reports (the count moves with the roster; read it
from `/healthz` `rostered_tools`). Prime families stay off by
default. `delegate_task` is served; without a provider env it returns
`not_configured: provider`. All seven Prime families (harness, goals,
heartbeat, autonomous, kernel, rlm, messaging) join when their
`omega-prime.json` flags are on. `rlm` is served on the desk parent and,
without a child-model provider, `rlm_spawn` and `rlm_create_session` return
`not_configured: provider`. `messaging` is served on the seat session
`bot-00-omega-prime`.

Remote endpoints, all on the same process:

| Path | Auth |
| --- | --- |
| `GET /healthz`, `GET /readyz` | public; `/readyz` is 503 while draining, when the audit log is not writable, or when a token store is unreadable |
| `GET /sse`, `POST /messages/` | bearer; legacy SSE |
| `GET` / `POST` / `DELETE /mcp` | bearer; Streamable HTTP |
| `GET /manifest.json` | scope `read`; served tools, `approval_required`, `digest` |
| `GET /metrics` | scope `read` by default |
| `/admin/approvals` | scope `admin`; not mounted when authentication is off |

The token is the `Authorization: Bearer` header only. `?token=` is 401.
Scopes are `read`, `call`, and `admin`. A non-loopback bind without a
credential exits 2. A failed strict preflight from the 1-click launcher
exits 3. Start with `./scripts/grokbot-1click.sh` or
`python -m omega_prime.grokbot.oneclick` (`--generate-token`,
`--token-file`, `--token-store`, `--public-url`, `--log-format`,
`--dry-run`, `--self-test`). Prove a running host with
`python -m omega_prime.grokbot.verify`. Details, curl examples, and limits:
[Grok Bot native host](grok-bot-native.md).

Policy and approval denials come back as MCP error results with
the registry's own text. Approval-gated tools (deploys,
publishes, merges, tracker writes) stay gated: pre-approve in
the host process or call them from a session that approves.

The host speaks MCP spec `2026-07-28` via the v2 Python SDK
(`mcp>=2,<3`): snake_case in Python (`is_error`,
`input_schema`), camelCase on the wire. Raw wire payloads
(Greptile KB client, scripted peers) stay camelCase.

`substrate_docs_search` returns extracted, redacted excerpts with bounded
document/dataset metadata and citation provenance, not the raw retrieval
response. A valid empty search returns `chunks: []`; an unconfigured or
malformed docs response returns an error.

## OpenShell

Prefer running the host sandboxed. NVIDIA OpenShell
(`omega_prime/hosting/openshell/sandbox-policy.yaml`) confines the
server: workdir included, repo readable, writes to scratch,
network default-deny (the shipped Omega Prime network allowlist is
empty — open hosts explicitly per install).

```bash
openshell sandbox create --name omega_prime \
  --policy omega_prime/hosting/openshell/sandbox-policy.yaml \
  --no-auto-providers
```

## Egress Hardening & Browser Confinement

Browser and preview operations require strict egress enforcement:

- Destination transport (`DestinationTransport` in `omega_prime/providers/destination.py`)
  enforces seat-level network policy (`SeatPolicy`). All DNS answers are resolved,
  classified, and validated; unlisted hosts or private/loopback/link-local/multicast IP
  addresses fail safe before any socket dial.
- Direct dialed connections verify TLS peer identities strictly, enforcing single-exchange
  request budgets, exact Content-Length framing, and chunked transfer decoding limits.
- Sandboxed browser sessions (`GuardedBrowserFactory` in `omega_prime/tools/browser_egress.py`)
  require verified kernel confinement, cgroup accounting, and persistent socket tracking.
  In environments without verified kernel sandbox readiness, browser launches fail closed.
- Preview and review orchestration (`WebClient` in `omega_prime/tools/webpack.py`) binds an explicit
  root operation, draining all in-flight scopes and validating terminal accounting receipts
  prior to publication or vision model analysis.

## AgentOS

Rivet AgentOS VMs run Wasm/V8 and do not run CPython, so the
host does not execute inside an AgentOS actor. Run the host on a
VM, VPS, or sandbox and have the actor reach it over MCP or
HTTP. Details: `omega_prime/hosting/agentos/NOTES.md`.

## Scraping & alerting

`/metrics` is pull-only Prometheus text (`scope: read`; bearer required
when auth is on, same rules as `GET /manifest.json`). Point an
operator-run Prometheus at it — nothing in this repo pushes alerts
anywhere; hosted alert delivery is operator wiring, not executed here.

SLO thresholds live in exactly one versioned artifact,
`omega_prime/telemetry/slo.json` (`schema_version: 1`): serve
availability, tool-call p99 latency, denial-rate budget. Never duplicate
a threshold into rules or docs — regenerate the rules file from it:

```bash
python scripts/slo_rules.py --slo omega_prime/telemetry/slo.json --output /tmp/omega-slo-rules.yaml
```

Load the generated file into the operator Prometheus (`rule_files`; one
alert per objective plus burn-rate recording rules). Validate the
document any time with `python -m omega_prime.telemetry.slo check`
(non-zero names the offending objective).

Nightly regression: `.github/workflows/nightly-readiness.yml` runs
`python -m omega_prime.grokbot.readiness --root . --json --output
readiness-report.json` from source, attaches the report as an artifact,
and compares `ok` / `summary.exercised` against
`omega_prime/telemetry/readiness_baseline.json` via
`scripts/nightly_readiness_check.py check`. A regression fails the job
and tracks one deduplicated `[nightly-readiness] readiness regression`
issue; a green run closes it. Move the baseline only with an explicit
`python scripts/nightly_readiness_check.py bless --baseline
omega_prime/telemetry/readiness_baseline.json --report <report>` inside a
reviewed PR — the workflow never blesses.

OTLP export stays opt-in: enable `telemetry.otlp.*` only with a collector
endpoint configured (`telemetry.otlp.endpoint`); without it the process
serves `/metrics` exactly as before.

## Usage quotas

Per-caller usage quotas close the "interceptors count but enforce nothing"
gap with a fixed rollout ladder: `report` → `soft` → `hard` → back to
`report`. Ship `report` everywhere first, collect one or two rollups, turn
on `soft` (warnings visible, no denials), then `hard` only after the quota
document holds real limits. Config lives in the `omega-prime.json`
`quotas` node (`mode`, `document`, `rollup_dir`, defaulting to `report`,
empty, and `.omega-quotas/`); the environment overrides are
`OMEGA_PRIME_QUOTAS_MODE` and `OMEGA_PRIME_QUOTAS_DOCUMENT`. A bogus mode
falls back to `report` loudly — config loading never crashes and never
silently enables enforcement.

The quota document is versioned JSON mapping token-store labels to daily
limits; a caller missing from the document is unlimited, and a missing or
empty document leaves the engine fully inert (no series, no counting):

```json
{"schema_version": 1, "callers": {"desk": {"requests_per_day": 1000, "tool_calls_per_day": 500}}}
```

Caller identity is the token-store label of the authenticating bearer
token — never raw token material, which never appears in a label, a log
line, or a series. Rotation preserves labels (the replacement is minted
with the old entry's label), so a rotated caller keeps its quota bucket
instead of appearing as a new unlimited caller. Callers without token
identity (unauthenticated or local transports) are counted under the fixed
safe bucket `caller="_unknown_"` and are NEVER denied in any mode: hard
mode is token-identity-scoped. At most 64 distinct caller labels are
exported; excess folds into `caller="_overflow_"`.

Windows are UTC calendar days. The live counters enforce within the
current day; the per-day rollup file is the audit source of truth.
Per-caller series (all scope-gated behind `/metrics` exactly like the
other series — unauthenticated scrapes still 401):

| Series | Meaning |
| --- | --- |
| `omega_caller_requests_total{caller}` | Tool-call requests per caller per UTC day |
| `omega_caller_tool_calls_total{caller}` | Completed tool calls per caller per UTC day |
| `omega_caller_quota_limit{caller,kind}` | Configured daily limit (`kind` is `requests` or `tool_calls`) |
| `omega_caller_quota_used_ratio{caller,kind}` | Used fraction of the daily limit |
| `omega_caller_quota_warn{caller}` | Soft-mode warning gauge (1 once the caller passes 80% of any limit) |

In `soft` mode the engine logs one warning per caller per kind per UTC
day at ≥80% of any limit and sets the warn gauge; it never denies. In
`hard` mode an over-limit request is refused pre-dispatch — never
partially executed — with the standard denial shape and `code="quota"`,
visible in the existing `omega_tool_denials_total{code="quota"}` series.
In `report` mode (the default) nothing is enforced: no denial, no
warning, no behavior change.

The nightly rollup is an ordinary `heartbeat_set` job invoking the rollup
CLI with explicit paths — no new scheduler, no new tool name:

```json
{"session": "ops-nightly", "prompt": "Run the quota rollup: .venv/bin/python -m omega_prime.quotas rollup --date <yesterday as YYYY-MM-DD> --rollup-dir /var/lib/omega-prime/.omega-quotas --document /etc/omega-prime/quotas.json", "interval_seconds": 86400}
```

```bash
.venv/bin/python -m omega_prime.quotas rollup --date 2026-10-12 --rollup-dir .omega-quotas/ --document /etc/omega-prime/quotas.json
```

The CLI writes `rollup_dir/usage-YYYY-MM-DD.json` — one object per caller
(`requests`, `tool_calls`, `limits`, `warn`, `denied`) with deterministic
key ordering, so re-running is byte-identical and reconciles exactly with
that day's `/metrics` counters. With quotas disabled (no document) the run
logs one skip line and writes nothing.

Escape hatch (T-82-06): flipping `quotas.mode` back to `report` stops
warnings and denials on live-reload with no restart; callers are unlimited
again exactly as before.

## Runtime and further reading

The host is the same in-process Python product (package floor `>=3.11`;
see [Setup](setup.md) for the exercised and CI interpreters) served over
MCP. Stdio is a local process; the SSE listener serves legacy SSE and
Streamable HTTP on one port. Optional native Prime providers and kernel
tools need the bridge build and stay off by default; separately started
backends (a Node red-team backend, external stores) are outside this
process. Approval-gated tools stay gated — pre-approve in the host process
or approve from a session that approves; see
[Grok Bot native host](grok-bot-native.md) for the exact approval API.
Execution and security boundaries live in [Architecture](architecture.md);
the hand-run checks live in [Local validation](local-validation.md). No
page here asserts a deployment, a live provider attachment, or any signed
approval beyond the host's own approval log.
