# Omega Prime setup

Four steps: install, secrets, optional tool host, smoke prompt.
Takes about ten minutes; only step 3 needs a machine of yours.

## 1. Install from the template

Open the Omega Prime share link in Grok and choose **Add to Grok Bot**.
This creates an independent copy on your account — your chats,
secrets, and settings stay yours.

What the template carries: the Omega Prime instructions, skills,
routines, and first-party plugins. What it never carries:
secrets, custom MCP servers, scripts, or private skills. Steps
2–3 re-add the ones you want.

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

Skip what you don't use: every unconfigured client reports
`not_configured` instead of failing.

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

### Option B: Remote MCP transport (SSE and Streamable HTTP)

Grok Bot cloud instances connect to your host over SSE. Other MCP clients use
`/mcp` on the same origin:

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

Generate the JSON manifest to import into Grok Bot settings or a custom assistant profile:

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
   Drives the same runtime the server uses (roster, seat policy, approval gates,
   scopes, audit), so a passing smoke run reflects what Grok Bot will see.

4. **Capability and prompt synchronizer**:
   ```bash
   python -m omega_prime.grokbot.sync --check grokbot-manifest.json
   ```
   Reports drift (exit 1) only in content Grok Bot would see: instructions, skills,
   routines, tools, approval-gated tools and capabilities. A manifest exported for
   another transport or URL is not drift.

## 4. Smoke prompt

Send the bot exactly this:

> `ultrathink` what tools do you have, and which need approval?

Expect back:

1. A short plan of how it will answer (the ultrathink notice fired).
2. A tool list matching `contracts/tool-rosters/omega-prime.yaml` — with
   the tool host attached; without it, the bot says which tools
   live behind the host instead of inventing results.
3. The approval-gated tools named as needing approval.
4. No secrets, ids, or tokens quoted back.

If any line is missing, re-check the step above it. Builders can
also run the local verification:

```bash
.venv/bin/python -m omega_prime.setup_check --root .
```

The Prime capability families are off by default. To turn them on and drive
the bot with them, see `docs/driving-the-bot.md`.
