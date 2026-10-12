# Omega Prime setup

Four operator steps: prepare source, configure private secrets, start an optional
tool host, and inspect the client. Local preparation is not live Grok attachment;
completion time and account-side template/share-link availability are unverified.

## 1. Prepare the source and template

Follow [the source setup route](../../docs/setup.md) to install the Python
environment and populate the pinned submodules. The repository's
[OMEGA_PRIME.md](templates/OMEGA_PRIME.md) is instructions for an operator's
authorized seat; its presence does not prove a published share link, an
**Add to Grok Bot** flow, account isolation or a successful import.
If your client exposes an authorized import/attachment route, verify that
client's prerequisites separately. Never include private secrets, scripts or
settings in a share/export to manufacture a working route.

## 2. Secrets

Omega Prime never asks for secrets in chat. Add each value where the
connector asks for it (Grok Bot settings or your host's env):

| Secret | Needed for | Where it goes |
| --- | --- | --- |
| `XAI_API_KEY` | Grok model calls outside Grok Bot | host env, resolved via the credential broker |
| `X_API_TOKEN` | X connector tools | host env, read by the tool host |
| `TELEGRAM_BOT_TOKEN` | Telegram connector tools | host env, read by the tool host |
| `DISCORD_BOT_TOKEN` | Discord connector tools | host env, read by the tool host |
| GitHub token | Greptile reviews, ship PRs | host via `gh auth` |
| `SUBSTRATE_TOKEN` | Substrate brief/events/memory (surface grok-bot) | host env, resolved via the credential broker |
| `HINDSIGHT_API_KEY` | Shared Hindsight episodic memory | host env, resolved via the credential broker |
| `MCP_AUTH_TOKEN` | Bearer token Grok Bot presents to a remote tool host | host env or a 0600 `--token-file` (never argv) |

`SUBSTRATE_URL` (default `http://127.0.0.1:7410`) and `HINDSIGHT_URL`
(default the Railway hindsight-api) override where those two clients point.

Configure only the integrations you intend to use. Failure behavior is
operation-specific: some tools return `not_configured`, some refuse or return
gateway errors, and some clients raise or degrade reads. Readiness's
`feature_disabled` or prerequisite-blocked rows are separate classifications;
see [connectors](../../docs/connectors.md) and [Red Team](../../docs/red-team.md).

## 3. Attach the tool host (1-Click Native Integration)

The tool host speaks MCP over **stdio** (a local process) or a **remote
listener** that serves legacy SSE (`/sse`, `/messages/`) and Streamable HTTP
(`/mcp`) on the same port. The remote host fails closed: it refuses to start
on a non-loopback address without a bearer token or a token store, an invalid
roster or seat policy stops startup (exit 2), and it never accepts a token in
the URL. The operator reference is `docs/grok-bot-native.md`. Deploying the
same process is `docs/deploy.md`.

The host runs from a checkout whose submodules are populated. An
ordinary `git clone` leaves `prime-agent/` and `redteam/` empty —
either clone with `--recurse-submodules` or run
`git submodule update --init --recursive` first (targeted
`git submodule update --init prime-agent` suffices when the optional
Red Team backend is not needed; never `git clone` a checkout manually
into those submodule paths). The Red Team backend itself is separate
upstream source started per `docs/red-team.md` — the host never
auto-launches it, and Grok's own recursive-clone behavior is not
attested by these instructions.

### Option A: Turnkey 1-click launcher (recommended)

```bash
# stdio (local IDE or sandbox)
./scripts/grokbot-1click.sh --transport stdio

# Remote: preflight, mint a token, export the manifest, serve SSE and /mcp
./scripts/grokbot-1click.sh --transport sse --host 0.0.0.0 --port 8000 \
  --public-url https://bot.example.com \
  --generate-token --token-file ~/.config/omega-prime/token \
  --token-store ~/.config/omega-prime/tokens.json \
  --log-format json \
  --export-manifest grokbot-manifest.json
```

`./scripts/grokbot-1click.sh` and `python -m omega_prime.grokbot.oneclick` take
the same flags. In order, the launcher runs the preflight doctor (a failed
check aborts with exit 3; warnings never do; `--no-strict` continues anyway),
refuses an unauthenticated non-loopback bind (exit 2), mints a bearer token
with `--generate-token` (shown once on stderr and, with `--token-file`, saved
there with mode 0600), exports the manifest (it never contains the token), and
serves. Add `--dry-run` to do everything except serve. `--self-test` starts a
throwaway loopback host, runs the live verifier, and requires a clean SIGTERM
(it conflicts with `--dry-run` and never prints the token). To reuse a token,
point `--token-file` at an existing 0600 file, set `MCP_AUTH_TOKEN`
(`--token-env NAME` renames the variable), or pass `--token-store` (a hashed
multi-token file; it counts as authentication). `--log-format` is `text` or
`json`. `--token` also works but warns, because argv is visible to other users
on the machine. Rate limits and the circuit breaker stay on
`python -m omega_prime.mcp_server`.

The launcher's `--work-root` is forwarded only to SSE. For stdio use
`OMEGA_PRIME_WORK_ROOT` in the child environment, or run
`python -m omega_prime.mcp_server --root . --work-root /path/to/owned/workspace`
directly. `--root` remains the source/roster/policy root; workspace selection
does not grant permission or bypass approvals.

### Option B: Remote MCP transport (SSE and Streamable HTTP)

An authorized client that supports SSE can connect to your host. Other MCP
clients can use `/mcp` on the same origin; this is protocol support in the host,
not proof that a Grok account has attached:

```bash
python -m omega_prime.mcp_server --transport sse --host 0.0.0.0 --port 8000 \
  --token-file ~/.config/omega-prime/token \
  --token-store ~/.config/omega-prime/tokens.json \
  --public-url https://bot.example.com \
  --audit-log /var/lib/omega-prime/audit.jsonl \
  --log-format json
```

Endpoints:

- `GET /sse` and `POST /messages/`: legacy SSE (bearer token required).
- `GET` / `POST` / `DELETE /mcp`: Streamable HTTP (bearer token required).
- `GET /healthz`: status, version, tools actually served, auth mode (public).
- `GET /readyz`: 200 when ready; 503 while draining, when the audit log is not
  writable, or when a token-store file is unreadable (public).
- `GET /manifest.json`: served tools, `approval_required`, and a `digest`
  (scope `read`).
- `GET /metrics`: Prometheus text (scope `read` unless `--metrics-scope` says
  otherwise).
- `GET` / `POST /admin/approvals` and `DELETE /admin/approvals/{tool}`: approval
  gateway (scope `admin`; not mounted at all when authentication is off).

Useful flags: `--allow-origin ORIGIN` and `--allow-host HOST[:PORT]` (repeatable),
`--max-body-bytes`, `--shutdown-grace SECONDS`, `--approve TOOL:APPROVER`,
`--rate-limit` (default 600/min), `--tool-rate-limit` (default 300/min),
`--auth-failure-limit` (default 10 per 60 s), `--breaker-threshold` (default 5;
`0` disables the breaker) and `--breaker-cooldown` (default 30 s). `0` also
disables `--rate-limit`, `--tool-rate-limit`, and `--auth-failure-limit`.
`--allow-insecure-no-auth` is the explicit opt-out of the token requirement.

What the host guarantees:

- The token is read from the `Authorization: Bearer` header only, compared in
  constant time; a missing or wrong token, including `?token=`, gets 401 with
  `WWW-Authenticate: Bearer`.
- Scopes are `read`, `call`, and `admin` (`call` includes `read`; `admin`
  includes both).
  The single `--token-file` / `MCP_AUTH_TOKEN` principal is `read`+`call`. Mint
  and revoke store tokens with `python -m omega_prime.grokbot.tokens new|list|revoke`.
  A revoked or expired token is rejected without a restart; the host re-reads
  the file within about a second.
- Host and Origin are validated on a loopback bind and whenever you set
  `--public-url` or `--allow-host`. CORS answers only origins you name.
- Every tool call and every authentication failure is appended to a hash-chained
  audit log. Arguments are recorded as key names plus a digest, never as values.
  `python -m omega_prime.grokbot.audit verify` detects an edited, removed or
  reordered record; `tail` prints the newest lines.
- Tool calls run one at a time in a worker thread, so a slow tool never freezes
  health checks or shutdown.
- SIGTERM or SIGINT drains in-flight calls (up to `--shutdown-grace` seconds,
  default 20) while `/readyz` answers 503, then exits 0. With nothing in flight
  that is about a second. A second signal forces exit.

An admin token approves a gated tool without a restart. The approver is that
token's label or id, never the bot. A `call` token receives 403:

```bash
curl -sS -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tool":"TOOL","ttl_seconds":600}' \
  http://127.0.0.1:8000/admin/approvals
```

`TOOL` must be one of the `gated_tools` from `GET /admin/approvals`. Omit
`ttl_seconds` for a one-hour grant (never longer than `--approval-max-ttl`,
default 24 h). `DELETE /admin/approvals/TOOL` revokes it. Full curl forms,
metric names, and the verifier are in `docs/grok-bot-native.md`.

### Option C: Grok Bot 1-click manifest export

Generate the host manifest for a client-side import route if your authorized
client supports one; export alone is not an observed account import:

```bash
python -m omega_prime.grokbot.manifest --transport sse \
  --public-url https://bot.example.com --out grokbot-manifest.json
```

The manifest lists exactly the tools the host serves (`mcp_server.tools`), which
of them need approval (`approval_required`), capabilities derived from your
configuration, any template problems (`bot.integrity`), and a content digest. It
never contains a token.

### Enterprise operations and tooling

1. **Preflight health doctor**:
   ```bash
   python -m omega_prime.grokbot.doctor --host 0.0.0.0 --auth --strict
   ```
   Checks Python and venv, the `prime-agent` submodule, assembled prompts, MCP
   dependencies, manifest generation, seat policy, roster, template integrity, the
   audit chain, bind exposure, token strength, configured connector secrets (names
   only) and port availability. `--json` prints machine-readable output; exit 1 on
   any failure (with `--strict`, on any warning too).

2. **Process watchdog and supervisor**:
   ```bash
   python -m omega_prime.grokbot.supervisor --state-file .planning/grokbot_supervisor.json \
     --health-url http://127.0.0.1:8000/healthz -- \
     python -m omega_prime.mcp_server --transport sse --token-file ~/.config/omega-prime/token
   python -m omega_prime.grokbot.supervisor --status --state-file .planning/grokbot_supervisor.json
   python -m omega_prime.grokbot.supervisor --stop --state-file .planning/grokbot_supervisor.json
   ```
   Restarts a crashed or unhealthy host with capped exponential backoff, resets the
   restart budget after stable uptime, and exits non-zero when it gives up.
   `--stop` stops the supervisor and its child.

3. **Local turn emulator and smoke harness**:
   ```bash
   python -m omega_prime.grokbot.emulator --smoke
   ```
   Exercises the local host runtime (roster, seat policy, approval gates,
   scopes, audit). A passing local smoke is not a live Grok account/model result.

4. **Capability and prompt synchronizer**:
   ```bash
   python -m omega_prime.grokbot.sync --check grokbot-manifest.json
   ```
   Reports drift (exit 1) only in content Grok Bot would see: instructions, skills,
   routines, tools, approval-gated tools and capabilities. A manifest exported for
   another transport or URL is not drift.

## 4. Client-side smoke prompt

Only after an operator has independently attached an authorized client, use
this proposed acceptance prompt:

> `ultrathink` what tools do you have, and which need approval?

Compare the response with the host's actual manifest and effective
roster/configuration intersection, not a fixed full-roster count. Check that
registration-specific approval requirements remain explicit and that no
private credentials are echoed. Prompt text is a request, not a guarantee
that a model lists tools correctly or that every handler executed. This
repository's local preparation does not establish a successful live response.

For source-local preparation checks, run the command below. Its result does not
attest that an account imported the template or attached to this host:

```bash
.venv/bin/python -m omega_prime.setup_check --root .
```

The Prime capability families are off by default. To turn them on and drive
the bot with them, see `docs/driving-the-bot.md`.
