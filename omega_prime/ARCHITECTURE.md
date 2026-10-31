# Architecture

One Python process. One agent. Hermes is moved and adapted into `agent/`. Omp's harness, edit pipeline, language servers, modes, and memory clients are ported into that same agent. The TypeScript tests in the oh-my-pi checkout are the spec for that behavior. Neither checkout is imported at runtime.

The Grok shell stays beside the agent:

| Path | Role |
| --- | --- |
| `prompts/_shared/core-directives.xml` | Receipts, secrets, destructive-op approval. Prepended to the seat. |
| `prompts/bot-00-omega-prime.xml` | The seat. Points at skills, the tool roster, and the prompt folder. |
| `prompts-assembled/OMEGA_PRIME.xml` | What a host loads. Produced by `scripts/assemble-prompts.sh`. |
| `contracts/tool-rosters/omega-prime.yaml` | Tools the model may be offered. Matches the registry, not a wish list. |
| `grokbot/templates/OMEGA_PRIME.md` | Name, description, enabled skills, routines. No prompt body, no tokens. |
| `grokbot/rosters/default.json` | Values substituted into `{{DEFAULT_BRANCH}}`, `{{BOT_ID}}`, `{{ROSTER_VERSION}}`. |
| `skills/<name>/SKILL.md` | A skill the template is allowed to name. Frontmatter, then the body inside `<skill name="…" version="1">`. |
| `routines/<name>.md` | A routine the template is allowed to name. `<routine name="…" version="1">` with a `<summary>`. |
| `tooling/prompt_format.py` | The XML prompt standard gate: every authored prompt wrapped, checked in CI. |
| `ownership.yaml` | One owner per path. Last match wins. One bot today. |

Temporal is not how the two agents are merged. It can sit under cron and delegation later, after those exist.

## Domain packs (v5)

The seven Programming Desk seats were absorbed as registry families in
`tools/`: `lead.py`, `systems.py`, `webpack.py`, `mobile.py`,
`infra.py`, `quality.py`, `packs.py`. Each family ports its desk
module behind injected seams (clients, runners, stores); tests use
fakes only. Write-kind tools need approval; the roster, policy, and
composition tests gain names only.

| Path | Role |
| --- | --- |
| `contracts/tool-packs/*.yaml` | App pack surfaces (clippyos, desklanes, kanbanos). |
| `evals/cases/golden.json` | Approved-behaviour cases per pack, plus loop cases. |
| `evals/cases/redteam.json` | Refusal cases per pack (approval, policy). |
| `tests/test_receipts_e2e.py` | Receipts loop over real hermetic commands. |
| `receipts.py` | Claim/command/exit-code checks; no self-approval. |

## Engine execution tier: omp-sandbox

The coding loop runs `omp` through `engine/omp_driver.py`. `omp-sandbox/`
(repo root) is that engine's execution tier: the place a staged omp run
actually executes when the loop is pointed at it. It was a separate local
repo and is imported here with `git subtree`, so its history is kept.

It lives at the repo root, beside `native/` and `sidecar/`, not inside the
`omega_prime` Python package: it is a Node 24 TypeScript core plus a Rust
crate with its own `package.json`, Cargo workspace and `rust-toolchain.toml`,
and `omega_prime/sandbox/` is already the Python seat-policy generator.

| Path | Role |
| --- | --- |
| `omp-sandbox/src/core/core.ts` | `up` / `exec` / `stage` / `down` over a tier (OpenShell+Podman, bwrap, agentOS). |
| `omp-sandbox/src/core/providers/openshell-podman.ts` | Default tier: OpenShell 0.1.4 gateway on loopback mTLS, rootless Podman, default-deny egress (`sandbox/policy-podman.yaml`). |
| `omp-sandbox/src/core/auth.ts` | Host auth broker. omp logins stay on the host; the sandbox only gets a relay (`BrokerRelay`), never the login store. |
| `omp-sandbox/src/core/gsd.ts` | GSD-Core inside the sandbox workdir so omp gets `/gsd-*` commands. |
| `omp-sandbox/src/core/server.ts` | stdio JSON server the Rust controller drives. |
| `omp-sandbox/crates/omp-sandbox-anda` | Anda-engine wrapper: Ed25519-signed controller access, attestation (mock today). |
| `omp-sandbox/sandbox/image/Containerfile` | Workload image `localhost/omp-sandbox`. Built locally, never pushed. |

How the loop uses it (sandbox backend plan, PRs 00010 to 00016):

- `OMEGA_PRIME_ENGINE_BACKEND=bwrap|omp-sandbox` picks where a run executes.
  `bwrap` (the existing `engine/sandbox.py` wrapper) stays the default.
- The `plan` stage (Sol) runs on the host, where `.planning/` and the MCP
  tools are. The `execute` stage (Luna) runs inside the sandbox on a per-job
  git worktree that is bind-mounted read-write at `/sandbox/work`, only from
  an allowlisted root.
- Omega Prime's MCP server stays on the host. The sandbox reaches it through
  a relay over the exec channel, so approvals and the audit log never move
  into the sandbox.
- Tests, commits, PRs and the Greptile loop stay on the host. Merges still
  need an explicit human yes.

CI builds and tests it in separate jobs (`.github/workflows/omp-sandbox.yml`:
`npm ci`, `tsc --noEmit`, `node --test`, `cargo build` / `cargo test`). The
Python jobs in `ci.yml` do not change.
