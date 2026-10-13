# Milestones

## v16 v16 (Shipped: 2026-10-13)

**Phases completed:** 6 phases, 12 plans, 6 tasks

**Key accomplishments:**
- Deterministic seat-to-OpenShell policy renderer with generated-file cutover, snapshot golden, sandbox config node, and CI drift guard.
- Fail-closed OpenShell runner routing verification gates

---

## v15 — Integrated Readiness and Repository Delivery (2026-10-12)

**Status:** Complete with one user-accepted gap. 5 phases (75, 76, 77, inserted 77.1, 78), 33 plans. Audit gaps_found: 21/22 requirements satisfied; GROK-03 is partial. Closeout type: override_closeout, approved by the user on 2026-10-12.

**Core value delivered:** One isolated readiness command accounts for all 165 roster tools through production dispatch with real local effects and no Grok credentials (default 50 exercised / 67 blocked / 48 feature-disabled; Prime 77 / 77 / 11; 0 failed). CI keeps the supported verify/lint/types matrices, retains sanitized readiness evidence and validates the image behind a manual publish boundary. Canonical docs are source-linked beside one complete, refreshable historical mirror of all 21 Greptile documents. The pushed repository carries a root `mcp.json`, Cursor plugin manifests, 46 generated `/omega-*` commands and a stamp-gated bootstrap launcher (exit classes 10–13), and the docs lead with a Bot-driven setup prompt. `docs/marketing.md`, an accessible banner and a social card state only verified capabilities, each tagged with its evidence level.

**Verification:** Final tree `2ccb3e8`: pytest 2544 passed. `mypy omega_prime/` exit 0 (345 files). `ruff check` and `ruff format --check` exit 0 (453 files). Evals 26 passed. `setup_check` serves 117 roster tools. `catalog --check`, `plugin --check`, `assemble --check`, `actionlint` and both readiness profiles exit 0. Phase verification: 75 passed 46/46, 76 passed 28/28, 77 passed 34/34, 78 passed 8/8, 77.1 human_needed 42/42 automated. Every phase has `SECURITY.md` (threats_open 0) and a validated, Nyquist-compliant `VALIDATION.md`. The milestone integration check fixed one blocker (stale include-prime counts in `docs/local-validation.md`) and five warnings, including the Bot template's first-run read paths.

**Known gaps:** GROK-03 — the user can push the repository and import it into Grok Bot so the default Bot sets up Omega Prime with the Python core attached. Local proof is verified; the six live import rows (77.1-UAT) are deferred by the user until after merge. Resume with `/gsd:verify-work 77.1`.

**Known verification overrides:** 15 newly acknowledged, 0 carried forward from a prior close (see STATE.md Deferred Items): the 77.1 UAT and verification gap, the archived v9 phase 48 UAT and 12 dormant seeds. Unlike v13 and v14, the seeds and the phase 48 UAT were acknowledged at the user's choice; the marker only suppresses close-out scans and lapses when an artifact changes or a seed is re-triggered.

**Limits:** no live Grok account, Cursor account, model or provider call. No image publication, deployment, tag, marketplace listing or hosted HTTPS host. Tech debt is listed in the audit (GitBook `../` links outside `./docs`, unhashed lock pins, deferred same-day KB rerun, info-level review items).

**Archive:** `milestones/v15-phases/`. `milestones/v15-MILESTONE-AUDIT.md`. Requirements `milestones/v15-REQUIREMENTS.md`. Roadmap `milestones/v15-ROADMAP.md`. Merged to `main` through PR #18 (`c26305f`) and the close-out PR from `v15-milestone-close`.

---

## v14 — Boundary alignment (2026-10-10)

**Status:** Complete. 4/4 phases, 10/10 requirements Done. Audit passed with no exceptions. Closeout type: verified for v14 requirements. Pre-existing dormant seeds and the archived v9 UAT were not acknowledged.

**Core value delivered:** The agent loop and harness are the center. The Grok Bot boundary serves the ported logic. `agent/` and `tools/` do not import `grokbot`. Nested `omega_command` calls re-enter the host roster and interceptor chain. `rlm` and `messaging` register when their flags are on. Cron admin, autolearn, advisor, and `durable_status` are served. GoalStore stays on the goals family.

**Verification:** Final tree: pytest 1992 passed. `python -m mypy omega_prime` exit 0 (327 files). `ruff check` and `ruff format --check` exit 0 (366 files). Evals 26 passed. `setup_check` serves 117 roster tools. All seven Prime flags: served set equals the roster, 154 names. `catalog --check` and `assemble --check` exit 0.

**Limits:** no live Grok account or live model call. Prime families stay off. `durable_status` reads the cron journal, not the desk-parent journal. `VALIDATION.md` and `SECURITY.md` were not generated for phases 71–74. This closeout does not commit or tag. The work is uncommitted on `v14-boundary-alignment`.

**Archive:** `milestones/v14-phases/`. `.planning/v14-MILESTONE-AUDIT.md`. Requirements `milestones/v14-REQUIREMENTS.md`. Roadmap `milestones/v14-ROADMAP.md`.

**Not acknowledged (still visible):** dormant seeds and phase 48 UAT (archived v9). Acknowledging them would hide future work. They are not v14 gaps.

## v13 — Grok Bot specialisation (2026-10-09)

**Status:** Complete. 3/3 phases, 10/10 requirements Done. Audit passed with no exceptions. Closeout type: verified for v13 requirements. Pre-existing dormant seeds and the archived v9 UAT were not acknowledged.

**Core value delivered:** Grok Bot calls Omega Prime on purpose. A user message that is only `/omega-*` runs `omega_command` and does not call the model. Workflows `onboard`, `python-clean`, `connectors`, and `desk` dispatch registry tools in a fixed order. The template names routines `onboard`, `python-clean`, and `connectors`. First run seeds one memory and lists missing connector env names without taking a secret in chat.

**Verification:** Final tree: pytest 1957 passed. `mypy omega_prime` exit 0 (317 files). `ruff check` and `ruff format --check` exit 0 (356 files). Evals 26 passed. `setup_check` serves 110 roster tools. `catalog --check` and `assemble --check` exit 0.

**Limits:** no live Grok account, live model call, or live connector call. Prime families stay off. SEED-010's MCP prompts and resources are not built. `VALIDATION.md` and `SECURITY.md` were not generated for phases 68–70.

**Archive:** `milestones/v13-phases/`. `.planning/v13-MILESTONE-AUDIT.md`. Requirements `milestones/v13-REQUIREMENTS.md`. Roadmap `milestones/v13-ROADMAP.md`.

**Not acknowledged (still visible):** 12 dormant seeds (SEED-001..004, 006..013) and phase 48 UAT (archived v9, 4 pending scenarios). Acknowledging them would hide future work. They are not v13 gaps.

## v12 — Programming Desk merge (2026-10-09)

**Status:** Complete. 4/4 phases, 13/13 requirements Done. Audit passed with no exceptions.

**Core value delivered:** One Omega Prime (`bot-00-omega-prime`) runs the seven-seat Programming Desk. Desk contexts are live, a work root is separate from the install root, `delegate_task` is served, the lead pass runs from the live server, receipts are checked against captured commands, gates can target the work repo, and service clients exist behind env tokens. Source pin: `programming-desk` @ `9de3aa3`.

**Verification:** Final tree: pytest 1942 passed. Phase 66 had first failed one catalog check; the catalog was refreshed before closeout. `mypy omega_prime` exit 0 (312 files). `ruff check` and `ruff format --check` exit 0. Evals 26 passed in Phase 65. `oneclick --dry-run` exit 0. `oneclick --self-test` 22 pass / 0 fail, 109 tools. A loopback SSE session called `lead_roster_status` (seven absorbed seats) and `delegate_task` (`not_configured: provider`).

**Limits:** no live Railway, Greptile, Vercel, Play, or App Store call (no credentials). No live model call for `delegate_task`.

**Archive:** `milestones/v12-phases/`. `.planning/v12-MILESTONE-AUDIT.md`. Requirements `milestones/v12-REQUIREMENTS.md`. Roadmap `milestones/v12-ROADMAP.md`.

## v11 — Grok Bot native (2026-10-09)

**Status:** Complete. 2/2 phases, 14/14 plans, 17/17 requirements Done. Audit passed with no exceptions.

**Core value delivered:** One command attaches Omega Prime to Grok Bot as a hardened, observable, verifiable tool
host. Phase 62 made the shipped runtime correct and secure by default (fail-closed auth and startup, truthful health
and manifest, tamper-evident audit, graceful drain, strict launcher, real-transport tests). Phase 63 added seven
improvements: Streamable HTTP, scoped revocable credentials, an approval gateway, rate limits with a circuit breaker,
metrics with trace context and NDJSON logs, a live conformance verifier, and a deployment kit with a hardened image.

**Verification:** `pytest omega_prime/tests` → exit 0, 1793 passed (three chunks). Evals 26 passed. `assemble-prompts.sh
--check`, `catalog --check`, `setup_check` → exit 0. `ruff`, `ruff format --check` → exit 0. `mypy omega_prime` → exit 0,
294 files. `pyright` → 0 errors. `actionlint` and `docker build --check` clean. `pip-audit -r requirements-lock.txt
--ignore-vuln PYSEC-2026-4114` → no known vulnerabilities. The image was built, run read-only as uid 10001, verified
by the live verifier inside the container, stopped with exit 0 in 1.3 s, and scanned: 0 vulnerabilities.

**Repaired on the way:** an uninstallable `requirements-lock.txt` (merged Dependabot majors) and a non-atomic cron job
store had left `main` red; event-loop blocking tool calls, a token leaking into access logs and scope denials missing
from the audit log were found and fixed.

**Archive:** `milestones/v11-phases/` (moved 2026-10-09 on user confirmation). `.planning/v11-MILESTONE-AUDIT.md`; requirements archived as `milestones/v11-REQUIREMENTS.md`; roadmap section archived as `milestones/v11-ROADMAP.md`.

**Decisions:** Python MCP host instead of the graph's TypeScript layout; static scoped bearer tokens instead of HMAC
webhook ingress; per-principal scopes instead of tenant sandboxes; Dependabot ignores `oauthlib` 4 and
`huggingface-hub` 2 until their dependents relax.

## v10 — Prime merge (2026-10-08)

**Status:** Complete. 8/8 phases, 10/10 plans, 32/32 requirements Done.

**Core value delivered:** One Python agent runs Hermes, Omp, and Prime.
Prime's RLM recursion, continual harness, goals, heartbeats, autonomous
mode, and agent messaging are behavior-ported behind default-off flags.
Typed connectors sit on that port. The pinned Rust workspace stays the
parity oracle. The repo is AGPL-3.0, Copyright (C) 2026 Spectrum Web Co.

**Verification:** `.venv/bin/python -m pytest omega_prime/tests -q` → exit 0, 528 passed.
`.venv/bin/python -m omega_prime.evals.runner omega_prime/evals/cases` → exit 0, 26 passed.
`assemble-prompts.sh --check` → exit 0. `omega_prime.setup_check` → exit 0.
`catalog --check` → exit 0. `ruff check` and `ruff format --check` → exit 0.
`python -m mypy omega_prime` → exit 0, 215 files.
`pip-audit -r requirements-lock.txt --ignore-vuln PYSEC-2026-4114` → exit 0.
`cargo-deny check licenses` → exit 0. Local `cargo test --workspace --locked` with the three ACP skips is not exit 0: 5089 passed, 2 failed, 19 ignored, 3 filtered (`VENDOR.md`). The two failures are ext4 hazards, not pin skips. All 32 requirements satisfied.

**Archive:** `milestones/v10-ROADMAP.md`, `milestones/v10-REQUIREMENTS.md`, `milestones/v10-MILESTONE-AUDIT.md`, `milestones/v10-phases/`.

**Decisions:** Behavior-port into Python (no PyO3 boundary); Prime joins the one loop; AGPL-3.0 relicense; degrade-with-warning; PYSEC-2026-4114 ignored because tweepy pins oauthlib below 4 and the bug is in an authorization-server grant this process does not host.

## v9 — SOTA upgrade (2026-10-08)

**Status:** Complete. 7/7 phases, 12/12 plans, 19/19 requirements Done.

**Core value delivered:** The same one-agent product brought to state-of-the-art
engineering and agent standards: SEC-NET 2.2.0 egress transport hardening and
verified browser sandbox boundaries, Ruff linter/formatter and typechecker
enforced in CI, reproducible dependency floors with committed lockfile and
Python 3.12-3.14 matrix with Discord guard, provider retry backoff with usage
accounting, Responses mode with chat-completions fallback and streamed turns,
PyRIT multi-turn attack campaigns with scorer judging in hermetic isolation,
extracted chunk docs answers, and 26 golden/red-team evals with truthful docs.

**Verification:** `.venv/bin/python -m pytest omega_prime/tests -q` → exit 0, 372 passed.
`.venv/bin/python -m omega_prime.evals.runner omega_prime/evals/cases` → exit 0, 26 passed.
`assemble-prompts.sh --check` → exit 0. `omega_prime.setup_check` → exit 0.
`catalog --check` → exit 0. `pip check` → exit 0. All 19 requirements satisfied.

**Archive:** `milestones/v9-ROADMAP.md`, `milestones/v9-REQUIREMENTS.md`, `milestones/v9-MILESTONE-AUDIT.md`, `milestones/v9-phases/`.

**Decisions:** Full-SOTA scope; remediate SSRF T-46-08/T-46-12 via SEC-NET 2.2.0; verified Python 3.12-3.14 matrix.

## v8 — Public launch (2026-10-03)

**Status:** Complete. 2/2 phases, 2/2 plans, 7/7 requirements Done.

**Core value delivered:** The repo launched in public — branded README with
icon/banner art, full community files, richer GitBook docs with a generated
tool catalog kept fresh by CI, Greptile review standards in-repo, and a KB
sync pipeline (script + scheduled workflow) publishing Greptile's knowledge
base into `kb/`.

**Verification:** `.venv/bin/python -m pytest omega_prime/tests -q` → exit 0, 310 passed. `.venv/bin/python -m omega_prime.evals.runner omega_prime/evals/cases` → exit 0, 23 passed. `assemble-prompts.sh --check` → exit 0. `omega_prime.setup_check` → exit 0. Workflows parse; catalog `--check` green.

**Archive:** `milestones/v8-ROADMAP.md`, `milestones/v8-REQUIREMENTS.md`, `milestones/v8-MILESTONE-AUDIT.md`, `milestones/v8-phases/`.

**Decisions:** connect Greptile now (blocked on org app install — user step); push branch + open PR; GitBook stays the host with verify-only CI.

**Deferred:** Pages deploy, per-module API reference, live KB content, PR merge (user).

**Tech debt / known limits:** prose counts refresh by hand; CODEOWNERS/FUNDING.yml skipped (no confirmed handle/sponsor); KB enrollment needs Greptile contact + org key.

## v7 — Substrate surface (2026-10-03)

**Status:** Complete. 5/5 phases, 5/5 plans, 10/10 requirements Done.

**Core value delivered:** Omega Prime as a first-class substrate surface — a fail-open substrate-mcp client (brief/events/shared memory/docs/graph), episodic retain/recall/reflect on the shared `ultrathink` Hindsight bank with local fallback, brief-on-open + turn/tool/file trail with graph provenance in the loop, five rostered substrate tools with approval-gated graph mutations, and a test-enforced ban on direct GreptimeDB/TimescaleDB/DragonflyDB clients.

**Verification:** `.venv/bin/python -m pytest omega_prime/tests -q` → exit 0, 294 passed. `.venv/bin/python -m omega_prime.evals.runner omega_prime/evals/cases` → exit 0, 23 passed. `assemble-prompts.sh --check` → exit 0. `omega_prime.setup_check` → exit 0. Live probes + audit E2E prove fail-open against the real (degraded) local substrate.

**Archive:** `milestones/v7-ROADMAP.md`, `milestones/v7-REQUIREMENTS.md`, `milestones/v7-MILESTONE-AUDIT.md`, `milestones/v7-phases/`.

**Decisions:** substrate-mediated direction (store lock enforced by test); shared `ultrathink` bank; fakes + opt-in read-only live probes, never in CI.

**Deferred:** signed handoff packets, lease steal/drift, A2A teachables, OTLP (upstream phases); live Hindsight round trip (needs API key).

**Tech debt / known limits:** docs_search returns the raw retrieval payload (no chunk extraction yet); local substrate degraded here so live emit/search degrade; Add-Bot template runs unwired by design (localhost unreachable from xAI).

## v6 — Grok ship (2026-10-03)

**Status:** Complete. 5/5 phases, 5/5 plans, 11/11 requirements Done.

**Core value delivered:** Omega Prime as a Grok Bot Add-Bot product — Omp magic keywords in the loop (differential-clean vs Omp), ultrathink natively integrated (skill + turn routine + 7 CLI bridge tools, zero AGPL vendored), the registry served over MCP stdio with OpenShell/AgentOS host profiles, a polished template + four-step setup flow with a runnable smoke check, MIT license, and a six-page docs set structured for GitBook Git Sync.

**Verification:** `.venv/bin/python -m pytest omega_prime/tests -q` → exit 0, 235 passed. `.venv/bin/python -m omega_prime.evals.runner omega_prime/evals/cases` → exit 0, 21 passed. `assemble-prompts.sh --check` → exit 0. `omega_prime.setup_check` → exit 0.

**Archive:** `milestones/v6-ROADMAP.md`, `milestones/v6-REQUIREMENTS.md`, `milestones/v6-MILESTONE-AUDIT.md`, `milestones/v6-phases/`.

**Decisions:** hybrid runtime (template + optional MCP tool host) from the runtime spike; MIT license with bridge-only ultrathink integration; docs verified claim-by-claim against the codebase.

**Deferred:** Grok marketplace listing (staff-added, no self-serve); GitBook/GitHub Sync connection (dashboard action); Greptile connection for the new repo (dashboard action).

**Tech debt / known limits:** ultrathink max-effort override not ported (no Omega Prime knob); workflowz eval-kernel contract rewritten for delegate batches; delegate excluded from the MCP server (needs a live parent); AgentOS cannot host CPython (actor-calls-host pattern documented).

## v5 — Desk packs (2026-10-03)

**Status:** Complete. 8/8 phases, 9/9 plans, 21/21 requirements Done.

**Core value delivered:** the entire Programming Desk absorbed into Omega Prime as Lead with domain packs — intake/dispatch/consolidate routines plus core/lead tools, systems/web/mobile/infra/quality tool families with platform skills, a real Playwright browser transport and Appium/adb/simctl device seams behind vision review, the 9 app-pack tools with pack load/unload and the 20-tool ceiling, all 24 desk skills, and the seat prompts/templates/roster/ownership/contract versions merged into Omega Prime contracts, guarded by per-pack evals and a receipts E2E.

**Verification:** `.venv/bin/python -m pytest omega_prime/tests -q` → exit 0, 219 passed. `.venv/bin/python -m omega_prime.evals.runner omega_prime/evals/cases` → exit 0, 21 passed. `assemble-prompts.sh --check` → exit 0.

**Archive:** `milestones/v5-ROADMAP.md`, `milestones/v5-REQUIREMENTS.md`.

**Decisions:** one Omega Prime Lead with packs over a 7-seat port, entire-desk scope, interactive device review (user chose all three); sync clients with injected seams and fakes only in tests; Omega Prime `{"error": "code: reason"}` shape; approval on desk write kinds; template Enabled-skills/Routines sections stay empty per contract; pack evals assert approval/policy machinery while pack logic stays in unit tests; receipts E2E runs hermetic local commands only.

**Deferred:** live browser/device runs (Playwright/Appium seams exist, tests use fakes); YAML contract-ack consumers (no YAML parser — JSON only); desk changes/events contracts (outside REM-03 scope).

**Tech debt / known limits:** no live-endpoint verification (fake transports/peers + scripted servers only, as in v1–v4); pack eval cases stub the tools so they assert machinery, not pack logic; ownership merge keeps the single Omega Prime owner with desk patterns recorded.

## v4 — Third-party integrations (2026-10-03)

**Status:** Complete. 5/5 phases, 5/5 plans, 11/11 requirements Done.

**Core value delivered:** the Grok Bot wired to third-party libraries as real pip dependencies — a tweepy X transport behind the existing connector, an official-SDK MCP session client beside the one-shot caller, a PyRIT target with a keyless adversarial battery, an APScheduler backend driving JobStore jobs, and Telegram + Discord connector families in the Phase 17 shape with roster and policy entries.

**Verification:** `.venv/bin/python -m pytest omega_prime/tests -q` → exit 0, 167 passed. `.venv/bin/python -m omega_prime.evals.runner omega_prime/evals/cases` → exit 0, 6 passed. `assemble-prompts.sh --check` → exit 0.

**Archive:** `milestones/v4-ROADMAP.md`, `milestones/v4-REQUIREMENTS.md`.

**Decisions:** pip deps over in-house ports for all six libraries (user chose all-candidates + pip deps); tweepy transport is bearer-only with an injectable client factory; media upload stays raw HTTP behind a fallback; PyRIT memory is in-memory SQLite; scheduler entries are memory-only with the JSON store authoritative and fires serialized; Discord is REST-only with no gateway.

**Deferred:** OAuth1 user-context for live posting; chunked media upload; PyRIT orchestrator-driven multi-turn attacks and scorer-based judging; sweep/nightly wiring to the scheduler service; live inbound listeners for Telegram/Discord.

**Tech debt / known limits:** no live-endpoint verification (fake transports/peers + scripted servers only); discord.py pulls an `audioop` deprecation warning on 3.12; CI install time grows with the six dependencies.

## v3 — Grok Bot (2026-10-03)

**Status:** Complete. 4/4 phases, 4/4 plans, 10/10 requirements Done.

**Core value delivered:** the hardened agent as a working Grok Bot — an X connector family (mentions, posts, threads, media) with approval-gated publishing and brokered credentials, an engagement sweep producing queued drafts with per-mention checkpoints, nightly learning through the curator, and a deterministic eval harness (golden + red-team) with CI.

**Verification:** `python3 -m pytest omega_prime/tests -q` → exit 0, 126 passed. `python3 -m omega_prime.evals.runner omega_prime/evals/cases` → exit 0, 6 passed. `assemble-prompts.sh --check` → exit 0.

**Archive:** `milestones/v3-ROADMAP.md`, `milestones/v3-REQUIREMENTS.md`.

**Decisions:** publishing requires approval, staging does not; sweeps draft but never publish; tool arguments still never logged; CI runs suite + evals + assemble check.

**Deferred:** X search, streaming, likes/follows, chunked upload, OAuth flows; model-judged evals; cron scheduling of sweep/nightly pass.

**Tech debt / known limits:** no live X verification (fake transports only); composer and transcripts are caller-supplied; eval coverage is six cases.

## v2 — Enterprise hardening (2026-10-03)

**Status:** Complete. 4/4 phases, 4/4 plans, 12/12 requirements Done.

**Core value delivered:** the v1 agent, hardened OpenShell/AgentOS-style — a declarative seat policy enforced at dispatch with advisor diffs and a persisted audit log, a credential broker that keeps keys out of transcripts with secret redaction, durable runs (SQLite turn journal, cron history, checkpointed workflows), and a real stdlib HTTP transport with structured trace export. Still one in-process Python agent with one seat.

**Verification:** `python3 -m pytest omega_prime/tests -q` → exit 0, 113 passed. `assemble-prompts.sh --check` → exit 0. v2 stack E2E probe (policy + broker + HTTP + audit + trace + journal in one turn) passes.

**Archive:** `milestones/v2-ROADMAP.md`, `milestones/v2-REQUIREMENTS.md`.

**Decisions:** JSON policy file (stdlib-exact, no YAML parser); tool arguments never logged; shipped network allowlist stays empty (deny by default); socketpair HTTP fixtures (loopback TCP is sandbox-blocked here).

**Deferred:** container/namespace execution drivers; fleet gateway and multi-seat registry; formal policy prover; MCP inspection server.

**Tech debt / known limits:** secret redaction is pattern-based; journal is single-process SQLite; no live-endpoint verification (fake transports + scripted peers only); no streaming, usage accounting, or secret managers beyond the environment.

## v1 — One Omega Prime agent (2026-10-03)

**Status:** Complete. 12/12 phases, 12/12 plans, 49/49 requirements Done.

**Core value delivered:** one Omega Prime agent runs both Hermes and Omp agent logic — loops, subagents, tools, skills, memory, and the rest of each runtime.

**Verification:** `python3 -m pytest omega_prime/tests -q` → exit 0, 94 passed. `bash omega_prime/scripts/assemble-prompts.sh --check` → exit 0. Provider→loop→registry E2E probe passes. No fixture process left running.

**Archive:** `milestones/v1-ROADMAP.md`, `milestones/v1-REQUIREMENTS.md`.

**Decisions:** one Python process with one agent class; Hermes moved and adapted, Omp ported as behavior checked against its TypeScript tests; Temporal deferred to a later durability adapter; product chrome stays out; roster/template match the registry exactly.

**Deferred:** Temporal adapter after cron and delegation; TUI, desktop, gateways, Rust crates, packaging.

**Tech debt / known limits:** recall paths are deterministic substring matches (no embeddings); repair passes are deterministic (no model regeneration); providers tested behind a fake transport only (no live network); learn-memory backend, streaming, retries, OAuth, and usage accounting are out of scope.
