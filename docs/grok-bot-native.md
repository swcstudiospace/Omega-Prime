# Grok Bot native host

Run the Omega Prime tool host for a Grok Bot. The default entry is a stdio
**Command** server the Bot runs on its own computer; the documented
alternative is an HTTPS host you run and the Bot reaches as a **Remote
HTTPS** server. Both are one server identity, `omega-prime`, with one
roster, one `omega_command` surface, one seat policy, and one approval log.
The HTTPS host serves legacy MCP SSE and Streamable HTTP from the same
process.

The Bot-driven setup prompt, the Cursor Teams/Enterprise import route and
the prompting contract are in
[grokbot/SETUP.md](../omega_prime/grokbot/SETUP.md) section 1; completion
time and live account attachment remain unverified, and the
[evidence ledger](#import-evidence-ledger) and [user UAT](#user-uat) below
say which platform behaviour is confirmed. This page is the operator
reference for the running host. Container, compose, systemd, and Kubernetes
installs are [Deploying the tool host](deploy.md). What each Prime family
does behind a bot is [Driving the bot](driving-the-bot.md). Sandbox notes
stay on [Tool host](tool-host.md).

The default roster serves the intersection `setup_check` reports (the count
moves with the roster; read it from `/healthz` `rostered_tools`).
`delegate_task` is served; without a provider env it returns
`not_configured: provider`. Prime families stay off until
`omega-prime.json` turns them on. Preparing the template and running the
checks below is local preparation on a machine you control: it does not
attach a Grok account, reach a live provider, or prove a cloud deployment.

## One-command start

`omega_prime/scripts/grokbot-1click.sh` (also reachable through the
`scripts/grokbot-1click.sh` symlink) resolves the repository from its own
location, following symlinks. The caller's working directory never selects
it. Without a bootstrap flag it runs
`python -m omega_prime.grokbot.oneclick --root <repository>` with your
arguments, using `.venv/bin/python`, then `$VIRTUAL_ENV`, then `python3`.

```bash
# the declared stdio entry: bootstrap once, then serve stdio
bash omega_prime/scripts/grokbot-1click.sh --bootstrap --transport stdio

# bootstrap, then exit 0 (the setup prompt's pre-run)
bash omega_prime/scripts/grokbot-1click.sh --bootstrap-only

# bootstrap, then check both plugin entries
bash omega_prime/scripts/grokbot-1click.sh --verify --json

# local process (IDE or sandbox) with an existing environment; no HTTP listener
./scripts/grokbot-1click.sh --transport stdio

# remote host: doctor, bind check, optional manifest, then serve
./scripts/grokbot-1click.sh --transport sse --host 0.0.0.0 --port 8000 \
  --public-url https://bot.example.com \
  --generate-token --token-file ~/.config/omega-prime/token \
  --token-store ~/.config/omega-prime/tokens.json \
  --log-format json \
  --export-manifest grokbot-manifest.json
```

### Bootstrap modes

- `--bootstrap` bootstraps, then serves stdio
  (`oneclick --transport stdio --skip-doctor` plus your arguments). The root
  `mcp.json` declares exactly
  `bash ${CURSOR_PLUGIN_ROOT}/omega_prime/scripts/grokbot-1click.sh --bootstrap --transport stdio`,
  with no `env` and no `cwd`; the Bot-driven setup uses the same command
  with the absolute clone path.
- `--bootstrap-only` bootstraps, then exits 0. It takes no other argument.
- `--verify [--json]` bootstraps, then runs
  `python -m omega_prime.tooling.plugin --verify`: it spawns exactly the
  declared `mcp.json` command for `initialize` plus `omega_command`
  `/omega-help`, validates the HTTPS example, and verifies a loopback bearer
  host at `/mcp`. `--json` prints the report on stdout; verify mode is not a
  protocol stream. It accepts no other argument and excludes
  `--bootstrap-only`.

The bootstrap checks its prerequisites (bash 4 or later, `git`, `flock`,
Python 3.11 or later with `venv`; 3.12 is preferred because
`requirements-lock.txt` was recorded on it), then holds an exclusive `flock`
on `.venv.lock` so concurrent starts wait. In a git checkout it initializes
the `prime-agent` submodule only, never `redteam`. A completion stamp,
`.venv.omega-prime-bootstrap.json`, records the lock, `pyproject.toml`, the
interpreter and the `prime-agent` commit. A matching stamp runs only an
import probe. Otherwise a fresh environment is built in `.venv.build` from
`requirements-lock.txt` plus `pip install --no-deps -e .`, probed, renamed
into `.venv`, and stamped last, so an interrupted first start rebuilds on
the next start. An existing `.venv` with no stamp is operator-managed: it is
used unchanged when the import probe passes and otherwise refused with exit
11 (`"step":"operator_venv"`); it is never moved or deleted. Every bootstrap
then runs `python -m omega_prime.tooling.plugin --check`. When they are
unset, the launcher exports `OMEGA_PRIME_STATE_DIR`
(`${XDG_STATE_HOME:-$HOME/.local/state}/omega-prime`) and
`OMEGA_PRIME_WORK_ROOT` (`/workspace` when it exists, else `$HOME`), so host
state stays outside a replaceable checkout. Bootstrap progress goes to
stderr, every bootstrap child reads `/dev/null`, and stdout carries nothing
until the final serve, whose stdout is MCP frames only.

The test suite never installs over the network. To observe a cold install,
run `bash omega_prime/scripts/grokbot-1click.sh --bootstrap-only` once in a
fresh `git clone --recurse-submodules` with no `.venv`. Success is exit 0
with nothing on stdout: stderr logs `building a fresh environment` and ends
with `bootstrap complete`, and `.venv` plus the stamp exist. A second run is
a no-op that logs `environment is up to date (stamp matches)` and leaves the
stamp unchanged. Any failure exits 10, 11 or 12 with one JSON class line on
stderr; see [Exit codes](#exit-codes).

### oneclick flags

`--transport` is `stdio` (the default) or `sse`. On SSE the launcher, in
order:

1. Resolves a bearer token: `--token-file` if that file exists, else the
   deprecated `--token`, else the environment variable named by
   `--token-env` (default `MCP_AUTH_TOKEN`). A missing `--token-file` is
   allowed only with `--generate-token`.
2. Refuses a non-loopback bind with no token and no `--token-store` (exit 2).
   `--allow-insecure-no-auth` is the explicit opt-out and prints a warning;
   never use it for a host a Bot reaches.
3. Runs the preflight doctor unless `--skip-doctor`. A failed check aborts
   with exit 3 when strict (the default for `sse`; stdio defaults to
   non-strict). Warnings do not abort. `--no-strict` continues anyway.
4. With `--export-manifest`, or on `--dry-run`, writes the manifest. The
   file never contains a token.
5. Serves, unless `--dry-run`, which stops after the checks and the export.

`--generate-token` is SSE-only. It mints a token when none is configured,
prints it once on stderr, and with `--token-file` saves it mode 0600. If a
token is already configured the flag is ignored. The token is never written
to stdout, to `--json` output, or to the manifest. Do not pass a token as an
argument: `--token` still exists but warns, because other users can read
argv.

`--public-url` must be an `http` or `https` URL with a host. The manifest
records `<url>/sse` as its `url` and the Streamable HTTP route under
`endpoints.mcp`. `--log-format` is `text` (default) or `json` and is
forwarded to the server. `--token-store PATH` is a hashed multi-token file;
giving one counts as authentication for the bind check.

Other launcher flags: `--root`, `--work-root` (forwarded to the stdio and
SSE host alike), `--host` (default `127.0.0.1`), `--port` (default 8000),
`--allow-origin` and `--allow-host` (repeatable), `--audit-log`, `--json`
(one JSON object on stdout — stderr under served stdio, where stdout carries
MCP frames; text diagnostics always on stderr).

### Exit codes

| Exit | Class | Cause |
| --- | --- | --- |
| 0 | ok | Served, bootstrapped, or verified |
| 2 | usage / configuration | Bad token, unsafe bind, bad public URL, `--generate-token` without SSE, or a launcher usage error (`--bootstrap-only` with other arguments, `--verify` with anything but `--json`) |
| 3 | preflight | A failed doctor check while strict |
| 10 | `missing_prerequisite` | Bash older than 4, or no `git`, `flock`, Python 3.11+, `venv`, `pyproject.toml` or `requirements-lock.txt`; the line lists them under `missing` |
| 11 | `install_failed` | Lock, venv, `pip`, import probe, swap, stamp or the plugin `--check` itself failed, or an unstamped `.venv` fails the probe; `step` names it and nothing is stamped (`"step":"plugin_check"` means the check crashed or the interpreter broke: fix the environment, not the catalog) |
| 12 | `catalog_drift` | `python -m omega_prime.tooling.plugin --check` failed: a plain 12 is drift that `python -m omega_prime.tooling.plugin --write` repairs; a 12 with `"detail":"content_invalid"` names source files `--write` cannot repair — fix them by hand, then rerun `--check` |
| 13 | `host_unreachable` | `--verify` only: the plugin verifier exited non-zero (`verifier_exit`) |

Classes 10–13 come from the shell launcher and print one JSON line on
stderr, for example `{"class":"install_failed","exit":11,"step":"pip_lock"}`,
with stdout empty. The launcher itself never produces 1 or 3; after its final
exec, the exit status is the served process's own.

Rate limits, the circuit breaker, session caps, and metrics stay at the
server defaults under the launcher. Set them on
`python -m omega_prime.mcp_server` (below).

## The server command

```bash
python -m omega_prime.mcp_server --transport sse --host 0.0.0.0 --port 8000 \
  --token-file ~/.config/omega-prime/token \
  --token-store ~/.config/omega-prime/tokens.json \
  --public-url https://bot.example.com \
  --audit-log /var/lib/omega-prime/audit.jsonl \
  --log-format json
```

`--transport sse` is the remote host. `--transport stdio` (the default)
speaks MCP on stdin/stdout and does not listen. Token order on the server
is `--token-file`, then `--token`, then `--token-env`. A token file must be
a regular file and must not be group- or world-accessible (`chmod 600`).
The minimum token length is 16 characters.

Useful server flags (each maps to the running host; `0` disables a limit
where the help text says so):

| Flag | Default | Effect |
| --- | --- | --- |
| `--rate-limit N` | 600 | HTTP requests per minute per principal |
| `--global-rate-limit N` | 0 | HTTP requests per minute for every principal together |
| `--tool-rate-limit N` | 300 | Tool calls per minute per principal |
| `--auth-failure-limit N` | 10 | Auth failures per 60 seconds per client |
| `--breaker-threshold N` | 5 | Infrastructure failures before a tool's circuit opens |
| `--breaker-cooldown SECONDS` | 30 | How long that circuit stays open |
| `--no-metrics` | metrics on | Do not mount `/metrics` |
| `--metrics-scope {read,call,admin}` | `read` | Scope required to read `/metrics` |
| `--log-format {text,json}` | `text` | Process log format |
| `--session-idle-timeout SECONDS` | 1800 | Drop an idle Streamable HTTP session |
| `--max-sessions N` | 64 | Concurrent Streamable HTTP sessions |
| `--approval-max-ttl SECONDS` | 86400 | Longest TTL `POST /admin/approvals` accepts |
| `--max-body-bytes N` | 1048576 | Largest accepted request body |
| `--shutdown-grace SECONDS` | 20 | Drain window after the first SIGTERM or SIGINT |
| `--approve TOOL:APPROVER` | none | Pre-approve one gated tool at startup (repeatable) |
| `--allow-host`, `--allow-origin` | loopback only | Extra Host and Origin values (repeatable) |
| `--allow-insecure-no-auth` | off | Listen on a non-loopback address with no credential |
| `--no-roster` | roster on | Serve every registered tool, not the roster |

A negative limit, or a non-positive `--session-idle-timeout`,
`--max-sessions`, or `--approval-max-ttl`, is a usage error (exit 2) before
either transport starts. Configuration the host refuses — a short token, an
unauthenticated non-loopback bind, a bad public URL, an invalid roster or
seat policy — also exits 2, with `omega-prime-mcp-server:` and the reason
on stderr.

## Endpoints

Public probes (no token):

| Method | Path | Response |
| --- | --- | --- |
| GET | `/healthz` | `{"status":"healthy","service":"omega-prime-mcp-server","version","package_version","rostered_tools","auth"}`. `auth` is `required` or `disabled`. |
| GET | `/readyz` | 200 `{"ready":true,"checks":...}` or 503. Checks: `registry`, `roster`, `policy`, `audit`, `shutdown`, `token_store`, `streamable_http`. 503 while draining, when the audit log is not writable, when a token-store file is unreadable or corrupt (`token_store: degraded`), or when the Streamable HTTP lifespan is down. |

Authenticated when a token or token store is configured (bearer; see below).
With authentication disabled, `/admin/*` is not mounted (404) and
`/manifest.json` is public. `/metrics` is still mounted unless
`--no-metrics`, but it requires a principal, so with authentication off it
answers 401.

| Method | Path | Who |
| --- | --- | --- |
| GET | `/sse` | Legacy SSE stream. |
| POST | `/messages/` | Legacy SSE inbound channel. |
| GET, POST, DELETE | `/mcp` | Streamable HTTP. A body over `--max-body-bytes` is 413. Session number `--max-sessions` + 1 is 503. A session idle for `--session-idle-timeout` seconds is dropped and its id then 404s. |
| GET | `/manifest.json` | Scope `read` when authentication is on; public when it is off. The manifest of the settings this process is actually using, built once and cached. Tool count matches `/healthz` `rostered_tools`. Includes `approval_required` and a `digest`. `auth.type` is `bearer` when authentication is on. The token value is never in the document. The recorded URL is the SSE URL; Streamable HTTP is the `/mcp` route on the same origin. |
| GET | `/metrics` | Scope from `--metrics-scope` (default `read`). Prometheus text, `text/plain; version=0.0.4; charset=utf-8`. Absent with `--no-metrics`. With authentication off there is no principal, so the route answers 401. |
| GET | `/admin/approvals` | Scope `admin`. Live approvals and gated tool names. |
| POST | `/admin/approvals` | Scope `admin`. Approve one gated tool. |
| DELETE | `/admin/approvals/{tool}` | Scope `admin`. Revoke one approval. |

Every response carries `X-Request-Id` and a W3C `traceparent`.

A client sends `Authorization: Bearer <token>`. Current MCP clients,
including a Grok Bot **Remote HTTPS** server, use `<origin>/mcp`; legacy SSE
clients use `<public-url>/sse` or `http://<host>:<port>/sse`. A session on
`/mcp` belongs to the principal that opened it: someone else's
`Mcp-Session-Id` is 404, the same answer as an unknown session.

## HTTPS alternative (Remote HTTPS)

A Grok Bot's computer runs in Cursor's cloud and cannot reach `localhost` on
your machine (CONFIRMED,
[private networks](https://docs.x.ai/grok-bot/private-networks)). Instead of
the stdio Command entry you can run the host yourself on a public HTTPS
origin and add it to the Bot as a **Remote HTTPS** custom MCP server, the
documented server type for a server that needs a secret (CONFIRMED,
[team bots](https://docs.x.ai/grok-bot/team-bots)). You own and run that
host; the project publishes no hosted deployment and no image.

1. **Host.** On a machine you control, behind a TLS-terminating reverse
   proxy or tunnel you own, serve with a bearer token in a mode-0600 file:

   ```bash
   ./scripts/grokbot-1click.sh --transport sse --host 127.0.0.1 --port 8000 \
     --public-url https://omega.example.com \
     --generate-token --token-file ~/.config/omega-prime/token
   ```

   `--generate-token` mints the token once, prints it on stderr and saves it
   mode 0600; on later starts the existing file is reused. Never use
   `--token` or `--allow-insecure-no-auth` for this host. A hashed store
   from `python -m omega_prime.grokbot.tokens new --file ~/.config/omega-prime/tokens.json --label bot`
   works too (`--token-store`), and [Deploying the tool host](deploy.md)
   covers container and service installs.
2. **Prove the host** from outside before adding it:

   ```bash
   python -m omega_prime.grokbot.verify --url https://omega.example.com \
     --transport http --require-auth --token-file ~/.config/omega-prime/token
   ```

3. **Bot.** Add a custom MCP server of type **Remote HTTPS** at
   `https://omega.example.com/mcp`, not `/sse`: Cloudflare quick tunnels do
   not carry SSE, while Streamable HTTP works through them (CONFIRMED for
   the adjacent Grok connectors product,
   [custom MCP tunneling](https://docs.x.ai/grok/connectors/custom-mcp-tunneling)).
   Store the token as a Bot secret, never in chat. Secret names use
   uppercase letters, digits and underscores and start with a letter, for
   example `OMEGA_PRIME_MCP_TOKEN` (CONFIRMED,
   [team bots](https://docs.x.ai/grok-bot/team-bots)). Send it as
   `Authorization: Bearer <that secret>`. How the Remote HTTPS form refers
   to the secret, and the live server answering `tools/list`, are UNPROVEN
   until [user UAT](#user-uat) row 6.

The non-secret example is
`omega_prime/grokbot/plugin/mcp.https.example.json`. `.cursor-plugin/plugin.json`
never references it, and the shipped `mcp.json` declares only the stdio
entry, so nothing loads it automatically:

```json
{
  "mcpServers": {
    "omega-prime": {
      "url": "https://omega.example.com/mcp",
      "headers": {
        "Authorization": "Bearer ${OMEGA_PRIME_MCP_TOKEN}"
      }
    }
  }
}
```

The token reference depends on who reads the configuration:

| Consumer | Token reference | Evidence |
| --- | --- | --- |
| Grok Bot Remote HTTPS server | A Bot secret; how the form references it is UNPROVEN (user UAT row 6) | CONFIRMED that secret-needing servers use Remote HTTPS, [team bots](https://docs.x.ai/grok-bot/team-bots) |
| Cursor plugin `mcp.json` | `${OMEGA_PRIME_MCP_TOKEN}`, a plugin variable whose value a team admin sets in the dashboard under **Plugins** | CONFIRMED, [plugin reference](https://cursor.com/docs/reference/plugins.md) |
| Standalone Cursor `mcp.json` | `${env:OMEGA_PRIME_MCP_TOKEN}` | CONFIRMED, [Cursor MCP](https://cursor.com/docs/mcp.md) |
| Grok Build CLI `.grok/config.toml` | `${OMEGA_PRIME_MCP_TOKEN}` (`${VAR:-default}` also expands) | CONFIRMED, [Grok Build MCP servers](https://docs.x.ai/build/features/mcp-servers) |

`python -m omega_prime.tooling.plugin --verify` checks the example's shape
(an `https://` URL ending in `/mcp`, only `${VAR}` placeholders, no literal
token) and verifies a loopback bearer host at `/mcp` over SSE and Streamable
HTTP. That is local proof only; a public host is user UAT row 6.

## Security model

Authentication reads the `Authorization` header only. A token in the query
string, including `?token=`, is ignored and the request is 401 with
`WWW-Authenticate: Bearer realm="omega-prime"`. Comparison is constant time
over SHA-256 digests. Failure records carry a reason (`missing`,
`malformed`, `invalid`), never the credential.

### Scopes

Three scopes, each covering the ones below it: `admin` includes `call` and
`read`; `call` includes `read`.

| Scope | Allows |
| --- | --- |
| `read` | `GET /manifest.json` and, by default, `GET /metrics` |
| `call` | Those, plus tool calls |
| `admin` | Those, plus `/admin/approvals` |

The single token from `--token-file`, `--token`, or `MCP_AUTH_TOKEN` is one
principal, id `env-token`, with scopes `read` and `call`. It can list and
call tools. It receives 403 on `/admin/approvals`. A read-only store token
that calls a tool gets an MCP error result
`forbidden: principal <id> lacks the call scope`. That denial is written to
the audit log and counted in `omega_tool_denials_total`.

There is no OAuth, no refresh, and no per-tenant seat. Principals are
credentials on this one process.

### Token file and the token store

`--token-file` is one plaintext line, mode 0600. The hashed store is a
different file, `--token-store`, managed by:

```bash
python -m omega_prime.grokbot.tokens new --file ~/.config/omega-prime/tokens.json \
  --label bot
python -m omega_prime.grokbot.tokens new --file ~/.config/omega-prime/tokens.json \
  --scope admin --label approver
python -m omega_prime.grokbot.tokens list --file ~/.config/omega-prime/tokens.json
python -m omega_prime.grokbot.tokens list --file ~/.config/omega-prime/tokens.json --json
python -m omega_prime.grokbot.tokens revoke --file ~/.config/omega-prime/tokens.json tok_1a2b3c4d
```

`new` prints the plaintext token once on stdout and the id on stderr. The
file stores SHA-256 only, mode 0600, shape
`{"version":1,"tokens":[{"id","sha256","scopes","label","created_at","expires_at","revoked_at"}]}`.
Ids are `tok_` plus 8 hex digits. `--scope` is repeatable (`read`, `call`,
`admin`); omitted, the token gets `read` and `call`. `--label` is free
text. `--ttl-days` must be positive. `list` never prints digests. `revoke`
marks the id; an unknown id exits 1.

The host re-reads the file when its size or mtime changes, at most once a
second. A revoked or expired token fails the next check with 401 and the
process does not restart. An unreadable or corrupt store denies every token
in that store and `/readyz` reports `token_store: degraded` (503). If a
single bearer token is also configured, that token still verifies; the file
store is consulted first. Readiness stays 503 until the file can be parsed.

Give the bot a `read`+`call` token. Keep `admin` on a separate token. The
approver recorded for a grant is that token's label, or its id when the
label is empty — never the bot.

### Host and Origin

Host validation is on when the bind is loopback, or you set `--public-url`,
or you pass `--allow-host`. Allowed Host values then include loopback
(`127.0.0.1`, `localhost`, `::1`), the bind address, and the public URL's
host. `--allow-origin` adds browser origins; `--public-url` adds its origin.
CORS answers only origins you named, never `*`.

On a non-loopback bind with a credential but no public URL and no
`--allow-host`, Host validation stays off. A request that sends an `Origin`
not on the allow list to `/sse`, `/messages`, or `/mcp` is still 403
(`Invalid Origin header`). A client that sends no `Origin` passes that check.

### Audit log

Every tool call and every authentication failure is one JSON line in a
hash-chained log. The default path is `$OMEGA_PRIME_AUDIT_LOG` or
`./.planning/grokbot_audit.jsonl`. The server's `--audit-log` overrides it.
New files are mode 0600.

Argument values are not stored. A tool-call record keeps the argument key
names (`arg_keys`) and a SHA-256 of the canonical arguments (`args_sha256`).
`details` is redacted before it is hashed (bearer tokens, `omk_` tokens,
API-key assignments, `Authorization` and `Cookie` values). Literal text and
file paths are different inputs to the secret gate, and a token-shaped path
gets no exemption: `assert_no_secrets` scans a `str` inline as text — even
when the string names a file — while any `os.PathLike` is treated as a file
whose 0600 mode is enforced before its bytes are read.

```bash
python -m omega_prime.grokbot.audit verify
python -m omega_prime.grokbot.audit verify --path /var/lib/omega-prime/audit.jsonl --all
python -m omega_prime.grokbot.audit tail -n 20
```

`verify` exits 0 when the chain is intact and 1 when a record was edited,
removed, or reordered (it names the first bad line). `--all` includes
rotated backups (`.1`, `.2`, …). `tail` prints the newest records as JSON
lines; `-n` defaults to 20.

## Approval gateway

Approval-gated tools (deploys, publishes, merges, tracker writes) stay
gated. `--approve TOOL:APPROVER` grants one at startup. Otherwise an admin
principal grants a TTL at runtime. The bot's own token cannot: a `call`
token receives 403 `{"error":"admin scope required"}`, and the approval log
refuses an approver equal to the seat id `bot-00-omega-prime`.

The routes exist only when authentication is enabled. Bodies larger than
4096 bytes are 413. The JSON object may contain only `tool` and
`ttl_seconds`. Omit `ttl_seconds` and the grant lasts
`min(3600, --approval-max-ttl)` seconds. A non-positive TTL, or one above
`--approval-max-ttl`, is 422 (it is not clamped). A name that is not in the
gated set is 404. Revoking a missing or already expired approval is 404.

```bash
# gated names, and approvals that are still live
curl -sS -H "Authorization: Bearer $ADMIN_TOKEN" \
  http://127.0.0.1:8000/admin/approvals

# 201: approved, tool, approved_by, expires_at
curl -sS -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tool":"TOOL","ttl_seconds":600}' \
  http://127.0.0.1:8000/admin/approvals

# {"revoked":true}
curl -sS -X DELETE -H "Authorization: Bearer $ADMIN_TOKEN" \
  http://127.0.0.1:8000/admin/approvals/TOOL
```

Replace `TOOL` with a name from `gated_tools` in the list response. Grants
are audited as `approval_granted` and revokes as `approval_revoked`, with
the tool, the TTL, and the approver. The approval log is in memory: a
restart drops it. Startup `--approve` flags are applied again on the next
start.

## Traffic protection

These knobs are on `python -m omega_prime.mcp_server`. `/healthz` and
`/readyz` are never limited and never count as auth failures. `0` disables
the HTTP limit, the global HTTP limit, the tool-call limit, the auth-failure
throttle, and the breaker (`--breaker-threshold 0`). `--breaker-cooldown 0`
does not disable the breaker; it only shortens the open period.

- **HTTP rate limit.** `--rate-limit` (default 600 requests/min per
  principal; the bucket holds that many, then refills). The key is the
  principal id, or the client address when there is no principal.
  `--global-rate-limit` (default 0, off) caps every principal together. A
  denial is 429 `{"error":"rate_limited","retry_after":n}` plus a
  `Retry-After` header, `n` in whole seconds.
- **Tool-call rate limit.** `--tool-rate-limit` (default 300/min per
  principal, or `local` when the caller has no principal). The denial is an
  MCP error, code `rate_limited`, and it is audited.
- **Auth-failure throttle.** `--auth-failure-limit` (default 10 failures in
  60 seconds per client address). Further requests from that client are 429
  `{"error":"too_many_auth_failures","retry_after":n}` and do not reach
  authentication, so they do not extend the window.
- **Circuit breaker.** `--breaker-threshold` (default 5) consecutive
  infrastructure failures on one tool open its circuit for
  `--breaker-cooldown` seconds (default 30), jittered by 20 percent. While
  open, calls are denied with code `circuit_open` and the tool does not
  run. After the cooldown one probe is allowed. Infrastructure means an
  `upstream_error`, a handler exception, a timeout, or a connection
  failure. Policy, approval, validation, `not_configured`, `forbidden`,
  `rate_limited`, and `circuit_open` do not open the breaker.

Both transports share one gate, so a limit or an open circuit applies
whether the client used `/sse` or `/mcp`.

## Observability

`GET /metrics` (scope `read` unless `--metrics-scope` says otherwise)
exposes:

| Series | Labels |
| --- | --- |
| `omega_http_requests_total` | `method`, `path` (a fixed group: `/sse`, `/messages`, `/mcp`, `/healthz`, `/readyz`, `/metrics`, `/manifest.json`, `/admin`, or `other`), `status` class |
| `omega_http_request_duration_seconds` | `method`, `path` |
| `omega_http_in_flight` | — |
| `omega_tool_calls_total` | `tool` (or `unknown` if the name is not served), `status` (`ok`, `error`, `denied`) |
| `omega_tool_call_duration_seconds` | `tool` |
| `omega_tool_calls_in_flight` | — |
| `omega_tool_denials_total` | `code` |
| `omega_auth_failures_total` | `reason` (`missing`, `malformed`, `invalid`, `other`) |
| `omega_build_info` | `version`, `package_version` (value 1) |

The host mints `X-Request-Id` (the first 16 hex characters of the trace id)
and ignores a client-supplied one. A well-formed inbound `traceparent` is
continued with a new span; a malformed one is replaced and not echoed. The
same request id is stored on the audit record for that call.

`--log-format json` writes one JSON object per line on the `omega_prime`
logger: `ts` (UTC), `level`, `logger`, `msg`, `request_id`, and any
JSON-safe extra fields. `text` is the plain format. Both redact credentials
before the line is emitted. The access log (`omega_prime.access`) records
method, path with the query string removed, status, duration, principal id,
and request id. It does not record headers or bodies. The server's own
uvicorn access log is off, so a token a client puts in a URL is not written
there either.

## Operations

**Doctor** — preflight, no server required:

```bash
python -m omega_prime.grokbot.doctor --host 0.0.0.0 --auth --strict
```

Checks Python and the venv, the `prime-agent` checkout, the assembled
prompt, MCP dependencies, manifest generation, seat policy, the roster,
template integrity, the audit chain, bind exposure, token strength,
connector secret names (not values), and whether `--port` (default 8000)
is free. `--json` prints a machine-readable summary. Exit 1 on any failure;
with `--strict`, on any warning too.

**Supervisor** — restarts a crashed or unhealthy child, capped exponential
backoff, budget resets after `--stable-after` seconds (default 60). Exit 1
when `--max-restarts` (default 5) is exhausted. The default child is
`python -m omega_prime.mcp_server --transport sse`.

```bash
python -m omega_prime.grokbot.supervisor \
  --state-file .planning/grokbot_supervisor.json \
  --health-url http://127.0.0.1:8000/healthz -- \
  python -m omega_prime.mcp_server --transport sse \
  --token-file ~/.config/omega-prime/token
python -m omega_prime.grokbot.supervisor --status \
  --state-file .planning/grokbot_supervisor.json
python -m omega_prime.grokbot.supervisor --stop \
  --state-file .planning/grokbot_supervisor.json
```

`--stop` signals the supervisor, which then stops the child (SIGTERM, then
SIGKILL after `--stop-grace`, default 10 seconds).

**Emulator** — the same runtime the server uses (roster, seat policy,
approvals, audit), in-process:

```bash
python -m omega_prime.grokbot.emulator --smoke
python -m omega_prime.grokbot.emulator --tool todo_read --args '{}' --json
```

`--approve TOOL:APPROVER` pre-approves for that run. A failed smoke exits 1;
bad configuration exits 2.

**Sync** — drift against a saved manifest, only in content the bot would
see (instructions, skills, routines, tools, approval-gated tools,
capabilities). A manifest exported for another transport or URL is not
drift. Exit 1 on drift, 2 on a bad file.

```bash
python -m omega_prime.grokbot.sync --check grokbot-manifest.json
python -m omega_prime.grokbot.sync --export-prompt prompt.xml --json
```

On SIGTERM or SIGINT the host sets `/readyz` to 503 and waits for in-flight
tool calls, up to `--shutdown-grace` seconds (default 20), then exits 0.
With nothing in flight that is about a second. A second signal forces exit.

## Live verifier

`python -m omega_prime.grokbot.verify` checks a host that is already
running (this machine or a deployed container). It does not print the
token.

```bash
python -m omega_prime.grokbot.verify \
  --url http://127.0.0.1:8000 \
  --token-file ~/.config/omega-prime/token \
  --admin-token-env OMEGA_PRIME_ADMIN_TOKEN \
  --transport both \
  --json
```

Flags: `--url` (required), `--token-env NAME` or `--token-file PATH`,
`--admin-token-env NAME`, `--transport sse|http|both`, `--gated-tool NAME`,
`--require-auth`, `--timeout S` (default 10), `--json`. The human table
goes to stderr. `--json` writes the report to stdout. Exit 0 when every
check passed or was skipped, 1 when any failed, 2 on bad usage or an
unreachable URL.

The checks cover `/healthz` and `/readyz`, missing and invalid bearer
tokens, a token in `?token=` still 401, a foreign `Origin` rejected,
`X-Request-Id` and `traceparent`, `/manifest.json` (tool count matches
`rostered_tools`, a digest is present, the token string is absent), and
`/metrics` (`omega_http_requests_total`, auth required). For each requested
transport it initializes, lists tools, calls one read-only tool (preferring
`todo_read`), calls an unknown name, and calls an approval-gated tool
expecting a refusal. With `--admin-token-env` it also checks that the call
token is forbidden on `/admin/approvals` and that approve, then revoke,
opens and closes the gate. The gated tool is not run for its side effects.

```bash
python -m omega_prime.grokbot.oneclick --self-test
```

`--self-test` runs in the same disposable layout as the readiness profile:
a free loopback port, a token minted into a mode-0600 temp file, and
`python -m omega_prime.mcp_server --transport sse` on that port. The child
consumes the one frozen layout allowlist and nothing else — no
parent-locale copy, no credential blacklist, no extra forwarding — with
host roots travelling as argv. It waits for `/readyz`, runs the verifier
on both transports with auth required, sends SIGTERM, and requires exit 0
within the drain grace plus five seconds. No source-install schedule is
ticked and no live credential is inherited: the fixture schedules start
empty and the child environment is built fresh, so this is a seat check,
never live attestation. Failure diagnostics are bounded and curated, not
excerpted: complete text reaches known-secret replacement plus audit redaction and then the unchanged secret gate BEFORE any tail/clip window — known-token replacement alone and final-only scanning do not close the leak. The gate recognizes only its documented patterns (audit credential shapes, `omk_` tokens with 8+ suffix chars, PEM armour, 32+ mixed-class runs at 4.2+ bits/char entropy with hex/UUIDs excluded), not every possible secret. A trip publishes only the fixed safe label with no classified fragment and stays failed, and the final self-test publication re-verifies complete report data including metadata the same way. It conflicts with `--dry-run` (usage error), never prints the token, removes its temp files, and exits 0 only when every check passed.

## Import evidence ledger

Levels: **CONFIRMED** means the cited platform documentation states it; it
was not exercised from this repository. **PARTIAL** means part is documented
and part is not. **UNPROVEN** means undocumented, inferred from absence, or
corroborated only by third parties; it stays [user UAT](#user-uat) and is
never claimed as working.

Local proof is separate. In this repository, `python -m omega_prime.tooling.plugin --check`
fails on every drift class, and `--verify` spawned exactly the declared
`mcp.json` command, which answered `initialize` and `/omega-help`
(54 commands); the loopback bearer host passed at `/mcp`. Those are owned
local processes. None of them reaches a Grok Bot or a Cursor account.

| Platform fact | Level | Source | UAT row |
| --- | --- | --- | --- |
| A custom Command (stdio) MCP server runs on the computer each Bot conversation uses | CONFIRMED | [team bots](https://docs.x.ai/grok-bot/team-bots) | 1 |
| A Command server with environment variables or credential-looking arguments runs only in the owner's chat; the shipped `mcp.json` has neither | CONFIRMED | [team bots](https://docs.x.ai/grok-bot/team-bots) | — |
| A server that needs a secret uses Remote HTTPS; Bot secret names are uppercase letters, digits and underscores, starting with a letter | CONFIRMED | [team bots](https://docs.x.ai/grok-bot/team-bots) | 6 |
| The Bot's computer runs Linux with a durable `/workspace`; manually installed packages are replaceable | CONFIRMED | [computer and apps](https://docs.x.ai/grok-bot/computer-and-apps), [identity and access](https://docs.x.ai/grok-bot/identity-and-access) | — |
| Bot computers run in Cursor's cloud and cannot reach the operator's `localhost` | CONFIRMED | [private networks](https://docs.x.ai/grok-bot/private-networks) | — |
| The Bot's computer has Python 3.11+ with `venv`, `git`, `flock` and network for the first install | UNPROVEN (not documented) | — | 1, 5 |
| The Bot adds a custom Command server itself when asked by the setup prompt | UNPROVEN (not documented) | — | 1 |
| The Bot's MCP client waits out a cold bootstrap; its startup timeout is undocumented (the Grok Build CLI documents only its own `startup_timeout_sec`) | UNPROVEN | [Grok Build MCP servers](https://docs.x.ai/build/features/mcp-servers) | 5 |
| A Bot template carries identity, description, skills and routines | CONFIRMED | [bots](https://docs.x.ai/grok-bot/bots) | — |
| A shared template never carries plugins or MCP servers | UNPROVEN (absence of mention) | [bots](https://docs.x.ai/grok-bot/bots) | 1 |
| Import from Repo is a Cursor dashboard action that creates a team marketplace, on Teams and Enterprise plans only | PARTIAL (Cursor documents it; no docs.x.ai page names it) | [Cursor plugins](https://cursor.com/docs/plugins.md) | 2 |
| Grok Bot inherits a team marketplace as its plugin and connector policy | CONFIRMED | [teams and enterprises](https://docs.x.ai/grok-bot/teams-and-enterprises) | 3 |
| The import reads `.cursor-plugin/marketplace.json`, `.cursor-plugin/plugin.json`, `mcp.json` and component paths, which must be relative without `..` | CONFIRMED for Cursor | [plugin reference](https://cursor.com/docs/reference/plugins.md) | 2 |
| The import accepts the repo-root plugin (`"source": "./"`) | UNPROVEN (third-party corroboration only) | — | 2 |
| Grok Bot loads a plugin's skills and command files; it documents `/` only for saved skills | UNPROVEN | [skills, routines and automations](https://docs.x.ai/grok-bot/skills-routines-and-automations) | 3, 4 |
| A stdio server declared by a marketplace plugin launches on the Bot's computer | UNPROVEN | — | 3 |
| `${CURSOR_PLUGIN_ROOT}` expands to the plugin's install path | CONFIRMED for Cursor; UNPROVEN on the Bot's computer | [plugin reference](https://cursor.com/docs/reference/plugins.md) | 3 |
| Extra skill frontmatter keys (`bots`, `gates`) are ignored | CONFIRMED for the Grok Build CLI; UNPROVEN for Cursor and Grok Bot | [Grok Build skills and plugins](https://docs.x.ai/build/features/skills-plugins-marketplaces) | 4 |
| Auto refresh of a GitHub import needs the Cursor GitHub App, and re-indexing happens at most every 10 minutes | CONFIRMED | [Cursor plugins](https://cursor.com/docs/plugins.md) | 2 |
| Cloudflare quick tunnels do not carry SSE; Streamable HTTP works | CONFIRMED (adjacent Grok connectors product) | [custom MCP tunneling](https://docs.x.ai/grok/connectors/custom-mcp-tunneling) | 6 |
| The Grok Build CLI loads `.grok/config.toml` and project `.mcp.json`; this applies to the CLI, not the Bot app, and the repository ships neither file | CONFIRMED (CLI only) | [Grok Build MCP servers](https://docs.x.ai/build/features/mcp-servers) | — |
| A skill saved under an old name (`secrets-handling`, `supply-chain`) persists in a private Bot skill library | UNPROVEN | — | 4 |

These sources were read on 2026-10-12 and change often; re-read them before
relying on a CONFIRMED row. The project lists nothing in a public
marketplace, runs no hosted HTTPS deployment and publishes no image for this
route.

## User UAT

Live import is unproven until you run these rows on your own accounts and
record the date and exact result. A local pass never substitutes for a row.
If a row fails, use the other route: the Bot-driven setup in
[grokbot/SETUP.md](../omega_prime/grokbot/SETUP.md) or the
[HTTPS alternative](#https-alternative-remote-https).

| # | Check | How | Status |
| --- | --- | --- | --- |
| 1 | The default Grok Bot clones the repository into `/workspace/omega-prime`, `--bootstrap-only` exits 0, and the custom Command server is added (by the Bot or by you) | Send the setup prompt; confirm `tools/list` shows `omega_command` and `/omega-help` answers. Note whether a Bot made from a shared template already had the server | UNPROVEN |
| 2 | Cursor Teams/Enterprise Import from Repo lists `omega-prime` | Cursor dashboard → Plugins & MCPs → Team Marketplaces → Add Marketplace → Import from Repo with the GitHub URL | UNPROVEN |
| 3 | Grok Bot shows the plugin as team-provided or installable, and its `omega-prime` server connects | Open the Bot after row 2 | UNPROVEN |
| 4 | Whole-message `/omega-help` and `/omega-code-review` run `omega_command` | Type each as the whole message. Record which `/omega-*` entries and plugin skills appear natively in `/`, whether skills load with their extra frontmatter keys, and whether old `secrets-handling` or `supply-chain` copies remain in your private skill library | UNPROVEN |
| 5 | The first attach completes the bootstrap within the client timeout or reports a class 10–12 line; the second attach is fast | Attach twice; record both timings and any class line | UNPROVEN |
| 6 | An operator-hosted `https://…/mcp` added as Remote HTTPS with a Bot secret answers `tools/list` | Follow the [HTTPS alternative](#https-alternative-remote-https) | UNPROVEN |

## Deployment

Images, compose, systemd, and Kubernetes are rendered and checked with
`python -m omega_prime.grokbot.deploy`. The hardening, the token mount, the
state volume, and the probe paths are [Deploying the tool host](deploy.md).
Do not copy those manifests from this page.

The image is about 2 GB because the dependency tree includes PyRIT and
transformers. Every target runs non-root on a read-only root filesystem
with one writable state volume.

## Limits

- Credentials are static scoped bearer tokens. This host does not speak
  OAuth.
- One seat. One process, one roster, one seat policy. Scopes separate
  callers; they do not create tenants.
- Tools run one at a time, in a worker thread, on one shared gate for both
  transports. They share files without locks. A slow tool does not freeze
  `/healthz` or shutdown.
- State needs a writable volume. Memory, the session database, and the
  audit log are files. On the read-only image that volume is
  `/var/lib/omega-prime` (`OMEGA_PRIME_STATE_DIR`,
  `OMEGA_PRIME_AUDIT_LOG`); see [Deploying the tool host](deploy.md).
  Approvals are not on that volume: they live in the process and are gone
  after a restart unless you pass `--approve` again.
- Streamable HTTP sessions live in the process (at most `--max-sessions`,
  default 64). One replica, unless a proxy pins a session to it.
- Python floor `>=3.11` (see [Setup](setup.md) for the exercised and CI
  interpreters). The verifier, emulator self-test, and sync checks above
  are local seat checks: they never print tokens, never inherit live
  credentials, and never assert a deployment or a signed approval beyond
  this host's own approval log.

## Further reading

Execution and security boundaries live in [Architecture](architecture.md);
the hand-run checks live in [Local validation](local-validation.md). The
Greptile-synthesized history is a separately labelled untrusted archive —
see [Greptile](greptile.md) — never current operator truth.
