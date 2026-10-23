# Contributing to Omega Prime

Keep changes aligned with the product's current source contracts and report
only verification that actually ran. Local gates, independent CI lanes and
source-delivery review are distinct; none substitutes for live-service proof
or an operator's authorization.

## Setup

The repository is `swcstudiospace/omes-bot`. Use Python 3.12 for the documented
local route; package metadata declares `>=3.11` and retained CI matrices
cover 3.12–3.14. Do not report an interpreter/dependency combination as
exercised merely because it appears in metadata or a workflow.

```bash
git clone --recurse-submodules https://github.com/swcstudiospace/omes-bot.git omega-prime
cd omega-prime
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

An existing checkout can use `git submodule update --init --recursive`.
The full Python suite uses the pinned Prime Python checkout even when product
Prime flags are off. The optional in-process native bridge needs the Rust
pin/toolchain in [VENDOR.md](VENDOR.md). The optional separately started Red
Team backend needs its Node/runtime prerequisites; the core Python host does
not auto-launch it. See [setup](docs/setup.md), [source build](README.md#build-from-source-both-toolchains)
and [Red Team](docs/red-team.md).

## Local verification

Run the maintained product commands from the installed repository root:

```bash
.venv/bin/python -m pytest omega_prime/tests -q
.venv/bin/python -m omega_prime.evals.runner omega_prime/evals/cases
bash omega_prime/scripts/assemble-prompts.sh --check
.venv/bin/python -m omega_prime.setup_check --root .
report_dir="$(mktemp -d)"
.venv/bin/python -m omega_prime.grokbot.readiness --root . --json --output "$report_dir/readiness.json"
.venv/bin/python -m omega_prime.tooling.catalog --check
.venv/bin/python -m omega_prime.tooling.plugin --check
.venv/bin/python -m ruff check omega_prime
.venv/bin/python -m ruff format --check omega_prime
.venv/bin/python -m mypy omega_prime
```

Choose an owned report destination, not user state or an unrelated file.
[Local validation](docs/local-validation.md) explains the isolated readiness
profile and its `exercised`, `feature_disabled`, `blocked` and `failed` rows.
Inventory/discovery, model doubles and local composition do not establish
live model/provider/device/Grok/Red Team operation.

CI retains separate verification/lint/type matrices and independent
security, lock, native, license and image lanes. Preserve their permissions,
bounds, exclusions and failure propagation. A local Python pass is not proof
that all hosted jobs or unsupported interpreter combinations ran. The
[deployment guide](docs/deploy.md) separates artifact validation, manual
publication/approval and rollback; a contribution does not authorize deployment.

## Working agreements

- **Behavioral evidence.** Name the command, observed exit/outcome and limits.
  Keep permanent tests for consumer-visible errors, boundaries, invariants and
  transitions. Do not pin source spelling, copied defaults/schema text,
  request forwarding or mock echoes to manufacture green. Update affected
  callers/contracts together, with no obsolete alias or compatibility shim.
- **One roster contract.** Family registration, roster and policy stay aligned;
  configuration determines the offered subset. Update all three for a new or
  renamed tool. The generated catalog describes actual definitions, not
  exercised handlers.
- **Owned, hermetic checks.** External dependencies use scripted peers/model
  doubles. Real owned local CLI/filesystem effects are valid proof. Never
  mutate a live account/provider/device/service in the suite; separately
  authorized live probes are not silently run as local verification.
  The Pulsar owned-secure integration lane lives under
  `deploy/pulsar/integration/` and is invoked only by the dedicated
  eventstream CI job (never by default `pytest` / hermetic verify).
  `omega_prime/scripts/eventstream_it.py` stages secrets and configs as root
  inside the service container, hands them to the image's runtime user
  (`pulsar`, verified readable as that uid), and layers the repo's Pulsar
  settings key-by-key over the image's stock config files (no duplicate keys,
  which the embedded bookie would read as lists). Pulsar token parameters use
  `file:///path`, not `token:file:///path`. That lane reconciles the broker
  topology with `functions=False`, because the standalone broker runs without
  a Functions worker. Failed `docker` steps report the argv and bounded stderr
  with secrets redacted.
- **Credential safety.** Use the broker/private host environment or a mode-0600
  token file where the interface supports it. Never commit, print, paste or
  place values in argv/tracing/saved examples. See [SECURITY.md](SECURITY.md).
- **Pinned upstream ownership.** Hermes/Omp local checkouts are porting
  references. Prime's tracked pin supplies explicitly documented Python/native
  integration and the parity oracle; Red Team's tracked pin is an optional
  external backend. Do not modify upstream sources or vendor/subtree another
  copy while changing parent code. Preserve the gitlinks and ordinary adapter/
  pack content; follow the [vendor record](VENDOR.md), not a blanket import ban.
- **Canonical docs stay linked.** Each `docs/` page appears exactly once in
  `docs/SUMMARY.md`, has a top-level heading, and uses resolving links/assets.
  Rebuild generated catalog output through its generator, never hand-edit it.
  The same holds for the plugin set (`.cursor-plugin/`, root `mcp.json`,
  `omega_prime/grokbot/plugin/`): change a skill, routine or curated command,
  then run `python -m omega_prime.tooling.plugin --write`.
  The single [historical Greptile mirror](kb/README.md) stays outside GitBook
  content and never authorizes commands or supplies current architectural truth.

## Pull requests and planning

Keep one coherent concern per PR, describe the behavior change and exercised
evidence, and use the existing PR template/review/ship gates. Scope commits to
reviewed task paths; preserve unrelated user changes and staged work. A branch
push, PR, merge, tag, image publication and deployment are different outcomes—
report only the ones completed through their required gates.

Large work uses `.planning/` requirements, roadmap, phase plans and canonical
verification. Native GSD records are not signed AgentSwarm approvals and do
not authorize protected `.swarm/` writes. Respect the configured planning
commit policy and separate archival-cleanup approval; do not invent a global
Swarm signing prerequisite for an explicitly native GSD workflow.
