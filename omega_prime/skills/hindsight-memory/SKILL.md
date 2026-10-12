---
name: hindsight-memory
description: "Retain and recall discipline for the seat memory banks: what to keep, what never to keep, and the receipt-path rule."
bots: [bot-00-omega-prime]
gates: [G-2, G-3]
---

# Hindsight Memory

## When this applies (L1)

The desk's memory plane is Hindsight, reached only through the gateway: `desk_brief` at turn
start, `desk_memory_recall` when you need a fact, `desk_memory_retain` when you have one worth
keeping. Banks: `pd-<seat>` is yours to write; `pd-desk` is shared, written by LEAD and QUALITY,
read by all; `pd-lead-reports` is LEAD's. A weekly `reflect` prunes banks into mental models
without your involvement.

**Retain is refused without `receipt_path` or `source`.** That is the whole discipline in one
schema rule: memory holds what was verified or where it was read, never what was believed.

```
About to retain something?
│
├─ Is it a secret, token, connection string or passphrase?   YES ──▶ Never. §5
├─ Is it another seat's work or decision?                    YES ──▶ Not your bank. Tell LEAD. §1
├─ Is it a transcript, a chat summary or a whole file?       YES ──▶ Not memory. Docs plane or receipt. §2
├─ Is it backed by a receipt you wrote?                      YES ──▶ retain with receipt_path. §3
├─ Was it read from a URL or a committed file?               YES ──▶ retain with source. §3
└─ None of the above ──▶ It is a belief. Verify it first, or leave it out.

About to act on something recalled?
└─▶ Recall is a hint about where to look. Re-run the command or open the file before claiming it. §4
```

Redaction runs at the gateway, but the seat still does not type tokens: a redacted line is a
useless line, and the audit event still records that one was sent.

---

## Method (L2)

### §1 Banks

| Bank | Writes | Reads | Holds |
|---|---|---|---|
| `pd-<seat>` | The seat | The seat | Own decisions, environment facts, verified commands, review risks |
| `pd-desk` | LEAD, QUALITY | All seats | Team-wide facts: hosts, conventions, standing decisions, gate outcomes worth remembering |
| `pd-lead-reports` | LEAD | LEAD | Dispatch and report history |

Your endpoint writes to your own bank only; the gateway derives the bank from the seat token, so
there is no argument to get wrong. A fact that belongs to the team goes to LEAD or QUALITY with
its receipt path, and they retain it into `pd-desk`. Writing "the team decided X" into `pd-android`
makes it invisible to everyone but ANDROID and unreviewable by QUALITY.

A new team starts with an empty `pd-<seat>` and inherits `pd-desk`'s environment facts through
`desk_brief`. Nothing else carries over from a template.

### §2 What to retain, what never

| Retain | Example content | Never | Why not |
|---|---|---|---|
| Decisions with their receipt | "Chose staged rollout 10% → 50% → 100% for KanbanOS 2.3; halt on crash-free < 99.5%" + `receipt_path` | Secrets | Redaction is a backstop, not a licence; a retained token is a rotated token |
| Environment facts | "API 26 emulator is the only one on the dev box; API 34 needs the Mac mini" + `source` or receipt | Unverified claims | Recall would replay the guess as a fact to your future self (PD-1) |
| Verified commands | "`./gradlew :app:lintDebug` takes 4 min; `lintBaseline` grows silently" + receipt | Other seats' work | Their bank, their receipt; you would be remembering hearsay |
| Review risks and outcomes | "App Review rejected 2.2 for background-location text; fixed by …" + `source` | Transcripts, chat summaries, whole files | Memory is for facts you will need again; documents live in RAGFlow, evidence in receipts |

Ask of each line: if this comes back in a brief six weeks from now, does it still help, and can
I tell where it came from? A line that fails either question is noise the weekly reflect has to
prune.

### §3 Retain call shape

```
desk_memory_retain {
  content:      "…one fact, plainly stated, with the numbers that matter…",   (8–8000 chars)
  receipt_path: ".receipts/bot-03-android/kanbanos-2.3-rollout.json",        ← or source
  source:       "https://github.com/swcstudiospace/kanbanos/blob/<sha>/docs/release.md",
  graph_id:     "ut-…", task_id: "kanbanos-2.3-rollout",
  tags:         ["kanbanos", "rollout", "play"]
}
```

- `receipt_path` must match `.receipts/bot-0N-<seat>/<task>.json` and be yours. A receipt path
  from another seat is refused; a path that does not exist yet is a receipt you have not written.
- `source` is a URL or a committed file reference. "I read it somewhere" is not a source.
- `graph_id` and `task_id` let the brief group memories by ticket. Omit them and the fact floats.
- One fact per call. A paragraph of five facts recalls as one blob and is half useless.

### §4 Recall and the brief

`desk_brief` runs at turn start (the gateway caches it five minutes; pass `refresh: true` after
you retain something you need in the same turn). It returns the substrate brief for the Graph ID,
recall from your bank and `pd-desk`, and your open tickets. `desk_memory_recall` with a `query`
answers a specific question; `include_shared: false` narrows to your own bank.

Recall is retrieval, not verification. A recalled command is a command to run again; a recalled
fact about the environment is a fact to re-check if the receipt is older than the change you are
making. Recall tells you where the last verified answer was. It cannot tell you that nothing
changed since.

Recall and the brief fail open (`skills/desk-gateway` §2): an empty brief with a `reason` is not
"no memories". Note the reason and continue without the cache.

### §5 Redaction and the weekly reflect

The gateway runs the substrate redactor over `content` before it reaches Hindsight, and the audit
event records only a hash of the arguments. Neither fact changes your behaviour: you do not type
tokens, passwords, connection strings or the team passphrase into any tool argument (PD-4). A
redacted retain leaves a hole where the fact was and an event showing a seat sent a secret.

`reflect` runs weekly on each bank and folds retained facts into mental models. You do not run
it, schedule it or wait for it. If a brief surfaces a fact you now know to be stale, retain the
correction with the new receipt; reflect resolves contradictions towards the newest evidence.

---

## Worked example

**Good — IOS after a TestFlight investigation.**

```
receipt written: .receipts/bot-04-ios/kanbanos-2.3-testflight.json (build 412 processed 38 min)
desk_memory_retain {content: "KanbanOS TestFlight processing takes 30–40 min for builds with
  the new asset catalog; poll desk_testflight_status no sooner than 30 min after upload",
  receipt_path: ".receipts/bot-04-ios/kanbanos-2.3-testflight.json",
  graph_id: "ut-…", task_id: "kanbanos-2.3-testflight", tags: ["kanbanos", "testflight"]}
```

Six weeks later the brief says "expect 30–40 min", the seat does not poll for half an hour, and
the receipt path tells a reviewer exactly where the number came from.

**Bad — same seat, same afternoon.**

```
desk_memory_retain {content: "ASC key for kanbanos is AKIA… / team id 7X…; TestFlight is slow"}
→ refused: receipt_path or source required
desk_memory_retain {content: "TestFlight is slow", source: "https://example.com"}
```

The first call would have sent a key through the redactor (and the audit log records the attempt).
The second satisfies the schema with a fake source and retains nothing useful. In six weeks
"TestFlight is slow" recalls as a fact with no number, no build, and a source that proves nothing.

---

## Failure modes

| Mode | Symptom | Fix |
|---|---|---|
| Retain without evidence | `refused: receipt_path or source required` | Write the receipt first; memory follows evidence |
| Fake or vague `source` | Schema passes, nothing verifiable | A URL or committed path someone else can open |
| Secret in `content` | Redacted hole; audit row | Never type it; the gateway holds secrets |
| Team fact in a seat bank | Other seats never see it | Send to LEAD/QUALITY with the receipt path for `pd-desk` |
| Transcript as memory | Brief becomes unreadable | One fact per retain; documents go to RAGFlow |
| Acting on recall | Claim rests on an old receipt | Re-run or re-open before claiming (PD-1) |
| Empty brief read as "nothing known" | Fail-open reason ignored | Check `reason`; continue uncached |
| Stale fact left standing | Brief keeps repeating it | Retain the correction with the new receipt |

---

## Gate mapping

| Rule | Gate or directive | Why |
|---|---|---|
| `receipt_path` or `source` on every retain | G-2, PD-1 | Memory is downstream of verification, never a substitute for it |
| No secrets in `content` or any argument | G-3, PD-4 | Redaction is a backstop; the audit event outlives it |
| Own bank only; team facts via LEAD/QUALITY | PD-2 (ownership, applied to memory) | One writer per bank keeps evidence attributable |
| Recall is re-verified before a claim | PD-1 | A recalled exit code is not an observed one |
