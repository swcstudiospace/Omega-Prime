## Unreleased
- CI runners (00058, runner image Greptile follow-ups from PR #92) for the Autonomous Event-Driven ADLC Programming Bot:
  - Android SDK packages are fixed archives now, not whatever `sdkmanager` resolves on the day: `platforms;android-36` r2 (`platform-36_r02.zip`), `build-tools;36.0.0` (`build-tools_r36_linux.zip`) and platform-tools 37.0.1 (`platform-tools_r37.0.1-linux.zip`), each sha256-pinned (`sha256sum -c`, fail closed) after cross-checking the sha1 in Google's `repository2-3.xml`. The build asserts each unpacked `source.properties` revision; the self-check asserts them again plus `adb` `Version 37.0.1`. `sdkmanager` only records licence acceptance.
  - `Microsoft.NET.ILLink.Tasks` 10.0.13 (sha512 = NuGet catalog `packageHash`) is shipped in the SDK's offline `library-packs` source, in its own layer; the build fails if the SDK's runtime stops matching the pin. Every `net10.0-android` restore needs it, so jobs and the self-check no longer fetch it from nuget.org.
  - Self-check really builds Android as uid 1500: `dotnet new android` + `dotnet build` (Debug, apk) **offline** (nuget.config clears nuget.org), with NuGet cache, CLI home and output in its scratch dir; asserts a signed apk with a manifest. Missing packs or writes into the read-only SDK trees now fail the image build.
  - Self-check cleanup: on any exit (a failed check, `set -e`, HUP/INT/TERM) the exit trap stops every PostgreSQL/GreptimeDB it started and waits for them (falling back to SIGQUIT/SIGKILL), shuts down the .NET build servers, then deletes scratch. A failed re-run on a live Desktop leaves nothing holding ports or memory. New `--only <sections>` to re-run part of it.
  - New `/usr/local/bin/omega-ci-db` helper for jobs: `start postgres|greptime [--name N] [--no-export]`, `env N`, `stop N|--all`. Free loopback ports per instance (all four GreptimeDB listeners too), data and socket dir under `$RUNNER_TEMP/omega-ci-db/<name>`, settings appended to `$GITHUB_ENV` (`PGHOST`/`PGPORT`/`PGUSER`/`PGDATABASE`/`DATABASE_URL`, `GREPTIME_*_ADDR`/`GREPTIME_HTTP_URL`), port races retried, stop waits and is idempotent. Parallel jobs on one Desktop no longer fight over fixed ports 54321/4000. The self-check starts two of each side by side through it.
  - Docs: `docs/ci-runners.md#databases-in-jobs-omega-ci-db` (why fixed ports clash, the variables, a full workflow example with `if: always()` stop), updated self-check and tool table; `docs/ci-runners-repo-setup.md` migration step; README provenance rows for the new pins.
  - Tests: `test_desktop_db_helper.py` (real scripts with stub database binaries: failed check stops both clusters even when `pg_ctl stop` is broken, failed GreptimeDB check stops both instances, SIGTERM still cleans up, distinct ports across 8 listeners, `$GITHUB_ENV` export/`--no-export`, duplicate/bad names refused, port-race retry) and new static checks in `test_desktop_toolchain.py`.
  - Evidence: `.receipts/bot-00-omega-prime/00058-ci-runner-image-followups.json`.
- CI runners (00056, runner image toolchains) for the Autonomous Event-Driven ADLC Programming Bot. Ove's rule (2026-10-24): every CI job in his repositories runs on the omega-ci pool, never on GitHub-hosted runners, so the image grows instead:
  - `deploy/ci-runner-desktop/Dockerfile` section 3b (base: Ubuntu 24.04.5 noble) adds git 2.43, gh 2.103.1, jq, make, build-essential, pkg-config, libssl-dev, zip/unzip, xz-utils, rsync, age, pipx, python3-venv/pip, OpenJDK 17, PostgreSQL 16 server binaries (no cluster, no service), podman 4.9.3 + podman-compose 1.6.0, gitleaks 8.30.1, trivy 0.76.1, semgrep 1.182.1, bandit 1.9.4, GreptimeDB 0.14.4, .NET SDK 10.0.402 (OmpOS `global.json`) with the `maui-android` workload, Android SDK (cmdline-tools 23.0, API 36, build-tools 36.0.0, platform-tools) and Rust 1.99.1 (rustup 1.29.2, rustfmt, clippy).
  - Read-only hosted tool cache at `/opt/hostedtoolcache` (`RUNNER_TOOL_CACHE` / `AGENT_TOOLSDIRECTORY`) in the `setup-*` actions' layout with `x64.complete` markers: Python 3.10.22 and 3.12.15 (`actions/python-versions` builds), Node 22.23.4 and 24.22.1. `actions/setup-python`, `setup-node` and `setup-dotnet` resolve without root; `oven-sh/setup-bun` installs into the job's `HOME`.
  - Supply chain: apt from a dated `snapshot.ubuntu.com` view (`APT_SNAPSHOT`); every download version-pinned and checked with `sha256sum`/`sha512sum -c` (fail closed); Python CLIs in their own venvs from hash-locked `requirements/*.txt` (`--require-hashes --no-deps --only-binary=:all:`); the Python builds' unpinned `pip install --upgrade pip` is skipped. Provenance table in `deploy/ci-runner-desktop/README.md`.
  - Job environment: new `runner-env`, copied to `/opt/actions-runner/.env`, which `Runner.Listener` loads at startup. That carries the toolchain variables past the agent's allowlisted child environment. The pre-job guard (`jobhook.py`) appends `~/.local/bin` and `~/.cargo/bin` to `GITHUB_PATH` for allowed jobs only, so `pip install` fallbacks to `--user` and `cargo install` work as on hosted runners (best effort; never fails an allowed job).
  - Build-time self-check `toolchain-check.sh`, run as uid 1500 with the job's environment: asserts every pinned version, that shared trees are not job-writable, and really uses the tools (venv, a throwaway PostgreSQL cluster, a GreptimeDB standalone instance answering `SELECT 1`, `podman-compose config`, a compiled Rust crate). New `test_desktop_toolchain.py` and two `test_jobhook.py` tests.
  - Docs: `docs/ci-runners.md#runner-image-toolchain` (tool table, how jobs see tools, limits: no running containers, no macOS, Rust channel names, `dotnet workload install`; disk needs; how to add a tool); D-120-08's "Docker jobs stay on the VPS runner" is superseded in `docs/ci-runners.md`, `docs/ci-runners-repo-setup.md` and the Desktop README.
  - Size: 9.6 GB uncompressed (base 0.7 GB; .NET SDK + `maui-android` alone is 4.0 GB). Built locally with rootless `podman build`; the self-check passed as uid 1500.
  - Not deployed: building and deploying the image on Railway stays an operator step.
  - Evidence: `.receipts/bot-00-omega-prime/00056-ci-runner-image.json`.
- CI runners (00055, runner workdir permissions + pre-job exit backoff) for the Autonomous Event-Driven ADLC Programming Bot:
  - Root cause of smoke run 39999949179: every JIT runner exited 0 within a second with "Permission to read the directory contents is required for <workdir> and each directory up the hierarchy", because the image and `Supervisor.prepare()` made `/var/lib/omega-ci` and its `work/` 0711. Both are now root-owned 0755 (image and `prepare()`); each runner's own workdir stays 0700 and owned by the job identity, and the hook/policy files stay root-owned. Nothing secret lives in the now-listable directories (runner names and the 0644 repository-name policy only).
  - `prepare()` refuses to start the agent (`UnsafeWorkdir`) when the work root or any parent is not listable by the job identity, and the image build runs the real `run.sh` as the job identity (`setpriv`) in the real work root and fails on that error.
  - Scaler: any runner exit before it took a job is a failed launch, whatever the exit code (actions/runner's run-helper exits 0 on terminal errors), so it backs off (15 s doubling, 240 s cap) and the existing circuit opens after 5 in a row (`launch_circuit_open`, now with `queued` and the action taken) instead of hot-looping (285 launches before). A job's `completed` webhook naming a pool runner also counts as healthy, so a lost `in_progress` webhook cannot open the circuit by mistake.
  - Agent: each runner's stdout/stderr go to the agent log (`runner <name>: …`, 200 lines / 64 KiB per runner) and, on a pre-job exit, the tail of its newest `_diag` log (60 lines / 8 KiB, no symlinks followed). Every line is redacted (token shapes, Bearer values, `token=` assignments and any long opaque blob, so a JIT config is never echoed). The pipe is always drained. Output goes through a pipe the agent owns, so `process.wait()` tracks the runner's exit alone: a helper left alive by `run.sh` holding the pipe cannot keep the slot (the drain is bounded by the grace period, then the runner's session is stopped). `_diag` logs are opened non-blocking and must be regular files once opened, so a FIFO swapped in by a job cannot stall cleanup.
  - Prompt: new `ci_runner_scope` rule (Ove, 2026-10-24): every CI/CD job in his repositories runs on the Omega CI pool (`[self-hosted, linux, x64, omega-ci]`), never on GitHub-hosted or side runners; missing capabilities mean extending the runner image or pool. Docker-needing jobs migrate onto the pool using binaries baked into the runner image (task 00056); the `vps-docker` carve-out is removed from the docs and `.github/actionlint.yaml`. Documented in `docs/ci-runners.md#runner-policy-every-job-on-the-pool-2026-10-24`.
  - Docs: troubleshooting entry for this symptom and its cause (`docs/ci-runners.md#troubleshooting`), directory modes and runner logs in `deploy/ci-runner-desktop/README.md`.
  - Tests: `test_workdir_permissions.py` (image and `prepare()` modes, chain check, redaction, bounded relay, diag tail; plus an opt-in test running the real actions/runner directory check as a non-root user via `OMEGA_CI_TEST_RUNNER_ROOT`), and scaler regressions for exit 0 before a job (backoff, then circuit, no loop).
  - Evidence: `.receipts/bot-00-omega-prime/00055-ci-runner-workdir.json`.
- CI runners (00054, org-wide repository wildcard) for the Autonomous Event-Driven ADLC Programming Bot:
  - `OMEGA_CI_REPOS`, `OMEGA_CI_PUBLIC_REPOS` and `OMEGA_CI_AGENT_REPOS` accept the owner wildcard `owner/*` (e.g. `swcstudiospace/*`), matched case-insensitively, so the pool can serve every repository of an organisation, including new ones. As of 2026-10-24, by Ove's decision, the runners serve all `swcstudiospace` repositories.
  - Fail closed: an empty list still means nothing (empty `OMEGA_CI_AGENT_REPOS` refuses every job); a bare `*`, `*/repo`, partial globs (`owner/foo*`) and `owner/**` are rejected at startup with a `SettingsError` naming the variable; a bad entry in the runner's policy file voids the whole policy.
  - One shared matcher: `parse_repo_patterns`, `repo_allowed` and `patterns_cover` in `omega_prime/ci_runners/agent/jobhook.py` (stdlib-only, so the hook still runs as a plain file under `python3 -I`) now back the settings parser, webhook admission (`wants_pool`/`matches`), the fork guard's opt-in check, the scaler and the runner-side hook.
  - The public subset check understands wildcards: `owner/repo` is covered by itself or `owner/*`; `owner/*` only by `owner/*`.
  - Org repositories that are themselves GitHub forks of upstream projects are never admitted by `owner/*` (their workflows come from upstream, and a plain `runs-on: self-hosted` would match the pool). They need their exact `owner/repo` entry. `WorkflowJob` gains `fork` (from `repository.fork`, unknown → `None`, which fails closed), the demand file stores it, and the hook refuses such jobs with `fork_repo_not_listed`. The exact-entry exception is for private forks only: a public fork is refused by the fork guard for every trigger.
  - Wildcard rollout keeps the saved queue: demand rows written before `fork` was persisted are no longer forgotten on the first restart under `owner/*`. Replay refreshes `repository.fork` from the job's workflow run; a non-fork is replayed (public ones still fork-guarded), a fork is dropped, and an unknown flag keeps the row in the demand file without ever launching a runner (`demand_fork_unknown`).
  - Every fork-guard rule is unchanged: fork pull requests, `pull_request_target` and unknown heads are refused, and public repositories run only push, workflow_dispatch and same-repository pull_request.
  - Docs: org-wide setup (runner group All repositories + Allow public repositories, the three `swcstudiospace/*` values, org-level "Require approval for all outside collaborators") in `docs/ci-runners.md`, `docs/ci-runners-repo-setup.md`, `deploy/ci-runner-desktop/README.md`, `deploy/ci-runner-controller/env.example` and the env inventory. Prompt: new `ci_runner_scope` rules in the core directives.
  - End-to-end smoke test: `.github/workflows/omega-ci-smoke.yml` (`workflow_dispatch` only, `contents: read`, one job on `[self-hosted, linux, x64, omega-ci]`, 10-minute timeout, `uname -a` / `whoami` / `echo omega-ci ok`, no checkout or secrets), `.github/actionlint.yaml` declaring the custom runner labels, and `test_smoke_workflow.py` guarding its shape. Documented in `docs/ci-runners.md#end-to-end-smoke-test`.
  - Evidence: `.receipts/bot-00-omega-prime/00054-ci-org-wildcard.json`.
- CI runners (00053, public-repo fork guard) for the Autonomous Event-Driven ADLC Programming Bot:
  - New setting `OMEGA_CI_PUBLIC_REPOS`: an explicit allowlist of public repositories, each also required in a non-empty `OMEGA_CI_REPOS`. Default empty, so public repositories stay refused and private behaviour is unchanged.
  - Fork guard (`omega_prime/ci_runners/forkguard.py`): before a queued job from an opted-in public repository reaches the scaler, the controller fetches its workflow run (`GET /repos/{repo}/actions/runs/{run_id}`). It serves the job only for a same-repository `push`, `workflow_dispatch` or `pull_request` whose `head_repository` is not a fork. `pull_request_target`, other triggers, forks, 404s, lookup errors and unexpected shapes are all refused. Definite verdicts are cached per run (one lookup per workflow run); lookup errors are not cached. Trust is never persisted, so restart replay re-verifies saved public jobs.
  - Each refusal emits `omega.ci.runner.refused` with `job_id`, `run_id`, `repo` and `reason`.
  - `WorkflowJob` gains `public` (explicit `private: false` plus `visibility: public`) and `trusted` (set only by the guard). `matches()` takes `public_repos`; the demand file stores `public`.
  - Docs: public-repo opt-in, its risk and GitHub's "Require approval for all outside collaborators" fork setting (`docs/ci-runners.md`, `docs/ci-runners-repo-setup.md`, `env.example`).
  - Deploy lessons from the first real deploy: Railway Root Directory must stay blank and `RAILWAY_DOCKERFILE_PATH=deploy/ci-runner-desktop/Dockerfile` must be set (Railway otherwise built the root MCP-server Dockerfile, which failed on `/run/secrets/omega_prime_token`); `OMEGA_CI_CONTROLLER_URL` is required for the agent to start; the VPS uses nginx on port 8798 (new `deploy/ci-runner-controller/nginx-site.conf.example`); a client secret is not the App private key.
  - Greptile round 1 on PR #89, root-cause fixes:
    - Runner-side pre-job guard. `deploy/ci-runner-desktop/job-started.sh` (logic in `omega_prime/ci_runners/agent/jobhook.py`) is set by the agent as every runner's `ACTIONS_RUNNER_HOOK_JOB_STARTED`. Before any workflow step, it fails jobs from repositories outside `OMEGA_CI_AGENT_REPOS`, `pull_request_target`, pull requests whose head is a fork or another repository, and, for non-private repositories, events other than push, workflow_dispatch and pull_request. Missing or unreadable data fails closed. The allowlist comes from a root-owned policy file the agent writes at startup, and the hook runs `/usr/bin/python3 -I` with absolute paths. This closes the gap where a refused job could still be assigned to a JIT runner through the shared group and labels.
    - State only moves forward. A `queued` event for a job already seen `in_progress` or `completed`, including one that finishes during its run lookup, is ignored, so no stale demand keeps runners awake.
    - Accepted public jobs are written to the demand file before their run lookup and re-verified on replay, so a restart mid-lookup no longer loses them. A refused job is removed from the file.
    - New agent settings: `OMEGA_CI_AGENT_REPOS` (required for jobs to run on the image), `OMEGA_CI_AGENT_JOB_HOOK` and `OMEGA_CI_AGENT_JOB_POLICY` (both set by the image).
    - `head_repository.fork` must be an explicit `false`.
  - Greptile round 2 on PR #89, root-cause fixes:
    - `OMEGA_CI_AGENT_JOB_POLICY` accepts only `/var/lib/omega-ci/job-policy.json`. Before, a custom path made the agent write the allowlist where the shipped `job-started.sh` never looks, so every job failed. A runner hook takes no arguments and the job's environment is not agent-controlled, so the path stays hard-coded in the hook and any other value is now a `SettingsError` at agent startup.
    - A 202 now means the job is on disk. Every accepted `queued` pool job (public or private) is written to the demand file before the delivery is committed. Before, a job acknowledged while startup was still reconciling, or still waiting in the worker queue, was lost on restart. During startup the write keeps the saved demand that has not been replayed yet. If the write fails, the webhook gets a 503 and is not committed (`demand_persist` error event) instead of a 202.
  - Evidence: `.receipts/bot-00-omega-prime/00053-ci-public-fork-guard.json`.
- Event stream (Phase 105, PR #86 fourth review round) for the Autonomous Event-Driven ADLC Programming Bot, with a regression test per finding:
  - Producer names: the broker-name mapping now gives every (logical name, encryption mode) pair its own name. Before, encrypted `worker` and plain `worker-encrypted` both became `worker-encrypted`. Encrypted names end in `-encrypted`. Plain names that already end in `-encrypted` or `-plain` get `-plain` appended. The rule is the same in Python and TypeScript. Tests: `test_pulsar_adapter.py::test_native_producer_names_are_injective_over_name_and_mode`, `::test_encrypted_and_suffixed_plain_publishers_do_not_collide`; `pulsar.test.ts` "maps every (logical name, encryption mode) pair to a distinct broker producer name".
  - TypeScript failure reasons are bounded by Unicode code points (Python `str[:256]` parity). An emoji at the boundary can no longer become a lone surrogate that Python cannot encode. Test: `errors.test.ts` "bounds reasons by code points without splitting astral characters".
  - TypeScript authentication health is tracked separately for send and receive. A successful encrypted send clears only send-side failures, and only a decoded auth delivery clears receive-side failures. A working public key therefore cannot report a bus with a broken private key as healthy. Test: `pulsar.test.ts` "does not let a successful send hide broken auth reads".
  - Each new test was also run against the unfixed sources from `a986841`, where it failed: 2 Python adapter cases and 3 TypeScript cases.
  - Evidence (2026-10-23 AEDT, `gsd/00046-event-stream` working tree = `a986841` + these fixes, Python 3.13.5, Node 20.19.2):
    - `python -m pytest omega_prime/tests/eventstream -q` → exit 0 (370 passed); `python -m pytest omega_prime/tests/test_docs.py omega_prime/tests/test_public_repo.py -q` → exit 0 (33 passed).
    - `ruff check omega_prime/` → exit 0; `ruff format --check omega_prime/` → exit 0; `mypy omega_prime/` → exit 0 (640 source files).
    - `python -m omega_prime.eventstream.native_schema_contract --check` → exit 0; `bash omega_prime/scripts/assemble-prompts.sh --check` → exit 0.
    - In `packages/eventstream-ts`: `npx vitest run` → exit 0 (73 passed); `npx tsc --noEmit` → exit 0; `npx eslint .` → exit 0.
    - Not run locally: the full `omega_prime/tests` suite and the live `eventstream-it` broker lane. CI on the pushed head covers them. CI at `a986841` (the third round) passed every job, including `eventstream-it`.
- Event stream (Phase 105, PR #86 third review round) for the Autonomous Event-Driven ADLC Programming Bot, fixed at root cause with a regression test per finding:
  - Producer names: encrypting producers register on the broker as `<name>-encrypted` (`nativeProducerName()` in TypeScript, `native_producer_name()` in Python). Retry/DLQ topics carry both auth-origin (encrypted) and plain traffic, and Pulsar rejects a second live producer with the same name on one topic. Tests: `pulsar.test.ts` "gives encrypted and plain producers on one retry/DLQ topic distinct broker names"; `test_pulsar_adapter.py::test_mixed_encryption_producers_get_distinct_broker_names`.
  - Dead letters: retry-only routing metadata (`omega_retry_target`, `omega_retry_previous`) is no longer copied onto dead letters in either SDK, and `DlqTriage` ignores it. A delivery dead-lettered for `omega_retry_previous="nan"` now re-emits its blocked signal instead of failing triage with `InvalidRetryMetadata`. Tests: `test_retry.py::test_dead_letter_for_rejected_retry_metadata_survives_triage`, `test_retry.py::test_dlq_triage_settles_legacy_dead_letter_with_rejected_retry_delay`; `retry.test.ts` "drops retry-only metadata such as a rejected omega_retry_previous from dead letters".
  - PR-state projector: source positions are `(partition, ledger, entry, batch index)`. Before this, two records for one PR in the same Pulsar batch shared ledger and entry ids, and the second was dropped as stale. A non-batched batch index of `-1` maps to `0`, and three-element positions already persisted are read as batch index `0`. Tests: `test_pr_state_projector.py::test_process_keeps_every_record_of_one_batched_entry`, `test_pr_state_projector.py::test_reducer_reads_legacy_three_element_positions_as_batch_zero`.
  - TypeScript failure reasons: `safeFailureReason()` trims the redacted message, falls back to `"unspecified failure"` and bounds it to 256 characters, matching Python `_safe_reason()`. It is used for retry, dead-letter and poison reasons, so `new Error()` or `PermanentError("")` can no longer make `CloudEvent` construction throw and strand the delivery. Test: `retry.test.ts` "dead-letters failures with empty messages instead of stranding the delivery".
  - TypeScript health: an encrypted publish, including an auth-origin poison dead letter, that succeeds after key files are repaired marks the bus healthy again, so a publisher-only bus can recover readiness. Test: `pulsar.test.ts` "restores authentication health after a protected publish succeeds again".
  - Each new test was also run against the unfixed sources from `6eafd92`, where it failed: 5 Python retry cases, 1 Python adapter case, 2 new projector cases (plus the 2 updated position assertions) and 4 TypeScript cases.
  - Evidence (2026-10-23 AEDT, `gsd/00046-event-stream` working tree = `6eafd92` + these fixes, Python 3.13.5, Node 20.19.2):
    - `python -m pytest omega_prime/tests/eventstream -q` → exit 0 (368 passed); `python -m pytest omega_prime/tests/test_docs.py omega_prime/tests/test_public_repo.py -q` → exit 0 (33 passed).
    - `ruff check omega_prime/` → exit 0; `ruff format --check omega_prime/` → exit 0; `mypy omega_prime/` → exit 0 (640 source files).
    - `python -m omega_prime.eventstream.native_schema_contract --check` → exit 0; `bash omega_prime/scripts/assemble-prompts.sh --check` → exit 0.
    - In `packages/eventstream-ts`: `npx vitest run` → exit 0 (69 passed); `npx tsc --noEmit` → exit 0; `npx eslint .` → exit 0.
    - Not run locally: the full `omega_prime/tests` suite and the live `eventstream-it` broker lane. CI on the pushed head covers them.
- Event stream (Phase 105, PR #86 second review round) for the Autonomous Event-Driven ADLC Programming Bot, fixed at root cause with behavioural regression tests:
  - Consumer: a busy idempotency claim (another live owner) is retried with capped backoff (50 ms doubling to 2 s) while the consumer is open, instead of stranding the delivery unsettled until reconnect; on stop it is still left unsettled for redelivery.
  - Retry metadata: `omega_retry_attempt` / `omega_retry_previous` are validated once (`parse_retry_attempt`, integer 1–5; finite positive previous delay). Malformed values raise `InvalidRetryMetadata` (a `SchemaError`) and the delivery is dead-lettered as schema poison before any idempotency claim, instead of crashing the worker task group.
  - Shutdown: the hard deadline keeps running through the graceful drain, so a handler that hangs after `stop()` is still cancelled and the run reports unclean.
  - Singleton retry handoff: producer names include the logical target (`omega-eventstream-handoff-<target>-<event id>`), so handoffs of one event to two subscriptions are no longer deduplicated against each other by Pulsar.
  - PR-state projector: non-partitioned topics (partition `-1`) are normalised to partition `0`, so ordering positions validate.
  - Outbox relay (Python): after a failed pass the relay backs off (100 ms doubling to 30 s) before honouring a wake. The failing send itself signals the wake, which previously made `serve()` spin.
  - TypeScript SDK: dead letters are `omega.control.signal.blocked` CloudEvents built like the Python runtime's (`buildDeadLetterRequest`, `pr`/`dlq` payload, matching key, id and properties, `omega_failure_history` plus legacy `omega_retry_history`). Auth-family payloads use native Pulsar end-to-end encryption (`publicKeyPath` / `privateKeyPath`, key name `omega-auth`, crypto failure `FAIL`), so it interoperates with Python. The application-level `PayloadCrypto` hook and `omega_encrypted` marker are removed, and `OMEGA_EVENTSTREAM_PRIVATE_KEY_FILE` is added. `relayOnce` iterates a snapshot so `cleanupSent()` during an awaited publish cannot skip rows. Redaction covers fine-grained `github_pat_` tokens.
  - Tests: two async tests whose coroutine was never awaited now run their scenarios.
  - Not covered: no live cross-SDK broker run of TypeScript-to-Python encrypted delivery. The TypeScript side is verified against the native client option contract with mocks.
  - Evidence: see the PR #86 third review round entry above. Its commands ran on a tree that contains every change in this round (`6eafd92`) with the third-round fixes on top.
- Event stream (Phase 105, PR #86 review fixes) for the Autonomous Event-Driven ADLC Programming Bot: undecodable deliveries are quarantined as schema-valid `omega.control.signal.blocked` (kind `dlq`) records that keep the original origin topic and stay encrypted for auth-origin traffic; native schema rejection surfaces as `SchemaError` at producer creation as well as at send; per-event retry/DLQ-triage producers are single-use and released after the send; the SQLite idempotency ledger stamps each claim with a worker incarnation that holds a `flock` on `<db>.owners/<incarnation>.lock`, so a restarted worker recovers claims left by a crashed process while live owners stay exclusive (an owner that resumes its token after its previous incarnation died moves the claim to its live incarnation; while the original incarnation still holds its lock, a same-token store is recorded as a co-holder in `eventstream_idempotency_holders` instead of replacing it, and a different owner may take over only once the stamped incarnation and every co-holder are dead; legacy rows still need explicit `reclaim`); dead-lettered deliveries count in consume metrics with their handler latency; the compacted PR-state projector keeps reduced results for other heads (early arrivals, the head it just left, and late results for earlier commits) in at most 8 buckets so a force-push back to an earlier commit restores them; Pulsar Function archives ship their full static import closure; and the TypeScript SDK reads native schemas from `packages/eventstream-ts/src/native-schemas.generated.ts`, rendered from Python by `python -m omega_prime.eventstream.native_schema_contract --write` (`--check` guards drift). Evidence (2026-10-23, `gsd/00046-event-stream`): `python -m pytest omega_prime/tests/eventstream -q` exit 0 (330 passed); `python -m pytest omega_prime/tests -q` exit 1 with 6039 passed, 21 skipped and 17 failed, all 17 in `prime_kernel`/oneclick tests that need the native prime-agent build (the same set the Command Center Phase 105 entry records as failing on the unchanged base; no eventstream test is among them); `ruff check omega_prime/`, `ruff format --check omega_prime/` and `mypy omega_prime/` exit 0; `python -m omega_prime.eventstream.native_schema_contract --check` exit 0; `bash omega_prime/scripts/assemble-prompts.sh --check` exit 0; in `packages/eventstream-ts`, `npx vitest run` exit 0 (52 passed), `npx tsc --noEmit` exit 0 and `npx eslint .` exit 0.
- Event stream (Phase 105, PR #86 `eventstream-it` lane) for the Autonomous Event-Driven ADLC Programming Bot. The root cause of the failure was that the helper ran `docker exec mkdir -p /pulsar/ci-secrets` as the image's non-root `pulsar` user (uid 10000) inside the root-owned `/pulsar`, so it was denied.
  - Staging: setup now runs as root, ownership goes to the runtime uid, and readability is checked as that uid.
  - Diagnostics: failed docker steps report the redacted argv and bounded stderr.
  - Config: the repo's sparse `standalone.conf`/`bookkeeper.conf` are merged key-by-key into the image's stock files, with no duplicate keys.
  - Token params: `token:file:///` is replaced with `file:///` in the broker and Functions-worker configs.
  - Broker process: identity matches the standalone JVM argv, and output goes to `/pulsar/logs/omega-ci-standalone.out`. Effective settings are checked through `/brokers/configuration/runtime` plus the bookie start line (`journalSyncData=true`).
  - Certificates: CI certs carry the CA and server extensions that Python 3.13 strict X.509 needs.
  - Client config: `client.env` points `OMEGA_EVENTSTREAM_ENCRYPTION_KEY_FILE` at the public key.
  - Admin calls now follow Pulsar 5.0 REST: deduplication is a bare boolean, `schemaCompatibilityStrategy` uses PUT, inherited namespace grants count in topic-permission drift checks, and `status()` reads the broker id list.
  - `reconcile(functions=False)` scopes a broker-only reconcile. A 409 from a broker without a Functions worker is reported as such, and the secure-deploy refusal is asserted.
  - Evidence (re-run 2026-10-23 AEDT on `gsd/00046-event-stream` after merging `main` 6b82efb and the live-claim fix, Python 3.13 venv):
    - `python -m pytest omega_prime/tests/eventstream -q` → exit 0 (343 passed).
    - `python -m pytest omega_prime/tests -q` → exit 1 (6364 passed, 22 skipped, 17 failed); all 17 are in `test_prime_kernel_{boundary,cells,host,skills}.py` and `test_grokbot_oneclick.py`, which need the native prime-agent build; no eventstream test failed.
    - `ruff check omega_prime/` → exit 0; `ruff format --check omega_prime/` → exit 0; `mypy omega_prime/` → exit 0.
    - Owned-secure lane, locally on rootless podman (a `docker` → `podman` shim normalises `inspect` output to Docker's shape; `wait` and pytest run inside the service container's network namespace via `podman unshare nsenter --net=…` because podman does not publish to the runner's loopback the way the Actions service does): `python omega_prime/scripts/eventstream_it.py prepare --container-id <id> --mode owned-secure --ownership-file <file>` → exit 0; `… start …` → exit 0; `… wait …` → exit 0; `OMEGA_EVENTSTREAM_IT_MODE=owned-secure python -m pytest deploy/pulsar/integration/test_pulsar_integration.py -m eventstream_integration -q` → exit 0 (14 passed); `… stop …` → exit 0.
    - On GitHub, the `eventstream-it` job concluded `success` in Actions runs 39905249075 and 39905253857 at head b83dbec (read back with `gh api repos/swcstudiospace/Omega-Prime/actions/runs/<run>/jobs`, exit 0). The merged head has its own CI run on push.
- Solo mode is the default for bot-00; Desk docs, bus, and notifications remain opt-in, with skill-link checks covering package documentation. Evidence: `.receipts/bot-00-omega-prime/phase-99.json` — focused solo/Desk/prompt/link tests `pytest omega_prime/tests/test_lead.py omega_prime/tests/test_desk_wiring.py omega_prime/tests/test_desk_lead.py omega_prime/tests/test_docs.py omega_prime/tests/test_public_repo.py omega_prime/tests/test_shell.py omega_prime/tests/test_prompt_format.py omega_prime/tests/test_command_generation.py -q` exit 0; `bash omega_prime/scripts/assemble-prompts.sh --check` exit 0.

- Omega Prime Command Center (Phase 105): the loopback-only dashboard now serves a self-hosted React interface with Overview, Tasks, CI/CD and Repositories views, durable server-issued task numbers, bounded cached GitHub evidence, local licensed fonts, and a retained `/legacy` route. The built assets remain static; GitHub parity, visual browser review, signed sandbox evidence and real-data screenshots are still unverified. Evidence (2026-10-23, `feat/command-center-polish`): `python -m pytest omega_prime/tests -q` exit 1 with 5691 passed, 21 skipped and 18 failed, all outside the dashboard and all reproduced on the unchanged base (17 `prime_kernel`/oneclick tests need the native prime-agent build; `test_docs` walks a local `frontend/node_modules`); dashboard subset `python -m pytest omega_prime/tests -k "dashboard or model or task" -q` exit 0; `npx vitest run` (Node 22) exit 0, 70 passed and 1 skipped; `npm run build` exit 0; `ruff check omega_prime/` and `ruff format --check omega_prime/` exit 0.
- Omega Prime Command Center (Phase 106): bounded recent-merge collection, backend-first repository/branch cards, validated merged-PR Greptile evidence, hash-addressable buckets and task detail, separate Idle sessions, and a bounded redacted read-only task terminal. Live GitHub/Herdr parity, responsive host screenshots and host security checks remain pending. Same evidence as the Phase 105 entry.
- prime-agent pin `967eb13` -> `72f790b` (submodule gitlink, `prime-agent.pin.json`, native bridge `Cargo.lock`). `rust-parity` was failing `imported_session_compacts` on most runs. The test read whichever `grown-import*` entry `readdir` returned first, and that was often the window-cache sidecar. Upstream #3678 restricts it to `.jsonl`; no test is skipped. The native bridge passes the new `images` argument of `pa_core::goals::create_goal_context_message` as empty, which keeps the same plain-text goal message. `deny.toml` adds `CC0-1.0` to mirror upstream's allowlist, for `notify` (via `pa-tui`).
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
- Engine review fixes preserve paid token usage, discard terminal duplicate UI answers, isolate skill-loader fixtures, reject unsafe control-store child directories, and retain attachment/cleanup events in `omega ui status`.
- Settings provenance follows the candidate repository, setup diagnostics share the loaded environment with Ultrathink, and malformed non-empty Substrate SSE frames fail as protocol errors.

# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/spec/v2.0.html).

## [Unreleased]

- Phase 102-03 tool contracts: `DeskDriver` owns desk/watchdog/pin/purple schedules; prompt jobs dispatch through an available delegate parent or write blocked receipts, and stale cron jobs are receipted missed. Retain the approval-gated registry-local `exec_job_*` tools, with plan-mode start/kill blocked and argv cwd fixed to the approved work root. Pin apply requires an exact human `pin_bump_apply` grant via the existing admin grant/revoke route. Plugins load only for exact `OMEGA_PRIME_PLUGINS=1`; they are trusted, unsandboxed local Python and can replace handlers/approval metadata. Documented default-off family exceptions; X remains urllib transport without posting behavior changes.
- Phase 102-05 diagnostics: doctor reports exact Prime-family availability, separate Red Team authorization metadata, and pending schedule kinds/counts/overdue status without ticking or dispatching. Standalone runner status is reported as “not observed by this diagnostic,” not absent. Adds the generated production-Python environment inventory and its CI drift check.
- Scheduler lateness: each queued prompt, desk, pin-bump, purple, and watchdog job is checked against a fresh clock reading (tick start plus elapsed whole seconds), so a slow earlier job cannot let a later one act past the grace. An overdue RoE watchdog still reads its grant (history entry marked `late`) instead of keeping a stale state for a full interval. Exec jobs get an empty stdin so they cannot read the stdio host's requests.
- Review fixes: reclaim abandoned scheduler claims and validate settings before claims; create heartbeat metadata atomically; isolate scheduled pin-bump output; drain exec-job pipes continuously and clean up remaining jobs on shutdown errors; align doctor diagnostics with supplied environments, grants, and runtime bindings; inventory copied environment snapshots.

### Agent data folder

- The agent's memory, session history and desk stores live in one folder,
  `<state>/agent` (`OMEGA_PRIME_AGENT_STATE_DIR` overrides it). The host MCP
  server and the sandboxed engine MCP child share it; the sandbox binds only
  that folder writable and hides `settings.json`, `secrets/` and
  `dashboard/`. A one-time, non-overwriting migration moves older data in,
  and unmoved stores are still read in place
  (`python -m omega_prime.agent_state --dry-run`).
- The migration never replaces a file: each move is published with a
  no-replace link (a copy to a scratch name first across filesystems), a
  database moves together with its `-wal`/`-shm`/`-journal` sidecars,
  `MEMORY.md` and `USER.md` move as one unit, and a failed move is undone.
  Learned skills on a read-only install use `<agent>/skills`. Credential
  files under the hidden `secrets/` folder are read on the host and passed
  to the engine by environment, and scheduled desk passes open the same
  intake file as the host's lead tools.

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
