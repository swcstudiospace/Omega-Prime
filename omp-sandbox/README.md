# omp-sandbox

A local sandbox for running omp + GSD-Core 1.18.0. The layers are:

- a Rust access layer built on the real Anda engine (anda_engine 0.17.4 + anda_engine_server 0.17.0): every request into the sandbox is a signed Anda `tool_call`
- a TypeScript core
- an agentOS VM
- a sandbox tier
- omp RPC

Attestation is **always `mock`** locally.

```
omp-sandbox-anda client (stdin/stdout JSON lines; tools: up, run_gsd_stage, answer_question, sandbox_status, abort, down)
  │   signs each request: ICP envelope (Ed25519, SHA3-256 of the body) + meta {engine id, nonce, ts_ms}
  └─ omp-sandbox-anda serve: anda_engine_server on 127.0.0.1 (HTTP POST /default, method tool_call)
     │   Anda Engine "omp_sandbox": Private, controller = client principal, nothing exported; replay guard
     └─ node src/core/server.ts (TS core; receipts record access {via:"anda", caller principal})
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

The commands below expect Node 24 in `~/tools/node24`, omp in `~/.bun/bin`, a prior `omp` device-code login on the host, and rustc 1.95 or later (pinned by `rust-toolchain.toml` to 1.99.1; install user-locally with rustup, no sudo).

For the preferred **openshell-podman** tier you also need:

- rootless Podman 5.x with its helpers (Debian: `podman uidmap slirp4netns passt fuse-overlayfs crun`) and `/etc/subuid` + `/etc/subgid` entries for your user. Check with `podman info` (rootless: true, cgroups v2) and `podman run --rm docker.io/library/alpine echo ok`.
- OpenShell 0.1.4 release binaries in `~/.local/opt/openshell/0.1.4/bin` and loopback mTLS PKI in `~/.local/state/openshell-vm/tls` (`openshell-gateway generate-certs`), with the CLI gateway registered at `https://127.0.0.1:18081`.
- No KVM. Never use the VM driver on a host that is itself a VM.

The tier starts the Podman API socket, the gateway (podman driver) and builds `localhost/omp-sandbox:0.1` from `sandbox/image/Containerfile` on first use if they are missing, and stops what it started on `down`.

```sh
npm ci
cargo build --release
cargo test --release                              # unit tests + in-process Anda auth test
npm test                                          # hermetic node tests (fakes only)
OMP_SANDBOX_REAL_MOUNT_TESTS=1 npm test           # opt-in: also drives the host bwrap for the pinned-mount check
./target/release/omp-sandbox-anda keygen          # once: identities (0600) in ~/.local/state/omp-sandbox-anda; prints principals only
./target/release/omp-sandbox-anda --list-tools
python3 tests/e2e.py          # full real run: Sol plan, Luna execute, ask/stale, abort, down
node --experimental-transform-types src/cli.ts up    # up + down smoke
```

Wrapper protocol: in `{"id":"1","tool":"run_gsd_stage","args":{"stage":"plan","gsd_command":"gsd-plan-phase","task":"1 ..."}}`.
Out: `{"id":"1","ok":true,"result":{...},"access":{"via":"anda_engine_server 0.17.0","auth":"ICP signed envelope (Ed25519)","caller":"<principal>","engine":"<principal>"},"attestation":"mock","tee":"none: ..."}`, plus unsolicited events: `wrapper_ready`, `question`, `stage_done`, `fallback`, `core_exit`.
The extra stdio tool `anda_auth_probe` runs the negative auth tests against the live engine (unsigned, unknown key, tampered body, wrong engine id, missing nonce, stale timestamp, replay; plus a signed control).

### Access layer (Anda)

- `omp-sandbox-anda` (no args) = `client --spawn`: starts `serve` on a random loopback port, then turns each stdio request into a signed Anda `tool_call`. Events come through signed `poll_events` long-polls.
- `serve` runs a real Anda `Engine` behind `anda_engine_server`. It is `Visibility::Private` with the local client principal as controller and exports no tools, so anonymous or unknown callers are refused by Anda itself. Bad signatures get HTTP 401.
- Anda's direct-key envelopes have no freshness, so every call also carries meta `{nonce, ts_ms}`. The engine refuses missing or stale (>120 s) timestamps and reused nonces.
- Keys: `~/.local/state/omp-sandbox-anda/{client,engine}.ed25519` (raw 32 bytes, 0600, dir 0700; refused if group/world-readable). The server only reads `client.principal`. Override the dir with `OMP_SANDBOX_ANDA_STATE`.
- Attestation is **mock** (no TEE on this host); every reply and receipt says so. See `docs/DTEE.md`.

Stage routing:

Each plan login has its own plan/build pair:

| Plan (`args.plan`) | plan stage | execute stage |
|---|---|---|
| `chatgpt` (default) | `openai-codex/gpt-6.1-sol` (Sol) | `openai-codex/gpt-6-luna` (Luna) |
| `zai` | `zai/glm-5.3` | `zai/glm-5.3-flash` |

| Stage | Default model | Fallback |
|---|---|---|
| plan | `openai-codex/gpt-6.1-sol` | `zai/glm-5.3`, only if no model ever answered |
| execute | `openai-codex/gpt-6-luna` | `zai/glm-5.3-flash`, only if no model ever answered |

`run_gsd_stage` takes an optional `plan` (`chatgpt` or `zai`) so Z.ai can be the primary for a job, not only the fallback. A Z.ai-primary job has no fallback. An explicit `model` still overrides the primary.

Every stage prompt is `/<gsd-command> orchestrate workflowz ultrathink\n\n<task>`.

GSD commands are omp **file slash commands** (`/gsd-plan-phase`, `/gsd-execute-phase`, ...). They come from the project-local install `npx -y @opengsd/gsd-core@1.18.0 --claude --local`. They are not `/skill:` commands.

The first install runs inside the sandbox in a fresh staging folder under the workdir. On a mounted job worktree (shared with the host and other jobs) the marker check and the copy into `.claude` also run inside the sandbox, so the host never reads the staging folder by name, and the install never fills the shared cache. A private work folder is checked on the host and fills the shared cache under a cross-process lock (`<cache>.lock`, an atomic `mkdir`). A complete cache another wrapper already published wins, a wrapper that finds the lock held skips the fill without failing `up`, and a lock older than 15 minutes is treated as a crashed filler's leftover and cleared.

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
