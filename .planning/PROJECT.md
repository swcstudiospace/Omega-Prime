# Omega Prime

## What This Is

Omega Prime is one Grok programming bot (`swcstudiospace/omes-bot`). It ports the Hermes Agent runtime, the oh-my-pi agent harness, and Prime Agent's differentiating logic into a single Python agent, then exposes that agent the way programming-desk exposes a seat: a prompt that points at a skills folder, a tool roster, and a prompt folder. The upstream checkouts in this directory (`hermes-agent/`, `oh-my-pi/`, `prime-agent/`) and `~/src/repos/programming-desk` and `~/src/repos/claude-ultrathink` are read-only sources. The bot lives under `omega_prime/`.

## Core Value

One Omega Prime agent runs Hermes, Omp, and Prime logic — loops, subagents, tools, skills, memory, and the rest of each runtime — so later tools, connectors, skills, memories, and routines have a real agent to attach to.

v2 milestone: that same agent, hardened the way OpenShell and AgentOS harden theirs — a declarative seat policy enforced at dispatch, a credential broker that keeps secrets out of transcripts, durable runs that resume after a crash, and real transports with structured traces. Still one in-process Python agent with one seat.

v3 milestone: that hardened agent as a working Grok Bot — an X connector tool family with approval-gated publishing, an engagement sweep routine producing queued drafts, nightly learning through the curator, and an eval harness with CI.

v4 milestone: that bot wired to third-party libraries as real pip dependencies — a tweepy X transport, an official-SDK MCP session client, PyRIT red-team depth, an APScheduler scheduler backend, and Telegram/Discord connectors. This reverses the v1–v3 "no new third-party dependencies" rule by explicit user decision (2026-10-03); the Hermes/Omp no-vendor no-import rule still stands.

v5 milestone: the entire Programming Desk absorbed into Omega Prime with Omega Prime as Lead — desk seats become domain packs (skills + tool families + routines + roster entries) in the one agent, and interactive browser/device review lands behind the vision and browser transports. User decisions (2026-10-03): lead+packs over a multi-seat port, entire-desk scope, interactive (not screenshot-only) device review. Ground truth: `.planning/codebase/`.

v6 milestone: Omega Prime as a Grok Bot Add-Bot product — Omp magic keywords in the loop, ultrathink natively integrated (prompt routines + CLI bridge, no AGPL vendoring), an MCP tool host installs can attach, a clean template + setup flow, and docs structured for GitBook Git Sync. User decisions (2026-10-03): hybrid architecture (template + optional tool host), MIT license, full milestone scope. Ground truth: `.planning/research/v6-runtime-spike.md`.

v7 milestone: Omega Prime as a substrate surface — the Grok Bot briefs on open, emits its turn/tool trail with graph provenance, shares memory through substrate-mcp, recalls episodes from the shared `ultrathink` Hindsight bank, and answers docs from RAGflow, while GreptimeDB/TimescaleDB/DragonflyDB stay behind the store lock. User decisions (2026-10-03): substrate-mediated direction, shared bank, fakes + opt-in live probes. Ground truth: `~/src/repos/agent-substrate` governance + grokbot loop docs, Hindsight OpenAPI v0.9.1.

v8 milestone: public launch — branded README, standard public-repo files, richer GitBook docs with a CI-kept tool catalog, Greptile connected with repo review standards, and a KB sync pipeline publishing Greptile's knowledge base into the docs. User decisions (2026-10-03): connect Greptile now, push branch + open PR, GitBook stays the host. Ground truth: Greptile docs corpus (config + KB MCP tools).

v9 milestone: SOTA upgrade — the same one-agent product brought to state-of-the-art engineering and agent standards: in-flight hardening landed, lint + types enforced in CI, pinned dependencies, current provider/protocol wire formats, and a deeper hermetic eval battery. User decisions (2026-10-07): full-SOTA scope, land the dirty tree first, hermetic verification stays (live probes manual opt-in).

v10 milestone: Prime merge — the third agent joins. Prime Agent (`prime-agent/`, PrimeIntellect, MIT, pinned `967eb13f`) contributes its differentiating logic — RLM subagent recursion (`rlm.spawn`/`collect`), the continual harness (`/refine` with snapshots and rollback), goals/heartbeats/autonomous mode, and agent-to-agent messaging — behavior-ported into the one Python agent per the v1–v9 precedent. The Rust workspace stays as an ignored read-only checkout and CI-built parity oracle. The repo relicenses to AGPL-3.0 (Spectrum Web Co 2026) and hardens to enterprise open-source standards. User decisions (2026-10-08): behavior-port architecture (no PyO3 boundary exists upstream; one-Python-process rule stands), capability-merge loop design, AGPL-3.0 relicense, degrade-with-warning for Prime capability failures.

## Current Milestone: v20 Grok Bot launch kit

**Goal:** Ship the public-facing collateral for the Grok Bot distribution: a Grok-Bot-focused README and CHANGELOG, a listing folder with icons and banner, an install prompt, and a set of follow-up prompts. Every claim keeps the existing evidence levels (local proof, optional capability, unproven external). Live Grok Bot import stays UNPROVEN until the user runs the UAT rows.

**Target features:**
- Grok-Bot listing folder with its own README, CHANGELOG and licensing notice
- Icon set and banner exported from the existing brand (`assets/icon.svg`, `assets/banner.svg`)
- Install prompt and follow-up prompts, sourced from the existing setup route
- Root README rewritten for the Grok Bot audience; the developer README is kept under `docs/`

Decisions (2026-10-15, user): new listing files in a new folder and a replaced root README; keep the AGPL-3.0 LICENSE unchanged; draft the standard follow-up prompt set; track as a new milestone. v19 is archived to `.planning/milestones/v19-phases/`.

v19 is complete (phase 96, installer bindings). Phase 77.1's live Grok Bot import UAT (six rows) stays deferred until the user runs it on `main`; resume it with `/gsd:verify-work 77.1`.

## Last Milestone: v16 Enterprise Hardening and Continuous Verification (shipped 2026-10-13)

**Status:** Shipped clean. Audit PASS, 13/13 requirements satisfied (`.planning/milestones/v16-MILESTONE-AUDIT.md`); full suite 2790 passed / 0 failed at close. Phase directories are in `milestones/v16-phases/`; the self-contained run package is `.planning/package/DELIVERABLE.md`. The purple-team sidecar ships BLOCKED pending the user-signed rules-of-engagement artifact; the first protected image publish awaits operator approval.

## Last Milestone: v15 Integrated Readiness and Repository Delivery (shipped 2026-10-12)

**Status:** Shipped with one user-accepted gap. Audit gaps_found, 21/22 requirements satisfied (`.planning/milestones/v15-MILESTONE-AUDIT.md`): GROK-03 is partial because its live Grok Bot / Cursor import UAT is deferred by the user, while its local proof is verified 42/42. Phase directories are in `milestones/v15-phases/`. Work merged to `main` through PR #18 (`c26305f`) and the close-out PR from `v15-milestone-close`.

**Goal:** Prove integrated local tools and workflows without Grok credentials, then deliver truthful documentation, a complete maintained Greptile mirror, safe CI/CD and polished repository marketing.

**Delivered:**
- Phase 75: one isolated readiness command accounts for all 165 roster tools through production dispatch with real local effects and no Grok credentials (default 50 exercised / 67 blocked / 48 feature-disabled; Prime 77 / 77 / 11; 0 failed).
- Phase 76: supported verify/lint/types matrices, retained sanitized readiness evidence, and a validated image with a manual publish boundary.
- Phase 77: source-linked canonical docs and one complete, refreshable historical mirror of all 21 Greptile documents.
- Phase 77.1 (inserted): root `mcp.json` plus Cursor plugin manifests, 46 generated `/omega-*` commands, a stamp-gated bootstrap launcher with exit classes 10–13, and the Bot-driven setup prompt; live import is user UAT.
- Phase 78: `docs/marketing.md` with evidence-tagged claims and a no-credential demo, an accessible banner, and a social card with its inspected raster.

## Prior: v14 Boundary alignment (shipped 2026-10-10)

**Status:** Shipped. Audit passed 10/10 with no exceptions (`.planning/v14-MILESTONE-AUDIT.md`). Phase directories are in `milestones/v14-phases/`. Application changes were merged through PR #17 at `027dc76e9b1bed61b74bd4704f9c45c1e299234e`.

**Goal:** The agent loop and harness core are the center. The Grok Bot boundary makes every ported logic usable (slash commands, nested dispatch, `rlm`, `messaging`, cron, learning, durable), and the core imports nothing from the boundary.

v13 Grok Bot specialisation closed 2026-10-09: audit passed 10/10 (`.planning/v13-MILESTONE-AUDIT.md`). v12 Programming Desk merge closed 2026-10-09: audit passed 13/13 (`.planning/v12-MILESTONE-AUDIT.md`).

## Prior: v11 Grok Bot native (shipped 2026-10-09)

**Status:** Shipped. Audit passed 17/17 with no exceptions (`.planning/v11-MILESTONE-AUDIT.md`). Phase directories are in `milestones/v11-phases/`.

**Goal:** Make the one-command Grok Bot attachment production-grade and enterprise-ready.
Phase 62 finishes the shipped `omega_prime/grokbot/` runtime (fail-closed remote auth,
audited and truthful, graceful lifecycle, strict 1-click launcher, proven over a real
socket). Phase 63 adds seven improvements: Streamable HTTP, scoped credentials, a human
approval gateway, traffic protection, observability, a live conformance verifier, and a
deployment kit. Source plan: the ultrathink graph `ut-mv0nfl17-58362dc4` (Linear
SPE-8895..SPE-8900), translated from its stack-agnostic wording to this Python MCP host.

**Target features:**
- Remote host: constant-time bearer auth, Origin/Host validation, fail-closed policy/roster/token loading, truthful `/healthz` and `/readyz`, graceful drain
- Hash-chained, rotating audit of every tool call and auth failure
- Truthful manifest (served tools, gated tools, config-derived capabilities, digest) with template lint and correct drift sync
- Strict launcher, doctor, emulator and supervisor
- Streamable HTTP at `/mcp`, scoped rotatable tokens, runtime approvals API, rate limits and circuit breaker, Prometheus/trace-context/NDJSON observability
- Live verifier and deployment kit (Docker, compose, systemd, Kubernetes) with image CI

v10 Prime merge closed 2026-10-09: audit passed, 32/32 requirements (30 wired and 2
user-approved exceptions for LOOP-07 and REPO-04), `prime-agent` registered as a git submodule.

## Requirements

### Validated

- ✓ Single-seat Grok shell, Hermes loop, registry, skills, memory, delegation, and the Omp harness — v1
- ✓ In-process policy, credential broker, durable runs, and transports — v2
- ✓ Grok Bot connector, engagement sweep, curator, and eval harness — v3
- ✓ tweepy, MCP SDK, PyRIT, APScheduler, Telegram, and Discord as real dependencies — v4
- ✓ Programming Desk absorbed as Lead plus domain packs — v5
- ✓ Add-Bot product: template, optional MCP host, GitBook docs — v6
- ✓ Substrate surface: briefs, trail, shared Hindsight bank, docs answers — v7
- ✓ Public-launch files, generated tool catalog, Greptile review standards — v8
- ✓ SOTA engineering: egress hardening, lint and types, lockfile, provider protocols, hermetic evals — v9
- ✓ Prime merge: loop wiring, typed boundary, durable RLM, `prime-agent` git submodule; audit passed 32/32 (2 user-approved exceptions) — v10
- ✓ Grok Bot native host: fail-closed remote auth and startup, truthful health and manifest, tamper-evident audit, Streamable HTTP, scoped revocable tokens, approval gateway, rate limits and circuit breaker, metrics and NDJSON logs, live verifier, hardened deployment kit; audit passed 17/17 — v11
- ✓ Programming Desk merge: configured desk, work root, served `delegate_task`, lead pass, receipts, target gates, service clients behind env tokens; audit passed 13/13 — v12
- ✓ Grok Bot specialisation: `/omega-*` via `omega_command`, fixed workflows, named routines, one seeded memory, connector requests that never copy a token; audit passed 10/10 — v13
- ✓ Boundary alignment: slash engine in core, nested host gates, `rlm` and `messaging` served behind flags, cron/learning/durable surfaces; audit passed 10/10 — v14
- ✓ Integrated readiness and repository delivery: isolated local readiness (LOCAL-01..03, FLOW-01..02, GROK-01..02), clean CI/CD (CI-01..03), canonical docs with one complete historical Greptile mirror (KB-01..03, DOC-01..05) and repository marketing (MKT-01..03); verification passed 46/46, 28/28, 34/34 and 8/8; Grok Bot import packaging has verified local proof (GROK-03 local, 42/42); audit 21/22 with the user-accepted GROK-03 gap — v15
- Prime behavior-port baseline and Rust parity-oracle CI are inherited archival evidence. Phase 61 loop wiring, typed boundary (all seven Prime families), durable RLM, config-derived prompt and the pre-v10 transcript comparison are exercised and independently reviewed (full suite 980 passed).
- AGPL repository and current native bridge license/build coverage — verified locally. Cargo Dependabot is a user-approved exception (2026-10-09): the Cargo path dependencies live in the ignored read-only prime-agent checkout; cargo-deny and pip-audit run.

### Active

- GROK-03 live import (deferred by the user to after merge): the six 77.1 UAT rows — Bot-driven setup on the default Grok Bot, Cursor Teams/Enterprise Import from Repo, Bot plugin inheritance, live whole-message `/omega-*` commands, attach timing and Remote HTTPS. Resume with `/gsd:verify-work 77.1`.
- Next milestone: no requirements defined yet (`/gsd:new-milestone`).

### Out of Scope

- Temporal as the way the two agents are merged — one Python process is the merge; Temporal can be a later durability adapter
- A sidecar that calls the Hermes checkout and the Omp checkout — both runtimes are ported, not wrapped
- Product chrome: Hermes TUI, desktop, website, locales, packaging, messaging gateways, Feishu, Yuanbao, Home Assistant, Spotify, kanban UI; Omp TUI, collab web, stats site, CLI gallery, Rust crates, bazel and nix packaging
- Tagging or pushing without an explicit user request (the user directed pushes to `main` with `prime-agent` as a submodule on 2026-10-09; tags stay out)
- New full native SessionEngine, TUI, CLI, daemon or telemetry integration. The existing optional pinned native wrapper is documented by ADR-0002; it is not a new engine in this gap-closure phase.
- Unapproved dependency-source packaging changes or silent Cargo automation exemptions. The ignored checkout stays read-only; REPO-04 must be resolved explicitly.
- Provisioning a Grok Bot account — the template markdown is what a person pastes into Share → Create template
- Seven-seat desk rules (channel size, QUALITY off-channel, cross-seat intake). `ownership.yaml` stays so the tree can grow later

## Context

programming-desk (`/root/src/repos/programming-desk`) is the pattern: shared core directives prepended to one bot XML, the XML pointing at `skills/.../SKILL.md` and `contracts/tool-rosters/<seat>.yaml`, and `grokbot/templates/<SEAT>.md` carrying name, description, enabled skills, and routines with no prompt body and no tokens. Hermes is Python. Omp is TypeScript plus Rust. The agent logic is what this milestone ports. Languages differ, so the host is Python: Hermes is moved and adapted, Omp is a behavior port checked against its TypeScript tests.

Pinned reads (see `VENDOR.md`): Hermes `1a4508e2aff2db5f50409893a2115be777bd5643`, oh-my-pi `0e2411c0df59fce8c56dc03a5f1c9afffcb0d746`. Both MIT.

State after v15 (2026-10-12): the default host serves 117 roster tools (154 with all seven Prime flags), `/omega-help` lists 46 commands (12 curated plus 34 generated), the suite is 2544 tests, and every claim in `docs/marketing.md` carries an evidence level. Accepted tech debt is listed in the v15 audit (GitBook `../` links outside `./docs`, unhashed lock pins, deferred same-day KB rerun, info-level review items).

## Constraints

- **Repo boundary**: This git root is `/root/src/repos/omega-prime`. Do not commit into `/root/src/repos` (`swcstudiospace/repos`). Push to `main` only on the user's explicit direction (given 2026-10-09: push `main` with `prime-agent` as a git submodule); never tag.
- **Runtime**: One Python process. No Node or Rust agent process beside Omega Prime. No new global toolchain.
- **Sources**: Do not vendor, subtree, or runtime-import `hermes-agent/` or `oh-my-pi/`.
- **Verification**: A completion claim needs a command and an exit code (programming-desk PD-1). No secrets (PD-4). No destructive operation without recorded approval (PD-5).
- **Providers**: A missing xAI key does not fail the milestone. The Grok adapter is tested with a fake transport.
- **Done means parity**: A phase is done when its parity checks pass, not when a scaffold exists.

## Environment Facts

- Planning root and code repo: `/root/src/repos/omega-prime`. On 2026-10-12
  `main` is at `c26305f` (PR #18) and the v15 close-out runs on branch
  `v15-milestone-close`. Never commit the `prime-agent/` checkout itself
  (ignored upstream, like `hermes-agent/` and `oh-my-pi/`). Do not push,
  merge, or tag without an explicit user request.
- GSD CLI: `node /root/.agents/gsd-core/bin/gsd-tools.cjs`, run from this repo.
  Existing reports use body-only `**Status:**` fields; the CLI requires leading
  YAML frontmatter. Repair the artifacts, not the installed GSD tools.
- Repo virtualenv: `.venv/bin/python` is Python 3.12.3; `.venv/bin/ruff` is
  0.16.10; `.venv/bin/mypy` is 2.4.0. No install or global toolchain is needed.
- Closeout baseline on 2026-10-12 at `2ccb3e8`: `.venv/bin/python -m pytest
  omega_prime/tests -q` passed 2544 tests. `.venv/bin/python -m
  omega_prime.evals.runner omega_prime/evals/cases` passed 26 evals. Readiness
  (`python -m omega_prime.grokbot.readiness --root . --json`, with and without
  `--include-prime`) exits 0 with zero failed tools.
- Other closeout gates, all exit 0: `bash
  omega_prime/scripts/assemble-prompts.sh --check`, `.venv/bin/ruff check omega_prime/`,
  `.venv/bin/ruff format --check omega_prime/` (453 files), `.venv/bin/python -m mypy omega_prime/`
  (345 source files), `.venv/bin/python -m omega_prime.setup_check --root .`,
  `.venv/bin/python -m omega_prime.tooling.catalog --check`,
  `.venv/bin/python -m omega_prime.tooling.plugin --check` and `actionlint`.
- Setup smoke check serves 117 roster tools over MCP; optional ultrathink and
  substrate connections remain unconfigured. Verification is hermetic;
  unconfigured live services are not evidence of live verification.
- Config retains auto_advance=true, skip_discuss=true, text_mode=true,
  use_worktrees=false, ui_phase=false, ui_review=false, commit_docs=false,
  and git.create_tag=false. Review, Nyquist, and security hooks are active.
  Cleanup/moving phase directories requires separate user confirmation.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| One Python process, one OmegaPrimeAgent | The user wants both agents' logic merged into one bot, not a router in front of two checkouts | — Pending |
| Hermes is move-and-adapt; Omp is a behavior port into that same agent | The loop already exists in Python. Omp's TypeScript tests are the spec for harness behavior | — Pending |
| Temporal is deferred | It was a candidate because the languages differ. It is not required to call the port done | — Pending |
| Milestone discuss is the approved plan | Re-asking the merge would stall a decision that is already made | ✓ Good |
| Product chrome stays out unless a phase's own tests cannot pass without it | The milestone is the agent, not the apps wrapped around it | — Pending |
| Grok shell stays in step with the registry | The roster is the index of tools actually registered. The template lists only skills and routines that exist on disk | — Pending |
| v2 stays in-process and single-seat | Container drivers and a fleet gateway are deferred; policy, broker, durability, and transports land first | — Pending |
| v4 uses real pip dependencies for third-party integrations | User chose pip deps over in-house ports for tweepy, MCP SDK, PyRIT, APScheduler, aiogram, discord.py (2026-10-03) | — Pending |
| v5 absorbs the desk as lead + domain packs | User chose one Omega Prime Lead with packs over a 7-seat port, entire-desk scope, interactive device review (2026-10-03) | ✓ Good |
| v6 ships the Add-Bot product on the hybrid runtime | Spike proved Grok Bot runs on xAI's computer and templates don't carry secrets/MCP; user approved template + optional MCP host (2026-10-03) | ✓ Good |
| v6 integrates ultrathink without vendoring; Omega Prime is MIT | claude-ultrathink is AGPL-3.0; user chose MIT + prompt/bridge integration (2026-10-03) | ✓ Good |
| swcstudiospace/omega-prime is the real repo | User created it 2026-10-03; history pushed there, old PR #2 closed as superseded | ✓ Good |
| v7 integrates substrate-mediated, not direct clients | Store lock + governance: only substrate-mcp touches Greptime/Timescale/Dragonfly; Omega Prime is a surface (user chose 2026-10-03) | ✓ Good |
| v7 shares the `ultrathink` Hindsight bank | One set of episodic beliefs across the ultrathink system; no silos (user chose 2026-10-03) | ✓ Good |
| v7 verifies with fakes + opt-in live probes | Committed tests stay hermetic; read-only Railway probes run manually, never in CI (user chose 2026-10-03) | ✓ Good |
| v8 launches public with Greptile connected | User chose connect-now, push + PR, GitBook + verify CI (2026-10-03); init blocked on org app install (user step), KB enrollment stays a Greptile-contact ask | ✓ Good |
| v9 upgrades to SOTA in place | User chose full-SOTA scope, land dirty tree first, hermetic verification stays (2026-10-07); one-agent architecture and Add-Bot unwired compat unchanged | ✓ Good |
| v10 ports Prime behavior into Python; Rust stays as parity oracle | prime-agent has no PyO3/maturin bindings (spawned-kernel process boundary); one-Python-process rule and the v1–v9 port precedent stand (2026-10-08) | ✓ Good |
| v10 merges Prime as capabilities in the single loop | Hermes+Omp are already one merged loop; Prime joins as a third logic source, not a separate runtime agent (2026-10-08) | ✓ Good |
| v10 relicenses to AGPL-3.0 (Spectrum Web Co 2026) | Explicit user request (2026-10-08); supersedes the v6 MIT decision; ported Prime code keeps MIT attribution | ✓ Good |
| v10 degrades with warning on Prime capability failure | Prime families are default-off flags; Prime-disabled behavior matches pre-v10 exactly (2026-10-08) | ✓ Good |
| v10 ignores PYSEC-2026-4114 instead of forcing oauthlib 4 | tweepy 4.17 pins `oauthlib<4`; the advisory is an authorization-server PKCE timing oracle this process does not host (2026-10-08) | ✓ Good |
| v11 hardens the Python MCP host and adds seven improvements instead of a new bridge | The ultrathink graph assumed a TypeScript gateway; this repo's Grok Bot surface is the MCP tool host in `omega_prime/grokbot/`. Static scoped bearer tokens replace HMAC webhooks; per-principal scopes replace tenant sandboxes (2026-10-09) | — Pending |
| v14 keeps the slash engine in core and serves every flagged family from the default host | Core must not import the boundary. `rlm` reuses the desk parent. `messaging` uses the seat session. Plane tools are always on. Families stay default-off (2026-10-10) | ✓ Good |
| v15 verifies integration before repository delivery | User approved all 21 requirements and phases 75–78; existing real intake and secret-guard test failures remain evidence until corrected and exercised | ✓ Good — readiness accounts for all 165 tools; the milestone integration check caught and fixed one stale doc count and a Bot template path |
| v15 uses native GSD instead of signed AgentSwarm | User explicitly selected native GSD on continuation; preserve independent review/security and parent-run checks, leave `.swarm/` untouched | ✓ Good |
| v15 inserts Phase 77.1 for Grok Bot import packaging (GROK-03) | User approved 2026-10-12 so the pushed repository sets up Omega Prime on the default Bot with generated `/omega-*` commands | ⚠️ Revisit — local proof verified 42/42; live import UAT deferred by the user |
| v15 ships before the live import UAT | User: "ship to main branch and I'll test after" (PR #18), then "continue with our phases and merge once completed the milestones" | — Pending (UAT on `main`) |
| v15 marketing states only verified capabilities | Every claim carries an evidence level; no timings, benchmarks or fixed tool totals; unproven rows stay UNPROVEN | ✓ Good |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-10-12 after v15 milestone (audit 21/22, GROK-03 live import UAT deferred by the user)*
