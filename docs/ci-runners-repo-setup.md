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

Jobs that need Docker (`container:`, `services:`, `docker build`, docker
compose) cannot run on the unprivileged Railway Desktop (D-120-08). Point them
at the VPS runner instead:

```yaml
    runs-on: [self-hosted, vps-docker]
```

> **Rollout caveat.** Because matching is "job labels ⊆ pool labels" (labels
> compare case-insensitively), a job that says only `runs-on: self-hosted`, or
> `[self-hosted, Linux, X64]`, also matches the pool. Before turning the pool
> on, retarget every Docker-needing `self-hosted` job to
> `[self-hosted, vps-docker]`, and give the VPS runner that extra label.

## Public repositories

Public repositories are refused by default, because anyone can run code on a
self-hosted runner through a fork pull request. To serve one anyway:

1. Add it to both `OMEGA_CI_REPOS` and `OMEGA_CI_PUBLIC_REPOS` in
   `/etc/omega-ci/controller.env`, then restart the controller.
2. In the repository, set Settings → Actions → General → fork pull request
   approval to **Require approval for all outside collaborators**.
3. Give the `omega-ci` runner group access to that repository and turn on its
   **Allow public repositories** option.

The controller then serves only jobs whose workflow run is a same-repository
`push`, `workflow_dispatch` or `pull_request`. Fork pull requests,
`pull_request_target` and anything it cannot verify are refused with an
`omega.ci.runner.refused` event. Details and the risk are in
[CI runner fleet](ci-runners.md#public-repositories).

## One-time organisation setup (HELD)

1. **Runner group.** Organisation settings → Actions → Runner groups → New
   group `omega-ci`. Repository access: *selected repositories* (only your
   private CI repositories). Leave "Allow public repositories" off. Note the
   group's numeric id (`gh api /orgs/<org>/actions/runner-groups`) for
   `OMEGA_CI_RUNNER_GROUP_ID`. If your plan cannot create extra groups, use the
   default group (id `1`) and rely on `OMEGA_CI_REPOS` as the allowlist.
2. **GitHub App** (D-120-09), installed on the organisation, selected repos only:
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
     that token's hourly budget. The App is preferred.
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

The controller runs the same CLI the unit uses:

```bash
python -m omega_prime.ci_runners controller --host 127.0.0.1 --port 8788
```

## Rehearse offline first

Run the end-to-end replay. It uses fake transports and a fake runner, with no
network:

```bash
python -m pytest omega_prime/tests/ci_runners/test_app.py omega_prime/tests/ci_runners/test_controller.py omega_prime/tests/ci_runners/test_agent.py omega_prime/tests/ci_runners/test_supervisor.py omega_prime/tests/ci_runners/test_desktop_entrypoint.py -q
```

## Rollout (non-cancelling)

1. Onboard **one** trusted private repository: add it to the runner group and
   to `OMEGA_CI_REPOS`, then merge the one-line `runs-on` change.
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
