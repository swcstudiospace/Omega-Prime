# CI runner repository setup

This is the how-to for putting repositories on the autoscaling runner pool of
the Autonomous Event-Driven ADLC Programming Bot, described in
[CI runner fleet](ci-runners.md). The per-repository change is one
line. Everything else is a one-time, organisation-level setup.

> Every step marked **HELD** is an operator action (D-120-10). Automation never
> creates the App, installs services, reloads Caddy or touches Railway.

## The one-line change per repository

Retarget the jobs that should use the pool:

```yaml
jobs:
  test:
    runs-on: [self-hosted, linux, x64, omega-ci]   # was: self-hosted / ubuntu-latest
```

That label set is the filter. The controller sees every `workflow_job` event
for the organisation, and only jobs whose labels are a **subset** of the pool
labels, in an allowlisted **private** repository, get a runner. Public
repositories are refused unless opted in (see
[Public repositories](#public-repositories)). No secrets,
tokens or extra steps are added to the workflow.

**Every job runs on this pool (Ove, 2026-10-24).** No job in a
swcstudiospace repository stays on GitHub-hosted runners, and none is parked
on a separate runner. The Railway Desktop is unprivileged (no Docker daemon,
no `sudo`), so the runner image ships the tools those jobs used to get from
`ubuntu-latest` or from containers instead (see
[Runner image toolchain](ci-runners.md#runner-image-toolchain)). When you
retarget a job:

1. Set `runs-on: [self-hosted, linux, x64, omega-ci]`.
2. Replace `services:` / `docker run` databases with the image binaries,
   started by `omega-ci-db start postgres` / `omega-ci-db start greptime`
   (free ports, data under `$RUNNER_TEMP`, settings exported to later steps)
   and stopped by `omega-ci-db stop --all` in an `if: always()` step. Never
   hard-code a port: parallel jobs share the Desktop's ports. See
   [Databases in jobs](ci-runners.md#databases-in-jobs-omega-ci-db).
3. Drop `sudo apt-get install` steps for tools the image already has
   (age, rsync, podman, jq, build-essential, ...). Anything missing is an
   image change, not a workflow workaround.
4. Keep `actions/setup-python` / `setup-node` / `setup-dotnet` on the versions
   the image pre-caches, so they resolve from the read-only tool cache.

> **Rollout caveat.** Because matching is "job labels ⊆ pool labels" (labels
> compare case-insensitively), a job that says only `runs-on: self-hosted`, or
> `[self-hosted, Linux, X64]`, also matches the pool. That is intended now
> that every job belongs here, but check such jobs need nothing the image
> lacks before the pool serves them.

## Org-wide setup (every swcstudiospace repository)

Since 2026-10-24 (Ove's decision) the pool serves every `swcstudiospace`
repository, public ones included, behind the fork guard. Instead of listing
repositories one by one, use the owner wildcard in all three allowlists:

1. Runner group `omega-ci`: Repository access **All repositories**, and tick
   **Allow public repositories** (see the one-time setup below).
2. Organisation settings → Actions → General → "Approval for running fork pull
   request workflows from contributors": **Require approval for all outside
   collaborators**. Set it once at org level so every repository, including
   new ones, inherits it.
3. VPS `/etc/omega-ci/controller.env`:
   ```bash
   OMEGA_CI_REPOS=swcstudiospace/*
   OMEGA_CI_PUBLIC_REPOS=swcstudiospace/*
   ```
   then restart the controller.
4. Railway → Ubuntu Desktop → Variables: `OMEGA_CI_AGENT_REPOS=swcstudiospace/*`
   (Railway redeploys the Desktop).

`owner/*` means every repository of that owner, matched case-insensitively,
including ones created later. There is no bare `*` and no partial glob such
as `swcstudiospace/foo*`; the controller and the agent refuse to start with
one. An org repository that is itself a **GitHub fork** of an upstream
project is *not* covered by `owner/*` (its workflows come from upstream). To
serve a **private** one on purpose, add its exact name next to the wildcard,
for example `swcstudiospace/*,swcstudiospace/some-fork`, in `OMEGA_CI_REPOS`
and `OMEGA_CI_AGENT_REPOS`; pull requests inside it stay refused. A **public**
fork is always refused, by design (the fork guard sees `fork: true` for every
trigger), even when listed exactly. Each repository still opts in with the
one-line `runs-on` change above. Opening the pool to another organisation
needs Ove's say. Details:
[CI runner fleet](ci-runners.md#org-wide-setup-every-repository-of-an-owner).

## Public repositories

Public repositories are refused by default, because anyone can run code on a
self-hosted runner through a fork pull request. To serve one anyway:

1. Add it to both `OMEGA_CI_REPOS` and `OMEGA_CI_PUBLIC_REPOS` in
   `/etc/omega-ci/controller.env` (or use `swcstudiospace/*` in both, as in
   the org-wide setup above), then restart the controller.
2. In the repository, set Settings → Actions → General → fork pull request
   approval to **Require approval for all outside collaborators**.
3. Give the `omega-ci` runner group access to that repository (or **All
   repositories**) and turn on its **Allow public repositories** option.

The controller then serves only jobs whose workflow run is a same-repository
`push`, `workflow_dispatch`, `pull_request`, or `schedule` on the default
branch. Fork pull requests,
`pull_request_target` and anything it cannot verify are refused with an
`omega.ci.runner.refused` event. On the runner itself, the Desktop image's
pre-job guard fails any such job before its first step, even if GitHub
assigns it to one of our runners, so also add the repository to the
Desktop's `OMEGA_CI_AGENT_REPOS`. Details and the risk are in
[CI runner fleet](ci-runners.md#public-repositories).

## One-time organisation setup (HELD)

1. **Runner group.** Organisation settings → Actions → Runner groups → New
   group `omega-ci`. Repository access: **All repositories** with **Allow
   public repositories** ticked for the org-wide setup (the allowlists and the
   fork guard do the filtering), or *selected repositories* with "Allow public
   repositories" off for a private-only pool. Note the
   group's numeric id (`gh api /orgs/<org>/actions/runner-groups`) for
   `OMEGA_CI_RUNNER_GROUP_ID`. If your plan cannot create extra groups, use the
   default group (id `1`) and rely on `OMEGA_CI_REPOS` as the allowlist.
2. **GitHub App** (D-120-09), installed on the organisation (all repositories
   for the org-wide setup, otherwise the selected ones):
   - Organisation permission **Self-hosted runners: Read and write**
     (registers JIT runners, lists and deletes them).
   - Repository permission **Actions: Read** (needed for the event, and for
     the one startup check of saved queued jobs against GitHub).
   - Webhook: active, URL `https://<controller host>/github/webhook`, a random
     secret, event **Workflow job** only.
   - Record the App id and installation id. Then generate and download the
     **private key**: it is in the "Private keys" section at the bottom of the
     App's General page. A "client secret" is a different value (OAuth only)
     and the controller cannot use it.
   - For repo scope instead (`OMEGA_CI_SCOPE=repo`, e.g. a personal
     repository), grant Repository **Administration: Read and write** instead
     of the organisation permission.
   - Fallback: a PAT file (`OMEGA_CI_GITHUB_PAT_FILE`) works, but it shares
     that token's hourly budget. The App is preferred. A fine-grained token
     needs resource owner `swcstudiospace`, organisation permission
     **Self-hosted runners: Read and write**, and repository **Actions: Read**
     on the repositories it serves (the fork guard's run lookup and the
     startup check of saved jobs).
3. **VPS controller.**
   - Create an unprivileged `omega-ci` account and a virtualenv with
     `pip install 'omega-prime[ci-runners]' cryptography`.
   - Write `/etc/omega-ci/controller.env` from
     `deploy/ci-runner-controller/env.example`, and secret files under
     `/etc/omega-ci/secrets/`, each owned by `omega-ci` with mode 0600. The
     credential broker refuses a secret file with any group or other bit.
   - Install `deploy/ci-runner-controller/omega-ci-autoscaler.service`, and add
     the proxy site with your host: `Caddyfile.snippet` for Caddy, or
     `nginx-site.conf.example` for nginx. The production VPS runs nginx, and
     its port 8788 is already taken, so the unit and site there use 8798.
   - Validate first, without installing anything:
     `systemd-analyze verify` on the unit, and `caddy validate` on a throwaway
     copy of the snippet (or `nginx -t` with the site added).
4. **Railway Desktop.** Follow `deploy/ci-runner-desktop/README.md`.
   - Set a `PASSWORD` of **at least 16 characters** first. The upstream
     desktop refuses to start without it, and a previous deploy failed on
     exactly this.
   - Leave **Root Directory blank** and set the service variable
     `RAILWAY_DOCKERFILE_PATH=deploy/ci-runner-desktop/Dockerfile`. On the
     first real deploy the config-as-code path was ignored and Railway built
     the root MCP-server `Dockerfile`, which failed on
     `/run/secrets/omega_prime_token`.
   - Deploy the overlay, keep the desktop domain on port 3000, and add a second
     domain on port 8787 for the agent.
   - Set `OMEGA_CI_AGENT_TOKEN` (same value as the controller's desktop token
     file), `OMEGA_CI_CONTROLLER_URL` and `SERVERLESS_FRIENDLY=1`.
     `OMEGA_CI_CONTROLLER_URL` is **required** even before the controller
     exists: without it the agent does not start and the 8787 domain answers
     502.
   - **Static outbound IP:** Railway warns it may keep an idle service awake
     (≈ $22/month). Keep it only if a third party allowlists your runners'
     egress IP; it plays no part in the webhook path.

The controller runs the same CLI the unit uses. 8788 is the default; the
production VPS listens on 8798 behind its nginx site because 8788 is taken
there:

```bash
python -m omega_prime.ci_runners controller --host 127.0.0.1 --port 8798
```

## Rehearse offline first

Run the end-to-end replay. It uses fake transports and a fake runner, with no
network:

```bash
python -m pytest omega_prime/tests/ci_runners/test_app.py omega_prime/tests/ci_runners/test_controller.py omega_prime/tests/ci_runners/test_agent.py omega_prime/tests/ci_runners/test_supervisor.py omega_prime/tests/ci_runners/test_desktop_entrypoint.py -q
```

## Rollout (non-cancelling)

1. Onboard **one** trusted private repository first: merge its one-line
   `runs-on` change (with the org-wide setup it is already covered by
   `swcstudiospace/*`; otherwise add it to the runner group and to
   `OMEGA_CI_REPOS`).
2. In-flight jobs are not affected. Jobs already queued for the old runner keep
   their old labels and finish there. Do not add `cancel-in-progress` as part
   of the migration.
3. Watch the events (`omega.ci.runner.demand → launch_requested → launched →
   busy → exited`), and check the job log shows a runner named `omega-ci-…`.
4. Add the remaining repositories the same way, a few at a time.

## Rollback

- **One repository:** revert its `runs-on` line. New jobs go back to the old
  runner; running pool jobs finish normally.
- **Whole pool:** `POST /v1/drain` on the agent (bearer auth) stops new
  runners while in-flight jobs finish. Then stop the controller service.
  Jobs still labelled `omega-ci` wait in the queue until a runner exists, so
  revert their `runs-on` lines too.
- Nothing needs a GitHub cleanup: ephemeral runners deregister themselves
  after their one job, and idle ones are deleted at their idle deadline.
