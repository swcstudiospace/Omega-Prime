---
name: vercel
description: Deploying to Vercel, edge runtime constraints, and rollback. Use before any deploy or when debugging an edge function.
bots: [bot-00-omega-prime]
gates: [G-5]
---
<skill name="vercel" version="1">
# Vercel

## L1 — Summary

**Preview first. Always.** A preview URL costs nothing and an incident costs a lot.

**The edge runtime is not Node.** No filesystem, no native modules, limited APIs, hard CPU and
memory ceilings, short timeouts. Code that runs locally under Node and fails on the edge is the
standard way people discover this.

**Decision tree:**

```
Deploying?
├─▶ Preview → verify ON the preview → then promote. §1
│   Production promotion needs a rollback plan in the receipt (G-5). §4
│
Writing an edge function?
├─▶ §2 constraints. Verify against the real runtime, not local Node.
│
Adding an environment variable?
├─▶ §3. Is it secret? Then it must NOT be NEXT_PUBLIC_ / client-exposed.
│
Something broken in production?
└─▶ §5. Roll back first, diagnose second.
```

**Instant rollback covers the deployment. It does not cover a migration or a poisoned cache that
shipped alongside it.** State those implications explicitly or the rollback plan is fiction.

---

## L2 — Method

### §1 Deployment flow

```
commit → preview deployment (automatic) → verify on the preview URL → promote to production
```

**Verify on the preview, not locally.** The preview runs the production build with production
environment wiring. Local dev differs in ways that matter: bundling, environment variables, edge
vs Node runtime, image optimisation.

What to check on a preview before promoting:

- The changed pages render and the critical path works
- No console errors
- Network requests hit the expected origins
- Performance has not regressed materially
- Accessibility check on changed interactive components

Record the preview URL in the receipt.

### §2 Edge runtime constraints

| Constraint | Consequence |
|---|---|
| No filesystem | No `fs`, no reading files at runtime. Bundle or fetch it |
| No native modules | Anything with a binary dependency will not run |
| Web APIs only | `fetch`, `Request`, `Response`, `crypto.subtle`. No `Buffer`, no `process` beyond env |
| CPU limit | Heavy computation is killed mid-request |
| Memory limit | Large in-memory structures fail |
| Short timeout | Long upstream calls need a timeout shorter than the platform's |
| Cold starts | Fast, but not zero. Keep the module graph small |

**Choose deliberately.** Edge for latency-sensitive, geographically distributed, lightweight work:
auth checks, redirects, personalisation, header rewriting. Node for anything needing the full
runtime, a heavy dependency, or longer execution.

Declare the runtime explicitly rather than relying on a default that may change.

### §3 Environment variables

| Scope | Visible to |
|---|---|
| Plain (server) | Server and edge functions only |
| `NEXT_PUBLIC_*` | **The browser. Public. Permanently.** |

Anything prefixed for the client is inlined into the bundle at build time. It is not obscured, it
is published. A key there is disclosed to every visitor and must be treated as rotated.

Before every deploy: confirm no secret is in the client bundle. Grep the built output for known
secret prefixes if you are unsure — it is a ten-second check against an expensive mistake.

Different values per environment (production / preview / development). A preview deployment
pointing at the production database is a common and costly misconfiguration — preview URLs are
shareable and often unauthenticated.

### §4 Rollback plans (G-5)

A plan names the command, the recovery time, and the data implications.

```
Rollback: promote deployment dpl_abc123 (previous production) via the Vercel dashboard
          or `vercel rollback dpl_abc123`.
Recovery: under 30 seconds — no rebuild required.
Data:     none. This change is presentation-only, no schema or cache changes.
Exercised: yes, rolled back and forward on preview.
```

**Where it is not so simple, say so:**

```
Data: this release writes a new `preferences_v2` field. Rolling back leaves those
      rows in place; the previous version ignores the field, so no corruption, but
      preferences set after this deploy will appear lost to users until roll-forward.
```

That second paragraph is the part that makes a rollback plan real. "We can roll back instantly"
is true of the deployment and frequently false of the system.

### §5 Incident response

1. **Roll back first.** Diagnose on the restored system, not the broken one. Users are affected
   while you investigate, and the investigation is not faster under pressure.
2. Confirm the rollback worked — check the actual site, not just the dashboard status.
3. Then diagnose: runtime logs, build logs, the diff between the two deployments.
4. Fix forward on a preview. Verify there. Promote.

Never debug a production incident by pushing experimental changes to production.

### §6 Caching

- Cache headers are the main performance lever and the main source of "why is it still showing the
  old version".
- `stale-while-revalidate` for content that tolerates brief staleness.
- Purge deliberately on deploys that change cached content. A stale cache survives a rollback and
  will make a fixed site look broken.
- Never cache a personalised response at the CDN layer without a `Vary` that actually distinguishes
  users. Leaking one user's page to another is the worst version of a caching bug.

---

## Failure modes

| Mode | Symptom | Fix |
|---|---|---|
| Local-only verification | Works locally, breaks on preview | Verify on the preview |
| Node API on edge | Runtime error only in production | Check §2, declare the runtime |
| Secret in `NEXT_PUBLIC_` | Credential in the bundle | Server-side only; rotate the exposed key |
| Preview → prod database | Test data in production | Per-environment variables |
| Rollback plan ignores data | "Instant rollback" leaves broken state | State data implications (§4) |
| Debugging in production | Prolonged incident | Roll back first |
| Stale cache after rollback | Fixed site still looks broken | Purge on deploy |
</skill>
