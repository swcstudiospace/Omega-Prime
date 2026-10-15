# Omega Prime

One Grok programming bot: a conversation loop with an MCP tool host, installed through a Bot-driven prompt.

Paste the [install prompt](omega_prime/grokbot/listing/prompts/install.md) into a Grok Bot on a computer you own. The prompt clones this repository recursively into `/workspace/omega-prime`, runs the one-click bootstrap, and registers this repository as the Bot's custom Command MCP server:

```bash
bash /workspace/omega-prime/omega_prime/scripts/grokbot-1click.sh --bootstrap --transport stdio
```

## Install

Start with the [install prompt](omega_prime/grokbot/listing/prompts/install.md). It is the primary route: the Bot sets up the checkout, runs the bootstrap, and adds a custom Command MCP server running:

```bash
bash /workspace/omega-prime/omega_prime/scripts/grokbot-1click.sh --bootstrap --transport stdio
```

A Cursor Teams or Enterprise team marketplace can import the repository as a secondary route (`.cursor-plugin/`, root `mcp.json`).

## Follow-up prompts

After installing, work through the [follow-up prompts](omega_prime/grokbot/listing/prompts/follow-up/INDEX.md): verify the install, tour the help, run a health check, complete a first real task, and troubleshoot.

## Docs

- [Docs index](docs/README.md) — canonical setup, usage, and integration guidance.
- [Developer guide](docs/developer-guide.md) — architecture, quickstart, configuration, source build, layout, and contributing.
- [Marketing](docs/marketing.md) — positioning, use cases, and launch copy.

<!-- Compatibility anchor: older links point at README.md#build-from-source-both-toolchains; the canonical instructions now live in docs/developer-guide.md. -->
<a id="build-from-source-both-toolchains"></a>

Build from source instructions for both toolchains are in the [developer guide](docs/developer-guide.md#build-from-source-both-toolchains).

[![license](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)

## License

AGPL-3.0-only — see [LICENSE](LICENSE).

Live Grok Bot and Cursor import is UNPROVEN until the 77.1 user-acceptance rows in [docs/grok-bot-native.md](docs/grok-bot-native.md#user-uat) run on a real seat. Nothing here claims a connected Bot, a published listing, or a marketplace entry.
