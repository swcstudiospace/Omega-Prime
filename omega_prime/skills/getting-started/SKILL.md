---
name: getting-started
description: "Self-install for a freshly imported bot: clone a pinned release into /workspace/omes-bot, bootstrap, ask the owner to add the stdio Command server, then collect secrets only through request cards."
bots: [bot-00-omega-prime]
gates: []
---
<skill name="getting-started" version="1">
# Getting started

Use this on the first turn of a bot imported from the Omega Prime template.
The template does not carry the Command server. You clone, bootstrap, and
then the owner adds the server. You never ask for a secret in chat.

## 1. Clone a pinned release

The repository has no git tag yet (checked 2026-10-19: `git tag` and
`git ls-remote --tags` are empty). `<release-tag>` is the placeholder pin.
Do not clone `main`. When a release tag exists, substitute that tag and
keep the same path.

```bash
git clone --recurse-submodules --branch <release-tag> https://github.com/swcstudiospace/omes-bot.git /workspace/omes-bot
```

## 2. Bootstrap, then verify

```bash
bash /workspace/omes-bot/omega_prime/scripts/grokbot-1click.sh --bootstrap-only
bash /workspace/omes-bot/omega_prime/scripts/grokbot-1click.sh --verify --json
```

Stop on exit 10, 11, or 12 and show the JSON class line from stderr.
`--verify --json` must print `{"ok": true, ...}` before you continue.

## 3. Ask the owner to add the server

Ask the owner to add a Command MCP server named `omega-prime`. The command
is absolute. It has no environment variables and no `env` block:

```bash
bash /workspace/omes-bot/omega_prime/scripts/grokbot-1click.sh --bootstrap --transport stdio
```

If you cannot add MCP servers yourself, stop and give the owner that
command. Do not invent a URL, a token argument, or an env map.

## 4. Check the command surface

Call `omega_command` with `/omega-help`. Show the owner the result.

## 5. Secrets, only through request cards

Collect a secret only with a Grok Bot secret-request card. Never ask the
owner to paste a token, key, or passphrase into chat.

Write each value to a mode `0600` file under `$OMEGA_PRIME_STATE_DIR/secrets/`.
The file name is the variable name (`X_API_TOKEN`, not a nickname). The host
reads, in order:

1. `NAME` in the environment the server process already inherited
2. `NAME_FILE`, a mode `0600` path
3. `$OMEGA_PRIME_STATE_DIR/secrets/NAME` when that file exists

The Command server entry itself stays without `env`. The launcher exports
`OMEGA_PRIME_STATE_DIR` (default `$XDG_STATE_HOME/omega-prime` or
`~/.local/state/omega-prime`), so the file in that directory is enough.

Offer these first: one delegate provider key (`XAI_API_KEY`,
`OPENAI_API_KEY`, or `ANTHROPIC_API_KEY`), GitHub auth, `LINEAR_API_KEY`,
`NOTION_TOKEN`.

Optional, same file rule: `X_API_TOKEN`, `TELEGRAM_BOT_TOKEN`,
`DISCORD_BOT_TOKEN`, `SUBSTRATE_TOKEN`, `HINDSIGHT_API_KEY`,
`RAILWAY_TOKEN`, `VERCEL_TOKEN`, `GREPTILE_API_KEY`, and the other delegate
keys (`ANTHROPIC_OAUTH_TOKEN`, `OPENROUTER_API_KEY`, `GEMINI_API_KEY`,
`GOOGLE_API_KEY`, `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, `GITHUB_TOKEN`).
`GREPTILE_GITHUB_TOKEN` uses the same rule when Greptile needs GitHub.

`HINDSIGHT_URL` stays unset until the owner runs their own service. Do not
point it at anyone else's host.

## 6. Gated tools

`lead_doctor` with action `check` is a read and does not need a grant.
`/omega-doctor` uses that check. Register, install, and repair stay gated.
Every other gated tool, including `lead_intake_next` inside `/omega-desk`,
stays gated until a person writes `$OMEGA_PRIME_STATE_DIR/approvals`.

That file is mode `0600`, lives outside the clone, and has one
`TOOL:APPROVER` line per grant. The approver is a person's name. It must
not be `bot-00-omega-prime` or another name for this bot. You do not create,
edit, or append that file. A person writes it, then restarts the server.
The grant is read once at startup. You cannot approve your own tool.
</skill>
