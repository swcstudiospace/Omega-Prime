---
name: security-secrets-handling
description: Detecting, preventing and responding to secret exposure. Use when handling credentials, reviewing for secrets, or responding to an exposure.
bots: [bot-00-omega-prime]
gates: [G-3]
---
<skill name="security-secrets-handling" version="1">
# Secrets Handling

## L1 — Summary

**A secret that reaches git is disclosed.** Reverting the commit does not undo it: the object
stays in history, in every clone, in every fork, and on any CI or mirror that fetched it.

**So the response to an exposure is rotation, not deletion.** Removing it from the working tree is
housekeeping, not remediation.

**Decision tree:**

```
Found a secret in the codebase?
├─▶ Is it committed (in git history at all)?
│   ├─ YES ──▶ COMPROMISED. Rotate first. §4. Then clean history.
│   └─ NO  ──▶ Remove before committing. §2
│
Need a secret at runtime?
├─▶ §2. Environment or secret manager. Never a literal, never a config file in git.
│
Building something client-side?
└─▶ §3. Anything in the browser bundle is public. No exceptions, no obfuscation.
```

**Gate G-3 scans commits and runs in the pre-commit hook.** It is a net, not a guarantee —
a secret that does not match a known pattern passes it.

---

## L2 — Method

### §1 What is a secret

Not only API keys:

| Category | Examples |
|---|---|
| Credentials | API keys, tokens, passwords, OAuth client secrets |
| Keys | Private keys, signing keys, certificates with private material |
| Connection | Database URLs with inline credentials, broker URIs |
| Session | JWTs, session tokens, refresh tokens |
| Infrastructure | Cloud access keys, kubeconfigs with embedded tokens, `terraform.tfstate` |
| Mobile | Keystore passwords, provisioning profiles, App Store Connect keys |
| Internal | Webhook signing secrets, encryption keys, salts |

**`terraform.tfstate` deserves specific mention:** it frequently contains secrets in plain text —
generated passwords, connection strings — because Terraform stores resource attributes verbatim.
It must never be committed. Remote state with encryption.

### §2 Handling secrets

**Where they live, by preference:**

1. A secret manager — Vault, AWS Secrets Manager, GCP Secret Manager, 1Password
2. Platform environment variables — Vercel, GitHub Actions secrets, Kubernetes secrets backed by a
   manager
3. A local file with mode `0600`, outside any git working tree

**Never:**

- A literal in source
- A config file that is committed
- A default value in code — `os.environ.get("KEY", "sk-live-...")` is a committed secret
- A test fixture, even a "fake" one that happens to be real
- A comment, including a commented-out line
- Shell history — see the remote-dev-machine skill §5
- CI logs — mask them, and check that the masking works

```python
API_KEY = os.environ["API_KEY"]  # fails loudly if unset
API_KEY = os.environ.get("API_KEY", "sk-live-...")  # committed secret with a fallback
```

Fail loudly on a missing secret. A silent fallback to a default means the application runs with
the wrong credential and nobody notices until it is the wrong one in production.

### §3 Client-side is public

Anything reaching a browser bundle, a mobile app binary, or a published package is **public**.

- `NEXT_PUBLIC_*` and equivalents are inlined at build time and served to everyone
- Mobile apps can be decompiled trivially — a key in an APK or IPA is extractable in minutes
- Obfuscation is not protection. It raises the cost from seconds to minutes

If a feature seems to require a secret on the client, **the design is wrong**. Proxy the call
through a server or edge function that holds the credential. This is not a workaround; it is the
correct architecture, and it also gives you rate limiting and an audit trail.

### §4 Exposure response

**Order matters. Rotate first.**

```
1. ROTATE      Invalidate the exposed credential. Immediately, before anything else.
2. ASSESS      What could it access? For how long? Any sign it was used?
3. CLEAN       Remove from history — git-filter-repo or BFG. Force-push coordinated.
4. NOTIFY      Whoever owns the affected system. Security, if the scope warrants it.
5. PREVENT     Why did the scanner miss it? Fix the gate.
```

**Rotation before cleanup**, because cleanup takes time and coordination while the credential is
live throughout. A public repository is scraped within minutes.

**History rewriting is disruptive.** It invalidates every clone and breaks open pull requests.
Coordinate it, and do not let the disruption become a reason to skip it — the secret stays
retrievable until you do.

**Assume it was used.** Absence of evidence in logs is not evidence of absence, especially where
logging was not configured to capture it.

### §5 Detection

Gate G-3 (`ci/gates/check_secrets.py`) runs in CI and pre-commit. It detects:

- Known prefixes: `AKIA`, `ghp_`, `gho_`, `xox[baprs]-`, `sk_live_`, `AIza`
- Private key blocks
- JWTs
- Connection strings with inline credentials
- High-entropy strings assigned to suspiciously named variables

**It will miss things.** Custom formats, base64-wrapped secrets, credentials split across lines.
The scanner is a net, not a guarantee — a review still looks.

**False positives:** mark the line and say why.

```python
EXAMPLE_KEY = (
    "sk_test_4eC39H..."  # pragma: allowlist secret — Stripe's published test key
)
```

An allowlist entry with a reason is reviewable. A disabled scanner is not — and PD-3 covers the
difference.

### §6 Rotation hygiene

- Rotate on a schedule, not only after an incident. A credential nobody has ever rotated is one
  whose rotation procedure is untested.
- Short-lived credentials where the platform supports them — OIDC federation for CI beats
  long-lived cloud keys entirely.
- Separate credentials per environment. A production key in a preview environment means every
  preview URL is a production credential.
- Least privilege. A key that can only read one bucket limits the blast radius of the next
  exposure, and there will be a next one.

---

## Failure modes

| Mode | Symptom | Fix |
|---|---|---|
| Deleting instead of rotating | Secret still live and retrievable | Rotate first, always |
| Default value in code | Committed secret with a fallback | Fail loudly on missing |
| `NEXT_PUBLIC_` secret | Credential in every page served | Server-side proxy |
| Key in a mobile binary | Extractable by anyone | Proxy through a server |
| `terraform.tfstate` committed | Plaintext generated passwords in git | Encrypted remote state |
| Scanner disabled for noise | Real secrets ship | Allowlist the line with a reason |
| Shared credential across environments | Preview URLs hold production access | Per-environment credentials |
| Never rotated | Untested rotation path during an incident | Scheduled rotation |
</skill>
