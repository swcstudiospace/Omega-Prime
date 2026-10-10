# Setup

Get Omega Prime running in Grok in about ten minutes. Only the
optional tool-host step needs a machine of yours.

## 1. Install from the template

Open the Omega Prime share link in Grok and choose **Add to Grok Bot**.
This creates an independent copy on your account — your chats,
secrets, and settings stay yours. The bot runs on Grok's cloud
computer with Grok's default model.

What the template carries: Omega Prime instructions, skills, routines,
and first-party plugins. What it never carries: secrets, custom
MCP servers, scripts, or private skills. The steps below re-add
the ones you want.

## 2. Secrets

Omega Prime never asks for secrets in chat. Add each value where its
connector asks for it:

| Secret | Needed for | Where it goes |
| --- | --- | --- |
| `XAI_API_KEY` | Grok model calls outside Grok Bot | host env, resolved via the credential broker |
| `X_API_TOKEN` | X connector tools | host env, read by the tool host |
| `TELEGRAM_BOT_TOKEN` | Telegram connector tools | host env, read by the tool host |
| `DISCORD_BOT_TOKEN` | Discord connector tools | host env, read by the tool host |
| GitHub token | Reviews, ship PRs | host via `gh auth` |

Skip what you don't use: every unconfigured client reports
`not_configured` instead of failing.

## 3. Optional: attach the tool host

Without this step Omega Prime still answers from its prompt, skills, and
routines. With it, the bot also calls the real Omega Prime tools (165 on
the roster; 117 served by default — the Prime families and the Red Team
family are config-gated, default off). `delegate_task` is served; without a provider env it returns
`not_configured: provider`.

On a machine you control (Linux, Mac, or WSL2, Python 3.11+):

```bash
git clone https://github.com/swcstudiospace/omes-bot.git omega-prime
cd omega-prime
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m omega_prime.mcp_server --root .
```

For the exact verified dependency set instead of fresh floors:
`.venv/bin/pip install -r requirements-lock.txt`.

Then add it as a **custom MCP** server in your Grok Bot's
settings, pointing at that command. Prefer a sandbox: see
[Tool host](tool-host.md) for the OpenShell profile and AgentOS
notes.

## 4. Smoke prompt

Send the bot exactly this:

> `ultrathink` what tools do you have, and which need approval?

Expect back:

1. A short plan of how it will answer (the ultrathink notice fired).
2. A tool list matching the roster — with the tool host attached;
   without it, the bot says which tools live behind the host
   instead of inventing results.
3. The approval-gated tools named as needing approval.
4. No secrets, ids, or tokens quoted back.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| No plan, just an answer | The word must be lowercase standalone prose, not in code |
| Tool list is vague | Tool host not attached — see step 3 |
| `not_configured` on a connector | Its secret is missing — see step 2 |
| Smoke prompt quotes a secret | Report it; that must never happen |

Builders can also run the local verification:

```bash
.venv/bin/python -m omega_prime.setup_check --root .
```

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
