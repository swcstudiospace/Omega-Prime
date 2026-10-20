# Substrate surface

Omega Prime is a surface on the agent substrate: the shared briefing, event
trail, memory, and docs plane that every agent on the install uses. When
the surface is wired, turns open with a brief of what other agents are
doing, report their own tool trail with graph provenance, share memory
across agents, recall episodes from Hindsight, and answer docs questions
from RAGflow.
When it is not wired, Omega Prime remains usable locally; substrate calls
degrade visibly instead of silently targeting localhost. No substrate URL
means `not_configured`; loopback is available only through the explicit
`SUBSTRATE_URL=local` opt-in.

## What is wired to what

| Omega Prime side | Substrate side | Backed by (Railway) |
| --- | --- | --- |
| Brief on session open | `POST /brief` | TimescaleDB index |
| Turn/tool/file/session events | `POST /events` | GreptimeDB ledger |
| Shared memory write/search | `memory_write` / `memory_search` | TimescaleDB index |
| Docs answers | `docs_search` tool | RAGflow |
| Episodic retain/recall/reflect | Hindsight service, `ultrathink` bank | hindsight-api |
| Task claims + leases | `substrate_graph_*` tools | TimescaleDB index |

Omega Prime never touches GreptimeDB, TimescaleDB, or DragonflyDB directly. Those
are backing stores; only substrate-mcp holds their clients. A store-lock
test (`omega_prime/tests/test_store_lock.py`) fails the build if a direct client
appears under `omega_prime/`.

| Variable | Meaning | Default |
| --- | --- | --- |
| `SUBSTRATE_URL` | substrate-mcp base URL; HTTPS required, or the explicit `local` opt-in | unset (`not_configured`) |
| `SUBSTRATE_TOKEN` | generic bearer token, resolved before the Grok Bot token | — |
| `SUBSTRATE_TOKEN_GROK_BOT` | grok-bot surface bearer token fallback | — |
| `HINDSIGHT_URL` | hindsight-api base URL | unset, which disables the remote bank. Set your own `https` URL to enable it. There is no hosted default. |
| `HINDSIGHT_API_KEY` | bearer token (or `HINDSIGHT_API_TOKEN`) | — |

Process environment wins over settings by key presence. Token values resolve
through the existing `NAME`, `NAME_FILE`, or
`$OMEGA_PRIME_STATE_DIR/secrets/NAME` (mode 0600) paths; the settings file
never accepts credentials. When no token resolves, calls are anonymous and
doctor/setup diagnostics warn about anonymous mode. Userinfo and non-HTTPS
substrate URLs are rejected; `SUBSTRATE_URL=local` is the only HTTP exception.

## Non-secret settings file

The connector can load optional non-secret settings from
`~/.omega-prime/settings.json`. `OMEGA_PRIME_SETTINGS_FILE` is the only path
override. The JSON object accepts string values for exactly these keys:
`SUBSTRATE_URL`, `HINDSIGHT_URL`, `ULTRATHINK_ROOT`, `ULTRATHINK_SHIP`, and
`OMEGA_PRIME_STATE_DIR`. The intended Ultrathink values are
`ULTRATHINK_ROOT=/workspace/claude-ultrathink` and `ULTRATHINK_SHIP=0`;
these are setup inputs, not evidence that the checkout or live file exists.

Keep the file outside every git worktree and tracked path, and do not make it
group- or world-writable. Malformed or rejected files are ignored atomically;
the connector continues with process environment only. Names containing
`TOKEN`, `KEY`, `SECRET`, `PASSWORD`, `PAT`, or `AUTH` are refused and
reported by name only. Unknown non-secret names warn and are ignored.
`*_FILE` credential references remain process-environment/secret-resolution
inputs, not settings-file keys. A missing file is silent. Doctor reports
loaded names or rejection status without exposing values.

## MCP transport and contract status

The client sends MCP POST requests with `Accept: application/json,
text/event-stream`, parses JSON or SSE responses, correlates SSE messages by
request id, and reuses `Mcp-Session-Id` when one is returned. The client code
currently targets `/mcp`; the current upstream mount and compatibility have
not been authoritatively verified. Offline fake-server tests prove local
transport behavior only, not Grok Bot or server compatibility.

The implemented argument mapping sends `docs_search`'s public `query` as
`question`; memory writes include an idempotency key and optional
`expected_version`; memory search includes optional `repo`; graph claim
returns a `lease_id` used by heartbeat, release, and complete. These names
follow the supplied audit expectations, not an authoritative current server
schema. Current upstream schema and response compatibility remain unverified.

## Failure behavior

Missing configuration returns `not_configured`; unreachable, unauthorized,
protocol, and tool errors remain distinct. Fail-open calls degrade visibly so
a substrate outage does not block a turn; fail-closed calls report explicit
errors so no caller mistakes a lost write for a landed one:

| Call | On outage |
| --- | --- |
| Brief (`POST /brief`) | Fail open: empty brief (`""`) |
| Event emit (`POST /events`) | Fail open: `stored: false` plus a redacted error |
| Shared memory search | Fail open: empty results (`[]`) |
| Shared memory writes, graph claim/release/complete/heartbeat | Fail closed: caller gets a state-labelled error |
| Docs search (`docs_search`) | Returns an explicit state-labelled error, never an empty success |
| Hindsight retain/recall | Falls back to the local bank-tagged store |
| Reflect, page search | Degrade to empty |

Briefs are cached per session and written to `.substrate/BRIEF.md` as the
offline fallback. Event intake carries kind/summary/surface plus caller
fields; tool summaries carry names and file paths only — tool
arguments never leave the process; prompt summaries are truncated and pass
through broker redaction. Memory-write arguments (`scope`, `kind`, `text`,
plus optional `trust`/`ttl`/`source_event`) are the write channel; reads
take a query plus optional `scope`/`limit` and return entries only.

## Probes

Four read-only checks, run by hand (never in CI):

```bash
.venv/bin/python -m omega_prime.substrate.probes
```

They hit substrate `/healthz` + `/brief` and hindsight `/health` +
`/version`, print one line each, and exit 0 only when all pass. No probe
emits, retains, writes, or claims anything.

## Add-Bot note

The Grok Bot template runs unwired: xAI's computer cannot reach a
`localhost` substrate-mcp, and templates carry no secrets. Preparing the
template locally does not attach a Grok account or prove any live
capability. Wired mode is for self-hosted runs — the tool host or any
checkout with `SUBSTRATE_URL` pointing at a reachable substrate-mcp.

## Further reading

Core execution and security boundaries live in [Architecture](architecture.md);
extension and contribution seams live in [Build aesthetics](build-aesthetics.md);
the hand-run readiness workflow lives in [Local validation](local-validation.md).
The Greptile-synthesized history is a separately labelled untrusted archive —
see [Greptile](greptile.md) — never current setup truth. This page describes
the optional wired surface only: the default product is in-process Python
with no sidecar, and unwired runs stay fully local.
