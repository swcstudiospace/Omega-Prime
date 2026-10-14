---
name: debugging
description: Finding the actual cause of a failure rather than a plausible one. Use whenever something is broken and the cause is not obvious.
bots: [bot-00-omega-prime]
gates: [G-2]
---
<skill name="debugging" version="1">
# Debugging

## L1 — Summary

**Reproduce before you fix.** A fix for a failure you never observed is a guess, and its most
common outcome is that you change something harmless while the real bug stays — now with the
reassuring appearance of having been addressed.

```
1. REPRODUCE   Make it fail on demand. Exit code, stack trace, screenshot.
2. ISOLATE     Narrow to the smallest failing case.
3. UNDERSTAND  Why does it fail? Not "what change makes it stop".
4. FIX         The cause, not the symptom.
5. VERIFY      Re-run the reproduction. It must now pass.
6. REGRESS     Run the suite. Nothing else broke.
```

Steps 1 and 5 are the pair that makes the receipt meaningful (G-2). Step 1 alone is the one most
often skipped — and skipping it makes step 5 prove nothing.

**If you cannot reproduce it, say so** and describe what you tried. Do not ship a speculative fix
described as a fix.

---

## L2 — Method

### §1 Reproduce

Before anything else, get a command that fails reliably.

| Situation | Get to a reproduction by |
|---|---|
| Failing test | You already have one. Run it, read the actual output |
| Production bug | Write a test from the report. It should fail |
| Intermittent | Loop it. `for i in {1..100}` until you have a failure rate |
| Environment-specific | Reproduce **there**. The difference from local is usually the answer |
| User report, vague | Get: exact steps, version, device/OS, timestamp. Then correlate with logs |

**A failing command is evidence** — record it in the receipt as command index 0 with its non-zero
exit code. It proves the bug was real, which is what makes the later green run mean something.

**Cannot reproduce?** That is a finding, not a failure. Record what you tried and what you ruled
out. Shipping a guess labelled as a fix is worse than an open bug, because the bug now looks
closed.

### §2 Isolate

Narrow until the failing case is as small as it can be.

- **Bisect the input.** Half the data. Still fails? Half again.
- **Bisect the history.** `git bisect` when it used to work — this is frequently the fastest path
  and is underused.
- **Bisect the code.** Comment out, stub, or short-circuit until it stops failing.
- **Remove variables.** One change at a time. Changing three things and observing improvement
  tells you nothing about which mattered.

### §3 Understand

**This is the step under time pressure that gets skipped, and skipping it is how a bug comes back
in a different shape three weeks later.**

Ask: why does this happen? Not: what change makes the symptom disappear.

| Symptom | Shallow "fix" | Actual cause |
|---|---|---|
| Null pointer | Add a null check | Why is it null? Something upstream failed silently |
| Flaky test | Add a retry or sleep | A real race — usually in the code, not the test |
| Slow endpoint | Add a cache | N+1 query. The cache hides it until the data grows |
| Intermittent 500 | Add a retry | Connection pool exhaustion under concurrency |
| Crash on older OS | Try/catch around it | Unguarded API call — guard the version instead |
| Wrong number | Adjust the constant | An off-by-one in the formula, wrong for other inputs too |

Each shallow fix works today and fails differently later, in a form that no longer resembles the
original report.

**Flaky tests deserve naming.** The reflex is to retry them. But a test that passes 95% of the
time is usually describing a genuine race in the code under test, and the retry converts a visible
bug into an invisible one that reaches production.

### §4 Tools

Use them rather than adding print statements and re-running.

| Need | Use |
|---|---|
| Step through state | A real debugger — `rust-gdb`, `pdb`, browser devtools, Xcode, Android Studio |
| Where is time going | `py-spy`, `cargo flamegraph`, Instruments, browser Performance panel |
| What is it doing | `strace`, `dtruss`, Charles/mitmproxy for network |
| Distributed failure | Traces with correlation ids across services |
| Memory | `valgrind`, `heaptrack`, Instruments Allocations, Chrome heap snapshots |
| Concurrency | `cargo miri`, TSan, Swift strict concurrency, Android StrictMode |
| Regression point | `git bisect` |

**Read the whole error.** Stack traces are frequently skimmed to the first line when the actual
cause is a `Caused by:` four frames down, or in the second half of a long message.

### §5 Fix

- The smallest change that addresses the cause.
- If the cause is architectural and a small fix is a workaround, **say so**. Ship the workaround if
  needed, but record the real problem rather than letting the workaround become the permanent state.
- Do not refactor adjacent code in a bug fix. It enlarges the review surface and obscures what
  actually changed — and if the fix needs reverting, the refactor goes with it.
- Add a regression test covering the specific case. It is the only thing that stops the bug
  returning.

### §6 Verify

```
Re-run the reproduction     → must pass now
Run the full suite          → nothing else broke
Run on the affected platform→ if platform-specific
Record both in the receipt  → indices for the failing and passing runs
```

**Re-running the reproduction is not optional.** "The fix looks right" is exactly the claim PD-1
exists to prevent.

### §7 Production debugging

- Roll back first if users are affected. Diagnose on a restored system — the investigation is not
  faster under pressure, and users are affected throughout.
- Preserve evidence before restarting anything: logs, heap dump, thread dump, metrics window.
  A restart destroys the state that explains the failure.
- Correlation ids across services. Without them, a distributed failure is unreadable.
- Never experiment on production to narrow down a cause.

---

## Failure modes

| Mode | Symptom | Fix |
|---|---|---|
| Fix without reproduction | Bug persists; "fixed" claim was unfounded | Reproduce first, always |
| Symptom treated | Returns in a different shape later | §3 — ask why |
| Retry on a flaky test | Real race hidden, reaches production | Find the race |
| Multiple simultaneous changes | Cannot attribute the improvement | One variable at a time |
| Stack trace skimmed | Chasing the wrong frame | Read all of it, including `Caused by` |
| Print-statement debugging | Slow, and misses state you did not print | Use a debugger |
| Refactor bundled into a fix | Unreviewable diff; revert takes the refactor too | Separate changes |
| No regression test | Same bug returns | Test the specific case |
| Restart before capture | Evidence gone | Preserve state first |
</skill>
