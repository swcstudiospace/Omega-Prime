# omp-sandbox

A local sandbox for running omp + GSD-Core 1.18.0. The layers are:

- a Rust wrapper with an Anda-shaped tool surface
- a TypeScript core
- an agentOS VM
- a sandbox tier
- omp RPC

Attestation is **always `mock`** locally.

```
omp-sandbox-anda (Rust, stdin/stdout JSON lines; tools: up, run_gsd_stage, answer_question, sandbox_status, abort, down)
  └─ node src/core/server.ts (TS core)
       ├─ omp auth-broker (host, loopback; the sandbox only ever gets an access-token client URL + bearer)
       ├─ sandbox tier: openshell (MicroVM) → bwrap → agentos-local   [first that works; skips recorded]
       │    └─ sandbox-agent server (inside the sandbox)
       ├─ agentOS VM (@rivet-dev/agentos-core 0.4.3), sandbox mounted at /mnt/sandbox + `agentos-sandbox` host fns
       └─ omp 18.8.4 --mode rpc (inside the sandbox, cwd = sandbox workdir), GSD-Core installed project-local
```

## Run

The commands below expect Node 24 in `~/tools/node24`, omp in `~/.bun/bin`, a prior `omp` device-code login on the host, and rustc 1.85 or later.

```sh
npm ci
cargo build --release
./target/release/omp-sandbox-anda --list-tools
python3 tests/e2e.py          # full real run: Sol plan, Luna execute, ask/stale, abort, down
node --experimental-transform-types src/cli.ts up    # up + down smoke
```

Wrapper protocol: in `{"id":"1","tool":"run_gsd_stage","args":{"stage":"plan","gsd_command":"gsd-plan-phase","task":"1 ..."}}`.
Out: `{"id":"1","ok":true,"result":{...},"attestation":"mock"}`, plus unsolicited events: `question`, `stage_done`, `fallback`.

Stage routing:

| Stage | Model | Fallback |
|---|---|---|
| plan | `openai-codex/gpt-6.1-sol` | `zai/glm-5.3`, only if no model ever answered |
| execute | `openai-codex/gpt-6-luna` | `zai/glm-5.3`, only if no model ever answered |

Every stage prompt is `/<gsd-command> orchestrate workflowz ultrathink\n\n<task>`.

GSD commands are omp **file slash commands** (`/gsd-plan-phase`, `/gsd-execute-phase`, ...). They come from the project-local install `npx -y @opengsd/gsd-core@1.18.0 --claude --local`. They are not `/skill:` commands.

Env:

| Variable | Meaning |
|---|---|
| `OMP_SANDBOX_TIERS=bwrap,agentos-local` | Tier order |
| `OMP_SANDBOX_OPENSHELL_OK=1` | Allow the MicroVM tier |
| `OMP_SANDBOX_KEEP_WS=1` | Keep the workspace after `down` |

See `docs/ARCHITECTURE.md`, `docs/DTEE.md` and `PROGRESS.md`.
