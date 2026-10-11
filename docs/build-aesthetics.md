# Build aesthetics

How Omega Prime is put together, and the rules every part follows.

## One process

Omega Prime's core conversation loop and registry run in one Python process
under `omega_prime/`. Optional native Prime helpers load in-process; the
optional Red Team gateway and other service peers are separate processes.
The pinned upstream checkouts are not a second Python loop or an automatic
backend launch. See the [process boundaries](architecture.md#the-shape).

## Packs, not seats

The seven Programming Desk seats were absorbed as domain packs —
tool families with roster entries, policy entries, and skills —
inside the one agent: lead, systems, web, mobile, infra, quality, app packs.
Messaging, IDE, platform, growth, coding, delegation, substrate, and
ultrathink bridge tools complete the roster alongside the seven gated Prime
families (RLM, harness, goals, heartbeat, autonomous, messaging, kernel)
and the default-off Red Team family (`tempest_*` loopback gateway proxies).
The live count is whatever the roster contract and generated catalog say
today — prose never pins a fixed total.

## Contracts over code

Three files decide what the bot may do, and tests enforce that
the code matches exactly:

- `omega_prime/contracts/tool-rosters/omega-prime.yaml` lists the inventory;
  feature configuration determines which registered definitions are offered.
- `omega_prime/contracts/policies/omega-prime.json` defines tool, write-path
  and destination policy.
- `omega_prime/ownership.yaml` maps source path patterns to ownership.

Prompts assemble the same way: seat XML plus shared directives
build `prompts-assembled/OMEGA_PRIME.xml`, and `--check` fails on drift.

## Seams, fakes, receipts

Models, transports, runners and stores expose explicit seams where the
current implementation supports injection. Use scripted peers/model doubles
for external dependencies, and real owned local handlers or CLI processes
when proving effects. This is not live-account/provider proof.
A completion claim identifies the command, observed outcome and limits;
the quality pack's receipt and stamp paths refuse self-approval.

### Definitions are not execution

[`ToolRegistry`](https://github.com/swcstudiospace/omes-bot/blob/main/omega_prime/tools/registry.py)
separates `register`/`schemas` from `dispatch`. A family supplies actual
definitions; the roster and policy stay aligned with their names. The
[generated catalog](tool-catalog.md) uses `full_inventory_registry` for
metadata without dispatching handlers. To extend a tool family, update its
registration, roster and policy together, then exercise a real consumer
effect through dispatch—not a copied schema or a canned handler echo.

### Storage ownership and failure semantics

Keep file-backed local state, durable workflow journals and remote memory
peers distinct; the [storage diagram](architecture.md#memory-and-skills)
names their owners. A successful local intake write does not prove that a
remote memory write succeeded. Client failure contracts are operation-specific:
for example, the [substrate guide](substrate.md) distinguishes degradable
reads from writes/search operations that raise. Do not turn every unavailable
peer into a universal `not_configured` or success response.

## Extension contract

Extensions are plain callables registered in order on the existing seam —
`omega_prime/agent/extensions.py::ExtensionHooks.register_before_model` —
and run by `run_before_model` before each model call. The first
`{"stop": True, ...}` verdict wins; later hooks do not run. The module
docstring says it directly: **no plugin loader ships here; hosts register
plain callables.** There is no discovery, install, or loading API to call —
do not invent one.

```python
from omega_prime.agent.extensions import ExtensionHooks

hooks = ExtensionHooks()

def cap_messages(messages, tools):
    # fn(messages, tools) -> {"stop": True, ...} | None
    if len(messages) > 200:
        return {"stop": True, "reason": "budget_guard"}
    return None

hooks.register_before_model(cap_messages)
verdict = hooks.run_before_model([{"role": "user", "content": "local"}] * 201, [])
assert verdict == {"stop": True, "reason": "budget_guard"}
print(verdict["reason"])
```

Registration order is append-only through `register_before_model`; the first
stop wins in `run_before_model`. This runnable example exercises the hook
seam, not a model call. Attach `hooks` using `extensions=hooks` when constructing
`Agent` or `OmegaPrimeAgent` **with an actual model adapter**; `Agent.model` is
required. See
[`Agent`](https://github.com/swcstudiospace/omes-bot/blob/main/omega_prime/agent/conversation_loop.py)
and
[`OmegaPrimeAgent`](https://github.com/swcstudiospace/omes-bot/blob/main/omega_prime/agent/runtime.py).

Hooks may inspect or mutate outgoing messages and stop a round. They are
trusted Python callables, not a sandbox. Tool extensions still belong in
families with roster/policy/registry registration; do not install an
unregistered dispatcher or derive approval/tenancy from untrusted content.

## Deterministic guards

- Hermetic pytest coverage for roster/policy/composition agreement,
  provider protocols, retrieval safety, and per-pack behavior.
- Deterministic golden and red-team evals, including refusal and approved
  behavior through real tool registration.
- CI reports the current test/eval totals; prose does not pin changing counts.
- `assemble-prompts.sh --check` and `omega_prime.setup_check` verify
  prompts, template, roster, registry, and install health.

## Layout

| Path | Holds |
| --- | --- |
| `omega_prime/agent/` | Loop, modes, skills runtime, delegation |
| `omega_prime/tools/` | Registry + tool families |
| `omega_prime/skills/` | Shipped skill packs; optional Red Team content remains separate |
| `omega_prime/routines/` | Prompt flows and local runners |
| `omega_prime/prompts/` | Seat + shared prompt sources |
| `omega_prime/contracts/` | Roster, policy, pack contracts |
| `omega_prime/grokbot/` | Template, roster JSON, setup flow |
| `omega_prime/evals/` | Runner + golden/redteam cases |
| `omega_prime/hosting/` | OpenShell profile, AgentOS notes |
| `docs/` | Canonical documentation; Git Sync configuration is included |
