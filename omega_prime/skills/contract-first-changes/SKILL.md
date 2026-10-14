---
name: contract-first-changes
description: Changing a shared contract safely — breaking-change analysis, versioning, consumer impact. Use before any change to contracts/, .proto files, or openapi.yaml.
bots: [bot-00-omega-prime]
gates: [G-4]
---
<skill name="contract-first-changes" version="1">
# Contract-First Changes

## L1 — Summary

Contracts are the only shared surface in a platform-decomposed system. **A change that takes
thirty seconds in the API repo can break the iOS client silently, and be discovered weeks later by
a user.**

**Decision tree:**

```
Is the change breaking?  (§1 — the list is longer than people expect)
│
├─ NO  ──▶ Contract PR first anyway. Acks informational. Version minor bump.
│
└─ YES ──▶ Full protocol. §2
           Version major bump + migration note + ack from EVERY consumer.
           Gate G-4 blocks the merge without them.
           │
           Can you avoid breaking it?  ──▶ §3 expand-contract. Usually yes,
                                           and usually worth the extra deploy.
```

**The most dangerous change is the one no tool detects:** a field whose *meaning* changed while its
name and type stayed the same. Nothing catches it. Declare it explicitly or it ships.

---

## L2 — Method

### §1 What counts as breaking

**Breaking:**

- Removing or renaming a field, endpoint, event, or method
- Making an optional field required
- Narrowing a type — `string` → `enum`, `int64` → `int32`
- Removing an enum value
- Tightening validation on an existing input
- Changing a default in a way that alters behaviour
- Changing error codes or their meanings
- **Changing what a field means without changing its name or type**

**Not breaking:**

- Adding an optional field
- Adding an endpoint or method
- Adding an enum value *where consumers handle unknown values* — verify that they do; a client
  with an exhaustive switch crashes on a new variant
- Widening a type
- Relaxing validation

**The semantic change deserves its own attention.** `timeout_ms` reinterpreted from "per attempt"
to "total across retries" is the same name, the same type, the same JSON — and every consumer's
behaviour silently changes. No schema diff, no compiler error, no test failure. The only defence is
declaring it, which requires noticing it, which requires asking the question deliberately.

### §2 The protocol

```
1. DRAFT       Contract change with version, breaking flag, migration note
2. IDENTIFY    Consumers from ownership.yaml → contract_consumers
3. ACKNOWLEDGE Every consumer confirms it can implement against this
4. MERGE       Contract lands BEFORE any implementation
5. IMPLEMENT   Each bot, in its own paths, in parallel
```

**Acknowledgement is a commitment, not a formality.** A consumer acking a contract says "I can
build this". Acking without checking the field types reintroduces exactly the bug the protocol
prevents, one step later and with a paper trail suggesting it was agreed.

A consumer that **cannot** implement must reject with the specific blocker. That is a cheap, early
conversation. The alternative is an expensive, late one.

### §3 Expand-contract

The technique that turns most breaking changes into non-breaking ones. Three deploys:

```
EXPAND    Add the new field alongside the old. Write both. Read either.
          → Non-breaking. Consumers migrate on their own schedule.

MIGRATE   Consumers move to the new field, independently, over a release or two.
          → Track who has migrated. This is where the real work is.

CONTRACT  Once every consumer has migrated, remove the old field.
          → Breaking, but by now nobody is using it.
```

Worth it because it decouples release schedules. Mobile clients cannot be updated in lockstep with
a backend deploy — users update when they choose, over weeks — so any change requiring
simultaneous client and server deployment is a change that will break someone.

**Renaming `user_id` to `account_id`:**

```
v1.1  Serve both user_id and account_id. Same value. Deprecate user_id in the schema.
v1.2  Web, Android, iOS each migrate. Track adoption in telemetry.
v2.0  Remove user_id — once telemetry shows no traffic on it.
```

Three deploys instead of one, and no coordinated release across four teams.

### §4 Versioning

Semantic versioning on the contract:

| Bump | For |
|---|---|
| Major | Any breaking change |
| Minor | Additive, non-breaking |
| Patch | Documentation, clarification, no behavioural change |

Where clients cannot be forced to upgrade — mobile, third parties — run the previous major version
in parallel with a stated sunset date and telemetry showing who is still on it. A sunset date with
no usage data behind it gets missed.

### §5 The migration note

What a consumer needs in order to act:

```markdown
## v2.0 — user_id → account_id

**Breaking.** `user_id` removed from all account endpoints.

**Why:** the field held an account identifier, not a user identifier, and the mismatch caused
repeated confusion in client code.

**Migrate:** replace `user_id` with `account_id`. Identical value, identical type — no data
transformation needed.

**Timeline:** both fields served since v1.1 (2026-06-01). v1.x sunsets 2026-12-01.

**Verify:** no requests reading `user_id` in the last 30 days of telemetry.
```

State **why**, not only what. A consumer that understands the reason can spot related cases in its
own code that the note did not mention.

---

## Failure modes

| Mode | Symptom | Fix |
|---|---|---|
| Semantic change undeclared | Consumers silently behave differently | Ask deliberately; declare it |
| Rubber-stamp ack | The bug the protocol prevents, one step later | Check field types before acking |
| Implementation before contract | Contract retrofitted to one implementation's accidents | Contract merges first |
| Enum value added carelessly | Client with exhaustive switch crashes | Verify consumers handle unknowns |
| Breaking change with no expand | Coordinated release across four platforms | §3 |
| Sunset with no telemetry | Removed while still in use | Measure before contracting |
| Migration note without "why" | Consumers miss related cases | Explain the reason |
</skill>
