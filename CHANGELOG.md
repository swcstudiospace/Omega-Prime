## Unreleased
- Solo mode is the default for bot-00; Desk docs, bus, and notifications remain opt-in, with skill-link checks covering package documentation. Evidence: `.receipts/bot-00-omega-prime/phase-99.json` — focused solo/Desk/prompt/link tests `pytest omega_prime/tests/test_lead.py omega_prime/tests/test_desk_wiring.py omega_prime/tests/test_desk_lead.py omega_prime/tests/test_docs.py omega_prime/tests/test_public_repo.py omega_prime/tests/test_shell.py omega_prime/tests/test_prompt_format.py omega_prime/tests/test_command_generation.py -q` exit 0; `bash omega_prime/scripts/assemble-prompts.sh --check` exit 0.

- omp engine (Phase A1a): the coding loop can run on omp 18.x over RPC. Tools stay on the stdio MCP server, plans go through an allowlist, and `omega_capacity` reports quota.
- A1b dispatch: `omega_dispatch` (approval plus confirm token), `omega_jobs_status`, and `omega_jobs_cancel`. One JobSpec goes to the local sandbox lane or the coding fleet. The fleet client reads `FLEET_COORDINATOR_URL` by name. `FLEET_COORDINATOR_TOKEN` is resolved by the credential broker after `SeatPolicy.allows_host`.
- Image evidence accepts a long `refs/heads/<branch>` push ref. `/` stays in the token alphabet outside a git ref. Inside a ref, each segment is scanned, and the segments are scanned again with the slashes removed, so a token split by `/` or hidden behind repeated padding still trips the secret gate. An ordinary branch name whose only high-entropy symbol is the slash does not.
- Slash commands: curated 24 -> 27 and total 58 -> 61. Added `/omega-redteam-conflicts`, `/omega-redteam-readiness`, and `/omega-audit`. Findings accept `--jsonl`. `durable_status` reports cycle and sync backlog. Purple-cycle jobs can be scheduled, and a live cycle takes a file lock plus an optional deadline.
- Install docs pin the clone to `/workspace/omes-bot` and a `<release-tag>` placeholder (no tag existed on 2026-10-19; do not clone `main`).
- `skills/getting-started` walks a fresh import through clone, `--bootstrap-only`, `--verify --json`, the stdio Command server with no env, `/omega-help`, and secret-request cards. Connector tokens resolve from `NAME`, `NAME_FILE`, or `$OMEGA_PRIME_STATE_DIR/secrets/NAME` (mode 0600).
- Stdio gated tools read a mode 0600 approvals file outside the repo and outside `OMEGA_PRIME_STATE_DIR` at startup. A person pins its sha256 with `--approvals-sha256` (or `OMEGA_PRIME_APPROVALS_SHA256`); a mismatch refuses to start. The state-dir file is not loaded. The launcher forwards the path and the digest, never `--approve`. The process does not write the file, and an approver that names the bot is refused. `lead_doctor` action `check` is ungated and does not write the roster; register, install, and repair stay gated.
- `HINDSIGHT_URL` defaults to unset, which disables the remote bank. There is no hosted default.
- Runtime files `omega_prime/sessions.db`, `omega_prime/state/`, `omega_prime/memory/MEMORY.md`, and `omega_prime/memory/USER.md` are gitignored. The `omega_prime/memory/` package stays tracked.
- Ultrathink integration documents the strict default-off `ULTRATHINK_SHIP`
  opt-in, read-only readiness and assessment boundaries, plan-discovery
  precedence and stale-plan stop behavior, and independent approvals.
  Optional native test lanes and test-only `OPENSHELL_BIN` are bounded
  without claiming production override or binary availability. The related
  code, test, setup, and skill changes do not make provider, live-config, or
  live-host changes.
- Settings provenance follows the candidate repository, setup diagnostics share the loaded environment with Ultrathink, and malformed non-empty Substrate SSE frames fail as protocol errors.

# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/spec/v2.0.html).

## [Unreleased]

### omp engine (Phase A1a)

#### Added

- `omega_prime.engine`: an omp RPC driver (`prompt`, `steer`, `abort`,
  `set_model`, `set_thinking_level`, `set_event_filter`, `get_session_stats`)
  that streams events to `$OMEGA_PRIME_STATE_DIR/jobs/<id>/events.jsonl`.
  `python -m omega_prime.engine.watch` tails that feed. A tmux session
  `omega-<job>` is optional (`OMEGA_PRIME_ENGINE_TMUX`).
- Per-session `mcp.json` starts `python -m omega_prime.mcp_server` and points
  omp at `omega_prime/skills`. Approvals and audit stay in the registry.
- Plan router for zai, xai, anthropic, muse, and cursor. The default
  allowlist is empty. API-key profiles are allowlisted when their environment
  variable is set. Anthropic and Muse subscription OAuth are never
  allowlisted. Z.ai, xAI, and Cursor subscription use are opt-in flags.
- Bubblewrap sandbox. Without `bwrap` the engine refuses unless
  `OMEGA_PRIME_ENGINE_UNSANDBOXED=1`.
- `omega_capacity` on the tool roster. It reports remaining quota from
  `omp usage --json` and the allowlist. It does not call a model HTTP API.
- Conversation, delegate, the prime-agent coding path, desk-lead, and
  ultrathink prefer the engine when it is ready and keep the previous
  behaviour when it is not.
- A refusal before the prompt is sent does not spend a caller-supplied
  iteration budget. The prompt keeps the current request, and stays within
  100,000 characters, when the prior transcript is long. A failed launch
  records `job_end` so `omega watch --follow` stops. That write is
  best-effort: a full state disk still returns the original failure.
  `--follow` waits for the event file instead of exiting while omp is
  still starting. Plan mode, a `child_tool_hook`, and a narrowed tool map
  stay on the Python loop, including a delegate whose parent has no
  child model.

### A1b dispatch

#### Added

- `omega_prime.engine.fleet_client`: posts one `JobSpec` to the coding-fleet
  coordinator (`POST /jobs`), polls `GET /jobs` and job events, and answers
  HITL on `POST /hitl/answer`. Coordinator URL and token come from
  `FLEET_COORDINATOR_URL` and `FLEET_COORDINATOR_TOKEN` (or the matching
  `*_FILE` names). Values are not logged.
- `omega_prime.engine.lanes`: the same `JobSpec` chooses the local lane or
  the fleet. Local caps are 60 minutes, 25 files, 2 concurrent jobs, 3 GiB
  free, and one `xai-oauth` job. Rust, Go, Docker, and mobile markers force
  the fleet. A full box spills or queues. A local job that hits a limit is
  re-posted with its partial branch, and the receipt records the lane change.
  The local executor in this change is a stub.
- `omega_dispatch` requires approval and `confirm_token`.
  `omega_jobs_status` is read-only. `omega_jobs_cancel` requires approval.
  The coordinator token is resolved by the credential broker, and only after
  `SeatPolicy.allows_host` allows that host. A token file that cannot be
  read stays a fleet error. A missing or empty file is not reported as a
  permissions failure. A local reservation is locked
  in the state directory. A local job with no file or
  minute bound is stored at the local caps. Escalation posts the original
  task text; receipts stay redacted.

### Substrate connection and uncapped local execution

#### Added

- A guarded non-secret settings file at `~/.omega-prime/settings.json`,
  overridden only by `OMEGA_PRIME_SETTINGS_FILE`. Process environment
  precedence, allowlisting, file safety checks, and value-free diagnostics
  keep credentials on their existing secret paths.
- MCP Streamable HTTP POST support with JSON/SSE response parsing, request-id
  correlation, and session-id reuse. Argument mappings follow supplied audit
  expectations; current upstream mount/schema compatibility remains
  unverified.

#### Changed

- Substrate configuration has no silent localhost default; `local` is an
  explicit opt-in. Missing configuration and transport/protocol/tool failures
  are reported distinctly. Doctor/setup checks report configuration without
  claiming reachability.
- Engine turns and default local lanes have no duration deadline. Process and
  pipe liveness detect failure; they do not limit inference. Other resource
  and capacity limits remain.


### Purple-team operator tooling (batch 18)

#### Added

- `redteam.findings_sync.sync_budget` (integer or per-tracker mapping with a
  `default`): caps non-urgent rows dispatched per tick and tracker. The
  budget is spent medium first, then low, then other severities, oldest
  first, and pushes stay in store order. `critical`/`high` rows bypass it
  and are deliberately uncapped. Quiet hours are decided first. Mappings
  resolve per entry and are looked up by the effective tracker (graph wins
  when its credentials are complete). The tick result gains `sync_budget`
  and `budget_held` (`reason: "sync-budget"`) when the budget is valid and
  `sync_budget_invalid` (`type`, `value` or `entry`) when the key is
  present but unusable. Unset leaves results unchanged. The `_main_<tracker>`
  CLI entry points apply neither quiet hours nor the budget.
- `pin_bump --report-to PATH`: writes the audit delta report to `PATH`
  instead of `<root>/report.md` (atomic, mode 0644, through a directory
  descriptor pinned with `O_PATH`, so ancestors need only search
  permission), validated up front (usage errors exit 2: trailing separator,
  a path ending in `/.` or `/..`, file name over 200 bytes, symlink or non-regular
  file, and an existing file that is neither empty nor a previous pin-bump
  report, so `VENDOR.md` is never overwritten), replay-validated for
  schedules, with a `report` key in `--json` only when set. Printed paths
  escape control characters.
- `scripts/engagement-report.py --digest`: a stdout-only digest of at most
  five lines (summary, `coverage covered=C/T gappy=G unexercised=U
  risk=S.S`, `worst=...`, an optional gate line). Exit 0, 1, 2 or 3; it
  cannot combine with `--json` or `--summary`. The gate line and all digest
  stderr name only schema classes or `other` and escape control characters.
- `scripts/engagement-report.py --export-jsonl OUTPUT_JSONL`: the coverage
  matrix as JSON Lines, one `coverage/1` object per class. Absent or
  regular targets are replaced atomically with mode `0666 & ~umask`; an
  existing FIFO, device or socket is written in place; a symlink to a
  regular file, directory or nothing is refused (exit 1); a symlink to a
  stream such as `/dev/stdout` is written through. `--digest --baseline
  other=N` is rejected.
- `roe_lifecycle.grant_conflicts` and the `roe_lifecycle conflicts` CLI
  (exit 0 none, 3 found, 2 usage): advisory detection of
  `duplicate-id-divergent`, `tool-scope-contradiction`,
  `tracker-cover-contradiction` and `operator-mismatch` across several RoE
  grants. At most 100 rows are listed, then one `conflicts-truncated`
  sentinel row; grant files over 1 MiB are ignored. `findings_sync.tick`
  surfaces a non-empty result as `grant_conflicts`, also on the grant-skip
  result. It never blocks authorization. With no budget set, tick results
  are identical unless two or more configured grants conflict.
- Three read-only chat commands, `/omega-redteam-report`,
  `/omega-redteam-coverage` and `/omega-redteam-sync-plan`, over the digest,
  the coverage matrix and the findings-sync plan. Output is bounded and
  path-free: `/omega-redteam-sync-plan` lists at most 20 entries per list
  with `<name>_total` counts (`grant_conflicts_total` also counts conflicts
  beyond the listed and detector-capped rows), escaped and clipped hashes
  and tracker, and grant path labels as basenames or non-clashing
  `grant-N`; non-structured failures return only the
  exception type name; `--since` and `--threshold` accept ASCII digits only.
- `python -m omega_prime.grokbot.purple_cycle` (modes `plan`, `run`,
  `apply`; six steps from preflight to findings dispatch; resumable
  checkpoints and an NDJSON ledger) and `/omega-redteam-cycle`, which plans
  by default and with `--run` refreshes release-watch state and audits a
  new upstream tag. Chat never dispatches findings or applies a pin bump
  and returns no checkpoint or ledger path. Apply is refused (exit 3)
  without a configured RoE (`roe-missing`), with an expired, missing,
  invalid or revoked RoE in the union of `redteam.roe_file(s)` and the
  findings-sync grant keys (revocations matched by realpath and file
  identity), with a config-level kill or `redteam.enabled: false`, or with
  a non-IDLE harness; the gate is re-checked on every pass and resume, and
  a blocked step is never checkpointed. Stored checkpoint records are
  validated strictly (invalid: `checkpoint-unreadable`). Cycle ids are `purple-cycle-YYYYMMDD-<mode>[-s<8hex>]
  [-HHMMSS]`; checkpoints live under `<reports>/purple-cycle/workflows/`,
  and a failed pin-bump audit is a non-halting `audit-failed` warning,
  retried through `<reports>/purple-cycle-pending.json`
  (`{"attempts": n, "tag": T}`) and abandoned after 3 attempts
  (`audit-abandoned`); only `v<digits>.<digits>...` tags are audited. `--now` is rejected in apply mode
  and option abbreviations are rejected. The release-watch `gh api` call
  times out after 60 s (exit 3).
  Usage: [Operator tools](docs/red-team.md#operator-tools).

#### Changed

- Slash command counts: curated 20 -> 24, total 54 -> 58 (generated stays
  34). The red-team command family grows from eight to twelve.
  `docs/red-team.md`, `docs/driving-the-bot.md`, `grokbot/SETUP.md` and
  `docs/marketing.md` carry the new counts.

### Repository marketing

#### Added

- `docs/marketing.md`: positioning, a one-pager, use cases each tagged local
  proof, optional capability or unproven external, a no-credential local
  demo that states every command's exit code, the Grok Bot setup route with
  links to the evidence ledger and user UAT rows, a launch announcement and
  short/social copy. `docs/SUMMARY.md` lists it once; the README and the docs
  landing page link it.
- `assets/banner.svg`: the third line now reads "Hermes and Omp behavior
  ports, one Python process." and moved from `#6b7484` to `#9aa4b2` for
  readable contrast; `<title>`, `<desc>` and `aria-labelledby` were added.
- `assets/social-card.svg` (1280x640) and its raster
  `assets/social-card.png`, rendered from the repository root with system
  CairoSVG 2.9.0 on PATH by
  `python3 omega_prime/scripts/render_raster.py assets/social-card.svg`
  (exit 0), the command recorded in the SVG header comment. The script
  stamps the SVG's SHA-256 into the PNG, and the asset test fails when an
  SVG no longer matches the digest its PNG carries. The SVG validity test
  covers the card, and banner and card must each carry a `<title>`.

Marketing copy claims no live Grok Bot or Cursor import: the Bot-driven
setup is a documented, locally verified path whose live rows remain
[user UAT](docs/grok-bot-native.md#user-uat). Local proof is the maintained
commands the demo lists, each with its exit-code contract. No timing,
performance figure, fixed tool total or screenshot is stated, and there is
no website, outreach, marketplace listing, hosted deployment, image
publication or social-preview upload.

### Grok Bot import packaging

#### Added

- Root `mcp.json` declares one stdio server, `omega-prime`:
  `bash ${CURSOR_PLUGIN_ROOT}/omega_prime/scripts/grokbot-1click.sh --bootstrap --transport stdio`,
  with no `env` and nothing credential-shaped. `.cursor-plugin/marketplace.json`
  and `.cursor-plugin/plugin.json` describe the repository as one Cursor
  plugin for a Teams/Enterprise team marketplace (secondary route). The
  HTTPS alternative ships only as the non-referenced
  `omega_prime/grokbot/plugin/mcp.https.example.json` with a `${VAR}`
  placeholder.
- One generated `/omega-<name>` command per checked-in skill and routine
  beside the 20 curated commands (54 today), routed through the existing
  `omega_command` tool; a generated name that collides takes a `-skill` or
  `-routine` suffix (`/omega-python-skill`, `/omega-onboard-routine`,
  `/omega-connectors-routine`, `/omega-ultrathink-skill`,
  `/omega-ultrathink-routine`). No new tool joins the roster.
- `python -m omega_prime.tooling.plugin --write|--check|--verify` renders 54
  command files plus the manifests deterministically, fails `--check` on any
  drift, frontmatter, collision or secret-shape problem, and `--verify`
  spawns the declared stdio command and a loopback bearer host. `setup_check`
  gained a `plugin` row and CI a plugin currency step.
- The launcher gained `--bootstrap`, `--bootstrap-only` and
  `--verify [--json]`: a stamp-gated, `flock`-serialized install from
  `requirements-lock.txt` that resumes after an interrupted start, never
  replaces an operator-managed `.venv`, initializes only `prime-agent`, and
  exits 10 `missing_prerequisite`, 11 `install_failed`, 12 `catalog_drift`
  or 13 `host_unreachable` with one stderr JSON line and empty stdout.

#### Changed

- The launcher resolves the repository from its own location, never the
  caller's working directory. Its `--work-root` now reaches the stdio host
  as well as SSE, and an SSE serve no longer exports the bearer token into
  the launcher's process environment, which serve children inherit.
- `security-secrets-handling` and `security-supply-chain` skill names now
  match their folders (were `secrets-handling` and `supply-chain`); four
  other skills' frontmatter parses as YAML and `lead-pack` gained
  frontmatter. Copies under the old names in a private Bot skill library are
  not touched.
- The host manifest no longer claims a Grok Bot settings import and lists
  Streamable HTTP at `/mcp`. The Bot template's first run uses the
  Bot-driven setup.
- `grokbot/SETUP.md`, `docs/grok-bot-native.md`, `docs/setup.md`,
  `docs/driving-the-bot.md` and the README document the Bot-driven setup
  prompt as the primary route, the Cursor Teams/Enterprise Import from Repo
  as secondary, the Remote HTTPS alternative with per-consumer placeholder
  syntax, the whole-message prompting contract, an evidence ledger and a
  six-row user UAT list. `--json` is documented on stderr under served
  stdio.

#### Fixed

- The Bot template's first run told the Bot to read
  `prompts-assembled/OMEGA_PRIME.xml` and
  `contracts/tool-rosters/omega-prime.yaml`, which do not exist at the clone
  root; it now names `/workspace/omega-prime/omega_prime/...`, like the
  launcher commands beside them.

Live import on a Grok Bot or Cursor account is unproven: Grok Bot loading
plugin skills and commands, a marketplace-declared stdio launch,
`${CURSOR_PLUGIN_ROOT}` on the Bot's computer and its MCP startup timeout
remain user UAT. Local proof is the generator `--check`/`--verify`, real
stdio through the declared command and a loopback bearer host. No
marketplace listing, hosted deployment or image publication.

### Documentation and knowledge base

#### Changed

- Import all 21 full exposed Greptile documents, including index, into one
  visibly historical/untrusted mirror with canonical namespace, complete
  path/version/count/hash provenance and actual observation clocks. Refresh
  admits the full before/get/after candidate before publication, restores
  handled publication failures through same-filesystem backups, and performs no
  writes for unchanged editions; it is not crash-safe or parallel-writer
  coordination. Direct transport, validation and filesystem exceptions and
  tracebacks withhold credential-bearing diagnostics without weakening the
  shared detector or rewriting source bodies. A cleanup failure after successful
  publication reports failure without claiming the new edition was rolled back.
  Reconcile canonical architecture/state/security diagrams, extension patterns,
  runnable setup/usage, repository URLs, Python/prerequisite distinctions and
  repository/GitBook navigation.
  Correct the linked operator setup's unproved share-link/time/universal-failure
  claims and distinguish launcher stdio environment roots, SSE-only workspace
  forwarding and adapter errors from readiness classifications. Actual served
  stdio now reserves stdout for JSON-RPC; diagnostics and optional JSON reports
  remain visible on stderr, with real initialized owned-file consumers.
  Keep generated catalog behavior consumers, not source-wording/default/schema
  copies. Local proof and readable historical
  publication do not establish live Grok/provider/backend operation, account
  enrollment, vendor redistribution rights or remote publication/deployment.

#### Fixed

- `docs/local-validation.md` states the current `--include-prime` counts
  (77 exercised, 77 blocked, 11 feature-disabled; the five refused RLM tools
  are no longer credited as exercised). `docs/setup.md` says `--verify`
  starts a loopback bearer host, not an HTTPS host, and documents the CI
  `docs` job.

### Slash commands

#### Added

- Served tool `omega_command` is on the canonical roster; the effective default/configured subset follows registration and flags. A user message that is only `/omega-...` runs that command and does not call the model. `/omega-help` lists the catalog. `/omega-onboard` is first run. `/omega-python` is ruff and compileall. `/omega-connectors` names missing env vars and does not ask for a secret in chat.
- Cron admin (`cron_jobs_list`, `cron_job_create`, `cron_job_remove`), learning (`autolearn_turn`, `advisor_note`, `advisor_render`), and `durable_status`. Mutating cron and autolearn calls require approval.
- `rlm` and `messaging` register when `OMEGA_PRIME_PRIME_RLM_ENABLED` or `OMEGA_PRIME_PRIME_MESSAGING_ENABLED` is on. Without a child-model provider, `rlm_spawn` returns `not_configured: provider`.
- Nested tool calls from a host `omega_command` re-enter that call's roster and interceptor chain.

### Remote tool host

#### Added

- Streamable HTTP at `/mcp` beside legacy SSE (`/sse`, `/messages/`), on one
  process, one roster, and one shared tool gate.
- Scoped bearer tokens (`read`, `call`, `admin`) in a hashed mode-0600 file,
  managed by `python -m omega_prime.grokbot.tokens new|list|revoke`, reloaded
  without a restart. The single `--token-file` / `MCP_AUTH_TOKEN` principal
  stays `read`+`call`.
- Admin approval API `GET`/`POST /admin/approvals` and
  `DELETE /admin/approvals/{tool}` (TTL, revoke, audited). The approver is
  the admin principal, never the bot. The routes are not mounted when
  authentication is off.
- Per-principal HTTP and tool-call rate limits (defaults 600/min and
  300/min), an auth-failure throttle (10 per 60 s), and a per-tool circuit
  breaker (5 infrastructure failures, 30 s cooldown). `0` disables each one.
- Prometheus `GET /metrics`, `X-Request-Id` and W3C `traceparent` on every
  response, and `--log-format json` NDJSON logs. Access logs omit the query
  string and headers.
- `GET /manifest.json` for the tools this process actually serves, plus
  `approval_required` and a digest. `GET /readyz` reports the token store
  and Streamable HTTP.
- Live checker `python -m omega_prime.grokbot.verify`, and
  `python -m omega_prime.grokbot.oneclick --self-test` (verifier, then
  SIGTERM). The 1-click launcher also accepts `--token-store` and
  `--log-format`.
- Operator guide `docs/grok-bot-native.md`. Deployment stays in
  `docs/deploy.md`; dependency manifests describe the PyRIT/transformers tree
  without an unmeasured image-size claim.

#### Changed

- `omega_prime/grokbot/SETUP.md` section 3, `docs/tool-host.md`, the README
  Grok Bot pointers, and `SECURITY.md` describe the remote host that ships:
  header-only bearer auth, both transports, and the admin approval API.
- CI keeps Python3.12–3.14 verification/lint/type matrices and independent
  lock/native/license gates with least permissions, concurrency and job bounds.
  Verify legs run maintained isolated readiness and retain only guarded canonical
  reports or private failure metadata alongside receipts/JUnit; duplicate JSON
  members refuse before admission, and failures/unobserved runs never become green.
  Image validation exercises both
  transports/admin transitions under hardening and SIGTERM, with SPDX plus the
  unchanged blocking fixed-CRITICAL scan. An optional default-off manual publisher
  binds protected environment/ref/source/run/artifact and exact transferred bytes,
  never a rebuild; KB refresh keeps missing-key skips truthful and updates PR-only.
  Release/rollback guidance distinguishes these controls from authorization and
  uses the prior validated artifact/digest. No image publication, deployment,
  cluster operation or live Grok/provider/Red Team backend proof was performed.

#### Fixed

- `requirements-lock.txt` installs again. Merged Dependabot majors had made
  it uninstallable (`oauthlib` 4 against tweepy's `<4` pin, and
  `huggingface-hub` 2 against tokenizers' `<2` pin). Dependabot now ignores
  those two major bumps.
- `JobStore._save` replaces the cron job file by temp file, fsync, and
  rename. A crash mid-write no longer truncates the schedule.
- Access logs no longer include the query string. The integrated host turns
  uvicorn's access log off; a token in `?token=` is not a credential and is
  not written there.
- Scope denials are in the audit log and in `omega_tool_denials_total`.
  Audit and metrics run before the interceptors that deny, so a `read`
  token's `forbidden` result is recorded.
- Tool calls run in a worker thread behind one gate, one at a time. A slow
  tool no longer blocks the event loop (health checks, keepalives, shutdown),
  and a tool that calls `asyncio.run` can run.

#### Security

- Remote authentication is header-only bearer tokens with `read` / `call` /
  `admin` scopes, constant-time digest compare, and a fail-closed
  non-loopback bind (exit 2 without a credential; exit 3 on a strict
  preflight failure). `/admin/approvals` requires `admin`. Argument values
  are not written to the audit log.

### Local readiness

#### Added

- Isolated readiness CLI `python -m omega_prime.grokbot.readiness --root
  SOURCE --json --output REPORT`, with `--include-prime` for the safe local
  Prime lifecycles. Exit 0 only when the validated report has `ok` true, 1
  on readiness failure, 2 on usage errors. The JSON v1 report carries every
  roster name exactly once with `exercised` / `feature_disabled` / `blocked`
  / `failed` status, evidence, and named prerequisites. Documented in
  `docs/local-validation.md` and linked from `docs/SUMMARY.md`.
- `IntakeStore` serializes cooperating writers: every public operation
  reloads `intake.json` first, and mutations run as reload–mutate–atomic
  replace under the thread lock plus an exclusive `flock` on the stable
  `intake.json.lock` sidecar. A corrupt file raises. Stale-lease completion
  is not fenced and the queue is not globally transactional with other
  stores.
- Slash doctor steps take explicit caller state only: keyword-only
  `doctor_context` with `prompt_sha256` and `installed_skills`. Missing or
  incomplete context never implies healthy, a workflow past a failed
  optional step stays `workflow_incomplete`, and the doctor check preserves
  the installed expected prompt digest when saving its outcome.
- The public `mcp_call` tool runs through the canonical initializing SDK
  session client; the obsolete uninitialized one-line client is removed.
  Inside an active loop the call moves to a joined non-daemon thread owning
  its loop, bounded by a deadline with the child tree reaped on timeout.
- `assert_no_secrets` treats `str` as literal text and any `os.PathLike` as
  a file with 0600 enforced before reading; token-shaped paths get no
  exemption. The readiness self-test runs in the disposable isolated layout,
  so no source-install schedule ticks and no live credential is inherited.

#### Fixed

- The disposable export routes regular entries, aliases, and resolved
  targets through one complete admission predicate: pruned and excluded
  shape first — including `.planning` ancestors by component equality —
  then provenance. Excluded-state aliases fail closed on both ends,
  regular Prime `.env` files are never exported, and untracked learned
  `SKILL.md` and helper scripts cannot ship on re-export. Fresh safe
  ordinary product Python stays Git-ungated, while private-named product
  Python is admitted only with verified Git-tracked or inherited-inventory
  membership — a deliberate trusted public-code distinction, not an
  exemption for credential data or untracked private Python. Optional
  Prime directories filter per entry by submodule-tracked or
  inherited-inventory membership, and every vetted export writes
  `.omega-readiness-inventory.json` so Git-less Docker and source-archive
  distributions carry trusted provenance or fail closed on the absent
  resource; missing Git or a malformed inventory never vouches.
- Published diagnostics are curated after full-text gating: complete text reaches known-minted-secret replacement plus audit redaction and then the unchanged secret gate before any clip — known-token replacement alone and final-only scanning do not close the leak. The gate recognizes only its documented patterns (audit credential shapes, `omk_` 8+ suffix, PEM armour, 32+ mixed-class runs at 4.2+ bits/char entropy with hex/UUIDs excluded), not every possible secret. A trip publishes only fixed safe metadata with no classified fragment and stays failed; readiness fragments are no longer published, self-test fragments reach the same full-input gate before any tail/clip window, raw loopback error slicing is removed, Prime producers
  pass complete raw error text to the same shared gate with no extra
  scanner or entropy relaxation, and the final verifier human/JSON/self-test publication re-verifies complete report data including metadata with the same failed-and-safe behavior.
- Verifier, self-test launcher and readiness CLI usage diagnostics are guarded
  before stderr publication. Recognized secret-shaped rejected arguments and
  missing-root paths produce only fixed safe labels; usage failures retain
  exit2 and clean argument/path guidance remains informative.

### v10 — Prime merge

#### Added

- Native `ai.complete` runs actual `pa_ai::complete_simple` in-process; native
  `ai.resolve_models` resolves Prime's real model catalog. One bounded Tokio
  runtime releases the GIL while waiting, checks Python interrupts, and bounds
  cancellation cleanup. Unknown options are errors, not silently ignored.
- `PrimeProviderModel` translates real tool schemas, images, tool results, and
  thinking. `OmegaPrimeAgent.from_prime` composes it with the existing loop and
  registry approvals. Durable `prime_message` transcript metadata preserves
  provider response IDs and opaque signatures across tool rounds and reloads.
- Restored Desk intake authority, contract-first change discipline, gate
  integrity, and live-gateway rules in the shared directives and assembled prompt.

- Prime Agent capabilities ported into the one Python agent (behavior port;
  see `docs/adr/0001-prime-merge-architecture.md`): RLM recursion
  (`rlm_spawn`/`rlm_collect`/`rlm_list_subagents`/`rlm_delete_subagent`/
  `rlm_create_session`/`rlm_progress_note`/`rlm_rename`), the continual harness
  (`harness_*` with evidence-backed refinement, snapshots, and exact
  rollback), persistent goals with token budgets and continuation prompts
  (`goal_*`), session heartbeats on the cron scheduler (`heartbeat_*`),
  bounded autonomous mode with a shell quality gate (`autonomous_*`), and
  agent-to-agent messaging (`agent_message_send`, `agent_observe`).
- `omega_prime/prime/` connector layer: typed, versioned adapters
  (`SCHEMA_VERSION`, strict decoders, structured `PrimeError`s) between the
  tool surface and every ported capability, with contract, failure-injection,
  and source-cited parity-fixture test suites.
- Registered capability handlers now consume the typed request/response boundary,
  including explicit versions and unknown fields, after policy and approval.
  Actual loop runs cover raises, timeout snapshots, malformed requests and stored
  responses, and valid recovery without fixed-sleep or guarded-hook-only proofs.
- `omega_prime/prime/heartbeat.py` (`SetRequest`, `ClearRequest`, `ListRequest`,
  `HeartbeatConnector` over `HeartbeatRuntime`) and `omega_prime/prime/kernel.py`
  (`CellRequest`, `FactoryRunRequest`, `RunRequest`, `FactoryGraphRequest`,
  `BashRequest`, `SkillListRequest`, `CratesRequest`, `GoalCommandRequest`,
  `AutonomousCommandRequest`, `KernelConnector`): every registered Prime tool
  now decodes through a versioned typed request. A rejected request never
  imports the pinned runtime, builds a kernel, or touches the filesystem.
- Assembler CLI `--output PATH`. With any `--enable-family` it is required; the
  canonical `prompts-assembled/OMEGA_PRIME.xml` is then never written or
  compared, and `--check` compares `PATH`.
- `OmegaPrimeAgent(..., session_dir=...)` supplies the durable directory the
  agent offers as an RLM parent. The RLM parent contract (`session_dir`,
  `session_name`, `delegate_depth`, `max_depth`, `max_children`) is checked by
  `require_parent` at `RlmHost` construction and at `register_rlm_tools` when a
  parent is given.
- RLM children persist atomic prompt/answer/identity/status documents, recover
  their owner's records without replay, and persist rename/delete lifecycle state.
  Collect waits for durable terminal settlement; list/delete are metadata-only.
  Bound kernel progress uses parent acceptance and throttling. Missing runners
  and kernel model selectors are explicit errors, not invented defaults.
- Default-off prompt assembly filters disabled Prime families; explicit config
  or repeated assembler family flags opt them in. Catalog metadata now reads
  real registrations rather than fabricated approval or parameter defaults.
- Source-executed pre-v10 transcripts at `79ff51af` preserve six actual consumer
  cases' messages, approvals, effects, errors, stops, and model-call accounting.
  The unmodified historical suite records two obsolete source/inventory-pin
  failures separately; no full unmodified-suite pass is asserted.
- `omega-prime.json` config surface: every Prime family is a default-off
  flag (`prime.<family>.enabled`, or `OMEGA_PRIME_PRIME_<FAMILY>_ENABLED`);
  default-off loop behavior is checked against actual historical transcripts.
- Wired registered goals and autonomous runs into the existing conversation
  loop: fresh goal state, once-only usage accounting across tool rounds and
  provider failures, cumulative continuation bounds, and honest stop reasons.
  Goal pause/clear/completion/limits veto continuation and completion gates;
  approval refusals remain refusals rather than capability degradation.
- Heartbeats now drive named live sessions through in-process APScheduler and
  wait behind foreground leases. Clear/reschedule remains safe during a beat;
  closing one owner preserves others, and last-owner shutdown settles callbacks.
  Due-now jobs fire immediately; later explicit stops cancel stale restarts.
- Capability hook failures emit redacted `prime_degraded` events and preserve
  the normal response without extending implicit work. Provider/control-flow
  exceptions remain errors; quality-gate success never implies goal completion.
- Rust parity-oracle CI (`rust-parity.yml`): the pinned prime-agent
  workspace builds and tests on a pinned toolchain with cargo-deny license
  enforcement; CI consumes the authoritative `contracts/prime-agent.pin.json`.
- Supply-chain CI: pip-audit workflow and dependabot for pip and
  github-actions; `.github/CODEOWNERS`.
- Docs: ADR 0001 (merge architecture), `connectors.md`, `agent-loop.md`,
  `migration.md`.
- `prime.kernel` (default off) loads the pinned `rlm` package in-process:
  persistent cells via `rlm.repl`, factory, `rlm.bash`, and skill packages
  from the checkout. `native/omega-prime-prime` is the PyO3 binding for
  the pinned crates. `prime_goal` and `prime_autonomous` call that
  binding. `crates.probe` calls each of the nine crates without starting
  the TUI or a daemon worker. See ADR 0002. The checkout sources are not
  modified.
- The seat prompt names each `skills/<name>/SKILL.md`, each roster tool,
  and `routines/desk-lead.md`, `routines/nightly.md`, `routines/sweep.md`,
  and `routines/ultrathink.md`. The Grok template lists those skills and
  routines. Bodies stay in the skill files.
- `docs/driving-the-bot.md`: operator guide for a Prime-enabled Grok Bot. It
  covers which families the tool host can serve and what each does behind a
  Grok Bot, turning families on, `--approve`, building the effective prompt,
  checks, and troubleshooting. Linked from the README, setup, and FAQ.

#### Changed

- The MCP tool host flags a result as an error only when its `error` field is
  non-null. A payload carrying `"error": null` (`prime_crates` once the
  extension loaded, a successful `prime_cell`) was reported as failed.
- `prime_cell` returns what the cell printed while it ran as `stdout` and
  `stderr`. That output used to go to the tool host's own streams, so the bot
  never saw it. A cell that raises `SystemExit` or `KeyboardInterrupt` (`exit()`,
  `sys.exit()`) is now a cell error instead of ending the host.
- `.gitignore` covers the Prime family state the tool host writes under
  `--root`: `goals/`, `harness-local/`, `harness-global/`, `cron/`,
  `prime-kernel/`, and `prime-agent-dir/`.
- Corrected runtime documentation: linked-crate probes are not a full Prime
  application merge. Disabled families are absent from effective tools and
  canonical prompt entries; the policy contract remains an allowed superset.
  Native provider selection is independent of the kernel-tool flag.
  Optional typed hook imports now occur at their capability boundary, avoiding
  the cold assembler/registry import cycle without breaking legacy agent exports.
- Corrected loop boundaries for normal Anthropic `end_turn`/`stop_sequence`,
  idle-family last-call completion, and accepted-answer preservation when a later
  continuation is interrupted. Mixed component/total usage now charges every
  paid call once while preserving raw public/native metadata.
- Heartbeat validation now rejects non-finite, unrepresentable or non-advancing
  calendars before persistence, contains malformed rows without hot re-arming,
  preserves foreign scheduler entries, and catches up overdue work once.
  Reusing a shut-down injected backend is an explicit error.
- Harness refinement snapshots pre-change entries, so actual `harness_refine`
  followed by `harness_rollback` restores prior entries without rewriting the
  fixed base prompt or audit trail.
- Removed obsolete source/default/wording pins rather than repinning incidental
  assertions; actual loop, source-policy, pinned-checkout and native checks
  retain consumer and provenance evidence.
- Native source headers and a tracked license allowlist cover the product bridge;
  its independent CI job builds and checks licenses even if upstream parity
  tests fail. Cargo Dependabot remains an explicit REPO-04 audit gate because
  its required pinned path manifests are ignored, not repository-fetchable.
- Corrected broken repository links and the clone URL to
  `swcstudiospace/omes-bot`. Upstream Rust diagnostics retain their known nonzero
  baseline rather than appearing as a passing quickstart step.
- Every Prime tool family (rlm, harness, goals, heartbeat, autonomous,
  messaging, kernel) accepts only its declared parameters plus `schema_version`.
  Any undeclared key, including session-bound or alias fields (`sender`,
  `session`, `scope`, `stale_after_turns`, `mark_read`, `cwd`), is rejected with
  `unknown_field` and is never merged into the decoded payload. Omitted required
  fields reach the typed decoder (`bad_type`; closed vocabularies such as the
  harness `kind` give `bad_value`), and `schema_version` must be >= 1.
- `ToolRegistry.dispatch` audits a typed failure with verdict `error` and reason
  `<code>: <reason>` and a policy or approval refusal with verdict `denied`;
  the order is policy, then approval, then decode.
- `agent_message_send` and `agent_observe` take `sender` and `session` only from
  the session bound at registration, so a forged sender and a cross-session
  inbox read or consume are impossible. Connector errors are mapped by cause:
  `bad_value` (empty body), `unknown_sender`, `unknown_recipient`, and
  `unknown_session`.
- `goal_set` replaces the whole goal (objective, steps, and the Prime sidecar)
  after validating everything up front (`PrimeGoalStore.replace_goal`). Blank
  steps are `bad_value`, a rejected request writes nothing, previous steps never
  carry into the new goal, and the result is the status after the steps are
  applied. `stale_after_turns` is not a tool argument.
- Harness tools take `global_` as the only scope input; a `scope` key is
  `unknown_field`.
- `autonomous_start` rejects a non-finite `max_minutes` (NaN, +/-Infinity) as
  `bad_value`.
- RLM spawn's depth bound now reads the parent's `delegate_depth`, and there is
  no silent working-directory default for the session directory.
- `rlm_create_session` has no `cwd`: the tool rejects it as `unknown_field`, and
  the typed request accepts `cwd=None` (the pinned SDK sends it) but rejects any
  other value with `bad_value`.
- RLM admission (name, child limit, depth bound, registration) is one atomic
  section, and the roster record is created only after admission.
- RLM recovery rewrites only interrupted records and survives a failing write:
  the record stays registered in memory as an error naming the failed write.
  One live host per (`session_dir`, owner `session_name`) is the supported
  assumption.
- RLM delete tombstones keep only identity, owner, and status; they hold no
  prompt, answer, or error text.
- `rlm_collect` reports `answer_preview` and `error` only for settled children.
  `collect` and `list_subagents` read the in-memory registry under a lock that
  no file write holds, so they never block behind a durable write.
- Tool-call arguments in the conversation loop must be a JSON object. `None`
  and the empty string mean no arguments; any other malformed or non-object
  payload (truncated JSON, a list, a number, `null`, whitespace-only text)
  yields an `error:` tool row and the call is not executed, while the other
  calls in the round still run.
- The refusal stop reasons `approval_refused` and `policy_refused` describe the
  most recent tool round of the run only, so a refusal the model recovered from
  does not make the turn terminal.
- The pre-v10 transcript comparator (`tests/test_prime_regression.py`) is
  documented as covering `Agent`/`run_conversation` with a test-local
  dispatcher; the registered public agent's error rows are covered by
  `tests/test_prime_failure_injection.py` and the boundary tests.

- The retained Rust diagnostic in `VENDOR.md` must run with no published
  `prime-agent` binary on `PATH`. That binary is not the TypeScript
  oracle `pa-cli` differential tests compare against. With 0.9.8 on
  `PATH`, `ts_daemon_differential_cli_output` runs and fails: `send`
  exits 1 on both sides with `No API key found for the selected model`
  instead of the `Sent to ...` receipt.
- The same command must run without ambient provider credentials
  (`GH_TOKEN`, `GITHUB_TOKEN`, `OPENROUTER_API_KEY`). The parity workflow
  unsets them. `cache_rejects_replacement_and_same_length_in_place_rewrite`
  can flake where `futimens` does not bump ctime (Linux 6.8.0-142 / ext4
  here); it is not a pin exclusion, and CI remains the gate.
- `imported_session_compacts` fails on this ext4 `/tmp` because the test
  reads the first `grown-import*` directory entry, and `readdir` returns
  the window sidecar `grown-import.window-cache.json` before the session
  file. The import succeeds; the counted message rows are 0 instead of
  3600. It is not a pin exclusion.

- **Relicensed from MIT to AGPL-3.0-only**, Copyright (C) 2026 Spectrum Web
  Co. Upstream-ported modules retain their MIT attributions (VENDOR.md);
  new source files carry SPDX headers. See `docs/migration.md`.
- Supply-chain audit reads `requirements-lock.txt` and ignores
  PYSEC-2026-4114. That advisory is an oauthlib authorization-server PKCE
  timing oracle; tweepy 4.17 pins `oauthlib<4`, and this process does not
  host that grant. See SECURITY.md.

### Added

- Pinned development lint/type checks and a Python 3.12–3.14 CI matrix with
  a reproducible dependency lock and an audioop-safe Discord import.
- Provider transient-failure retries, per-turn token usage, OpenAI Responses
  routing with chat-completions fallback, and streamed conversation turns.
- Hermetic PyRIT multi-turn attack campaigns and scorer-based judging.
- SEC-NET 2.2.0 egress transport hardening and verified browser sandbox boundary
  confinement (see [SECURITY.md](SECURITY.md)).

### Changed

- Renamed the product from Omes Bot to Omega Prime: Python package `omes` is
  now `omega_prime`, the distribution is `omega-prime`, the seat is
  `bot-00-omega-prime`, contracts moved to `contracts/tool-rosters/omega-prime.yaml`
  and `contracts/policies/omega-prime.json`, the template is
  `grokbot/templates/OMEGA_PRIME.md`, and `OMES_*` environment variables are
  now `OMEGA_PRIME_*`.
- Documentation search now returns bounded, redacted excerpts and explicit
  citation provenance instead of copying the raw retrieval response.
- Ultrathink mark evals exercise the real approval contract through an injected
  runner; removing the production approval requirement fails the red-team case.
- Setup and tool documentation reflect the default tool surface: the roster YAML
  lists 146 names including the 37 Prime tools, the default assembled prompt names
  109, and the default MCP registry serves 108 because it skips `delegate_task`
  (it needs a live agent). Test/eval totals are reported by CI instead of
  pinned in prose.
- Replaced incidental source/configuration-copy assertions with executable
  lint/type/catalog gates and consumer-visible regression coverage.
- CI jobs share a stdlib receipt helper, preserving command failures and
  distinguishing missing, skipped, cancelled, and failed validation evidence.

### Fixed

- Prevented retrieval content, metadata, and error messages from bypassing
  existing credential redaction at registry, MCP, and model boundaries.
- Distinguished malformed docs responses from valid searches with no hits.

## [0.1.0] — 2026-10-03

First public release: one Grok programming bot, eight milestones deep.

### Added

- One-agent runtime (v1): Hermes conversation loop plus the oh-my-pi
  harness — tools, skills, memory, delegation, sessions, modes — in a
  single Python process with a Grok provider and Add-Bot template.
- Enterprise hardening (v2): declarative seat policy, credential broker
  with redaction, durable SQLite runs, stdlib HTTP transport, audit log,
  structured traces.
- Grok Bot flows (v3): X connector with approval-gated publishing,
  engagement sweeps, nightly learning, deterministic eval harness, CI.
- Third-party integrations (v4): tweepy, MCP SDK sessions, PyRIT battery,
  APScheduler backend, Telegram + Discord connectors as real pip deps.
- Desk packs (v5): the Programming Desk absorbed as lead + domain packs —
  systems/web/mobile/infra/quality families, Playwright browser and device
  seams, 24 desk skills, pack evals.
- Grok ship (v6): magic keywords, native ultrathink, MCP tool host,
  template + setup flow, MIT license, GitBook docs.
- Substrate surface (v7): brief-on-open, turn/tool event trail, shared
  memory, Hindsight episodes on the shared bank, RAGflow docs, graph
  coordination, store-lock guard, read-only live probes.
- Public launch (v8): branded README, community files, generated tool
  catalog with CI freshness, Greptile review standards + KB sync pipeline.

[Unreleased]: https://github.com/swcstudiospace/omes-bot/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/swcstudiospace/omes-bot/releases/tag/v0.1.0
