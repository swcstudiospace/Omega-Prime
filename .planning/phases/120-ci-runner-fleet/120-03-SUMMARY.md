---
phase: 120-ci-runner-fleet
plan: "03"
subsystem: infra
tags: [github-actions, self-hosted-runners, railway, asgi, jit-runners, supply-chain]
requires:
  - phase: 120-02
    provides: CLI `python -m omega_prime.ci_runners agent`, desktop wake client reservation contract, POST /agent/events callback endpoint
provides:
  - plain-ASGI runner agent on :8787 (GET /healthz, GET/POST /v1/runners, DELETE /v1/runners/{name}, POST /v1/drain) with constant-time bearer auth and a streamed 1 MiB body cap
  - Supervisor: atomic slot reservation before spawn (429 when full, default 8, hard ceiling 10), 201 first / 200 same-generation replay / 409 conflict or draining, one run.sh child per reservation
  - JIT only as ACTIONS_RUNNER_INPUT_JITCONFIG in an allowlisted child env (never argv, disk, logs, responses, repr)
  - dedicated non-root job UID/GID, nice 10 + RLIMIT_NPROC/NOFILE, private per-runner workdir hard-linking a read-only root-owned runner install, SIGTERM→SIGKILL bounded process-group teardown
  - process.wait-driven cleanup then exactly one authenticated exit callback (no timers, polling or retry loop)
  - deploy/ci-runner-desktop overlay (Dockerfile, wrapper entrypoint, railway.toml, README) — NOT deployed, cutover HELD (D-120-10)
affects: [120-04]
actuals:
  tasks: 3
  commits: 1
tech-stack:
  added: []
  patterns: ["preexec_fn with injectable syscalls so limits are checked in-process", "wrapper starts agent then execs upstream CMD unchanged"]
key-files:
  created:
    - omega_prime/ci_runners/agent/{__init__,app,supervisor}.py
    - omega_prime/tests/ci_runners/{_runner,test_agent,test_supervisor,test_desktop_entrypoint}.py
    - deploy/ci-runner-desktop/{Dockerfile,entrypoint.sh,railway.toml,README.md}
  modified:
    - omega_prime/ci_runners/scaler.py (coverage pragma removed; closed-union branch now tested)
    - omega_prime/tests/ci_runners/{_support,test_scaler}.py
key-decisions:
  - "Executed inline as the Desk Lead executor subagent (gsd-runtime rule 5b); resumed from staged-but-uncommitted work after an interruption."
  - "Image work root is /var/lib/omega-ci/work, not the /config/ci-runners settings default: upstream's entrypoint runs `chown -R headless:headless /config` on every boot."
  - "Drain stops new admissions (409) and lets in-flight jobs finish; DELETE is the per-runner hard stop."
  - "Every `# pragma: no cover` in ci_runners code and tests removed (6, incl. one inherited from 120-01); the lines are now exercised by real tests or left honestly uncovered in test files."
  - "Recommend upstream `SERVERLESS_FRIENDLY=1` so Chromium housekeeping traffic doesn't keep the idle Desktop awake."
requirements-completed: [D-120-02, D-120-03, D-120-04, D-120-05, D-120-06, D-120-08, D-120-10]
---

# 120-03 Summary — Desktop runner agent + immutable overlay

## Verification (host, 2026-10-23 AEDT)

- `python -m pytest omega_prime/tests/ci_runners -q` → 226 passed, 1 skipped (root-only UID-denial test; runs inside the image / a root CI)
- `ruff check omega_prime/` → all checks passed; `ruff format --check omega_prime/` → 710 files formatted
- `mypy omega_prime/` → no issues in 583 source files
- docs job locally: test_docs + test_public_repo 33 passed; catalog/plugin/prompt_format/env_inventory `--check` exit 0
- `bash -n deploy/ci-runner-desktop/entrypoint.sh` OK; railway.toml parses (tomllib); fields checked against https://railway.com/railway.schema.json
- Branch coverage (coverage.py, `--branch`): omega_prime.ci_runners total 98%, scaler.py 99%, agent/app.py 99%, agent/supervisor.py 99%
- polyglot-check: 0 error(s), 0 warning(s) in 25/25 file(s) checked [pyright]
- Wrapper smoke (test_desktop_entrypoint.py, real entrypoint.sh with fake python3/upstream): agent started with exact argv, PASSWORD stripped, upstream exec keeps the PID and receives SIGTERM, no-command exits 64.

## Supply-chain provenance (re-derived 2026-10-23, not copied)

| Input | Pin | Evidence |
|---|---|---|
| XavTo/Railway-Desktop-Pro | `a915efba6d6a2c4b90f266f34fd8b56c1a446703` | GitHub commits API (committed 2026-10-19 06:54 AEDT) |
| base image | `accetto/ubuntu-vnc-xfce-g3@sha256:de1b0375…055b2` | pinned in upstream `ubuntu/Dockerfile` @ a915efba |
| upstream `ubuntu/Dockerfile` | sha256 `39c40090…8eeff` | raw.githubusercontent @ a915efba |
| upstream `ubuntu/entrypoint.py` | sha256 `d1bc4e03…c8b82` | raw.githubusercontent @ a915efba, verified with `sha256sum -c` at build |
| actions/runner 2.338.1 linux-x64 | sha256 `08e9ccb3…44c37` | GitHub release body + asset digest |
| uvicorn 0.54.0 / click 8.5.0 / h11 0.16.0 wheels | see Dockerfile ARGs | PyPI JSON digests |

## Deviations

- Work root moved off `/config` (see key decisions). Settings default unchanged.
- No live image build, deploy, domain or variable change (D-120-10 HELD).
