---
phase: 120-ci-runner-fleet
plan: "04"
subsystem: infra
tags: [github-actions, self-hosted-runners, docs, systemd, caddy]
requires:
  - phase: 120-03
    provides: runner agent API, supervisor, Desktop overlay
provides:
  - docs/ci-runners.md — architecture, every controller/agent setting, API statuses, security, events, cost, failure handling, offline replay, held boundary, source audit
  - docs/ci-runners-repo-setup.md — the one-line runs-on change, vps-docker routing, App/group/webhook setup, VPS + Railway cutover (HELD), non-cancelling rollout, rollback
  - deploy/ci-runner-controller/{omega-ci-autoscaler.service,Caddyfile.snippet,env.example} — inert, sanitized templates
affects: []
actuals:
  tasks: 4
  commits: 1
tech-stack:
  added: []
key-files:
  created:
    - docs/ci-runners.md
    - docs/ci-runners-repo-setup.md
    - deploy/ci-runner-controller/omega-ci-autoscaler.service
    - deploy/ci-runner-controller/Caddyfile.snippet
    - deploy/ci-runner-controller/env.example
  modified:
    - docs/SUMMARY.md (both new pages linked once; required by test_summary_links_resolve_exactly_once)
key-decisions:
  - "Executed inline as the Desk Lead executor subagent (gsd-runtime rule 5b)."
  - "Rollout caveat documented: matching is job labels ⊆ pool labels, so a bare `runs-on: self-hosted` job also matches the pool — Docker jobs must move to [self-hosted, vps-docker] first."
  - "Runner-group fallback documented: if the org plan cannot create extra groups, use the default group (id 1) with OMEGA_CI_REPOS as the allowlist (GitHub docs are inconsistent on Free vs Team)."
  - "systemd unit drops MemoryDenyWriteExecute and ~@resources to avoid breaking Python/cffi at runtime; keeps NoNewPrivileges, ProtectSystem=strict, empty capability set, ~@privileged."
  - "No source, tests or workflows changed by this plan."
---

# 120-04 Summary — operator docs + held VPS templates

## Verification (host, 2026-10-23 AEDT)

- `python -m pytest omega_prime/tests/ci_runners -q` → 226 passed, 1 skipped
- `ruff check` / `ruff format --check` / `mypy omega_prime/ci_runners` → clean
- `python -m pytest omega_prime/tests/test_docs.py omega_prime/tests/test_public_repo.py -q` → 33 passed
- catalog / plugin / prompt_format / env_inventory `--check` → exit 0
- `systemd-analyze verify` on the unit: only error is the placeholder `/opt/omega-ci-venv/bin/python` (expected); a throwaway copy with `/usr/bin/python3` verifies clean (exit 0)
- `caddy validate --adapter caddyfile --config /tmp/omega-ci-caddy.Caddyfile` → "Valid configuration" (Caddy v2.10.2 release tarball, sha512 checked against the release checksums, used locally only)
- Private-identifier scan of the new docs/templates: clean
- polyglot-check: no changed code files in this plan

## Held (D-120-10)

GitHub App + runner group creation, systemd install/enable, Caddy reload, Railway image build/deploy/domains/variables, and any paid cutover all remain HELD for the operator. None were run.
