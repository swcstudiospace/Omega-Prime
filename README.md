![Omega Prime banner](assets/banner.svg)

[![ci](https://github.com/swcstudiospace/omes-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/swcstudiospace/omes-bot/actions/workflows/ci.yml)
[![license](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

# Omega Prime

One Python programming agent combines Hermes conversation-loop and oh-my-pi
harness behavior ports with selected, default-off Prime capabilities. It
ships an MCP tool host and a Grok Bot template, not proof of a connected
Grok account or a whole upstream-runtime port. Provider/service operations
need their actual credentials, authorization and runtime prerequisites.

## What it is

Omega Prime is the Autonomous Event-Driven ADLC Programming Bot (ADLC: Agentic
Development Life Cycle). The operator provides the start of a task, as a
`/gsd-autonomous` prompt; Omega Prime provides the end: uplift, tracker
kickoff, build, pull request, a Greptile 5/5 plus green-CI merge gate, its
own merge, and a report back. Work is triggered by events (pull request,
review, CI, intake, message), never by timers. The only stop is new paid
spend, which it presents with a projected cost. The standing rules live in
the prime directives (`omega_prime/prompts/_shared/core-directives.xml`) so
every exported copy carries them. See
[Autonomous Event-Driven ADLC Programming Bot](docs/adlc.md) for the loop and for what is
implemented here versus provided by the host.

## Architecture

One conversation loop: Hermes and Omp behavior ports, with selected Prime
capabilities. `prime.kernel.enabled` (default off) enables pinned Python
runtime tools and native crate helpers. `OmegaPrimeAgent.from_prime` separately
selects the native `pa-ai` provider path for that same loop. No sidecar daemon;
linking all nine crates does not import Prime's entire application runtime.


Current process, execution, storage and authorization boundaries are in
[architecture](docs/architecture.md), the [agent loop](docs/agent-loop.md)
and [design/extension patterns](docs/build-aesthetics.md). The two merge
ADRs record historical decisions, not a substitute for current source.

- **One agent, one process** — registry-backed tools and the existing conversation
  loop, with an explicitly selectable native Prime provider.
- **One tool roster** — coding, browser, devices, X/Telegram/Discord connectors,
  Programming Desk packs, ultrathink, substrate tools, and Prime families.
  Enabled registrations and policy/approval checks determine availability.
  Disabled Prime families are absent from the effective tools and assembled
  tool list; the policy contract remains an allowlist superset.
- **Shipped skills and routines** — prompt-native coding, review, intake,
  sweep and learning flows, with an optional Red Team pack kept distinct.
- **Substrate surface** — briefs on open, reports its tool trail with graph
  provenance, shares memory, recalls Hindsight episodes, answers docs from
  RAGflow. Unwired installs run fully local.
- **Repeatable local proof** — a maintained isolated readiness command
  exercises eligible handlers and classifies every remaining roster member.
  [Local validation](docs/local-validation.md) separates exercised effects
  from disabled features, named prerequisites and unproven live integrations.

## Quickstart

Use Python 3.12 for this documented route. Package metadata declares
`>=3.11`; the retained CI verification/lint/type matrices target 3.12–3.14.
Those are distinct compatibility claims. The full Python suite also needs
the pinned Prime submodule; Node/Rust are optional capability prerequisites,
not requirements to run the core Python tool host.

```bash
git clone --recurse-submodules https://github.com/swcstudiospace/omes-bot.git omega-prime
cd omega-prime
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest omega_prime/tests -q
.venv/bin/python -m omega_prime.setup_check --root .
```

An ordinary `git clone` does not populate the child checkouts. Either
clone with `--recurse-submodules` as above, or populate afterwards:

```bash
git submodule update --init --recursive
```

When the optional Red Team backend is not needed, init only Prime:

```bash
git submodule update --init prime-agent
```

Never `git clone` a Prime checkout manually into the populated
`prime-agent/` submodule path. `prime-agent/` is a tracked gitlink
preserved as-is; the new root `redteam/` gitlink (T3MP3ST, pinned in
[VENDOR.md](VENDOR.md)) sits alongside it. The Python package, the
typed adapters, and the vendored 14-skill / five-seat Red Team pack
under `omega_prime/redteam/pack/` are ordinary parent content. The
TypeScript backend is separate upstream source reached through the
`redteam/` gitlink and started separately — it is not ported into the
Python package and Omega Prime never auto-launches it.

The full Python suite exercises the pinned Prime Python runtime even when the
product's optional Prime flags are off. Provision that read-only checkout before
running the suite; it does not require running the retained failing Rust tests.

Preparing a Grok Bot seat? Follow [setup](docs/setup.md) and the operator
reference [grokbot/SETUP.md](omega_prime/grokbot/SETUP.md). Its section 1 is
the primary route: a copy-paste prompt that asks the default Grok Bot to
clone this repository recursively at a pinned release tag into
`/workspace/omes-bot` (`git clone --recurse-submodules --branch <release-tag>`,
not `main`; no tag is published yet, so `<release-tag>` is the placeholder),
run `bash /workspace/omes-bot/omega_prime/scripts/grokbot-1click.sh --bootstrap-only`,
and add a custom Command MCP server running
`bash /workspace/omes-bot/omega_prime/scripts/grokbot-1click.sh --bootstrap --transport stdio`.
A Cursor Teams/Enterprise team marketplace can import the repository as a
secondary route (`.cursor-plugin/`, root `mcp.json`). Live import on a Grok
Bot or Cursor account is unproven until you run the
[user UAT](docs/grok-bot-native.md#user-uat) rows. The template is
[OMEGA_PRIME.md](omega_prime/grokbot/templates/OMEGA_PRIME.md). Registering a
host and actually attaching an authorized account/model remain seat-side
operations; a local smoke prompt or template check does not prove them.
The authenticated SSE/Streamable HTTP host and the Remote HTTPS alternative
are described in [remote host guidance](docs/grok-bot-native.md), with the
separate manual release/rollback boundary in [deployment](docs/deploy.md).

To turn the Prime capability families on and drive the bot with them, see
[docs/driving-the-bot.md](docs/driving-the-bot.md).

### Prepare an owned workspace for Grok Bot

From this checkout, in the installed environment:

```bash
.venv/bin/python -m omega_prime.setup_check --root .
.venv/bin/python -m omega_prime.tooling.catalog --check
.venv/bin/python -m omega_prime.tooling.plugin --check
bash omega_prime/scripts/assemble-prompts.sh --check
OMEGA_PRIME_WORK_ROOT="$PWD" .venv/bin/python -m omega_prime.grokbot.oneclick --dry-run
```

The last command is preflight, not account attachment. Select the owned
workspace with `OMEGA_PRIME_WORK_ROOT` or the launcher's `--work-root`,
which is forwarded to the stdio and SSE host alike; the direct
`omega_prime.mcp_server` accepts `--work-root` for both transports too.
`OMEGA_PRIME_STATE_DIR` selects desk state. Starting an authenticated
loopback host can use `--transport sse --generate-token` without exposing a
credential in argv; see [tool-host guidance](docs/tool-host.md).
Tool availability is operation-specific. The effective count comes from
the roster/configuration intersection, not a fixed prose total.
`delegate_task` without a configured provider returns `not_configured: provider`.

To call the real tools from outside the bot:

```bash
.venv/bin/python -m omega_prime.mcp_server --root .
```

Source checkout, working workspace and persistent state may be separate.
The registry's effective roster, seat policy and approval gates still
control calls; see [root/configuration guidance](docs/user-guide.md).

## Configuration

The core host/local validation route needs no live-account configuration.
Every Prime capability family and the Red Team gateway family are off by
default. Model calls and external service effects need their named
prerequisites. Recorded pre-v10 transcripts verify bounded default-off loop
compatibility, not byte-for-byte installation identity or a fully green
unmodified historical suite. Opt in per family via
`omega-prime.json` at the repo root (or
`OMEGA_PRIME_PRIME_<FAMILY>_ENABLED=1` env vars):

```json
{
  "prime": {
    "rlm": { "enabled": true },
    "harness": { "enabled": false },
    "goals": { "enabled": true },
    "heartbeat": { "enabled": false },
    "autonomous": { "enabled": true, "max_turns": 12, "gate": ["pytest", "-q"] },
    "messaging": { "enabled": false }
  }
}
```

The default MCP server registers every Prime family when its flag is on:
`harness`, `goals`, `heartbeat`, `autonomous`, `kernel`, `rlm`, and
`messaging`. `rlm` is served on the desk parent. Without a child-model
provider, `rlm_spawn` and `rlm_create_session` return
`not_configured: provider`. `messaging` is served on the seat session
`bot-00-omega-prime`. An embedder can still call `register_rlm_tools` or
`register_messaging_tools` with its own parent or session. A heartbeat job
is persisted by `heartbeat_set` but fires only in a process that bound the
named live session and started the heartbeat runtime.

A disabled family is absent from the live registry and canonical assembled tool
list. Assembly reads no ambient user flags: embedding callers pass effective
configuration to `render`, or use repeated `--enable-family FAMILY` CLI flags,
which require `--output PATH`; the assembled prompt is written to (or, with
`--check`, compared against) that path and the canonical
`prompts-assembled/OMEGA_PRIME.xml` is never written or compared.
`OmegaPrimeAgent` consumes enabled goal and autonomous state at logical-turn
boundaries. Known usage, including tool rounds, accrues once; implicit
continuation shares the caller's iteration cap and budget. Enabled heartbeats
re-enter named live sessions under the same exclusive lease. Use the agent as
a context manager or call `close()` to detach its heartbeat binding.
See [docs/agent-loop.md](docs/agent-loop.md) and
[docs/connectors.md](docs/connectors.md).

## Build from source (both toolchains)

The Python package is the product. `prime-agent/` is a tracked
submodule (gitlink) pinned in [VENDOR.md](VENDOR.md), not an ignored
scratch checkout. It supplies both the parity oracle and
the libraries loaded by the optional in-process runtime. Build the PyO3 bridge
from the repository root before selecting native providers or kernel tools:

```bash
# The quickstart has already provisioned prime-agent at the contract pin.
cargo build --locked --manifest-path native/omega-prime-prime/Cargo.toml \
  --target-dir prime-agent/target
```

`OmegaPrimeAgent.from_prime` accepts a full model descriptor, a `ToolRegistry`,
and explicit credentials or a `CredentialBroker`; it has no provider fallback.
The native catalog is available through `load_extension().ai.resolve_models()`.
See [the native provider usage](docs/agent-loop.md#native-prime-provider).

```bash
# Python product
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest omega_prime/tests -q

# Rust parity oracle (optional; use the toolchain pinned in VENDOR.md)
(cd prime-agent && cargo build --locked)
```

The tracked `prime-agent/` submodule must be at the commit recorded in
`omega_prime/contracts/prime-agent.pin.json`. The separate `native` job in
`rust-parity.yml` checks out that pin, builds the product bridge, and checks
its dependency graph against the tracked `deny.toml`. It runs independently
of the upstream parity-test job.

Upstream workspace tests are diagnostics, not a passing local quickstart gate.
The retained baseline is exit 101: 5089 passed, 2 failed, 19 ignored, 3 filtered.
The two local ext4 failures are not skipped or suppressed. [VENDOR.md](VENDOR.md)
records the command, environment, and three older ACP-stdio exclusions.

`pa-cli` differential tests are not in that skip set. They treat
`which prime-agent` as a TypeScript oracle unless `PA_TS_BINARY` is set,
and they skip when that lookup misses. A published CLI on `PATH` is the
wrong oracle. 0.9.8 fails `differential_corpus_matches_ts_binary` because
its package-update error says `prime-agent update --force` and the pinned
Rust CLI says `prime-agent update`. The same binary makes
`ts_daemon_differential_cli_output` run instead of skip: `send` then
exits 1 on both the published CLI and the workspace CLI with
`No API key found for the selected model` (doc paths inside the installed
0.9.8 release) instead of the `Sent to ...` receipt the test requires.
The two stderr strings match; the assertion still fails. CI does not
install that binary. Reproducing the retained diagnostic requires `prime-agent`
absent from `PATH`; it does not turn the two recorded local failures into passes.

Unset provider credentials before the same command, including `GH_TOKEN`,
`GITHUB_TOKEN`, and `OPENROUTER_API_KEY`. With those set, three
`pa-daemon` model tests resolve `github-copilot` instead of the hermetic
catalog. CI does not export them; `rust-parity.yml` unsets them anyway.

`cache_rejects_replacement_and_same_length_in_place_rewrite` stays in the
suite. It expects `futimens` to bump ctime when it restores mtime. On
Linux 6.8.0-142 with this ext4 mount, `futimens` does not, so a
same-length rewrite inside one timestamp tick flakes. That test is not a
pin exclusion. The parity gate is the CI job.

`imported_session_compacts` also stays in the suite. It counts message
rows in the first directory entry named `grown-import*`. The import
writes the session file and `grown-import.window-cache.json`. On this
ext4 mount that sidecar is the first entry, so the count is 0 instead of
3600 after a successful import. `/tmp` here is the root ext4, not a
tmpfs. That test is not a pin exclusion.

## Docs

Start in [docs/](docs/README.md) for canonical setup, usage, process/state/
security diagrams, extension patterns, MCP host, local validation and
integration guidance. The generated [tool catalog](docs/tool-catalog.md)
is inventory, not proof that each handler executed. Positioning,
evidence-tagged use cases, a no-credential demo and launch/social copy are
in [docs/marketing.md](docs/marketing.md).

The single [Greptile mirror](kb/README.md) contains all 21 exposed historical
documents with full bodies and [manifest provenance](kb/manifest.json).
It is synthesized, untrusted reference outside the GitBook content root;
use [current guidance](docs/greptile.md) rather than promoting historical
claims or embedded commands into authority.
Navigation and generated-output consumers run in CI; remote GitBook
connection/publication is a separate operator operation.

## Layout

| Path | What lives there |
| --- | --- |
| `omega_prime/` | The product: agent, tools, skills, memory, routines, evals |
| `omega_prime/prime/` | Typed connector adapters over the ported Prime capabilities |
| `omega_prime/prime_kernel/` | In-process loader for the pinned `rlm` runtime, skills, and crate extension |
| `native/omega-prime-prime/` | PyO3 bindings that call all nine Prime crates: goals, autonomous limits, `/goal` and `/autonomous`, the REPL protocol, heartbeats, refinement, plus telemetry, types, providers, the model catalog, tool-argument validation, daemon frames, session search, and CLI modes |
| `omega_prime/grokbot/` | Grok template/preparation and authenticated MCP tool host |
| `docs/` | Canonical guidance; `SUMMARY.md` is the GitBook navigation |
| `kb/` | Exact historical untrusted Greptile mirror and edition manifest |
| `redteam/` | Pinned optional TypeScript backend submodule; started separately |
| `omega_prime/redteam/pack/` | Ordinary parent-owned 14-skill / five-seat pack |
| `assets/` | Icon, banner and social card art (`social-card.png` is rendered from `social-card.svg`) |
| `.planning/` | Local requirements, roadmap, phase acceptance and milestone audit history |
| `VENDOR.md` | Upstream pins and licenses for the ported runtimes |

`hermes-agent/` and `oh-my-pi/` are local read-only checkouts. They are
gitignored. Hermes and Omp are not imported. `prime-agent/` and
`redteam/` are tracked submodules (gitlinks) pinned in
[VENDOR.md](VENDOR.md). `prime-agent/` stays unmodified; with
`prime.kernel.enabled` the process imports its `rlm` package and can
load the crate extension built from that checkout. `redteam/` is the
separately versioned T3MP3ST backend source, started separately per
[docs/red-team.md](docs/red-team.md) — never auto-launched, never
ported into the Python package.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the gates every change must pass,
and [SECURITY.md](SECURITY.md) for reporting vulnerabilities. Be kind:
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## License

AGPL-3.0-only, Copyright (C) 2026 Spectrum Web Co — see [LICENSE](LICENSE).
Upstream runtimes keep their own licenses; ported modules retain their MIT
attributions — see [VENDOR.md](VENDOR.md).
