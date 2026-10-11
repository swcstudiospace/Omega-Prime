# Deploying the tool host

The Grok Bot tool host (`python -m omega_prime.mcp_server --transport sse`) ships with a deployment kit: a hardened
image definition, a renderer for four deployment targets, a checker, and an image CI workflow. Rendered defaults use
a non-root user, read-only root filesystem, writable state and `/tmp`, mounted token files, and `/healthz`/`/readyz`
probes. Generated configuration references credential files rather than embedding their values. The checker rejects
recognized inline credentials; it is not a guarantee that every possible secret has been detected.

```bash
python -m omega_prime.grokbot.deploy render --target k8s --out deploy/k8s
python -m omega_prime.grokbot.deploy check deploy/k8s
```

`render` options: `--target {docker,compose,systemd,k8s}`, `--out DIR`, `--name` (default `omega-prime-mcp`, a DNS
label), `--image` (default `ghcr.io/swcstudiospace/omega-prime-mcp:0.1.0`), `--port` (default 8000), `--public-url`
(adds `--public-url` to the server arguments), `--uid` (default 10001, at least 1000) and `--force` (replace a file this
tool did not generate). Output is deterministic: the same options always produce the same bytes. `check` exits 0 when
clean or warnings only, 1 on any error finding, and 2 on bad usage; `--json` prints machine-readable findings.

## The token file

The host refuses to start on a non-loopback address without a token, and it refuses a token file that group or others
can read. Create a one-line file and hand it to the platform; never put the token in a command line, environment
variable, compose file, unit file or manifest.

```bash
umask 077
openssl rand -hex 24 > token
```

Clients send it as `Authorization: Bearer <token>`.

## Docker

```bash
python -m omega_prime.grokbot.deploy render --target docker --out .   # already committed at the repo root
docker build -t omega-prime-mcp .
sudo chown 10001:10001 token
docker run -d --name omega-prime-mcp \
  --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges:true \
  -v omega-prime-state:/var/lib/omega-prime \
  -v "$PWD/token:/run/secrets/omega_prime_token:ro" \
  -p 127.0.0.1:8000:8000 omega-prime-mcp
```

The generator owns the committed `Dockerfile` and `.dockerignore`. After a change, regenerate and compare a fresh
`render --target docker`; the exercised default projections match byte-for-byte. A base-image update must be mirrored
in `BASE_IMAGE` in [the generator](../omega_prime/grokbot/deploy.py). The build installs `requirements-lock.txt` into
`/opt/venv` and copies `omega_prime/` (minus tests and caches) to `/app/omega_prime`, with `--root /app`. The root
`redteam/` upstream submodule is excluded from the context; ordinary Python adapter and pack resources under
`omega_prime/redteam/` are retained. This does not start the optional Node backend or enable Red Team tooling.
The image uses numeric user `10001:10001`, a stdlib `/healthz` `HEALTHCHECK`, and `STOPSIGNAL SIGTERM`.

## Compose

```bash
python -m omega_prime.grokbot.deploy render --target compose --out deploy/compose
cd deploy/compose
install -d -m 0700 secrets && openssl rand -hex 24 > secrets/omega_prime_token
sudo chown 10001:10001 secrets/omega_prime_token && chmod 600 secrets/omega_prime_token
docker compose up -d
```

The service uses `read_only: true`, `tmpfs: [/tmp]`, `cap_drop: [ALL]`, `no-new-privileges`, a named state volume and a
file-based secret mounted at `/run/secrets/omega_prime_token`. Compose bind-mounts the file with its host owner, so the
file must be owned by the container user (uid 10001) and mode 0600. The port is published on `127.0.0.1` only; put a TLS
reverse proxy in front and pass `--public-url`.

## systemd

```bash
python -m omega_prime.grokbot.deploy render --target systemd --out /tmp/unit
sudo install -d /opt/omega-prime /etc/omega-prime
sudo cp -r omega_prime /opt/omega-prime/omega_prime
sudo python3.12 -m venv /opt/omega-prime/venv
sudo /opt/omega-prime/venv/bin/pip install -r requirements-lock.txt
sudo install -m 0600 token /etc/omega-prime/token
sudo install -m 0644 /tmp/unit/omega-prime-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now omega-prime-mcp
```

The unit uses `DynamicUser=yes`, `StateDirectory=omega-prime` (state under `/var/lib/omega-prime`) and
`LoadCredential=omega_prime_token:/etc/omega-prime/token`, so the service reads the token from its private credentials
directory (`%d`) and never from a world-visible path. It binds `127.0.0.1`; front it with a reverse proxy. Run
`systemd-analyze verify` on the installed unit; before the install layout above exists it only reports that the
`ExecStart` binary is missing.

## Kubernetes

```bash
python -m omega_prime.grokbot.deploy render --target k8s --out deploy/k8s
kubectl create secret generic omega-prime-token --from-file=omega_prime_token=./token
kubectl apply -f deploy/k8s/omega-prime-mcp.yaml
```

One multi-document file: a `Deployment`, a ClusterIP `Service`, a `NetworkPolicy` (ingress only from pods labelled
`omega-prime-mcp-client: "true"` in the same namespace) and a `PodDisruptionBudget`. The pod runs as `runAsUser` and
`fsGroup` 10001 with `readOnlyRootFilesystem: true`, `allowPrivilegeEscalation: false`, all capabilities dropped,
`seccompProfile: RuntimeDefault` and `automountServiceAccountToken: false`. `emptyDir` volumes back `/tmp`, the state
directory and the token copy.

- A Secret volume with `fsGroup` is group-readable, which the host rejects. An init container (same image, same
  hardening) therefore copies the token into an in-memory `emptyDir` as a 0600 file owned by uid 10001.
- One replica: SSE and Streamable HTTP sessions live in the process. Scale out only behind session affinity.
- Probes: `startupProbe` and `livenessProbe` on `/healthz`, `readinessProbe` on `/readyz`.
- State is an `emptyDir`; mount a PersistentVolumeClaim at `/var/lib/omega-prime` to keep memory and the audit log.
- Egress is not restricted; add an egress policy for your cluster if the connectors you enable need it.

## State volume and the read-only root filesystem

The image's root filesystem is read-only in every target. Everything the host writes goes to `/tmp` or the state volume
mounted at `/var/lib/omega-prime` (`HOME` and the `--home` directory):

- `OMEGA_PRIME_STATE_DIR=/var/lib/omega-prime/state` moves the memory store and the session database out of the source
  tree. Without it the host writes `omega_prime/memory/` and `omega_prime/sessions.db` under `--root`.
- `OMEGA_PRIME_AUDIT_LOG=/var/lib/omega-prime/audit/audit.jsonl` is the hash-chained audit trail.

Every write location of the default tool set:

| Location | Written by | Where it lands in the image |
| --- | --- | --- |
| `$OMEGA_PRIME_STATE_DIR/memory/` (`MEMORY.md`, `USER.md`) | `memory` tool | state volume |
| `$OMEGA_PRIME_STATE_DIR/sessions.db` | `session_search` store | state volume |
| `<root>/omega_prime/memory/`, `<root>/omega_prime/sessions.db` | the same, when the variable is unset | read-only; set the variable |
| audit log path (`--audit-log`) | host audit trail | state volume |
| `<home>/.omega-prime-exec/*.py` | `execute_code` scratch scripts | state volume |
| `<root>/omega_prime/skills/` | `skill_manage` (runtime skill creation) | read-only; mount a writable skills volume at `/app/omega_prime/skills` to allow it |
| `<root>/omega_prime/**` | `write_file`, `edit_file` and the other coding tools | read-only; these tools return an error |
| `<root>/.receipts/quality/`, receipt files under `<root>` | `qua_receipt_approve`, `qua_waiver_record` | read-only; the tools fail |
| caller-chosen screenshot paths | device, Appium and Playwright screenshot tools | choose a path under `/tmp` or the state volume |
| `/tmp` (render scratch, browser sandbox profile) | lead render, browser egress probe | `/tmp` tmpfs |
| `<root>/cron/jobs.json`, `<root>/prime-kernel/`, goals, harness and autonomous state | Prime families, config-gated and off by default | read-only; mount a writable volume before enabling a family |

## Findings

`check` reports findings with stable ids. Errors make the CLI exit 1; warnings do not.

| Id | Severity | Meaning |
| --- | --- | --- |
| `run-as-root` | error | the workload runs as root or declares no non-root user |
| `privileged` | error | the workload runs privileged |
| `host-network` | error | the workload shares the host network namespace |
| `host-pid` | error | the workload shares the host PID namespace |
| `missing-healthcheck` | error | no Dockerfile `HEALTHCHECK` or compose `healthcheck` |
| `missing-probe` | error | a Kubernetes container lacks a liveness or readiness probe |
| `latest-tag` | error | an image reference is untagged or uses `latest` |
| `inline-secret` | error | a credential is written into the artifact (token-looking value, `*_TOKEN`/`*_KEY`/`PASSWORD` set to a literal, `--token VALUE`, a Secret object with data) |
| `privilege-escalation` | error | `allowPrivilegeEscalation: true` |
| `missing-no-new-privileges` | error | `no-new-privileges` (compose), `NoNewPrivileges=yes` (systemd) or `allowPrivilegeEscalation: false` (Kubernetes) is missing |
| `unparseable` | error | the file could not be read, so it could not be verified |
| `writable-rootfs` | warning | the root filesystem is not read-only (`read_only`, `readOnlyRootFilesystem`, `ProtectSystem`) |
| `missing-resource-limits` | warning | no memory limit is set |
| `missing-network-policy` | warning | a Kubernetes workload is present without any NetworkPolicy in the scanned files |
| `unrecognized-file` | warning | a file named explicitly is not a Dockerfile, compose, Kubernetes or unit file |
| `promote-without-staging-evidence` | warning | a `promotion*` marker is present without a staging receipt (`*staging*`) or image digest (`*.digest`) file |
| `missing-promotion-approval-record` | warning | a `promotion*` marker is present without an approval (`*approv*`) or audit (`*audit*`) record file |
`check` reads YAML with a built-in block-YAML reader (no PyYAML needed). Anchors, aliases, tags, merge keys and
multi-line flow collections are not supported and are reported as `unparseable`.

## Staging first, then gated promotion

Promote the same digest through staging before production. There are no `--staging` or `--prod` flags: `check`
infers a production promotion from a promotion marker file placed alongside the artifact set — any file whose
name (case-insensitive) starts with `promot` (i.e. `promotion*`, `promot*`) or with `prod-`, `prod_` or `prod.`
— and both promotion findings below are warnings, so they never change the exit code (exit 0 is preserved).

1. `render` the artifact set once, then run `render`+`check`+`verify` against a staging host first. Keep the
   staging receipt (any `*staging*` file) or the image digest file (any `*.digest` file) alongside the artifact
   set; without either, `check` warns `promote-without-staging-evidence`.
2. Record the approval in the audit log and keep the approval or audit record (any `*approv*` or `*audit*`
   file) alongside the artifact set; without it, `check` warns `missing-promotion-approval-record`.
3. Promote the same digest: apply the identical artifact set to production with the promotion marker
   (`promotion*`, `promot*`, `prod-*`, `prod_*` or `prod.*`) present. Never re-render with different options
   between staging and production.

These marker findings are warnings, not authorization. A staging/approval filename cannot grant a tool approval,
approve a GitHub environment, or authorize a registry push. Publication controls belong to the workflow below.

### Rollback to a validated version

Before a release, retain the prior successful validation run, its immutable artifact IDs, source SHA, archive
SHA-256, local image ID, both transport reports, SBOM and scan result. If an authorized push occurred, also retain
the separately observed registry `RepoDigest`. A local image ID, tar checksum and registry manifest digest are
different identities; do not substitute one for another.

Rollback uses that prior validated version, not a rebuild at its tag or commit. For an unpublished image, retrieve
the exact `validated-image-bytes` archive plus `validated-image-evidence` from the recorded run, verify the archive
checksum **before** `docker load`, then compare the loaded image ID and source/run/artifact bindings with the
receipt. For a previously published image, select its retained registry digest and matching reviewed deployment
artifacts. Do not resolve a moving tag, re-render with changed options, or call freshly rebuilt bytes the prior
validated image. Apply only through the deployment platform's separately approved procedure; this guide is not
evidence that a release or rollback was performed.


## Stop and drain

On SIGTERM the host stops accepting new calls, drains in-flight tool calls for up to `--shutdown-grace` seconds
(20 by default) and exits 0. `/readyz` returns 503 while draining. Platform stop timeouts must be longer: 30 s for
`docker stop`, compose (`stop_grace_period`) and systemd (`TimeoutStopSec`). With nothing in flight the host exits
within a fraction of a second of SIGTERM, so Kubernetes runs a 5 s `preStop` sleep first, which gives the load balancer
time to see `/readyz` go 503 and stop routing; `terminationGracePeriodSeconds: 45` covers that sleep, the 20 s drain and
a margin.

## Image CI

The [image workflow](../.github/workflows/grokbot-image.yml) validates by default; it does not publish on push,
pull request, schedule, or a dispatch with `publish=false`. One locally loaded image starts two hardened containers
with the same supported server command (`--transport sse`). The verifier selects `--transport sse` for one and
`--transport http` for the other, each with `--require-auth` and owned call/admin token files. The listener exposes
both `/sse` and `/mcp`; there is no server `--transport http` flag.

Validation observes the running uid, read-only rootfs, dropped capabilities and no-new-privileges. Every required
transport/admin check must pass once with coherent counts and observed exit0; a skipped admin roundtrip is not
complete approval proof. Each container must exit0 after SIGTERM. Sanitized reports are retained on success/failure;
diagnostic text reaches the full-input secret gate before clipping, with private fixed-safe withholding on rejection.
The existing SPDX SBOM upload and Trivy `scanners=vuln`, `severity=CRITICAL`, `ignore-unfixed=true`, `exit-code=1`
remain blocking. This policy does not attest all severity levels, secret scanning or license clearance.

Only after those controls pass does CI save the exact image bytes and record their archive checksum, image ID,
source/ref/event/run, immutable upload artifact ID and actual validation/SBOM/scan outcomes. The separate `publish`
job is default-off and requires explicit `workflow_dispatch` opt-in plus exact `confirm_ref`, a protected ref and the
`grokbot-image-publish` environment's approval gate. Declaring an environment name alone is insufficient: the workflow
reads actual required-reviewer protection and refuses missing/unreadable/ambiguous configuration. No publication
environment existed at the 2026-10-11 local inventory check. Configuring it with named required reviewers and
prohibiting self-review/admin bypass is a separately authorized operator prerequisite, not an observed approval.

The publisher downloads the same run's immutable artifacts, gates strict complete evidence, verifies ref/event/run/
source/artifact identity, compares the archive checksum before loading and the loaded image ID before any tag/login/
push. It never rebuilds. Package-write permission is confined to that job; its artifact-API read is read-only.
Publication runs use a noncancelling group separate from cancellable validation. A successful check, warning-only
promotion marker or manual trigger alone is not publication authorization.

The [main CI](../.github/workflows/ci.yml) preserves Python3.12–3.14 verification/lint/type matrices. Its three verify
legs run the maintained `python -m omega_prime.grokbot.readiness` command through existing identity/exit receipts;
always-run retention admits a canonical report or private fixed-safe failure metadata alongside JUnit. Nonzero
observed exits stay nonzero, rejected evidence after observed0 becomes non-success, and an unobserved run records
null rather than inventing exit0. Raw candidate output is never uploaded as a fallback. Independent lock audit and
pinned native/parity/license jobs remain separate. KB refresh reports missing-key **skipped/not-run**, and updates
only `kb/` through a human-reviewed PR: skip, no-change and generated PR are not completed reviewed retrieval.

## What was verified

Historical kit checks at initial authorship included deterministic rendering/checking of all four targets, an image
health/unauthenticated-SSE/SIGTERM probe, `docker build --check`, Compose configuration and systemd unit analysis.
Those are historical results, not repeated platform deployment evidence.

The 2026-10-11 phase76 local exercise built the current generated image, observed the hardening above, passed both
SSE and Streamable HTTP conformance including call-token admin refusal and genuine approval grant/revoke transitions,
and observed clean SIGTERM for each. The safe verifier omits required handler arguments after granting approval:
it proves admin-state/gate effects, not arbitrary gated handler execution. Actual SPDX generation and the configured
fixed-CRITICAL Trivy scan passed. Deleting the owned image tag and loading the retained tar preserved the exact image
ID, without rebuild. Current generator render/check, actual Docker-context exclusions and consumer/quality gates
also passed. Hosted CI matrix legs, artifact upload and environment approval were not exercised locally.

This milestone performed **no live image publication, deployment, rollback, cluster operation or live Grok/provider/
Red Team backend validation**. Kubernetes artifacts were not applied to a live cluster and no systemd service was
started. Keep those prerequisites separate from local proof; use [local validation](local-validation.md) for the
maintained isolated dry run, not as a live-service attestation.
