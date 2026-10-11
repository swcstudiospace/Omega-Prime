# FAQ

Short answers, grounded in the repo. Anything here that stops matching the
code is a bug — file it.

## Install and run

**What do I need installed?**
Python `>=3.11` (the formal floor in `pyproject.toml`), 3.12 recommended
and exercised, and git. `pip install -e '.[dev]'` inside a venv, then the
four gates in CONTRIBUTING.md. CI additionally runs 3.12–3.14. Optional
lanes need their own toolchains: Rust/cargo for the native extension, Node
`>= 22.19.0` for the Red Team backend, and provider keys / device
executables / browser backends for the tools that use them — those tools
report `blocked` with the gap named until the prerequisite exists. There is
no single "no other toolchain" rule; the default Python-only path needs
none of the optional ones.

**Which repository do I clone?**
`https://github.com/swcstudiospace/omes-bot.git`, recursively — an ordinary
clone leaves the `prime-agent/` and `redteam/` submodule checkouts empty:

```bash
git clone --recurse-submodules https://github.com/swcstudiospace/omes-bot.git omega-prime
```

or `git submodule update --init --recursive` inside an existing checkout
(`--init prime-agent` alone suffices when the Red Team backend is not
needed).

**The setup check skips ultrathink/substrate. Is that a failure?**
No. Skips mean "unwired" — those planes stay local until you configure
them. Only `FAIL` fails.

**Where do secrets go?**
The host process environment, never chat. Token files (`--token-file`,
`--token-store`, `redteam.bearer_token_file`, `T3MP3ST_BEARER_TOKEN_FILE`)
are the other private surface — `0600` files the host reads, never values
in `argv`, examples, or saved output. The credential broker resolves values
per approved host and redacts them from transcripts, events, and errors.
See SECURITY.md and `docs/setup.md`.

## The agent

**Skills vs routines — what's the difference?**
Skills are `SKILL.md` prompt files the agent reads when relevant.
Routines are flows the agent runs: engagement sweeps, nightly learning,
ultrathink turns. The template carries pointers, not bodies — both live in
this checkout.

**Why did my tool call need approval?**
Mutating and shared-state tools (publishing, graph claims, memory writes
to shared scopes) wait for a person. A gated tool answers `approval
required` unless the host was started with `--approve TOOL:APPROVER` for it
or an admin token granted it via `/admin/approvals`. Reads and local writes
run free. The approver name is recorded, not authenticated. Nothing
approval-gated ever self-approves.

**What are receipts?**
Proof attached to claims: the command, its exit code, and what it changed.
A completion claim without one is not a claim. `setup_check`,
`grokbot.readiness --json`, and `grokbot.doctor --json` are the local
receipt commands. A readiness row of `blocked` or `feature_disabled` names
an unexecuted prerequisite — only `exercised` with a verified effect counts
as run.

**Why did my call fail with `unknown_field` / `bad_type` / `bad_value`?**
The call was rejected before any effect; nothing was written. A gated tool
answers `approval required` before its arguments are checked. Fix the
arguments per the tool catalog. Approval never turns missing required
inputs into success.

## Grok Bot specifics

**What does the template carry?**
`omega_prime/grokbot/templates/OMEGA_PRIME.md` holds the Omega Prime
instructions, skills, routines, and first-party plugin pointers. What it
never carries: secrets, custom MCP servers, scripts, or private skills —
those stay in this checkout or on your host, and steps 4–6 of
`docs/setup.md` re-add the ones you want. How a seat takes the template or
a custom MCP server is a Grok Bot setting this repository neither controls
nor tests.

**Why does the bot say a tool isn't available?**
The roster is the truth: the bot claims only tools in
`omega_prime/contracts/tool-rosters/omega-prime.yaml`. Anything else is a
gap — file it. The usual causes are the tool host not being attached, or
the tool's family flag being off in the host process (Prime families and
Red Team are default-off; restart the host after any flag change).

**How do I turn the Prime families on for my bot?**
Set the flags on the tool host, build an effective prompt with the same
families, and pre-approve the gated tools you want. See
[Driving the bot](driving-the-bot.md).

**Does the setup prove a live Grok attachment?**
No. Local commands prove only this checkout on loopback. A workflow that
ran under an explicitly labeled `ScriptedModel` double proves local
composition, not provider behavior. Missing-key, skipped, and
`not_configured` outcomes are unexecuted prerequisites, never passes.

## Substrate and memory

**Do I need the substrate running?**
No. Unwired installs run fully local: briefs are empty, events go
nowhere, memory stays in files. Wire it when you want cross-agent briefs,
shared memory, Hindsight episodes, and RAGflow docs.

**Local memory vs Hindsight vs substrate memory?**
Local files are the offline base. Hindsight episodes (shared `ultrathink`
bank) are recallable experience with local fallback. Substrate memory is
the cross-agent plane with accepted/quarantined/denied verdicts.

## Docs and reviews

**How do docs stay current?**
Generated pages (tool catalog) are rebuilt by script and CI fails on
drift. Every page is link-checked by the suite. Greptile reviews each PR
against the repo's review standards in `.greptile/`.
