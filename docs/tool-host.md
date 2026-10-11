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
