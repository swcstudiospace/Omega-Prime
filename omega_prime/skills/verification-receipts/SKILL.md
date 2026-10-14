---
name: verification-receipts
description: Produce a verification receipt that passes gate G-2. Use before making any completion claim — this is the artefact that separates "I ran it" from "it looks right".
bots: [bot-00-omega-prime]
gates: [G-2]
---
<skill name="verification-receipts" version="1">
# Verification Receipts

## L1 — Summary

**A claim without a command behind it is not a claim. It is a guess with confident phrasing.**

The receipt records what you actually ran and what actually happened. Gate G-2 validates it, and
CI can re-run your commands — which is the point. You can be certain about code you never
executed; you cannot fabricate an exit code that survives re-execution.

**Decision tree:**

```
About to say "done", "works", "fixed", "tested", or "verified"?
│
├─ Did you run a command and see its exit code?
│   ├─ NO ──▶ You are not done. Run it. §1
│   └─ YES
│       │
│       Does the command actually prove the claim?
│       ├─ NO ──▶ Wrong evidence. §3 — a lint pass does not prove tests pass
│       └─ YES ──▶ Write the receipt. §2
│
└─ Could not verify some of it?
    └─▶ That is fine and normal. Put it in `unverified` with the reason. §4
        Honest incompleteness passes. Silent incompleteness does not.
```

**The one-line test:** if someone re-ran every command in your receipt right now, would the
claims still hold? If you are not sure, you have not verified them.

---

## L2 — Method

### §1 What counts as verification

| Claim | Insufficient | Sufficient |
|---|---|---|
| "The code compiles" | Reading it | `cargo build` → 0 |
| "Tests pass" | Tests exist | `pytest` → 0, with the output |
| "The bug is fixed" | The fix looks right | Reproduction fails before, passes after |
| "The UI renders" | The component type-checks | Rendered it and looked at it |
| "It's faster" | The algorithm is better | Benchmark, before and after, with numbers |
| "The migration is safe" | It reads fine | Applied AND reverted on representative data |
| "It works on Android 8" | It compiles at minSdk 26 | Ran on an API 26 device or emulator |
| "No secrets committed" | I didn't add any | Secret scanner → 0 |

The pattern: **the evidence has to be capable of failing.** Reading your own code cannot fail —
it confirms the code matches your intent, which was never in question. A test run can fail, which
is exactly why it is evidence.

### §2 Receipt structure

**Path:** `.receipts/<bot-id>/<task-id>.json` — your own directory. You own it; no other
bot may write there, which keeps one bot from editing another's evidence.

```json
{
  "task_id": "fix-pagination-offset",
  "bot": "bot-02-web-edge",
  "started_at": "2026-09-17T09:14:00Z",
  "completed_at": "2026-09-17T09:41:00Z",
  "commands": [
    { "cmd": "deno test web/pagination_test.ts", "exit_code": 1, "duration_s": 3.1,
      "output_tail": "FAILED page 2 with exactly 20 items returns empty" },
    { "cmd": "deno test web/pagination_test.ts", "exit_code": 0, "duration_s": 3.0,
      "output_tail": "ok | 14 passed | 0 failed" },
    { "cmd": "deno check web/**/*.ts", "exit_code": 0, "duration_s": 5.4 },
    { "cmd": "deno lint web/", "exit_code": 0, "duration_s": 1.2 }
  ],
  "claims": [
    { "claim": "Reproduced the reported bug", "evidence_command_index": 0,
      "expects_failure": true },
    { "claim": "Fix resolves it; all pagination tests pass", "evidence_command_index": 1 },
    { "claim": "Type-check passes", "evidence_command_index": 2 }
  ],
  "unverified": [
    "Not checked against a live dataset — local fixtures only",
    "Safari not tested; no browser available in this environment"
  ],
  "files_changed": ["web/lib/pagination.ts", "web/pagination_test.ts"],
  "contract_changes": [],
  "rollback_plan": null,
  "approvals": [],
  "approved_by": "bot-06-quality-security"
}
```

Note command index 0: **a failing command is valuable evidence.** It proves the bug was real
before the fix. A receipt that only ever shows green is often a receipt where the reproduction
step was skipped.

A claim citing a failing command sets `"expects_failure": true`. The gate then requires that
command to have a **non-zero** exit — so the flag cannot be used to excuse a claim whose evidence
actually failed. It asserts "this was supposed to fail", not "ignore the exit code".

### §3 Matching evidence to claims

The most common G-2 failure is a claim citing a command that does not prove it.

| Claim | Cited | Problem |
|---|---|---|
| "Tests pass" | `ruff check` | Lint is not tests |
| "It builds" | `tsc --noEmit` | Type-checking is not building |
| "Works on device" | `assembleDebug` | Building is not running |
| "Migration is safe" | `pytest tests/unit` | Unit tests do not exercise a migration |
| "No regressions" | `pytest tests/test_new.py` | One new test file is not the suite |
| "Performance improved" | `cargo test` | Tests passing says nothing about speed |

Ask of every claim: **could this command have failed if the claim were false?** If not, it is the
wrong evidence.

### §4 `unverified` — the honest field

Mandatory. An empty array asserts you verified everything, which is rarely true and is checkable
against the change.

**Good entries** name what and why:

```
"iOS 15 not tested — only iOS 17 simulator available here"
"Behaviour under concurrent writes not verified — no load test in this environment"
"Production data volumes not simulated; tested against 1k rows, production is ~40M"
"Rollback exercised in staging, not production"
```

**Bad entries** are vague enough to be meaningless:

```
"Some edge cases"           ← which ones?
"Full testing"              ← what does that mean?
"Minor things"              ← minor to whom?
```

A specific `unverified` list is the most useful thing in the receipt for whoever reviews it. It
tells them exactly where to look, and it is the difference between a reviewer trusting your work
and a reviewer re-checking all of it.

### §5 Forbidden commands

These produce a green result from work that never ran. G-2 rejects them outright:

```
git commit --no-verify          pytest ... || true
./gradlew -x test               npm test --passWithNoTests   (on a suite that should have tests)
xcodebuild -skipTesting         terraform apply -auto-approve  (without recorded approval)
--skip-checks                   2>/dev/null                   (hiding an error stream)
```

If a check genuinely does not apply, say so in `unverified`. Do not silence it.

### §6 Self-approval

`approved_by` is never your own bot id. Reviews go to Bot 6 or a human.

A bot approving its own work is the same failure as the reviewer fixing the code: the independent
check disappears and nobody notices, because the paperwork still looks complete.

---

## Worked example — a bug fix done properly

```
1. REPRODUCE   deno test pagination_test.ts   → exit 1   ← the bug is real
2. INVESTIGATE read the code, find the off-by-one
3. FIX         change the offset calculation
4. RE-RUN      deno test pagination_test.ts   → exit 0   ← the fix works
5. REGRESS     deno test web/                 → exit 0   ← nothing else broke
6. CHECK       deno check && deno lint        → exit 0
7. RECEIPT     claims cite indices 0, 1, 3; unverified lists Safari and live data
```

Step 1 is the one most often skipped, and skipping it means step 4 proves nothing — you cannot
demonstrate you fixed a failure you never observed. The fix might be addressing a bug that was
never there while the real one persists.

---

## Failure modes

| Mode | Symptom | Fix |
|---|---|---|
| Claim without evidence | "Should work", "looks correct" | Run something that could fail |
| Mismatched evidence | Lint cited for a test claim | §3 — could this command have failed? |
| Empty `unverified` | Mobile change, no OS caveats | Almost always dishonest. Name what you skipped |
| Vague `unverified` | "some edge cases" | Name them specifically |
| No reproduction | Fix claimed, bug never observed failing | Reproduce first, always |
| Bypass command | `-x test`, `|| true` | §5 — rejected outright |
| Self-approval | `approved_by` is you | Route to Bot 6 |
| Summary exceeds receipt | "Fully working" on one unit test | Language matches evidence |
</skill>
