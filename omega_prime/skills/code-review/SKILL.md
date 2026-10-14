---
name: code-review
description: Reviewing another bot's work — what to check, in what order, and what to let go. Use for every review.
bots: [bot-00-omega-prime]
gates: [G-2]
---
<skill name="code-review" version="1">
# Code Review

## L1 — Summary

**Review in priority order.** Time spent on naming while a race condition sits unexamined is time
spent badly — and a review that leads with style trains people to skim your comments, which is how
you stop catching the things that matter.

```
1. Correctness    Does it do what it claims?
2. Verification   Is the receipt honest?              ← highest yield
3. Security       Secrets, injection, authz
4. Contracts      Breaking changes, consumer impact
5. Blast radius   What if this is wrong in production?
6. Maintainability Clarity, consistency, test quality
7. Style          Only what tooling has not settled
```

**You do not fix what you find.** Report it to the owning bot with a concrete failure scenario. A
reviewer who patches the code stops being an independent check — the fix inherits your assumptions
rather than testing them, and nobody reviews it.

**Every finding states a concrete failure scenario.** "This could be a problem" is not a finding.
"With a null `user_id` this throws before the auth check" is.

---

## L2 — Method

### §1 Receipt review — start here

The highest-yield check, because a dishonest receipt invalidates everything else in the change.

| Check | What it catches |
|---|---|
| Receipt exists | Silent completion claim |
| Every claim cites a command index | Claims floating free of evidence |
| Cited commands exited 0 | Success claimed on a failing command |
| **Commands actually prove the claims** | "Tests pass" citing a lint run |
| `unverified` is honest | Silent partial verification |
| `approved_by` ≠ authoring bot | Self-approval |
| No bypass commands | `--no-verify`, `-x test`, `|| true` |
| `files_changed` matches the diff | Undeclared changes |
| Summary language ≤ evidence | "Fully working" on one unit test |

**The mismatch check is the one that requires actual attention.** A receipt can be perfectly
formed and still cite the wrong evidence. Ask of each claim: *could this command have failed if
the claim were false?*

**Empty `unverified` on a cross-platform change is almost always a bot that forgot**, not a bot
that tested everything. An Android change with no OS-version caveats deserves a question.

### §2 Correctness

- Edge cases: empty, null, zero, one, maximum, boundary, duplicate, out-of-order.
- Error paths — usually far less exercised than the happy path and far more likely to be wrong.
- Concurrency: shared mutable state, lock ordering, check-then-act races, cancellation.
- Off-by-one in pagination, slicing, ranges. Perennial.
- Resource cleanup on the failure path, not just on success.
- Integer overflow, precision loss, timezone and DST handling.

**Read the tests, not just the code.** Assertions that never fire are common:

```python
def test_rejects_invalid():
    with pytest.raises(ValueError):
        validate(bad_input)  # also passes if validate() has a typo and raises NameError
```

### §3 Security

- Secrets in code, config, logs, error messages, or the client bundle.
- Input validation at the trust boundary, not only in the UI.
- Parameterised queries. No shell string interpolation.
- Authorisation on **every** path, not only the one the feature added. The missing check is
  usually on an adjacent endpoint nobody thought about.
- Unsafe deserialisation of untrusted input.
- New dependencies: provenance, maintenance, licence, transitive weight.
- Rate limiting on anything publicly reachable.
- PII in logs, traces or analytics.
- Crypto from standard libraries only. No ECB, no static IVs, no hand-rolled anything.

### §4 Contracts

For any change to `contracts/`:

- Is it breaking? Removing/renaming a field, narrowing a type, making optional required, removing
  an enum value, tightening validation.
- **Semantic changes without syntactic ones** — a field whose meaning changed but whose name and
  type did not. No tool detects these. They must be declared, and asking is often the only way to
  find one.
- Version bumped? Migration note present? Consumer acknowledgements from everyone listed?

### §5 Writing findings

```json
{
  "severity": "high",
  "category": "correctness",
  "file": "web/lib/pagination.ts",
  "line": 42,
  "summary": "Offset calculation drops the last page when total is an exact multiple of page size",
  "failure_scenario": "40 items, pageSize 20: page 2 requests offset 40 and returns empty. Users cannot reach the final page.",
  "suggested_owner": "bot-02-web-edge"
}
```

- Concrete failure scenario, with inputs.
- Name the owning bot — you are not fixing it.
- **Do not inflate severity to force attention.** It works exactly once, and afterwards your
  criticals get treated like your mediums.

### §6 What to let go

A review that flags everything gets skimmed, and a skimmed review catches nothing. Spend
credibility where it matters.

- Style an autoformatter already settled
- Naming that is merely not your preference
- Patterns consistent with surrounding code, even where you would choose differently —
  **consistency beats your taste**
- Optimisation on paths that are not hot
- Refactoring outside the change's scope — note it separately, do not block

### §7 Verdicts

| Verdict | When |
|---|---|
| `approve` | Correct, verified, no significant findings |
| `request_changes` | Findings the owning bot should address; not urgent |
| `block` | Security issue, data loss risk, dishonest receipt, or a gate bypass |

A dishonest receipt is a `block` even when the code is fine. The code being fine this time is
luck, not process — and the process is what you are reviewing.

---

## Failure modes

| Mode | Symptom | Fix |
|---|---|---|
| Style-first review | Real bugs ship under a pile of naming comments | Priority order, always |
| Reviewer fixes it | No independent check remains | Report with a scenario; let the owner fix |
| Vague findings | Ignored or misunderstood | Concrete failure scenario |
| Severity inflation | Criticals stop being read | Rank honestly |
| Receipt skimmed | Unverified work approved | §1 — it is the highest-yield check |
| Tests not read | Assertions that never fire | Read them |
| Blocking on taste | Slow reviews, friction, no safety gained | §6 |
</skill>
