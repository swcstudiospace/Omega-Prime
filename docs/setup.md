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
git clone --recurse-submodules https://github.com/swcstudiospace/omes-bot.git omega-prime
cd omega-prime
```

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
| `XAI_API_KEY` | Grok model calls outside a Grok Bot seat | host env, resolved via the credential broker |
| `X_API_TOKEN` | X connector tools | host env, read by the tool host |
| `TELEGRAM_BOT_TOKEN` | Telegram connector tools | host env, read by the tool host |
| `DISCORD_BOT_TOKEN` | Discord connector tools | host env, read by the tool host |
| GitHub token | Reviews, ship PRs | host via `gh auth` |
| `SUBSTRATE_TOKEN` | Substrate brief/events/memory | host env, resolved via the credential broker |
| `HINDSIGHT_API_KEY` | Shared Hindsight episodic memory | host env, resolved via the credential broker |
| `MCP_AUTH_TOKEN` or `--token-file` | Bearer token a remote tool host requires | host env or a `0600` file (never `argv`) |

Configure only the integrations you intend to use. Availability and failure
behavior are tool-specific: some tools return `not_configured`, readiness
accounts for named prerequisites, and some clients raise or degrade reads.
See [substrate](substrate.md) and [connectors](connectors.md) for those contracts.
`SUBSTRATE_URL` (default `http://127.0.0.1:7410`) and `HINDSIGHT_URL` override
the respective peers; `omega_prime/grokbot/SETUP.md` has the full secret table.

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
against the declared stdio entry and a loopback HTTPS host. Bootstrap
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

## Claim limits

- No step above attaches a live Grok account, model, or provider. A local
  model double (for example the `ScriptedModel` used by readiness and
  workflow checks) proves local composition only — never live attachment.
- Missing-key, skipped, and `not_configured` outcomes are unexecuted
  prerequisites, not successful runs.
- No time estimate is given: completion time depends on your machine and
  network, and this page does not measure it.
