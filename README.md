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
       ├─ sandbox tier: openshell-podman → openshell (MicroVM, opt-in) → bwrap → agentos-local   [first that works; skips recorded]
       │    ├─ openshell-podman: OpenShell 0.1.4 gateway (loopback mTLS) + rootless Podman driver,
       │    │   workload container (network=none, Landlock) + supervisor (default-deny egress policy)
       │    ├─ broker relay: sandbox-local 127.0.0.1:18990 ⇄ exec stdio ⇄ host auth-broker
       │    └─ sandbox-agent server (inside the sandbox)
       ├─ agentOS VM (@rivet-dev/agentos-core 0.4.3), sandbox mounted at /mnt/sandbox + `agentos-sandbox` host fns
       └─ omp 18.8.4 --mode rpc-ui (inside the sandbox, cwd = sandbox workdir), GSD-Core installed project-local
```

## Run

The commands below expect Node 24 in `~/tools/node24`, omp in `~/.bun/bin`, a prior `omp` device-code login on the host, and rustc 1.85 or later.

For the preferred **openshell-podman** tier you also need:

- rootless Podman 5.x with its helpers (Debian: `podman uidmap slirp4netns passt fuse-overlayfs crun`) and `/etc/subuid` + `/etc/subgid` entries for your user. Check with `podman info` (rootless: true, cgroups v2) and `podman run --rm docker.io/library/alpine echo ok`.
- OpenShell 0.1.4 release binaries in `~/.local/opt/openshell/0.1.4/bin` and loopback mTLS PKI in `~/.local/state/openshell-vm/tls` (`openshell-gateway generate-certs`), with the CLI gateway registered at `https://127.0.0.1:18081`.
- No KVM. Never use the VM driver on a host that is itself a VM.

The tier starts the Podman API socket, the gateway (podman driver) and builds `localhost/omp-sandbox:0.1` from `sandbox/image/Containerfile` on first use if they are missing, and stops what it started on `down`.

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
| `OMP_SANDBOX_TIERS=openshell-podman,bwrap,agentos-local` | Tier order (default `openshell-podman,openshell,bwrap,agentos-local`) |
| `OMP_SANDBOX_PODMAN_IMAGE` | Workload image (default `localhost/omp-sandbox:0.1`) |
| `OMP_SANDBOX_OPENSHELL_PORT` | Gateway port (default `18081`) |
| `OMP_SANDBOX_AGENT_LOG` | Sets `RUST_LOG` for the in-sandbox sandbox-agent (log in `.state/openshell-podman/`) |
| `OMP_SANDBOX_OPENSHELL_OK=1` | Allow the MicroVM tier (needs working KVM; never on this box) |
| `OMP_SANDBOX_KEEP_WS=1` | Keep the workspace after `down` |

There are no session time caps: omp runs without `--max-time`, prompt waits have no timeout, and OpenShell exec uses `--timeout 0`. GSD-Core runs can last hours.

See `docs/ARCHITECTURE.md`, `docs/DTEE.md` and `PROGRESS.md`.
