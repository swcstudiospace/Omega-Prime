# Omega Prime setup

Four operator steps: set Omega Prime up on a Grok Bot, configure private
secrets, attach or run the tool host, and inspect the client. Local
preparation is not live Grok attachment: every platform behaviour this page
relies on carries its evidence level, and live import stays a user-side
check ([evidence ledger](../../docs/grok-bot-native.md#import-evidence-ledger),
[user UAT](../../docs/grok-bot-native.md#user-uat)). Completion time is not
measured.

## 1. Set up Omega Prime on a Grok Bot

Push the repository to GitHub (or use `swcstudiospace/Omega-Prime`), then pick
one route. The Bot-driven setup is primary; the Cursor team marketplace is
a secondary route for Cursor Teams/Enterprise plans.

### Primary: ask the default Grok Bot to set itself up

The Bot runs Omega Prime on its own computer as a custom **Command** (stdio)
MCP server. Send the default Grok Bot this prompt, replacing the URL if you
pushed your own copy:

```text
<task name="setup-omega-prime" version="1">
<goal>Set up Omega Prime on your own computer.</goal>
<steps>
<step n="1">In your shell: git clone --recurse-submodules --branch <release-tag> https://github.com/swcstudiospace/Omega-Prime.git /workspace/omes-bot
No release tag is published yet, so <release-tag> is the placeholder. Do not clone main. Substitute the tag when one exists.</step>
<step n="2">bash /workspace/omes-bot/omega_prime/scripts/grokbot-1click.sh --bootstrap-only
Then bash /workspace/omes-bot/omega_prime/scripts/grokbot-1click.sh --verify --json
Wait for exit 0. On exit 10, 11 or 12, show me the JSON class line it
printed on stderr and stop.</step>
<step n="3">Add a custom MCP server of type Command, named omega-prime, with no
environment variables, running exactly:
bash /workspace/omes-bot/omega_prime/scripts/grokbot-1click.sh --bootstrap --transport stdio
If you cannot add MCP servers yourself, tell me that exact command so I
can add it in your settings.</step>
<step n="4">Check that the omega-prime server lists the omega_command tool, then call
omega_command with the command /omega-help and show me the result.</step>
</steps>
<rule>Never ask me to paste a token, key or other secret in chat.</rule>
</task>
```

What each step does:

1. A recursive clone of a pinned release tag into the Bot's durable
   `/workspace` populates the pinned submodules. No git tag is published
   yet (checked 2026-10-19), so the prompt's `<release-tag>` is a
   placeholder and `main` is not the pin. The launcher resolves the
   repository from its own location, so the server command needs only
   the clone's absolute path.
2. `--bootstrap-only` runs the bootstrap ahead of time and exits 0: a locked
   install into `/workspace/omes-bot/.venv` from `requirements-lock.txt`,
   the `prime-agent` submodule only, and the generated plugin `--check`.
   Running it first means the MCP server's first start finds a matching
   completion stamp and skips the install. Failures exit 10
   (`missing_prerequisite`), 11 (`install_failed`) or 12 (`catalog_drift`)
   with one JSON line on stderr: a plain 12 is drift `--write` repairs, while
   a 12 with `"detail":"content_invalid"` names source files to fix by hand;
   see [exit codes](../../docs/grok-bot-native.md#exit-codes).
3. The server command is the root `mcp.json` entry,
   `bash ${CURSOR_PLUGIN_ROOT}/omega_prime/scripts/grokbot-1click.sh --bootstrap --transport stdio`,
   with `${CURSOR_PLUGIN_ROOT}` replaced by the absolute path
   `/workspace/omes-bot`. It carries no `env` and nothing credential-shaped,
   because a Command server with environment variables or credential-looking
   arguments runs only in the owner's own chat. `--bootstrap` reruns the
   bootstrap on every start (only an import probe and the plugin `--check`
   when the stamp matches; a full install under a lock, resuming safely
   after an interrupted start, when it does not), then serves stdio with
   stdout reserved for MCP frames.
4. `/omega-help` through `omega_command` lists every command.

Evidence: a custom Command server runs on the computer each Bot conversation
uses (CONFIRMED, [team bots](https://docs.x.ai/grok-bot/team-bots)), and that
computer runs Linux (CONFIRMED,
[identity and access](https://docs.x.ai/grok-bot/identity-and-access)) with a
durable `/workspace`
(CONFIRMED, [computer and apps](https://docs.x.ai/grok-bot/computer-and-apps)).
Whether the Bot itself can add the server, which Python it has, and whether
its MCP client waits out a cold install are UNPROVEN (user UAT rows 1 and 5).
A Bot template carries identity, description, skills and routines
(CONFIRMED, [bots](https://docs.x.ai/grok-bot/bots)); that it never carries
the Command server is UNPROVEN (row 1), so plan on running this setup on
each Bot.

The [Bot template](templates/OMEGA_PRIME.md) is instructions for an
operator's authorized seat. Its presence does not prove a published share
link, account isolation or a successful import. Never include private
secrets, scripts or settings in a share/export to manufacture a working
route.

### Secondary: Cursor Teams/Enterprise Import from Repo

On a Cursor **Teams or Enterprise** plan only, an admin can import the
repository as a team marketplace: Cursor dashboard → **Plugins & MCPs** →
**Team Marketplaces** → **Add Marketplace** → **Import from Repo**, with the
GitHub URL. The import reads `.cursor-plugin/marketplace.json` (one plugin,
`omega-prime`, at the repository root), `.cursor-plugin/plugin.json` (skills
from `omega_prime/skills`, commands from
`omega_prime/grokbot/plugin/commands`, servers from `mcp.json`), and the
root `mcp.json` stdio entry above. Turn on **Enable Auto Refresh** only with
the Cursor GitHub App installed on the repository; otherwise the marketplace
stays at the imported commit until a manual **Refresh**.

Evidence: Import from Repo and its Teams/Enterprise gating are PARTIAL —
documented by Cursor ([plugins](https://cursor.com/docs/plugins.md)), with no
docs.x.ai page naming it. Grok Bot inheriting a team marketplace as its
plugin policy is CONFIRMED
([teams and enterprises](https://docs.x.ai/grok-bot/teams-and-enterprises)).
Whether the import accepts the repo-root plugin (`"source": "./"`), whether
Grok Bot loads the plugin's skills and commands, whether it launches the
plugin's stdio server, and whether `${CURSOR_PLUGIN_ROOT}` expands on the
Bot's computer are all UNPROVEN (user UAT rows 2–4). Live import is
unproven until you confirm it. If the route does not work, use the primary
route; it needs no plan tier.

### Prompting contract

Send `/omega-<name>` as the whole message, with any arguments after a space
(`/omega-code-review src/app.py`). The `omega_command` tool description and
the `omega-commands` skill tell the Bot to pass that exact text to the
`omega_command` tool of the `omega-prime` server. A sentence that only
mentions a command is ordinary prose, not a command. `/omega-help` lists
every name: the 27 curated commands, one `/omega-<skill>` per checked-in
skill and one `/omega-<routine>` per routine (61 today). A generated name
that matches a curated command or another generated name takes a `-skill`
or `-routine` suffix: `/omega-python-skill`, `/omega-onboard-routine`,
`/omega-connectors-routine`, `/omega-ultrathink-skill` and
`/omega-ultrathink-routine`. A host that surfaces skills natively may also
show `/python`: native `/python` shows the python skill's instructions and
runs nothing, `/omega-python` runs ruff and compileall through
`omega_command`, and `/omega-python-skill` loads the python skill's
instructions through `omega_command`. Two skills now match their folder
names: `secrets-handling` is `security-secrets-handling` and `supply-chain`
is `security-supply-chain`. A copy saved under an old name in a private Bot
skill library may persist until you remove it (UNPROVEN, user UAT row 4).
Whether a live Bot follows this contract, and whether `/omega-*` entries
appear natively in its `/` menu, is user UAT row 4.

## Install configuration

Skills shipped in this repository do not contain a person's name, a VPS
address, a device, an approval id, or this install's Railway, Tailscale,
or RAGflow names. Fill the placeholders for your install and keep the
values out of git:

| Placeholder | Used by | What to put |
| --- | --- | --- |
| `REMOTE_DEV_USER`, `REMOTE_DEV_HOST` | `skills/remote-dev-machine` | SSH user and host. No address is shipped. |
| `ADMIN_DEVICES` | `skills/railway-tailscale` | Machines that carry `tag:admin`. |
| `RAGFLOW_DATASET_DESK`, `RAGFLOW_DATASET_SUBSTRATE`, `RAGFLOW_DATASET_SKILLS`, `RAGFLOW_DATASET_PRODUCT` | `skills/ragflow-docs` | Dataset names `lead_docs_search` should query. |
| `RAILWAY_PROJECT`, `RAILWAY_SERVICE`, `TAILSCALE_FORWARDER_DATA`, `TAILSCALE_FORWARDER_APPS`, and the `RAILWAY_*_HOST` names | `skills/railway-tailscale`, `skills/desk-gateway` | This install's project, service, and forwarder names. |
| `APPROVAL_ID` | gated infra and web tools | A new recorded approval for the change in front of you. |
| `NOTION_TASK_GRAPH_COLLECTION` | `skills/trackplan-dispatch` | The Notion collection URL for task graphs. |
| `AGENT_DATA_DIR` | `skills/desk-bootstrap` | Directory that holds `agents/<uuid>/`. |

Call the person running the install "the operator".

Routines a new install can create, each prompt calling `omega_command`
(see `grokbot/templates/OMEGA_PRIME.md`). Every routine is an event-triggered
listener; do not give any routine a timer or cron schedule:

| Routine | Command | Trigger |
| --- | --- | --- |
| desk-lead | `/omega-desk-lead` | a new intake item or ticket, or a blocked ticket changing state |
| ultrathink | `/omega-ultrathink-routine` | an operator message containing `ultrathink` |
| sweep (drafts only, never publishes) | `/omega-sweep` | a new X mention |
| nightly | `/omega-nightly` | a session ends and appends turns |

The same entry points run in a shell: `python -m omega_prime.routines.desk_lead`,
`python -m omega_prime.routines.ultrathink_turn`, `python -m omega_prime.routines.sweep`,
and `python -m omega_prime.routines.nightly`.

## 2. Secrets

Omega Prime never asks for secrets in chat. Add each value where the
connector asks for it (Grok Bot settings or your host's env):

| Secret | Needed for | Where it goes |
| --- | --- | --- |
| `XAI_API_KEY` | Grok model calls outside Grok Bot | host env, `XAI_API_KEY_FILE`, or `$OMEGA_PRIME_STATE_DIR/secrets/XAI_API_KEY` (mode 0600). Same `*_FILE` rule for `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `ANTHROPIC_OAUTH_TOKEN`, `OPENROUTER_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, `GITHUB_TOKEN` |
| `X_API_TOKEN` | X connector tools | host env, `X_API_TOKEN_FILE`, or `$OMEGA_PRIME_STATE_DIR/secrets/X_API_TOKEN` (mode 0600) |
| `TELEGRAM_BOT_TOKEN` | Telegram connector tools | host env, `TELEGRAM_BOT_TOKEN_FILE`, or `$OMEGA_PRIME_STATE_DIR/secrets/TELEGRAM_BOT_TOKEN` (mode 0600) |
| `DISCORD_BOT_TOKEN` | Discord connector tools | host env, `DISCORD_BOT_TOKEN_FILE`, or `$OMEGA_PRIME_STATE_DIR/secrets/DISCORD_BOT_TOKEN` (mode 0600) |
| GitHub CLI | Greptile reviews, ship PRs | `gh auth login`, or `GH_TOKEN` in the environment of the process that runs `gh`. `gh` does not read `GH_TOKEN_FILE` or `$OMEGA_PRIME_STATE_DIR/secrets/GH_TOKEN` |
| `SUBSTRATE_TOKEN` | Substrate brief/events/memory (surface grok-bot) | host env, `SUBSTRATE_TOKEN_FILE`, or `$OMEGA_PRIME_STATE_DIR/secrets/SUBSTRATE_TOKEN` (mode 0600). `SUBSTRATE_TOKEN_GROK_BOT` uses the same rule |
| `HINDSIGHT_API_KEY` | Shared Hindsight episodic memory, only after `HINDSIGHT_URL` is set | host env, `HINDSIGHT_API_KEY_FILE`, or `$OMEGA_PRIME_STATE_DIR/secrets/HINDSIGHT_API_KEY` (mode 0600). `HINDSIGHT_API_TOKEN` uses the same rule |
| `RAILWAY_TOKEN` | Railway status and logs | host env, `RAILWAY_TOKEN_FILE`, or `$OMEGA_PRIME_STATE_DIR/secrets/RAILWAY_TOKEN` (mode 0600) |
| `VERCEL_TOKEN` | Vercel deployments | host env, `VERCEL_TOKEN_FILE`, or `$OMEGA_PRIME_STATE_DIR/secrets/VERCEL_TOKEN` (mode 0600) |
| `GREPTILE_API_KEY` | Greptile review and knowledge-base sync | host env, `GREPTILE_API_KEY_FILE`, or `$OMEGA_PRIME_STATE_DIR/secrets/GREPTILE_API_KEY` (mode 0600). `GREPTILE_GITHUB_TOKEN` uses the same rule |
| `MCP_AUTH_TOKEN` | Bearer token Grok Bot presents to a remote tool host (HTTPS alternative only; the stdio Command entry needs none) | host env or a 0600 `--token-file` (never argv); on the Bot, a Remote HTTPS Bot secret |
| `NOTION_TOKEN` | Notion Task dashboard. Read, update, and insert content on the duplicated database only. | host env, or a mode 0600 file named by `NOTION_TOKEN_FILE` |
| `LINEAR_API_KEY` | Linear team and issue actions for this install | host env, or a mode 0600 file named by `LINEAR_API_KEY_FILE` |

Do not paste either token into chat. `/omega-connectors` walks the Notion
template (`docs/notion-task-dashboard.md`), the Notion connection, the
Linear team, and an optional gateway. Reply with the verb it asks for.
Unset gateways are a valid steady state after you reply `skip-gateway`.

`SUBSTRATE_URL` has no silent localhost default. Set it to an HTTPS endpoint
you operate, or use the explicit `SUBSTRATE_URL=local` opt-in for a local
service at `http://127.0.0.1:7410`. Ready saved substrate bindings take
precedence; a blocked saved binding remains blocked. `SUBSTRATE_TOKEN` is
resolved before `SUBSTRATE_TOKEN_GROK_BOT`; without either, the client is
anonymous and diagnostics warn. URLs with userinfo are rejected.

Non-secret settings may be stored in `~/.omega-prime/settings.json`, with
`OMEGA_PRIME_SETTINGS_FILE` as the sole path override. The only accepted
string keys are `SUBSTRATE_URL`, `HINDSIGHT_URL`, `ULTRATHINK_ROOT`,
`ULTRATHINK_SHIP`, `OMEGA_PRIME_STATE_DIR`, and
`OMEGA_PRIME_AGENT_STATE_DIR`. For example, the non-secret
Ultrathink inputs are `ULTRATHINK_ROOT=/workspace/claude-ultrathink` and
`ULTRATHINK_SHIP=0`. Keep the file outside git worktrees and tracked paths,
with no group/world write permission. Do not put credentials or `*_FILE`
references in it; use the existing host secret paths instead. Process
environment wins. Invalid settings are ignored atomically and startup
continues with process environment only.

Doctor/setup reports configuration only; they do not probe the endpoint.
Without a URL, substrate calls report `not_configured`, not a request to
localhost. The MCP client's current upstream mount/schema compatibility is
unverified. Local fake-server checks are not live Grok Bot or endpoint
verification. Do not write the live settings file or restart the connector
without the operator.

Hindsight has no default URL: set `HINDSIGHT_URL` explicitly only for a
service you operate, and use HTTPS. Hindsight URLs must not include user
information, a query, or a fragment. The Hindsight key is resolved via the
credential broker from the environment or a mode-checked 0600 secret file,
and is not a setting. To let the broker supply that key, add the hostname
from `HINDSIGHT_URL` (hostname only, not the full URL) to the
`network.hosts` allowlist in
`omega_prime/contracts/policies/omega-prime.json`; the broker rejects hosts
that are not listed. Regenerate the sandbox policy after changing the
allowlist. Do not put a URL or key in Omega Prime settings, a Bot template,
or chat. If `HINDSIGHT_URL` is unset, no Hindsight credential lookup or
network request occurs.

### Memory backend behavior

`OMEGA_PRIME_MEMORY_BACKEND` accepts `local`, `substrate`, or `hindsight`.
It selects the preferred write backend only; recall searches local memory
and every configured remote independently for every selector value. If not
set, the default is `substrate` only when the authoritative resolver has an
explicitly configured Substrate URL; otherwise it is `local`. An explicitly
selected but unconfigured remote is reported as `not_configured`, not
silently replaced.

Recall and doctor report local, Substrate, and Hindsight with exactly these
states: `answered`, `empty`, `not_configured`, `unreachable`, or
`unauthorized`. Diagnostics are sanitized; a failed source is not presented
as empty, and its failure does not suppress healthy-source results. Results
retain provenance and enforce repo isolation across backends. Substrate
transport failures follow phase 97's source-aware contract and are reported
as unreachable or unauthorized, not empty.

Retention is local-first. Configured Substrate receives the write regardless
of selector; Hindsight receives writes only when selected as `hindsight`,
although configured Hindsight remains a recall source. A required remote
write failure is visible and does not roll back the local save. There is no
service-first bridge or silent fallback, and the documentation makes no
server idempotency or Hindsight document-ID guarantee.

Use the conservative command form
`/omega-retain --source <source> -- <content>` or
`/omega-retain --receipt-path <path> -- <content>`. Retain requires approval
through the lead path and source or receipt evidence; secret-shaped content
is refused. A local save by itself is not success if a required configured
remote publication fails. The growth memory `target=user` operation and
memory replace/remove remain local-only and do not publish to shared backends.

Configure only the integrations you intend to use. Failure behavior is
operation-specific: other tools may return `not_configured`, refuse, or
return gateway errors. Readiness's `feature_disabled` or
prerequisite-blocked rows are separate classifications; see
[connectors](../../docs/connectors.md) and [Red Team](../../docs/red-team.md).

## 3. Attach the tool host (1-Click Native Integration)

The tool host speaks MCP over **stdio** (a local process) or a **remote
listener** that serves legacy SSE (`/sse`, `/messages/`) and Streamable HTTP
(`/mcp`) on the same port. Both are one server identity, `omega-prime`, with
one roster and one `omega_command` surface; the stdio Command entry in
section 1 is the default, and an operator-run HTTPS host is the documented
alternative ([HTTPS alternative](../../docs/grok-bot-native.md#https-alternative-remote-https)).
The remote host fails closed: it refuses to start on a non-loopback address
without a bearer token or a token store, an invalid roster or seat policy
stops startup (exit 2), and it never accepts a token in the URL. The
operator reference is `docs/grok-bot-native.md`. Deploying the same process
is `docs/deploy.md`.

The host runs from a checkout whose submodules are populated. An
ordinary `git clone` leaves `prime-agent/` and `redteam/` empty —
either clone with `--recurse-submodules` or run
`git submodule update --init --recursive` first (targeted
`git submodule update --init prime-agent` suffices when the optional
Red Team backend is not needed; never `git clone` a checkout manually
into those submodule paths). The launcher's bootstrap initializes only
`prime-agent`, and only in a git checkout. The Red Team backend itself is
separate upstream source started per `docs/red-team.md` — the host never
auto-launches it, and a live Bot's recursive clone is user UAT row 1, not
attested by these instructions.

### Option A: Turnkey 1-click launcher (recommended)

```bash
# the declared stdio entry: bootstrap once, then serve stdio
bash omega_prime/scripts/grokbot-1click.sh --bootstrap --transport stdio

# bootstrap only (the setup prompt's pre-run), or bootstrap then verify
bash omega_prime/scripts/grokbot-1click.sh --bootstrap-only
bash omega_prime/scripts/grokbot-1click.sh --verify --json

# stdio with an existing environment, no bootstrap
./scripts/grokbot-1click.sh --transport stdio

# Remote: preflight, mint a token, export the manifest, serve SSE and /mcp
./scripts/grokbot-1click.sh --transport sse --host 0.0.0.0 --port 8000 \
  --public-url https://bot.example.com \
  --generate-token --token-file ~/.config/omega-prime/token \
  --token-store ~/.config/omega-prime/tokens.json \
  --log-format json \
  --export-manifest grokbot-manifest.json
```

The launcher (`omega_prime/scripts/grokbot-1click.sh`, also reachable
through the `scripts/` symlink) resolves the repository from its own
location and never from the caller's working directory. `--bootstrap`,
`--bootstrap-only` and `--verify [--json]` are its own flags; bootstrap
details and exit classes 10–13 are in
[One-command start](../../docs/grok-bot-native.md#one-command-start). Every
other flag goes to `python -m omega_prime.grokbot.oneclick`. In order,
oneclick runs the preflight doctor (a failed check aborts with exit 3;
warnings never do; `--no-strict` continues anyway), refuses an
unauthenticated non-loopback bind (exit 2), mints a bearer token with
`--generate-token` (shown once on stderr and, with `--token-file`, saved
there with mode 0600), exports the manifest (it never contains the token),
and serves. Add `--dry-run` to do everything except serve. `--self-test`
starts a throwaway loopback host, runs the live verifier, and requires a
clean SIGTERM (it conflicts with `--dry-run` and never prints the token). To
reuse a token, point `--token-file` at an existing 0600 file, set
`MCP_AUTH_TOKEN` (`--token-env NAME` renames the variable), or pass
`--token-store` (a hashed multi-token file; it counts as authentication).
`--log-format` is `text` or `json`. Do not pass a token as an argument:
`--token` is deprecated and warns, because argv is visible to other users
on the machine. Rate limits and the circuit breaker stay on
`python -m omega_prime.mcp_server`.

The launcher's `--work-root` is forwarded to the stdio and SSE host alike.
Without it, `OMEGA_PRIME_WORK_ROOT` applies; a bootstrap start defaults it
to `/workspace` when that exists, else `$HOME`, and defaults
`OMEGA_PRIME_STATE_DIR` outside the checkout, only when they are unset.
`--root` remains the source/roster/policy root; workspace selection does not
grant permission or bypass approvals.

### Option B: Remote MCP transport (SSE and Streamable HTTP)

An authorized client that supports SSE can connect to your host. Other MCP
clients can use `/mcp` on the same origin; this is protocol support in the host,
not proof that a Grok account has attached. For a Grok Bot, add the host as a
**Remote HTTPS** custom server at `https://<your-host>/mcp` with the token in a
Bot secret; the full route, the placeholder syntax per consumer and its
evidence levels are in
[HTTPS alternative](../../docs/grok-bot-native.md#https-alternative-remote-https):

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
`--allow-insecure-no-auth` is the explicit opt-out of the token requirement;
never use it for a host a Bot reaches.

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

### Option C: Host manifest export

Write the manifest of what the host serves. No documented Grok Bot settings
field imports this file; exporting it is not an account import, and the
import routes are the ones in section 1:

```bash
python -m omega_prime.grokbot.manifest --transport sse \
  --public-url https://bot.example.com --out grokbot-manifest.json
```

The manifest lists exactly the tools the host serves (`mcp_server.tools`), which
of them need approval (`approval_required`), capabilities derived from your
configuration plus the offered transports (`stdio`, `sse`,
`streamable-http`), the Streamable HTTP endpoint under `endpoints.mcp`, any
template problems
(`bot.integrity`), and a content digest. It never contains a token. Use it
with `python -m omega_prime.grokbot.sync --check` to detect drift.

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
Then send `/omega-help` as the whole message and expect the `omega_command`
catalog; record the result against
[user UAT](../../docs/grok-bot-native.md#user-uat) rows 1 and 4.

For source-local preparation checks, run the commands below. Their results do
not attest that an account imported the template or attached to this host:

```bash
.venv/bin/python -m omega_prime.setup_check --root .
.venv/bin/python -m omega_prime.tooling.plugin --check
```

`setup_check` includes a `plugin` row; `tooling.plugin --check` exits 1 when
a generated plugin file (`.cursor-plugin/`, `mcp.json`, the command files or
the HTTPS example) drifts from its sources. `--write` regenerates them.

The Prime capability families are off by default. To turn them on and drive
the bot with them, see `docs/driving-the-bot.md`.
