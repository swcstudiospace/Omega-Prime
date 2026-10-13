# PLAN-F1: Upstream dependency remediation loop

## Problem

The `redteam-node-audit` CI job (Phase 79, `.github/workflows/supply-chain.yml:42-72`)
is RED on `main`. The upstream purple-team backend
(`https://github.com/swcstudiospace/purple-team.git`) at the pinned gitlink
`831a6b0ee09f12c0f069f51d62b2f0945d505980` (see `git submodule status`) ships
one CRITICAL advisory (`proxy-addr`, open-redirect via `X-Forwarded-For`
spoofing) plus 7 high-severity advisories in its Node dependency tree. The
upstream `v1.3` lock still carries all of them (verified: local `npm audit`
of the `v1.3` lock shows `fixAvailable: true` for each, i.e. upstream has
not yet shipped the bumps). There is no automated path from "audit RED" to
"upstream fix landed, pin re-proven green" — today that loop is manual
runbook steps in `docs/red-team.md` ("Bumping the redteam submodule") plus
the `gitsubmodule` Dependabot entry (`.github/dependabot.yml:41`).

## User-facing behavior

A weekly scheduled workflow keeps the audit RED visible AND actionable until
upstream ships fixes:

1. Every Monday (~04:30 UTC, after the `supply-chain.yml` 04:17 run) and on
   `workflow_dispatch`, the workflow audits the `redteam` submodule at the
   current pin (`npm ci --ignore-scripts`, `npm audit --omit=dev
   --audit-level=high --json`, triaged by `scripts/redteam-audit-triage.py`
   with its dated `TRIAGED` block — still empty as of 2026-10-13).
2. While untriaged high+ advisories remain, it opens dependency-bump PRs on
   `swcstudiospace/purple-team` via `gh api` cross-repo (one PR per
   top-level fixable path, e.g. `proxy-addr`, with the `npm audit fix`
   lockfile delta as the proposed change and the advisory IDs in the body).
3. When an upstream bump lands and upstream `main` audits green, it comments
   on the omes-bot pin-bump tracking issue (repo `swcstudiospace/omes-bot`)
   with the green upstream commit SHA, so the operator can run the existing
   `docs/red-team.md` bump runbook (provenance `check`/`pack-diff`/`surface`
   + `redteam-provenance` CI) to move the gitlink.
4. If PR creation is denied (token lacks cross-repo scope), it degrades: the
   proposed lockfile diff + advisory report upload as a workflow artifact,
   and it opens an omes-bot issue instead with the same content pasted in.

## Interface

- New file `.github/workflows/redteam-remediation.yml` (only new workflow):
  ```yaml
  on:
    schedule: [{ cron: "30 4 * * 1" }]  # weekly, after supply-chain 04:17
    workflow_dispatch: { inputs: { dry-run: { type: boolean, default: false } } }
  permissions: { contents: read }  # job-level escalate only where needed
  ```
- `audit` job: `git submodule update --init redteam`, `setup-node@v4`
  (Node 22, matching `supply-chain.yml:54-56`), `npm ci --ignore-scripts`
  in `redteam/`, `npm audit --omit=dev --audit-level=high --json`, then
  `python3 ../scripts/redteam-audit-triage.py` — reuses the Phase 79 chain
  verbatim, never a second audit convention.
- `remediate` job (`needs: audit`, `if: failure()` of triage step):
  `gh api repos/swcstudiospace/purple-team/pulls -f ...` with
  `REMEDIATION_TOKEN` secret (PAT with `repo` scope on purple-team);
  `permissions: { pull-requests: write }` scoped on that job only.
- Config: no new `omega_prime/config.py` node (CI-only loop, no runtime
  surface); no new `omega_prime/commands/` slash command; no new
  `omega_prime/cron/heartbeat.py` kind. Secret `REMEDIATION_TOKEN` documented
  in the workflow `env:` comment; absent secret selects the degrade path.
- Degrade path: `actions/upload-artifact@v4` with `redteam-remediation-report/`
  (lockfile diff + `redteam-audit.json` + advisory table), then `gh issue
  create -R swcstudiospace/omes-bot`.

## Non-goals

- Auto-approving or auto-merging upstream purple-team PRs (operator reviews).
- Changing the `TRIAGED` policy in `scripts/redteam-audit-triage.py` or the
  `PYSEC-2026-4114` ignore precedent — triage stays dated + rationale.
- Shipping a vulnerable pin or weakening `npm audit --audit-level=high`.
- Touching runtime (`omega_prime/tools/redteam.py` stays a pure HTTP proxy),
  the vendored pack (`omega_prime/redteam/pack/`), or `VENDOR.md` pins.
- Replacing Dependabot `gitsubmodule` PRs or the `docs/red-team.md` runbook.

## Done-criteria

1. `.github/workflows/redteam-remediation.yml` exists with `schedule` +
   `workflow_dispatch` triggers and reuses the `supply-chain.yml` audit chain.
2. Upstream-bump PR path uses `gh api` against `swcstudiospace/purple-team`
   with advisory IDs in the PR body; never pushes directly to upstream main.
3. Green-upstream comment path posts the green commit SHA to the omes-bot
   tracking issue; comment includes the `npm audit` exit-0 proof line.
4. Degrade path (no `REMEDIATION_TOKEN`) uploads the artifact AND opens the
   omes-bot issue; workflow exits 0 in degrade (signal, not red).
5. Permissions: top-level `contents: read`; only the remediate job adds
   `pull-requests: write`; no `contents: write`, no `actions: write`.
6. Workflow YAML parses (`python3 -c yaml.safe_load`) with no write scopes
   beyond (5).

## Test plan

- Happy: hermetic `test_redteam_remediation.py` (repo pytest level,
  `omega_prime/tests/`): YAML parses; `on.schedule.cron` == `30 4 * * 1`;
  `workflow_dispatch` present; `gh api` target greps
  `swcstudiospace/purple-team`; artifact step greps `upload-artifact`.
- Edge: `REMEDIATION_TOKEN` absent → degrade steps present (`upload-artifact`
  + `gh issue create ... omes-bot`); dry-run input skips all `gh` writes.
- Failure: triage-green (audit exit 0) → remediate job skipped (`if:
  failure()` gate greps); malformed audit JSON fails loudly via the triage
  script (never passes as zero findings); cross-repo 403 → degrade, exit 0.
- Mirror of 79-03 style: pure shape checks (yaml parse, step greps,
  permissions grep) — no network, no `gh` calls, runnable offline in CI.

## Docs surfaces

- `docs/red-team.md`: new "Upstream remediation loop" subsection under
  "Bumping the redteam submodule" (schedule, token setup, degrade path).
- `SECURITY.md`: one line noting the loop + `REMEDIATION_TOKEN` scope.
- No changes to `docs/tool-catalog.md`, `docs/deploy.md`, or `VENDOR.md`.

## Rollback

Single revert: `git rm .github/workflows/redteam-remediation.yml` + revert
the two doc subsections; `supply-chain.yml`, Dependabot, and the pin are
untouched so audit RED simply returns to manual-runbook handling.

## Grounding notes

Symbols/paths cited above verified read-only on 2026-10-13: audit chain at
`supply-chain.yml:42-72`, Node 22 pin at `:54-56`, triage `TRIAGED` block at
`scripts/redteam-audit-triage.py:27-30`, gitsubmodule entry at
`.github/dependabot.yml:41`, runbook section "Bumping the redteam submodule"
in `docs/red-team.md:196`, gitlink `831a6b0` via `git submodule status`.
