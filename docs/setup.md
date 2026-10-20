# Setup

Install Omega Prime from source, configure secrets privately, and verify the
checkout locally. The no-credential validation route below uses owned local
fixtures. Optional provider, authenticated-host and bot-seat routes need the
prerequisites stated in their sections; preparation is not attachment proof.

## 1. Prerequisites

Python floor, recommendation, and CI matrix are three separate facts —
do not collapse them:

- **Formal package floor:** `>=3.11`, declared in
  [`pyproject.toml`](../pyproject.toml) (`requires-python = ">=3.11"`,
  classifiers `3.11`–`3.14`).
- **Recommended and exercised interpreter:** 3.12. Use 3.12 for onboarding
  unless you have a reason not to.
- **Retained CI matrix:** 3.12–3.14 (`.github/workflows/ci.yml`). The matrix
  passing on 3.13 or 3.14 is not a claim that every dependency is pinned
  identically there; the recorded resolution is `requirements-lock.txt`.

You also need `git`. The optional prerequisites below are only needed for
the lanes that use them — the default Python-only path needs none of them:

| Optional prerequisite | Needed for | Source |
| --- | --- | --- |
| `prime-agent/` submodule checkout at the pin | Full test suite, native extension, kernel tools | `omega_prime/contracts/prime-agent.pin.json`, `VENDOR.md` |
| Rust toolchain per `VENDOR.md` + `cargo` | Building the native extension | `README.md#build-from-source-both-toolchains` |
| Node `>= 22.19.0` | Red Team backend only | `redteam/package.json` `engines`, `docs/red-team.md` |
| Browser backend / device executable / provider key | The tools that need them | They report `blocked` with the gap named; see `docs/local-validation.md` |

## 2. Clone

The repository is `swcstudiospace/omes-bot` (see `[project.urls]` in
`pyproject.toml`). An ordinary clone leaves the tracked submodule checkouts
empty, so clone recursively:

```bash
git clone --recurse-submodules --branch <release-tag> https://github.com/swcstudiospace/omes-bot.git /workspace/omes-bot
cd /workspace/omes-bot
```

`<release-tag>` is a placeholder. This repository has no git tag yet
(checked 2026-10-19). Do not clone `main`. Substitute the tag when one
is published. `/workspace/omes-bot` is the only install path the Bot
instructions use.

Inside an existing checkout, populate afterwards instead:

```bash
git submodule update --init --recursive
```

When the optional Red Team backend is not needed, initializing only Prime
suffices:

```bash
git submodule update --init prime-agent
```

Never `git clone` a checkout manually into the `prime-agent/` or `redteam/`
submodule paths; both are tracked gitlinks pinned in `VENDOR.md`.

## 3. Install

From the repository root, with Python 3.12:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

For the recorded dependency resolution instead of fresh floors:

```bash
.venv/bin/pip install -r requirements-lock.txt
.venv/bin/pip install --no-deps -e .
```

## 4. Secrets go in the private environment, never in examples

Omega Prime never asks for secrets in chat. Supply each value privately in
the host process environment (or a `0600` token file where the tool supports
one). Never put a credential in `argv`, a config example, shell tracing, or
saved output — `--token` exists only as a deprecated flag that warns because
`argv` is visible to other users on the machine.

| Secret | Needed for | Where it goes |
| --- | --- | --- |
| `XAI_API_KEY` | Grok model calls outside a Grok Bot seat | host env, `XAI_API_KEY_FILE`, or `$OMEGA_PRIME_STATE_DIR/secrets/XAI_API_KEY` (mode 0600). The same `*_FILE` rule covers `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `ANTHROPIC_OAUTH_TOKEN`, `OPENROUTER_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, and `GITHUB_TOKEN` |
| `X_API_TOKEN` | X connector tools | host env, `X_API_TOKEN_FILE`, or `$OMEGA_PRIME_STATE_DIR/secrets/X_API_TOKEN` (mode 0600) |
| `TELEGRAM_BOT_TOKEN` | Telegram connector tools | host env, `TELEGRAM_BOT_TOKEN_FILE`, or `$OMEGA_PRIME_STATE_DIR/secrets/TELEGRAM_BOT_TOKEN` (mode 0600) |
| `DISCORD_BOT_TOKEN` | Discord connector tools | host env, `DISCORD_BOT_TOKEN_FILE`, or `$OMEGA_PRIME_STATE_DIR/secrets/DISCORD_BOT_TOKEN` (mode 0600) |
| GitHub CLI | Reviews, ship PRs | `gh auth login`, or `GH_TOKEN` in the environment of the process that runs `gh`. `gh` does not read `GH_TOKEN_FILE` or `$OMEGA_PRIME_STATE_DIR/secrets/GH_TOKEN` |
| `SUBSTRATE_TOKEN` | Substrate brief/events/memory | host env, `SUBSTRATE_TOKEN_FILE`, or `$OMEGA_PRIME_STATE_DIR/secrets/SUBSTRATE_TOKEN` (mode 0600) |
| `HINDSIGHT_API_KEY` | Shared Hindsight episodic memory, only when `HINDSIGHT_URL` is set | host env, `HINDSIGHT_API_KEY_FILE`, or `$OMEGA_PRIME_STATE_DIR/secrets/HINDSIGHT_API_KEY` (mode 0600) |
| `RAILWAY_TOKEN` | Railway status and logs | host env, `RAILWAY_TOKEN_FILE`, or `$OMEGA_PRIME_STATE_DIR/secrets/RAILWAY_TOKEN` (mode 0600) |
| `VERCEL_TOKEN` | Vercel deployments | host env, `VERCEL_TOKEN_FILE`, or `$OMEGA_PRIME_STATE_DIR/secrets/VERCEL_TOKEN` (mode 0600) |
| `GREPTILE_API_KEY` | Greptile review | host env, `GREPTILE_API_KEY_FILE`, or `$OMEGA_PRIME_STATE_DIR/secrets/GREPTILE_API_KEY` (mode 0600) |
| `MCP_AUTH_TOKEN` or `--token-file` | Bearer token a remote tool host requires | host env or a `0600` file (never `argv`) |
| `NOTION_TOKEN` or `NOTION_TOKEN_FILE` | Notion Task dashboard (read, update, and insert content) | host env, or a mode 0600 file path |
| `LINEAR_API_KEY` or `LINEAR_API_KEY_FILE` | Linear team selection and issue create | host env, or a mode 0600 file path |

Configure only the integrations you intend to use. Availability and failure
behavior are tool-specific: some tools return `not_configured`, readiness
accounts for named prerequisites, and some clients raise or degrade reads.
See [substrate](substrate.md) and [connectors](connectors.md) for those contracts.
`SUBSTRATE_URL` (default `http://127.0.0.1:7410`) overrides the substrate
peer. `HINDSIGHT_URL` defaults to unset, which disables the remote bank;
set it only to a service you operate. `omega_prime/grokbot/SETUP.md` has the
full secret table.

Send `/omega-connectors` to walk installer setup: the Notion template in
[notion-task-dashboard.md](notion-task-dashboard.md), then Notion, then
Linear, then an optional gateway. Bindings are stored outside the
repository under `OMEGA_PRIME_STATE_DIR` (or `~/.omega-prime`). The file
holds team and database ids, never tokens. An empty `prime-agent/`
checkout fails `python -m omega_prime.setup_check` with
`git submodule update --init --recursive`. `redteam/` stays optional.

There is no public Notion gallery listing for the dashboard. The
markdown page is the template: recreate that database in your own
workspace. Do not point the bot at someone else's page id.

The shipped seat policy allowlist is empty. Before `/omega-connectors`
can call Notion, Linear, or a gateway, add `api.notion.com`,
`api.linear.app`, and each gateway host to `network.hosts` in
`omega_prime/contracts/policies/omega-prime.json`, then regenerate the
sandbox policy. A host that is not listed is refused and the saved
gateway is left unchanged.

Sibling checkouts that setup does not import stay out of `.gitmodules`:

| Checkout | Why it is not a submodule |
| --- | --- |
| `hermes-agent/`, `oh-my-pi/` | Behavior ports. `VENDOR.md` says they are not imported. They stay gitignored. |
| `programming-desk` | Skills and routines are copied into `omega_prime/`. The desk repo is not started by setup. |
| `agent-substrate` | The bot talks to it over `SUBSTRATE_URL`. The server source is not imported. |
| `claude-ultrathink` | Optional. `ULTRATHINK_ROOT` points at a checkout the operator already has. |
| `vps-agent-bus` | Not imported. The desk bus is `OMEGA_PRIME_DESK_BUS_URL` when the operator sets it. |
| `hermes-linear-connector` | A separate Linear-to-kanban sync. Omega Prime does not call it. |

## 5. Verify locally with no credentials

These run with an empty credential surface and prove only this checkout:

```bash
.venv/bin/python -m omega_prime.setup_check --root .
.venv/bin/python -m omega_prime.tooling.catalog --check
.venv/bin/python -m omega_prime.tooling.plugin --check
```

`setup_check` runs the roster, template, assembly, plugin, registry,
ultrathink, and substrate checks and exits 0 only when every required check
passes. Skips (ultrathink/substrate unconfigured) are reported, never
failures — only `FAIL` fails. `tooling.catalog --check` fails when
[the tool catalog](tool-catalog.md) is stale. `tooling.plugin --check` fails
when a generated plugin file (`.cursor-plugin/`, `mcp.json`,
`omega_prime/grokbot/plugin/commands/omega-*.md`, the HTTPS example) drifts
from the checked-in skills, routines and curated commands, or when a skill's
frontmatter breaks the platform rules; `--write` regenerates the set.

The isolated readiness profile accounts for every roster tool, actually
calling eligible local handlers and classifying the rest with named
prerequisites or disabled features. It writes one JSON report:

```bash
.venv/bin/python -m omega_prime.grokbot.readiness --root . --json --output readiness-local.json
```

Exit 0 only when the validated report has `ok` true. Each tool row carries a
`status` of `exercised`, `feature_disabled`, `blocked` (with `prerequisites`
naming the real gap), or `failed`. The profile proves only this seat — a
disposable fixture on loopback — not live provider, device, browser, or
remote-service behavior. Operator reference:
`docs/local-validation.md`.

Preflight doctor for host/transport readiness:

```bash
.venv/bin/python -m omega_prime.grokbot.doctor --host 127.0.0.1 --json
```

Add `--auth --strict` when checking a remote authenticated bind; exit 1 on
any failure (with `--strict`, on any warning too).

## 6. Optional: start the tool host

Without this step the checked-out code still answers every local command
above. With it, a bot seat can also call the real tools over MCP. Stdio is a
local process; the remote listener serves legacy SSE and Streamable HTTP on
one port with bearer auth:

```bash
.venv/bin/python -m omega_prime.mcp_server --root .
```

`--root` is the repository checkout (default: cwd). `--work-root` selects the
directory the desk tools act on (default: `OMEGA_PRIME_WORK_ROOT`, else
`--root`). `--home` is the working directory of the `execute_code` and
`mcp_call` tools (default: `~`). Desk state is stored under
`OMEGA_PRIME_STATE_DIR`. Without `--no-roster` the server exposes the shipped
roster's default set — the intersection `setup_check` reports. Prime families
stay off by default. `delegate_task` is served; without a provider env it
returns `not_configured: provider`.

Prefer the 1-click launcher. It resolves the repository from its own
location, runs the preflight doctor before a plain serve, and refuses an
unauthenticated non-loopback bind:

```bash
bash omega_prime/scripts/grokbot-1click.sh --bootstrap --transport stdio
bash omega_prime/scripts/grokbot-1click.sh --bootstrap-only
bash omega_prime/scripts/grokbot-1click.sh --verify --json
./scripts/grokbot-1click.sh --transport stdio
./scripts/grokbot-1click.sh --transport stdio --dry-run
```

`--bootstrap` builds `.venv` from `requirements-lock.txt` once, or reuses it
when its completion stamp matches (an unstamped operator `.venv` is used
unchanged or refused, never replaced), runs the plugin `--check`, then
serves stdio without the doctor; it is the command the root `mcp.json`
declares. `--bootstrap-only` stops after the bootstrap.
`--verify` bootstraps, then runs `python -m omega_prime.tooling.plugin --verify`
against the declared stdio entry and, for the HTTPS example, a loopback
bearer host at `/mcp` (a public HTTPS host is not provable locally). Bootstrap
failures exit 10 (`missing_prerequisite`), 11 (`install_failed`), 12
(`catalog_drift`) or 13 (`host_unreachable`, verify only) with one JSON
line on stderr. `--dry-run` does everything except serve. `--self-test`
starts a throwaway loopback host and runs the live verifier. Setting
Omega Prime up on a Grok Bot is section 1 of
[grokbot/SETUP.md](../omega_prime/grokbot/SETUP.md). Operator references:
`docs/tool-host.md` (server), `docs/grok-bot-native.md` (launcher, remote
host, HTTPS alternative, evidence ledger), `docs/deploy.md` (deploying it).

Sandbox the host whether or not any Prime flag is on: the default host
already serves ungated tools that run code and write files. See
`docs/tool-host.md#openshell`.

## 7. Smoke prompt (needs a bot seat)

This step is preparation only — it does not attach any live model, account,
or provider. With the host running and registered on your seat, send the bot
exactly this:

> `ultrathink` what tools do you have, and which need approval?

Expect back:

1. A short plan of how it will answer (the ultrathink notice fired).
2. A tool list matching `contracts/tool-rosters/omega-prime.yaml` — with
   the tool host attached; without it, the bot says which tools
   live behind the host instead of inventing results.
3. The approval-gated tools named as needing approval.
4. No secrets, ids, or tokens quoted back.

| Symptom | Check |
| --- | --- |
| No plan, just an answer | The word must be lowercase standalone prose, not in code |
| Tool list is vague | Tool host not attached — see step 6 |
| `not_configured` on a connector | Its secret is missing — see step 4 |
| Smoke prompt quotes a secret | Report it; that must never happen |

The Prime capability families are off by default. To turn them on and drive
the bot with them, see [Driving the bot](driving-the-bot.md).

## 8. Token rotation (scheduled host-token rotation)

Host-token rotation replaces a live bearer token with a new one without an
authentication outage. The old token stays valid for a configurable overlap
window, then expires on the same expiry path `--ttl-days` uses; `revoke`
stays immediate and is the break-glass path. Every rotation appends one
hash-only `token_rotated` event to the audit chain. Manual minting, listing,
revocation, and TTL behavior are unchanged.

Manual rotation (works regardless of any flag):

```bash
.venv/bin/python -m omega_prime.grokbot.tokens new --file /run/secrets/omega_prime_tokens.json --label ci --ttl-days 30
.venv/bin/python -m omega_prime.grokbot.tokens rotate --file /run/secrets/omega_prime_tokens.json --label ci --overlap-hours 1
.venv/bin/python -m omega_prime.grokbot.tokens list --file /run/secrets/omega_prime_tokens.json --json
.venv/bin/python -m omega_prime.grokbot.tokens revoke --file /run/secrets/omega_prime_tokens.json tok_1a2b3c4d
```

`rotate` takes `--label LABEL` or `--id ID` (exactly one), prints the new
plaintext exactly once to stdout (the id goes to stderr), and links the two
entries via `rotated_from`/`rotated_to` digests visible in `list --json`.
Use `--dry-run` with `--label`/`--id` to preview without mutating.
The audit event lands in `<store-dir>/grokbot_audit.jsonl` unless
`--audit-file PATH` names the deployment's chain file.

Scheduled rotation rides the existing cron heartbeat — no new scheduler.
First do a dry run, then enable:

```bash
.venv/bin/python -m omega_prime.grokbot.tokens rotate --file /run/secrets/omega_prime_tokens.json --due-only --dry-run
export OMEGA_PRIME_ROTATION_ENABLED=1
export OMEGA_PRIME_ROTATION_OVERLAP_HOURS=24
```

The same two keys live in `omega-prime.json` as
`{"rotation": {"enabled": false, "overlap_hours": 24}}` (off by default;
file values merge under environment overrides). Invalid
`OMEGA_PRIME_ROTATION_OVERLAP_HOURS` values warn on stderr and fall back to
the default — config loading never crashes and never silently enables
rotation.

Create one heartbeat job (via `heartbeat_set`) whose prompt runs the
explicit command on an explicit store path, for example:

```text
session: ops-token-rotation
prompt: Run `.venv/bin/python -m omega_prime.grokbot.tokens rotate
  --file /run/secrets/omega_prime_tokens.json --due-only` and report the
  stderr summary. If it prints a disabled-configuration skip line, do
  nothing. Never print a token plaintext anywhere except the command's own
  stdout capture; restrict access to the job log the same way you restrict
  the token file (0600, operators only).
interval: daily (86400 seconds), or faster than the smallest token TTL you use.
```

With `rotation.enabled` false the job prints one skip line
(`rotation disabled by configuration ...; skipping`) and mutates nothing —
that is the expected output until the flag is on. `rotate --due-only`
rotates only entries expiring within the window (default: the configured
`rotation.overlap_hours`, or `--overlap-hours` when given), exits 0 with a
`no tokens due for rotation` line when there is nothing to do (exit 1 is
reserved for real errors), and treats an unreadable audit log as a stderr
warning rather than a failure. Manage the job with `heartbeat_list` /
`heartbeat_clear`.

Overlap sizing: keep the CLI default (1h) for manual rotations and 24h for
scheduled runs unless tokens live long enough to need more. Smaller windows
bound the exposure when both tokens leak; either token can still be revoked
immediately at any point inside the window.

Rollback: set `OMEGA_PRIME_ROTATION_ENABLED=0` (or the node key false) — the
heartbeat job skips and nothing rotates. Already-overlapping entries keep
their horizon expiry; revoke either side immediately with the pre-existing
`revoke` command.

Visibility: `/metrics` (same scope-gated endpoint as before) exposes
`omega_token_rotations_total` (completed rotations),
`omega_token_expiry_total` (entries observed expired, overlap-close or TTL),
and `omega_token_active{label_hash}` (live entries per SHA-256 label hash —
never the raw label). Denials still surface on
`omega_tool_denials_total{code}`. Verify the chain after one rotation with
`python -m omega_prime.grokbot.audit verify` and confirm the newest record
is `token_rotated` with no token material in the file.

## 9. omp engine

The coding loop can run on [omp](https://omp.sh) (`oh-my-pi`, major 18) over
its RPC mode. Omega Prime supplies the tools, skills, prompts, and loop
policy. omp performs the inference through its own provider logins or API
keys. The Python process does not call model HTTP APIs for that path.

Install omp 18.x and sign in with `omp login` for the plans you intend to
use, or export the API key for that provider in the host environment. The
engine reads remaining quota with `omp usage --json`.

The default compliance allowlist is empty. A plan is used only when it is
allowlisted:

| Plan | How it becomes allowlisted |
| --- | --- |
| Z.ai | `OMEGA_PRIME_OMP_ALLOW_ZAI=1` (the `ZAI_API_KEY` variable alone does not allow it) |
| xAI | `XAI_API_KEY`, or `OMEGA_PRIME_OMP_ALLOW_XAI=1` for the subscription opt-in |
| Anthropic | `ANTHROPIC_API_KEY` only. Subscription OAuth is never allowlisted |
| Muse | `META_API_KEY` or `MUSE_API_KEY` only. Subscription OAuth is never allowlisted |
| Cursor | `CURSOR_API_KEY`, or `OMEGA_PRIME_OMP_ALLOW_CURSOR=1` for the subscription opt-in |
| ChatGPT | `OMEGA_PRIME_OMP_ALLOW_CHATGPT=1` after `omp login openai-codex` (or `openai-codex-device` on a headless host). `OPENAI_API_KEY` does not allow it |

The ChatGPT plan is only for the operator's own work, with their own single
ChatGPT account. It covers two places: local omp sessions on the operator's own
machine, and the Railway coding fleet's opt-in ChatGPT lane, which runs only the
operator's own jobs, at most one ChatGPT job at a time, and only when a job opts
in. Z.ai and Grok Build stay the fleet defaults. Do not pool or rotate accounts,
and do not serve anyone else's requests. Fleet support itself ships in the
coding-fleet repository, not here.

`omega_capacity` reports each plan's remaining quota and whether it is
allowlisted. It does not return key values.

Each run writes `$OMEGA_PRIME_STATE_DIR/jobs/<id>/events.jsonl` (or
`~/.omega-prime/jobs/<id>/events.jsonl`). Tail it with:

```bash
python -m omega_prime.engine.watch JOB --follow
```

The `omega watch JOB` console script is the same command. When `tmux` is on
`PATH`, the engine also opens a session named `omega-<job>`. Set
`OMEGA_PRIME_ENGINE_TMUX=0` to skip that session.

The engine uses bubblewrap when `bwrap` is on `PATH`. Without bubblewrap it
refuses to start unless `OMEGA_PRIME_ENGINE_UNSANDBOXED=1` is set.

The session `mcp.json` starts `python -m omega_prime.mcp_server` with
`--root` set to this installation and `--work-root` set to the checkout.
Connector settings already in the environment (`X_API_TOKEN`,
`TELEGRAM_BOT_TOKEN`, `DISCORD_BOT_TOKEN`, `SUBSTRATE_TOKEN`, and the other
desk client variables) are passed through to that process. They are not
written into `mcp.json`. Plan mode, a narrowed tool roster, and a
`child_tool_hook` stay on the Python loop so those turns do not receive
tools the caller removed. `omega watch JOB --follow` waits for the event file to appear, then until
the job ends (`agent_end`, or `job_end` when a launch fails after the event
file exists). Pass `--timeout` to bound that wait. A refusal before the prompt
is sent does not spend a caller-supplied iteration budget.

`OMEGA_PRIME_ENGINE=omp` makes the conversation loop, delegate, desk-lead
pass, and ultrathink turn require the engine. Unset, or `auto`, uses the
engine when omp, an allowlisted plan, and a sandbox are all available, and
otherwise keeps the existing Python loop. `OMEGA_PRIME_ENGINE=off` keeps the
Python loop. Dispatch backends (`omega_dispatch`, job status, job cancel,
and the fleet client) are a later change.

## Contributor CI

The `verify`, `lint`, and `types` jobs each run on Python 3.12, 3.13,
and 3.14. They use the stdlib-only
`omega_prime/scripts/ci_receipt.py` helper to record the actual interpreter,
command exit codes, and stage outcomes without masking command
failures. The verification summary also checks the named Discord
missing-library guard in the suite's JUnit report.

Receipt summaries run even after an earlier step fails. Missing, skipped,
cancelled, or failed required evidence prevents a passing summary; a green
summary is not a replacement for the underlying job results.

The `docs` job runs on Python 3.12 from a base install
(`pip install -e . pytest`). It runs the documentation and asset tests
(`omega_prime/tests/test_docs.py`, `omega_prime/tests/test_public_repo.py`),
then `python -m omega_prime.tooling.catalog --check` and
`python -m omega_prime.tooling.plugin --check`, so a stale tool catalog or
plugin set fails CI.

## Claim limits

- No step above attaches a live Grok account, model, or provider. A local
  model double (for example the `ScriptedModel` used by readiness and
  workflow checks) proves local composition only — never live attachment.
- Missing-key, skipped, and `not_configured` outcomes are unexecuted
  prerequisites, not successful runs.
- No time estimate is given: completion time depends on your machine and
  network, and this page does not measure it.
