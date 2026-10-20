# Substrate surface

Omega Prime is a surface on the agent substrate: the shared briefing, event
trail, memory, and docs plane that every agent on the install uses. When
the surface is wired, turns open with a brief of what other agents are
doing, report their own tool trail with graph provenance, share memory
across agents, recall episodes from Hindsight, and answer docs questions
through the substrate `docs_search` tool. When it is not wired, Omega Prime
runs locally. Substrate is an optional integration, but individual calls have
different failure contracts; a failed write is not reported as successful.

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
| `SUBSTRATE_URL` | substrate-mcp base URL (must be an `http`/`https` URL) | `http://127.0.0.1:7410` |
| `SUBSTRATE_TOKEN` | bearer token (or `SUBSTRATE_TOKEN_GROK_BOT`) | — |
| `HINDSIGHT_URL` | hindsight-api base URL | unset, which disables the remote bank. Set your own `http`/`https` URL to enable it. There is no hosted default. |
| `HINDSIGHT_API_KEY` | bearer token (or `HINDSIGHT_API_TOKEN`) | — |

Auth resolution order in `omega_prime/substrate/client.py`: an explicitly
passed token first, else the credential broker (`key_for` for the substrate
provider against the base URL), else anonymous (no `Authorization` header)
for local runs. `omega-prime-setup-check` reports which names are
set (never values); unset means the surface stays local. Transports redact
broker-known secrets from error text before it is stored or returned.

## Failure behavior

Brief and event calls fail open so an outage does not block a turn; writes
that must be durable fail closed rather than pretending they landed:

| Call | On outage |
| --- | --- |
| Brief (`POST /brief`) | Fail open: empty brief (`""`) |
| Event emit (`POST /events`) | Fail open at the client boundary: `stored: false` plus a redacted error |
| Shared memory search | Fail open: empty results (`[]`) |
| Shared memory writes, graph claim/release/complete/heartbeat | Fail closed: the caller gets an explicit error |
| Docs search (`docs_search`) | Raises: the caller gets an explicit error, never an empty success or local fallback |
| Hindsight retain/recall | Falls back to the local bank-tagged store |
| Reflect, page search | Degrade to empty |

Lead-tool events are first appended to the local `events.ndjson` mirror.
That local record remains the truthful local receipt when forwarding fails;
the caller receives an error marked `local_mirror: true`, not a claim that
the substrate accepted it. Event kinds are mirrored from the upstream
substrate contract, not a separate source of truth.

The public Omega Prime lead docs-search API is `lead_docs_search(query=...)`;
the Python client keeps that `query` argument. The substrate MCP wire contract
is distinct: Phase97's `docs_search` call sends the question as
`question=...`, not `query=...`. This page documents the contract and does not
claim that the wire mapping is implemented. If docs search fails, the caller
gets an explicit error; it never returns an empty success or falls back to
local search.

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
