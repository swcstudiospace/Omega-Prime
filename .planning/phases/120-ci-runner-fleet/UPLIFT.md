# Phase 120 — UPLIFT (GoTxCoT, rule 4c) — task 00050

First uplift: 2026-10-23 12:05 AEDT. Second uplift (tracker URLs): see `<tracking>` below and TRACKING.md.

```xml
<uplift task="00050" phase="120" slug="ci-runner-fleet" keywords="ultrathink workflowz orchestrate">
  <ORIGINAL><![CDATA[/gsd-autonomous ultrathink workflowz orchestrate Program our codebases code quality and programming patterns State-of-the-Art, Best-in-Class, Production-Ready and Enterprise-Grade. In Railway we've configured a Serverless Linux Desktop Pro Container with a Static IP hosted in Singapore in the Linux Containers project. Now we're trying to configure Autoscaling CI/CD GitHub Runners that can trigger the serverless to ramp up and down when necessary. We need these runners for our projects we program on our VPS. Since we only have 1 self-hosted runner currently which slows things down.

Once programmed please advise how we'd actually configure this for our projects]]></ORIGINAL>
  <goal>Event-driven autoscaling pool of ephemeral self-hosted GitHub Actions runners that wakes the serverless Railway "Ubuntu Desktop" (24 vCPU/24 GB, Singapore) on demand, runs up to N jobs in parallel for the swcstudiospace VPS projects, and lets Railway sleep it when the queue drains — plus a plain-English per-repo configuration guide.</goal>
  <constraints>
    <c id="timer-ban">No cron, schedule, APScheduler or sleep-loop. Scale only on workflow_job webhook events; idle reap is a one-shot delayed event.</c>
    <c id="spend">No paid Railway deploy. Build to the cutover and stop with a projected cost.</c>
    <c id="secrets">Secrets by file-path env var only; never printed, logged, or committed. JIT configs single-use, never persisted.</c>
    <c id="quality">mypy, ruff, ruff format, pytest clean; stdlib-only core (CI installs .[dev] only); optional deps lazy.</c>
    <c id="mesh">Publish omega.ci.runner.* through an EventBus port compatible with 00046; standalone JSONL fallback.</c>
  </constraints>
  <got>
    <node id="n1" title="Signal and decision: workflow_job webhooks into a pure scaler">
      <cot>Verify HMAC-SHA256 X-Hub-Signature-256 with constant-time compare and a body cap.</cot>
      <cot>Dedupe X-GitHub-Delivery in a bounded LRU; accept workflow_job and ping only.</cot>
      <cot>Parse queued/in_progress/completed into a typed WorkflowJob; match labels subset and repo allowlist.</cot>
      <cot>Pure Scaler: ledger of jobs and runners → Decisions (Launch, Reap, ScheduleIdle, CancelIdle); monotonic transitions.</cot>
      <cot>Property-style tests: never exceeds max_runners; drains to zero; out-of-order events are safe.</cot>
    </node>
    <node id="n2" title="GitHub side: App auth, JIT runners, rate-limit discipline">
      <cot>GitHub App JWT (RS256 via lazily imported cryptography) → installation token cached until 5 min before expiry; PAT mode as fallback.</cot>
      <cot>generate-jitconfig at org or repo scope with runner group id and pool labels; name omega-ci-&lt;job&gt;-&lt;rand&gt;.</cot>
      <cot>Delete idle runner by id; list pool runners once at controller start for reconcile.</cot>
      <cot>Honour Retry-After / x-ratelimit-reset with bounded retries; surface omega.ci.runner.error events.</cot>
    </node>
    <node id="n3" title="Serverless Desktop wake and teardown on Railway">
      <cot>Wake = first HTTPS request to the agent's Railway domain; retry 502/503/connect errors with jittered backoff inside a 150 s budget.</cot>
      <cot>Teardown = Railway Serverless sleep after 5–10 min of zero outbound traffic once runners exit; no stop call.</cot>
      <cot>Document static-IP keep-awake caveat and the failed PASSWORD deploy that blocks cutover.</cot>
      <cot>Cutover runbook: switch service source to deploy/ci-runner-desktop, add domain → 8787, set variables; held for Ove.</cot>
    </node>
    <node id="n4" title="Runner agent on the Desktop">
      <cot>Stdlib ASGI agent: POST/GET/DELETE /v1/runners, POST /v1/drain, GET /healthz; bearer auth; capacity cap → 429.</cot>
      <cot>Supervisor: spawn run.sh with ACTIONS_RUNNER_INPUT_JITCONFIG env, own workdir, nice, rlimits, scrubbed env; reap on exit (event from process wait, not polling).</cot>
      <cot>Image overlay: pinned upstream desktop entrypoint (sha256), pinned actions/runner 2.338.1 (sha256), wrapper entrypoint.</cot>
      <cot>Tests with a fake run.sh: launch, exit cleanup, drain, cap, auth failures, name validation.</cot>
    </node>
    <node id="n5" title="Concurrency and idle policy">
      <cot>Default max 8 runners (2 vCPU / 2.5 GB each), hard ceiling 10, desktop reserve 2 vCPU / 3 GB.</cot>
      <cot>Idle grace 180 s via one-shot delayed event, cancelled on assignment.</cot>
      <cot>Defence in depth: agent cap independent of controller cap.</cot>
      <cot>Restart-safe reconcile triggered by the controller.started event.</cot>
    </node>
    <node id="n6" title="Event mesh integration">
      <cot>EventPublisher port + Null / JSONL / EventStream (lazy 00046) adapters.</cot>
      <cot>CloudEvents-shaped payloads for omega.ci.runner.* and omega.ci.desktop.*; redacted.</cot>
      <cot>DelayedEvents port: asyncio call_later adapter; Pulsar deliverAfter adapter seam.</cot>
      <cot>Controller app emits demand/launch/busy/reaped for the Command Center feed.</cot>
    </node>
    <node id="n7" title="Operator guide and per-repo configuration">
      <cot>docs/ci-runners.md: architecture, env reference, failure modes, cost.</cot>
      <cot>Per-repo: runs-on: [self-hosted, omega-ci]; docker jobs stay on vps-docker; concurrency groups; no secrets in workflows beyond existing.</cot>
      <cot>GitHub App + runner group + webhook setup steps; VPS systemd + Caddy templates (held for go).</cot>
      <cot>Checklist for pointing the 5–8 VPS projects at the pool, plus rollback (switch label back).</cot>
    </node>
  </got>
  <plans>
    <plan id="120-01" nodes="n1 n5 n6">Core: config, webhook verify/dedupe, WorkflowJob model, pure Scaler, delayed-event + publisher ports.</plan>
    <plan id="120-02" nodes="n2 n3 n6">Adapters + controller: GitHub App/PAT client, desktop client with wake retry, controller orchestration, ASGI controller app, CLI.</plan>
    <plan id="120-03" nodes="n4 n3">Runner agent + supervisor + Desktop image overlay + railway.toml (not deployed).</plan>
    <plan id="120-04" nodes="n7 n3">Docs, per-repo guide, VPS systemd/Caddy templates, workflow snippet, cutover runbook, cost.</plan>
  </plans>
  <tracking uplift="second" at="2026-10-23T12:00+11:00">
    <task linear="https://linear.app/swcstudio/issue/SPE-19875" notion="https://app.notion.com/p/402bc1a0c7ae81bcae85c89978e0c99e"/>
    <plan id="120-01" linear="SPE-19876" notion="https://app.notion.com/p/402bc1a0c7ae817a807cc55ff7f61941"/>
    <plan id="120-02" linear="SPE-19877" notion="https://app.notion.com/p/402bc1a0c7ae81c6bb6ae656ecadab1f"/>
    <plan id="120-03" linear="SPE-19878" notion="https://app.notion.com/p/402bc1a0c7ae8154b907e8cb962ad543"/>
    <plan id="120-04" linear="SPE-19879" notion="https://app.notion.com/p/402bc1a0c7ae81bebe3af5cf3bfdc571"/>
  </tracking>
</uplift>
```
