---
name: terraform-k8s
description: Infrastructure-as-code with Terraform and Kubernetes. Use before any infrastructure change, plan review, or cluster operation.
bots: [bot-00-omega-prime]
gates: [G-5, G-6]
---
<skill name="terraform-k8s" version="1">
# Terraform & Kubernetes

## L1 — Summary

**Read the plan. Every resource, every time.**

Terraform expresses "replace" and "destroy" quietly, in the middle of long output. A change you
believe is an in-place update can be a destroy-and-recreate, and for a database or a volume that
is data loss. The plan is the only place that distinction appears before it becomes irreversible.

**Decision tree:**

```
Plan contains a destroy or forced replacement?
├─▶ Is the resource stateful (database, volume, bucket)?
│   ├─ YES ──▶ STOP. Data loss. Human approval (G-6) + a data plan. §2
│   └─ NO  ──▶ Understand WHY before applying. §2
│
Touching state directly — import, mv, rm?
├─▶ §3. Back up first. State loss means unmanageable infrastructure.
│
Changing something shared — VPC, node pool, IAM?
├─▶ §4 blast radius. Non-production environment first.
│
Deploying to Kubernetes?
└─▶ §5. Resource limits, probes, rollout strategy.
```

**Verification:** `fmt -check`, `validate`, `plan` reviewed and recorded, `tflint`, a security
scan, and a rollback plan (G-5).

---

## L2 — Method

### §1 Structure

- Modules for anything used more than once. Not for things used once — a module with one caller
  is indirection without reuse.
- Remote state with **locking and versioning**. Never local state for anything shared.
- Workspaces or separate state files per environment. One state file spanning production and
  staging means every production change risks staging and vice versa.
- Pin provider versions. An unpinned provider changes behaviour under you between runs.
- Variables validated (`validation` blocks) so bad input fails at plan rather than at apply.

### §2 Reading a plan

```
  # module.db.aws_db_instance.main must be replaced
-/+ resource "aws_db_instance" "main" {
      ~ engine_version = "14.7" -> "15.3" # forces replacement
```

`must be replaced` and `forces replacement` are the two phrases to scan for. They appear amid
dozens of unremarkable lines.

| Symbol | Meaning |
|---|---|
| `+` | Create |
| `~` | Update in place — safe |
| `-` | **Destroy** |
| `-/+` | **Destroy then create — data loss on stateful resources** |
| `+/-` | Create then destroy (`create_before_destroy`) |

**Before applying anything with `-` or `-/+`:**

1. Is the resource stateful? Database, volume, bucket, disk → assume data loss.
2. Why is replacement forced? Usually an attribute changed unintentionally alongside the intended
   change. Fix so the plan shows in-place update.
3. If replacement is genuinely required, it is a migration with a data plan and recorded approval
   — not the change you thought you were making.

`prevent_destroy = true` on stateful resources is cheap insurance. It converts an accident into an
error message.

### §3 State operations

State is the map between config and reality. Lose it and you own infrastructure you cannot manage.

- **Back up before any state surgery.** `terraform state pull > backup.tfstate`, kept somewhere
  durable.
- Never hand-edit state when a state command exists.
- Never apply against a workspace someone holds a lock on. Force-unlocking a live apply corrupts
  state — establish who holds it first.
- `import` before recreating something that already exists.
- Record every state operation in the receipt. These are the changes hardest to reconstruct later.

### §4 Blast radius

Ask before every change: **what is the largest thing this could break?**

| Change | Affects |
|---|---|
| Security group rule | One service |
| IAM policy | Everything using that role |
| VPC / subnet | Every resource in it |
| Node pool | Every workload scheduled on it — a rolling restart |
| DNS | Everything resolving that name, for the TTL |
| Shared secret rotation | Every consumer, simultaneously |

Non-production first. Where no non-production equivalent exists, that gap is itself worth fixing
before the change that needed it.

### §5 Kubernetes

**Always set resource requests and limits.** A pod without limits can starve its neighbours; a pod
without requests gets scheduled somewhere it does not fit. Most "random" cluster instability is
missing resource specification.

**Probes, and the right kind:**

| Probe | Answers | Failure means |
|---|---|---|
| `liveness` | Is it wedged? | Kubernetes restarts the pod |
| `readiness` | Can it take traffic? | Removed from the service endpoints |
| `startup` | Has it finished booting? | Protects slow starters from liveness |

A liveness probe that checks a dependency causes cascading restarts when the dependency wobbles —
check only whether *this* process is healthy.

**Rollout:**

- `RollingUpdate` with `maxUnavailable` and `maxSurge` set deliberately.
- `PodDisruptionBudget` for anything that must stay available during node operations.
- `kubectl rollout undo` is the rollback — verify it works for the workload before relying on it
  in the plan.

**Other:**

- Namespaces per environment or team, with resource quotas.
- Never `kubectl apply` by hand to production. It drifts from config, and the drift is invisible
  until the next apply reverts someone's fix.
- Secrets in a real secret manager, not in manifests. A secret in a manifest is a secret in git.
- `--dry-run=server` validates against the actual API, including admission controllers, where
  client-side validation does not.

### §6 Progressive delivery

- Canary or blue-green for anything user-facing.
- Automated rollback triggered on error-rate or latency thresholds, not on someone noticing.
- Feature flags decouple deploy from release, which turns a rollback into a config change —
  far faster than a redeploy and far less disruptive.

### §7 Cost

Infrastructure changes have a monthly bill attached. Before applying, know the delta — an
oversized instance class or an accidentally multi-AZ NAT gateway is a quiet, recurring cost
nobody notices for months. Escalate above the agreed threshold.

---

## Failure modes

| Mode | Symptom | Fix |
|---|---|---|
| Plan skimmed | Database replaced by a "tagging change" | Read every resource action |
| Local state | Concurrent applies corrupt it | Remote state with locking |
| Unpinned providers | Behaviour changes between runs | Pin versions |
| Hand-edited state | Unmanageable infrastructure | State commands, backup first |
| No resource limits | Noisy-neighbour instability | Requests and limits, always |
| Liveness checks a dependency | Cascading restarts | Check only this process |
| Manual `kubectl apply` | Config drift, fixes silently reverted | Everything through IaC |
| No `prevent_destroy` | One bad plan destroys a database | Set it on stateful resources |
</skill>
