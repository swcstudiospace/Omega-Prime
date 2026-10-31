# Phase 122: Omega-Prime main green on the omega-ci pool (task 00065) — Context

**Gathered:** 2026-10-24 (AEDT) · **Mode:** /gsd-autonomous, grey areas auto-accepted (gsd-runtime rule 5b) · `ultrathink orchestrate workflowz`

## Domain
On main `0d6da2c` (24 Oct 2026, 14:15 AEDT), runs 40057560934 (ci), 40057560913 (grokbot-image) and 40057560823 (supply-chain) are red. Every job must stay on `[self-hosted, linux, x64, omega-ci]`. No check may be weakened. The fix goes in the runner image or the pool.

## Root causes
| Check | Root cause | Fix |
|---|---|---|
| verify 3.12/3.13/3.14 (38 failures each) | The tool cache is read-only, so pip fell back to `~/.local`. Tests that set `HOME`/`PYTHONNOUSERSITE` spawn children that cannot import pydantic/MCP. | 122-01: a per-job venv in every pip job |
| verify (2 failures), docs | `GITHUB_REF` is read by jobhook.py:270 but has no inventory purpose | 122-01: purpose and doc row (cherry-pick 3182ec6) |
| flaky `test_export_failure_leaves_no_base` | It diffs the shared `/tmp`, which races with concurrent tests and jobs | 122-01: private temp-root fixture that records the exact base |
| redteam-node-audit | At pin 6679b82: body-parser (high, GHSA-27r3-jxmh-p828), undici 8.11.2 (high ×3), fast-uri (moderate) | 122-02: lockfile-only purple-team bump, pin 1a1a4b0 |
| types (3.12) "operation was canceled", pip-audit exit 143 | The #99 merge redeployed the agents at 03:17:47Z (also 01:15:39Z), and agent shutdown stopped runners mid-job | 122-03: budgeted drain, `RAILWAY_DEPLOYMENT_DRAINING_SECONDS=3900` |
| grokbot-image (`remount /, flags 0x44000: permission denied`) and eventstream-it (`docker: command not found`) | The Railway sandbox denies every mount, even in a user namespace: CapBnd 0x800405fb (no CAP_SYS_ADMIN), no `/dev/fuse`. No OCI runtime (podman, buildah, runc, crun) can create a rootfs. | 122-04: needs a container-capable pool member, held for Ove |

## Decisions (all `auto-accepted`)
| # | Grey area | Answer taken |
|---|---|---|
| D-122-01 | Where pip installs go | A per-job venv under `$RUNNER_TEMP`, not a writable tool cache. The shared cache stays read-only and immutable. |
| D-122-02 | Flaky test fix | Redirect `lv._SYSTEM_TMP` to a private dir and record the base `mkdtemp` made. The assertion is stricter than the old set diff, and the production default `/tmp` is asserted too. |
| D-122-03 | npm advisories | Lockfile-only bump on a purple-team branch (00059 pattern). No triage exception and no `package.json` change. No tag (tags are held for Ove); the branch `omega-pin/f6d2fc2-audit-20261024` keeps it reachable. |
| D-122-04 | Drain budget source | Railway's own SIGTERM→SIGKILL window (`RAILWAY_DEPLOYMENT_DRAINING_SECONDS`) minus a 30 s stop margin, so there is one number to keep in sync. 3900 s covers the longest job timeout (60 min). The variable is set on all 3 services with `skipDeploys`. |
| D-122-05 | Idle runners during drain | Stop them at once so no new job lands on a stopping agent. Only runners that printed `Running job:` are waited for. |
| D-122-06 | grokbot-image and eventstream-it | A single root cause (no mount capability on Railway). The fix is a pool member that can run containers, so the jobs keep testing the real digest-pinned images. A Pulsar-native harness was rejected: prod runs the container image, and it would not fix grokbot-image. |

## Deferred / held for Ove
- 122-04: a container-capable runner agent. Options are a dedicated VM, or the VPS (not recommended: load 9–11 on 8 cores, disk 86%, and it holds the controller's GitHub App key). Spend and security posture are his call.
- Merging while image and eventstream-it stay red. The ship gate needs every check green.
