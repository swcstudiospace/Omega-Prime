# Local validation

Run the whole isolated readiness profile on your own machine, with no Grok
account and no live credentials. One command exercises every roster tool —
really calling the local handlers, refusing or blocking the rest with named
reasons — and writes one JSON report. This page is the operator reference
for that command and for reading its report honestly.

The profile proves only this seat: a disposable fixture on loopback. It is
not live Grok attestation, and a green report does not mean providers,
devices, browsers, or remote services work.

## The command

```bash
.venv/bin/python -m omega_prime.grokbot.readiness --root . --json --output readiness-local.json
.venv/bin/python -m omega_prime.grokbot.readiness --root . --json --output readiness-prime.json --include-prime
```

| Flag | Meaning |
| --- | --- |
| `--root DIR` | Source checkout holding the canonical roster. Default: the nearest ancestor of the working directory that holds it. |
| `--json` | Print the validated report object on stdout. Without it, print one human summary line plus one line per failure, problem, and failed check. |
| `--output PATH` | Write the same report bytes to `PATH`. This explicit path is the only intended write outside the disposable layout. Optional. |
| `--include-prime` | Also enable and exercise the safe local Prime lifecycles. Without it, the 37 Prime tools are `feature_disabled`. |

Exit codes: 0 only when the validated report has `ok` true. 1 on any
readiness failure — the child failed, the report failed shape or roster
accounting, the secret gate tripped, or `ok` is false. 2 on usage errors
(no canonical roster under `--root`, an unreadable roster, an empty
roster).

The controller itself never initializes production handlers and never
copies the parent environment. It builds the disposable layout, spawns
exactly one fresh product child that runs the whole profile, and receives
only validated sanitized output. Diagnostics are fixed safe labels, never
raw child content, and the report passes the secret gate before it is
printed or written.

## Isolation

`isolated_layout` exports a vetted safe subset of the source tree into a
private disposable base under the system temp location — never under a
caller-inherited `TMPDIR`. `HOME`, every `XDG_*` directory, `TMPDIR`, the
work, state, audit, and command-log roots, and every known native mutable
root stay under that base. Fixture schedules start empty, so a run never
ticks schedules installed in the source checkout.

Every regular entry, alias, and resolved target passes one admission
predicate (`_source_entry_eligible`): pruned/excluded shape first, then
provenance. Pruned ancestors — excluded directory names, the `state`
runtime directory, and excluded file names including `.planning` — refuse
by component equality, so `omega_prime/state.py` stays admissible while
`omega_prime/state/...` and anything under `.planning/` never re-enter,
including through a symlink: both the alias and its target pass the full
predicate, and escaping or directory symlinks are refused before any byte
copies. Regular `.env` files, credential-named files, keys, database,
state, config, log, and history material are skipped, never copied, even
inside otherwise public directories; the canonical nested seat policy
still ships because the user-config exclusion is scoped to its exact
path.

Fresh safe ordinary product Python stays Git-ungated so newly implemented
code always exports, but private-named product Python is admitted only
with verified Git-tracked or inherited-inventory membership. That is a
deliberate trusted public-code distinction — shipped `secret_guard` /
`tokens`-style modules and public tests survive, while a fresh
credential-named file fails closed and is never promoted into the
inventory. It is not an exemption for credential data or untracked
private Python. Non-Python data, skill-content Python helpers, and Prime
resources fail closed without verified membership: a newly seeded learned
skill arrives untracked, so its `SKILL.md` and helper scripts can never
ship on re-export. Optional Prime directories are filtered per entry —
pinned static files by pin plus safe shape, everything else by
submodule-tracked or inherited-inventory membership — and the export
records every actually vetted resource in
`.omega-readiness-inventory.json` for nested export. Missing Git or a
malformed inventory never vouches: provenance-gated resources fail
closed, and the child reports them as named capability prerequisites.

The child gets a small explicit environment built from required
interpreter, locale, executable, and fresh fixture values — never a copy
of the calling `os.environ`, and never inherited credential, endpoint,
config, executable, or native-state selectors. Its config is a fresh
owned file with the Prime families off. With `--include-prime`, the child
first constructs a genuine OFF runtime, checks Prime-name absence and
direct-call refusal as control-only evidence, then writes all seven
enabled fixture flags and constructs the main ON runtime that the Prime
scenarios use.

## Reading the report

JSON v1: `schema_version` 1, `ok`, `scope` (`isolated-local`), `profile`
(`local-only` or `include-prime`), `checks`, `tools`, `summary`, and
`limitations`. Each tool row carries `name`, `family`, `status`,
`evidence`, and `prerequisites`. Every canonical roster name appears
exactly once — never duplicated, never missing — and family labels reuse
the existing tooling catalog, not a second taxonomy.

| Status | Meaning |
| --- | --- |
| `exercised` | A real local handler ran and its semantic result or effect was verified. Registration, authorization-only outcomes, and swallowed errors are never positive evidence. |
| `feature_disabled` | A capability whose flag is off and runtime absence is verified. Red Team remains disabled in both profiles; Prime tools are disabled only without `--include-prime`. |
| `blocked` | Not runnable here. `prerequisites` names the real gap: a provider key, a device executable, a browser backend, a service token, an unconfigured transport. Never generic `not_tested`, and never success. |
| `failed` | An eligible local handler failed, or roster accounting lapsed. Any failure makes global `ok` false. |

`--include-prime` enables Prime only. The Red Team family stays
default-off behind `redteam.enabled` in both profiles: its eleven
`tempest_*` tools report `feature_disabled`, never `blocked` and never
`exercised`. Enabling Prime does not enable Red Team.

Current owned-fixture runs exit 0 with `ok: true` and all 165 canonical
roster names accounted exactly once. The default profile offers 117
tools: 50 exercised, 67 blocked, and 48 feature-disabled. The
`--include-prime` profile offers 154: 82 exercised, 72 blocked, and 11
feature-disabled. Both report zero failed tools, zero failed checks,
and no accounting problems. The Red Team backend was not called.

The earlier missing Red Team accounting produced eleven failed
`absent-from-offered` rows in both profiles; that failing-before evidence
is retained in the phase audit. The repair derives disabled status from
the actual flag and verified absence, not a blanket exception.
The report's `limitations` remain explicit: no live provider, credential,
device, or browser integration; unconfigured services stay blocked;
approval state is in-memory; model doubles prove local composition only.

If an eligible local handler fails, or a served name is never exercised
and never blocked with a named prerequisite, the run fails with a nonzero
exit — the report names the tool or check, and nothing is rounded up to
green.

## What is actually proven

**Both transports, approvals, and shutdown.** The core scenario starts a
real loopback host and checks the served set against the runtime
manifest, a file write/read roundtrip with exact bytes over SSE and over
Streamable HTTP, and the approval lifecycle over the gated local
`sys_cache` tool on each transport: set denied before grant, targeted
grant, real set-then-get of the exact value, revoke, denial again with
the value unchanged (regranting briefly to read and prove persistence,
then revoking again). Coding file tools are not gated, so their effects
run independently of the approval checks. The audit log is present with
no credential bytes — records keep argument key names and a SHA-256 of
the canonical arguments, with sensitive details redacted before hashing
— and the host exits 0 after SIGTERM. The existing `oneclick --self-test`
conformance smoke (22 pass, 0 fail, 0 skip, shutdown 0) is a subset of
this, not the whole proof. `sys_cache` and approvals are in-memory: a
restart drops them.

**Local effects.** File write/read/patch/edit/search with escape refusal,
TODO mutation with its in-memory boundary, memory-to-lead recall plus a
fresh-process reload proving durability, the skill create/edit/view
lifecycle against reopened bytes, harmless terminal and code execution
with command-log and receipt validation, and reachable cache, quality,
mobile, and pack reads. A real executable or language server is exercised
when safely available (ruff and compileall over the disposable install,
diagnostics from the installed language server); absent ones are named
blocked prerequisites. Effect-free local handlers are called; anything
needing a web or vision transport, an MCP peer, or a device backend stays
blocked with the fixture gap named.

**Workflows and delegation.** `/omega-onboard`, `/omega-desk`,
`/omega-python-clean`, and `/omega-connectors` run for semantic results,
and `doctor.green` is inspected — `ok: true` with a red doctor step is a
failure, not a pass. A warm runtime predates an independent intake
producer, then claims without rebuild; cron create/tick/list/remove runs
in the disposable install with an explicit intake path. Cron and desk
passes run parent-less and through an explicitly labeled `ScriptedModel`
double only — no live provider model is configured. The public
`delegate_task` tool without a provider returns `not_configured:
provider` and stays blocked; composing a `ScriptedModel` directly is
local test wiring, not that public tool.

**Prime lifecycles (`--include-prime` only).** Harness refine with
snapshot rollback verified by fresh reads, goals set/pause/resume/clear
verified through fresh status reads, heartbeat delivery observed inside
a bound runner with bindings dropped on stop, the autonomous controller
with real turn payloads, cells sharing one namespace, `prime_bash` exit
codes, the owned skill listing, and callable probes for the nine linked
crates when the native artifact is available. RLM spawn and session
creation without a child-model provider refuse with `not_configured:
provider`; per-child verbs refuse naming the missing child. Factory
verbs need a genuine `run_child` and real fixture run or spec
identifiers — unknown names report the gap, and the desk model alone
never supplies child wiring. `prime_bash`, `prime_skill_list`,
`prime_goal`, and `prime_autonomous` have local paths that need no child
runner, so no such prerequisite is invented for them.

**Memory and intake boundaries.** Lead-bank recall needs an actual
receipt-backed retain — a receipt citing a never-captured command is
refused, and plain child memory is read fresh, not leaked across banks.
The intake queue (`intake.json`, resolved from the state directory) is
authoritative on disk at every public operation: reads reload the file
first, and mutations run as serialized reload–mutate–atomic-replace
transactions under the instance thread lock plus an exclusive `flock` on
the stable `intake.json.lock` sidecar, which is never unlinked. A corrupt
file raises instead of reading as empty. Stale-lease completion is not
fenced — `ack` is an ordered merge with no expected-claim condition —
and the queue is not globally transactional with any other store. See
[Driving the bot](driving-the-bot.md#intake-queue) for the live-host
shape of the same queue.

**Doctor context.** Slash doctor steps take only explicit caller state:
the keyword-only `doctor_context` mapping with `prompt_sha256` and
`installed_skills` and nothing else. Missing, invalid, or incomplete
context never implies healthy — the step fails, the command fails, and a
workflow that runs on past a failed optional step stays `ok: false` with
`workflow_incomplete` while keeping every step result. The doctor check
preserves the installed expected prompt digest when saving its outcome,
so an independently reopened check does not erase its own prerequisite.
The prepared-seat checks use disposable installed prompt bytes, their
digest, installed skill files, and lead registration made by real
helpers — proof for that disposable seat only, never for a live
installation. No model or environment lookup invents installed caller
state, and the context cannot override actions, registrations, or
approvals.

**Canonical MCP dispatch.** The public `mcp_call` tool runs through the
canonical initializing SDK session client (`mcp_session_call`); the
obsolete uninitialized one-line client is removed. Inside an active event
loop the call moves to a joined non-daemon thread that owns its loop —
never detached, never returned before SDK cleanup completes — with the
call bounded by a deadline and the child tree reaped on timeout. The
child environment keeps the isolated temp, XDG, and bytecode policy and
inherits no providers. Refusals, spawn failures, and timeouts return
`error` only, with private diagnostics kept out of the envelope.

**Secret-guard inputs and diagnostic curation.** `assert_no_secrets`
scans literal `str` inline as text and any `os.PathLike` as a file. File
inputs first enforce the 0600 mode — a group- or world-accessible file
trips `0600-violation` before its bytes are read — while a `str` that
happens to name a file is still treated as literal text and never touches
disk. Token-shaped paths get no exemption: the same literal string is
scanned for secret patterns either way. Error messages carry only the
bundle name and the match kind, never secret bytes.

Published diagnostics are curated, not excerpted. Complete diagnostic text reaches known-minted-secret replacement plus audit redaction and then the unchanged secret gate on that full redacted text BEFORE any flattening or 200-character clip; known-token replacement alone and final-only scanning do not close the leak. The gate recognizes only its documented patterns — audit credential shapes, `omk_` tokens with 8+ suffix chars, PEM private-key armour, and 32+ mixed-class runs at 4.2+ bits/char entropy (hex digests and UUIDs excluded) — not every possible secret. A trip publishes only fixed safe metadata — no tail bytes, no fragment — and the rejected check stays failed with a nonzero exit. The
self-test's bounded diagnostic follows the same order, with the unchanged
gate also re-run on the clipped result as defense in depth, and raw
loopback error slicing is removed: readiness fragments are no longer published, and self-test fragments reach the same full-input gate before any tail/clip window. Prime producers follow the same rule with no extra
scanner or entropy relaxation: complete raw handler and exception text
reaches the shared full-input redact-then-gate before any producer-side
clip, and raw text is never windowed at the producer. Observed on the
real 154-tool run, the `rlm_rename` missing-child `ValueError` still
echoes the full audit-clean scanner candidate at the handler level, but
the published check carries no emitted prefix — the final guard accepts
the safely-withheld check. Final serialized-report gates are retained
downstream and re-verify complete report data including `base_url` and `server_version` metadata, coercing any metadata trip to failed with fixed safe labels. The retained failing-before proof (synthetic exception through the actual verifier `_step` plus the public formatter: full input rejected, fragment in the formatter accepted by the existing guard, report not ok) is kept in the phase audit; the concurrent verifier-report publication repair's runtime pass is parent-verified, not claimed here.

CLI usage failures are gated before stderr too. The verifier and self-test
launcher guard rejected choices, typed values and unknown arguments without
echoing recognized secret candidates. The stdlib-only readiness controller
applies the unchanged full-input secret gate to parser errors and missing-root
diagnostics; a trip publishes fixed safe metadata. Usage errors still exit2,
and benign invalid arguments or missing roots retain helpful guidance.


## What stays unproven and what distributions must carry

Genuine provider, service, device, browser, and Grok prerequisites are
not proven here and must not be read as green: connector credentials,
child-model provider keys, mobile store tokens and device executables
(checked by `PATH` lookup only, never executed), the guarded-browser
session and cgroup, the debug adapter, egress fetches that would leave
the seat, and every approval-gated remote mutation. Fixture schedule and
fixture health checks say nothing about a live installation. A green
local report plus these named gaps is the complete honest result — the
remaining checks are real attachments, tokens, and devices, exercised
where they live.

Do not assume an arbitrary checkout, archive, or container root works.
Git-less Docker and source-archive distributions MUST carry the verified
`.omega-readiness-inventory.json` written by a vetted export: without
trusted provenance, data, skill helpers, and Prime resources fail closed
and the child reports them as named missing prerequisites instead of
running on silently admitted bytes. This isolation is about source
admission and secret handling, not an OS or network sandbox — tools that
run still run as the invoking user. Container provenance is phase 76
work, not a claim made here.

Evidence summaries in reports and docs use synthetic identifiers only:
never raw credentials, host environment, sensitive bodies, or private
paths.
