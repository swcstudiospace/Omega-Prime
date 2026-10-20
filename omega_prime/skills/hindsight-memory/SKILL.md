---
name: hindsight-memory
description: "Evidence-backed retain and recall across local memory and configured shared sources."
bots: [bot-00-omega-prime]
gates: [G-2, G-3]
---
<skill name="hindsight-memory" version="1">
# Hindsight Memory

## Served tools

Use the Omega Prime tools named below (`lead_*`, `app_*`, `web_*`, `mob_*`, `infra_*`, `sys_*`, `qua_*`). Do not substitute an external gateway or infer a Hindsight service route.

## When this applies (L1)

Omega Prime provides a local-first memory plane with optional configured
Substrate and Hindsight sources. Use `lead_brief` at turn start,
`lead_memory_recall` when you need a fact, and `lead_memory_retain` only when
you have a fact worth keeping and its evidence. The selected backend controls
write preference; it does not disable recall from other configured sources.
Hindsight is not reached only through an external gateway.

**Retain is refused without `receipt_path` or `source`.** That is the whole discipline in one
schema rule: memory holds what was verified or where it was read, never what was believed.

```
About to retain something?
│
├─ Is it a secret, token, connection string or passphrase?   YES ──▶ Never. §5
├─ Is it another seat's work or decision?                    YES ──▶ Do not retain it as your own verified fact. §1
├─ Is it a transcript, a chat summary or a whole file?       YES ──▶ Not memory. Docs plane or receipt. §2
├─ Is it backed by a receipt you wrote?                      YES ──▶ retain with receipt_path. §3
├─ Was it read from a URL or a committed file?               YES ──▶ retain with source. §3
└─ None of the above ──▶ It is a belief. Verify it first, or leave it out.

About to act on something recalled?
└─▶ Recall is a hint about where to look. Re-run the command or open the file before claiming it. §4
```

Do not type tokens, keys, connection strings, or passphrases into a memory
tool argument. Credentials are resolved through the broker; diagnostics are
sanitized. Secret-shaped retain content is refused.

---

## Method (L2)

### §1 Backends and boundaries

`OMEGA_PRIME_MEMORY_BACKEND` accepts `local`, `substrate`, or `hindsight`.
When unset, the default is `substrate` only if the authoritative resolver
has an explicitly configured Substrate URL; otherwise it is `local`. This
setting is a write preference. Recall queries local memory and each
configured remote independently regardless of the selector.

Retain saves locally first. Configured Substrate receives each shared retain
regardless of selector. Hindsight receives writes only when selected as
`hindsight`, but remains a recall source whenever configured. A remote
failure is reported; it does not erase a local save or become a silent local
fallback. No Hindsight document-ID or server idempotency guarantee is made.

Recall and doctor report `local`, `substrate`, and `hindsight` separately
using exactly `answered`, `empty`, `not_configured`, `unreachable`, or
`unauthorized`. Sanitized diagnostics are separate from these states:
unreachable is not empty, and healthy-source results survive another
source's failure. Provenance is retained and repo isolation applies across
sources; do not rely on bank labels or caller input to cross repo boundaries.

Memory `target=user` (the user profile), and memory replace/remove operations
remain local-only. They do not write to Substrate or Hindsight.

Configure Hindsight only by explicitly setting `HINDSIGHT_URL` to an HTTPS
service you operate. There is no default URL. Add only the hostname from
`HINDSIGHT_URL` (not the full URL) to `network.hosts` in
`omega_prime/contracts/policies/omega-prime.json`; `CredentialBroker.key_for`
rejects unlisted hosts before resolving the key. Hindsight credentials are
obtained through the credential broker from environment or a mode-checked
0600 secret file. Never put the URL or key in settings, a Bot template, or
chat. With the URL unset, Hindsight credential lookup and network access are
skipped.

### §2 What to retain, what never

| Retain | Example content | Never | Why not |
|---|---|---|---|
| Decisions with their receipt | "Chose staged rollout 10% → 50% → 100% for KanbanOS 2.3; halt on crash-free < 99.5%" + `receipt_path` | Secrets | Redaction is a backstop, not a licence; a retained token is a rotated token |
| Environment facts | "The dev box has one emulator image; a newer API needs {{ADMIN_DEVICES}}" + `source` or receipt | Unverified claims | Recall would replay the guess as a fact to your future self (PD-1) |
| Verified commands | "`./gradlew :app:lintDebug` takes 4 min; `lintBaseline` grows silently" + receipt | Unverified or other-seat claims | Recall must not turn hearsay into fact |
| Review risks and outcomes | "App Review rejected 2.2 for background-location text; fixed by …" + `source` | Transcripts, chat summaries, whole files | Memory is for facts you will need again; documents live in RAGFlow, evidence in receipts |

Ask of each line: if this comes back later, will it help, and can you show where it came from?
One useful, evidence-backed fact is better than a paragraph of unrelated claims.

### §3 Retain call shape

The approved command syntax is:

```
/omega-retain --source <source> -- <content>
/omega-retain --receipt-path <path> -- <content>
```

Retain also requires the existing lead approval/grant policy. Supply either
a source or a receipt path as evidence; a receipt must already exist and be
within the authorized root. Never use a fake or vague source. Secret-shaped
content is refused. Keep one concise fact per retain.

The lead tool accepts this evidence-bearing shape:

```
lead_memory_retain {
  content:      "…one verified fact, plainly stated…",
  receipt_path: ".receipts/bot-00-omega-prime/task.json",  ← or source
  source:       "committed file reference or evidence URL",
  graph_id:     "…", task_id: "…",
  tags:         ["topic", "decision"]
}
```

An operation is saved locally first. A configured Substrate write is required
regardless of selector; Hindsight is written only when selected. If a required
remote write fails, the result reports failure even when `local_saved` is
true.

### §4 Recall and the brief

`lead_brief` runs at turn start. `lead_memory_recall` searches local memory
and every configured remote independently, retaining source provenance and
enforcing repo isolation. The selected write backend does not narrow recall.
Each source reports one of `answered`, `empty`, `not_configured`,
`unreachable`, or `unauthorized`. A source failure is not an empty result and
does not suppress valid hits from other sources.

**Current integration limit:** on this branch, `SubstrateClient.memory_search`
still collapses `SubstrateError` to `[]`. A Substrate transport failure may
therefore look empty until phase 97's source-aware contract is rebased; do not
interpret an empty result as proof of healthy reachability yet.

Recall is retrieval, not verification. Re-run a recalled command or re-check
an environment fact against current evidence before relying on it.

### §5 Secrets and stale information

Never type tokens, passwords, connection strings, or passphrases into a
memory argument. Hindsight credentials come through the broker; do not put
the Hindsight URL or key in settings, templates, or memory. Errors and
provenance are handled without exposing credentials.

Re-check recalled facts against current evidence. Do not assume an external
reflect schedule or Hindsight server behavior.

---

## Worked example

**Good — a verified operational fact.**

```
receipt written: .receipts/bot-00-omega-prime/task.json
/omega-retain --receipt-path .receipts/bot-00-omega-prime/task.json -- \
  "The local fake integration confirmed the selected backend and source state."
```

The evidence path points to a receipt that already exists. The retained
statement remains a memory, not a substitute for reopening its evidence.

**Bad — retaining without evidence.**

```
/omega-retain -- "Service is slow."
→ refused: provide `--source` or `--receipt-path`
```

This is a policy-enforced refusal: retain requires either evidence argument.
When a source is supplied, the caller must judge whether it actually supports
the claim; a vague source may be accepted by the tool and is not proof of
verification. Secret-shaped content is refused separately.

---

## Failure modes

| Mode | Symptom | Fix |
|---|---|---|
| Retain without evidence | Refused | Write the receipt first or cite a checkable source |
| Fake or vague `source` | Does not establish evidence | Give a URL or committed file reference that can be checked |
| Secret in `content` | Refused | Never type secrets; credentials are broker-sourced |
| Another seat's unverified claim | No attributable evidence | Ask the owner or lead to verify and retain it |
| Transcript as memory | Poor recall signal | Keep one fact; documents live in the docs plane |
| Acting on recall | Claim rests on stale evidence | Re-run or reopen before claiming |
| Source unavailable | Source reports failure, not empty | Record its state and continue only with supported results |
| Stale fact left standing | Memory may be out of date | Verify and retain a correction with current evidence |

---

## Gate mapping

| Rule | Gate or directive | Why |
|---|---|---|
| `receipt_path` or `source` on every retain | G-2, PD-1 | Memory is downstream of verification, never a substitute for it |
| No secrets in `content` or any argument | G-3, PD-4 | Secret-shaped content is refused; credentials stay out of memory |
| Preserve evidence and repo boundaries | PD-2 | Provenance and isolation keep recall attributable |
| Recall is re-verified before a claim | PD-1 | A recalled exit code is not an observed one |
</skill>
