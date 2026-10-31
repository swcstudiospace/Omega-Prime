# Phase 122 — GoTxCoT uplift (task 00065)

```xml
<uplift task="00065" phase="122" slug="main-green">
  <ORIGINAL at="2026-10-24T16:25+11:00">/gsd-autonomous ultrathink orchestrate workflowz Program our codebases State-of-the-Art, Production-Ready, Best-in-Class and Enterprise-Ready. Well i mean review all these Outstanding tasks for Omega Prime now. Since we've got the runners to handle them</ORIGINAL>
  <goal>Every workflow on Omega-Prime main is green on the omega-ci pool, with each failure fixed at its root cause.</goal>
  <constraints>
    <c>Every job stays on [self-hosted, linux, x64, omega-ci]; no GitHub-hosted runners.</c>
    <c>No test is weakened or skipped; the fix goes in the image or the pool.</c>
    <c>Railway agents stay serverless (sleep on, /healthz, /config volume); no redeploy while jobs run.</c>
    <c>Tags, VPS changes, keys and new spend wait for Ove.</c>
  </constraints>
  <got>
    <node id="g1" kind="python">Per-job venv in every pip job; GITHUB_REF purpose; private temp root for the readiness base tests.</node>
    <node id="g2" kind="supply-chain">Lockfile-only purple-team bump (body-parser, undici, fast-uri); pin, VENDOR.md, docstring and receipt agree.</node>
    <node id="g3" kind="pool">Agent drain waits for running jobs within Railway's draining window; 3900 s window on all 3 services.</node>
    <node id="g4" kind="pool" held="true">A container-capable pool member for grokbot-image and eventstream-it.</node>
  </got>
  <tracking notion="https://app.notion.com/p/403bc1a0c7ae81988824f491d5a3c2f1" linear="none (free limit)"/>
</uplift>
```
