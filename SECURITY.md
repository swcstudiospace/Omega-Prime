# Security Policy

## Reporting a vulnerability

**Do not open a public issue.** Report through a private GitHub Security
Advisory on this repository
(`Security` tab → `Report a vulnerability`). Include:

- What is affected (tool, transport, prompt, workflow)
- Steps to reproduce, with the smallest possible example
- What you expected to happen vs. what happened

We aim to acknowledge reports promptly, keep the fix quiet until a release
is cut, and credit reporters who want credit.

## Supported versions

| Version | Supported |
| --- | --- |
| 0.1.x | Yes |
| < 0.1 | No (pre-release history) |

## Secrets posture

Omega Prime is built to keep secrets out of reach:

- Keys resolve from the environment through the credential broker
  (`omega_prime/credentials/broker.py::CredentialBroker.key_for`, scoped per
  host via `allows_host`) and are redacted from transcripts, events, errors,
  and eval output (`credentials/redact.py::redact_text`, `SecretLeakError`
  via `grokbot/secret_guard.py::assert_no_secrets`).
- A private token file IS supported alongside the environment: `--token-file`
  beats `--token` beats `--token-env` (default `MCP_AUTH_TOKEN`), read via
  `tooling/fs.py::read_secret_file`. The hashed token store is mode 0600 and
  holds SHA-256 digests; `grokbot/tokens.py revoke` takes effect on the next
  check without a restart. Never paste a credential value into an example,
  diagnostic, log, or saved output — pass the file path or variable name.
- Tool arguments are never logged and never leave the process in event
  summaries (the audit log stores key names and a digest, never values).
- Tests are hermetic — no live credentials exist in CI.
- If you find a secret committed anywhere in history, report it as above
  so it can be rotated and purged; do not quote it in an issue or PR.

## Network & Browser Hardening

Network operations and browser sessions enforce fail-closed egress boundaries:

- Destination policy: `DestinationTransport` strictly validates hostnames, port ranges
  (1–65535), IP classification, and authority framing. Private, loopback, and reserved
  ranges refuse before dial.
- Browser sandbox confinement: `GuardedBrowserFactory` validates accounting receipts,
  cgroup lifecycle, and socket tracking. Unverified sandbox environments fail closed.
- Test coverage and evidence are validated through hermetic test suites and runtime
  verifications (`omega_prime/tests/test_web.py` and `omega_prime/tests/test_browser_egress.py`).

## Remote tool host

The Grok Bot listener (`python -m omega_prime.mcp_server --transport sse`,
or `./scripts/grokbot-1click.sh`) authenticates with static scoped bearer
tokens. It is not OAuth.

- The credential is the `Authorization: Bearer` header only. A token in the
  query string is ignored (401) and is not written to the access log.
- Scopes are `read`, `call`, and `admin`. `admin` includes the lower two.
  The single `--token-file` or `MCP_AUTH_TOKEN` principal is `read` and
  `call`. `/admin/approvals` requires `admin` and is not mounted when
  authentication is off. The approver stored on a grant is that token's
  label or id, never the bot seat.
- The hashed token file (mode 0600) stores SHA-256 digests.
  `python -m omega_prime.grokbot.tokens revoke` takes effect on the next
  check, without a restart. An unreadable store denies that file's tokens
  and `/readyz` returns 503.
- A non-loopback bind without a token or a token store exits 2.
  `--allow-insecure-no-auth` is the only opt-out. A strict preflight
  failure from the 1-click launcher exits 3.
- Host and Origin are checked. CORS answers only origins configured on the
  command line, never `*`. Another principal's `Mcp-Session-Id` on `/mcp`
  is 404.
- The audit log is hash-chained. Tool arguments are stored as key names and
  a digest, never as values. `verify` reports an edited, removed, or
  reordered line.

One process, one seat. Tools run one at a time. Operator detail is
`docs/grok-bot-native.md`.

## Dependency exceptions

`requirements-lock.txt` pins `oauthlib==3.3.1` because `tweepy==4.17.0`
requires `oauthlib>=3.2.0,<4`. [PYSEC-2026-4114](https://osv.dev/vulnerability/PYSEC-2026-4114)
is a PKCE timing oracle in oauthlib's authorization-server grant
(`code_challenge_method_plain` / `code_challenge_method_s256`). Omega Prime
does not host that grant. The supply-chain workflow ignores only that
advisory, against the lockfile, until a tweepy release accepts `oauthlib>=4`.
The runner's own `pip` is not part of the audit.

The weekly `redteam-remediation` workflow proposes upstream purple-team dependency bumps while the redteam pin audits red; its optional `REMEDIATION_TOKEN` secret carries only `repo` scope on `swcstudiospace/purple-team` (fix branches + bump PRs, never approve/merge), and without it the workflow degrades to artifact + tracking issue. See `docs/red-team.md`.

## Approvals and authorization boundaries

- Approval is registration-specific: `ToolRegistry.dispatch` checks
  `ApprovalLog.is_approved` only for tools registered with
  `requires_approval=True`. A gated tool cannot self-approve.
  Local coding `write_file`, `patch_file`, `edit_file` and `run_terminal`
  are not approval-gated by that flag; they can mutate files or run code as
  the host user. Sandbox the host and constrain workspace/policy access.
  No diagram or page grants approval, and not every mutation waits for a person.
- The `/admin/approvals` surface requires the `admin` scope and is not
  mounted when authentication is off. The approver recorded on a grant is
  the token's label or id, never the bot seat.
- Native GSD authorization is NOT signed Swarm approval. Protected `.swarm/`
  writes keep their own approval path; nothing in these docs claims a
  protected write or deployment occurred. Do not present local
  registry approval as Swarm signing.
