---
name: rust
description: Writing and reviewing Rust in this codebase. Use for any change to .rs files, Cargo manifests, or crate structure.
bots: [bot-00-omega-prime]
---
<skill name="rust" version="1">
# Rust

## L1 — Summary

The compiler catches most of what other languages catch at runtime. That means **a fight with the
borrow checker is usually information, not an obstacle** — it is describing a real aliasing or
lifetime problem in your design.

**Decision tree:**

```
Fighting the borrow checker?
├─▶ Do not reach for clone()/Rc<RefCell<>>/unsafe to make it compile. §2
│   Ask what it is telling you about ownership. The fix is usually structural.
│
Writing async?
├─▶ §4. Blocking in an async context stalls the executor — the failure is
│   a mysterious latency cliff, not an error.
│
Adding a dependency?
├─▶ Check Cargo.toml first. Then omega_prime/skills/security-supply-chain/SKILL.md.
│
Reaching for unsafe?
└─▶ Almost certainly no. §5. If genuinely yes, write the SAFETY comment
    stating the invariant you are upholding, or you have not finished.
```

**Verification:** `cargo test`, `cargo clippy -- -D warnings`, `cargo fmt --check`. All three,
every time. Clippy catches real bugs, not only style.

---

## L2 — Method

### §1 Error handling

- **Libraries:** `thiserror`, concrete error enums. Callers need to match on what went wrong.
- **Applications:** `anyhow`, with `.context()` at each layer. Nobody can act on `NotFound` with
  no indication of what was not found.
- **`unwrap()` / `expect()`:** only where the invariant is genuinely guaranteed, and then
  `expect()` with a message stating why. `unwrap()` in a service is a panic waiting for the input
  that violates your assumption.
- **Never** `let _ = fallible()`. Handle it or propagate it.

```rust
// good — the caller can act on this
#[derive(Debug, thiserror::Error)]
pub enum StoreError {
    #[error("account {0} not found")]
    NotFound(AccountId),
    #[error("database unavailable")]
    Unavailable(#[source] sqlx::Error),
}
```

### §2 Ownership

The borrow checker is a design review that runs on every build.

| You reached for | It usually means | Consider |
|---|---|---|
| `.clone()` to satisfy it | Unclear ownership | Borrow, or restructure so one owner is obvious |
| `Rc<RefCell<T>>` | Shared mutable state | Does it need sharing? Message passing? |
| `Arc<Mutex<T>>` everywhere | Contention and deadlock risk | Channels, or narrow the critical section |
| `'static` bounds spreading | Lifetime escaping its scope | Own the data, or scope the task |
| `unsafe` | Fighting the model | See §5 — nearly always unnecessary |

Cloning for genuine reasons is fine. Cloning to silence the compiler is deferring a design
question, and it comes back at the point where the clone diverges from the original.

### §3 Types

Use the type system for correctness rather than documentation.

```rust
// newtypes — the compiler now prevents the argument swap
pub struct AccountId(pub Uuid);
pub struct UserId(pub Uuid);

// make invalid states unrepresentable
enum Connection { Disconnected, Connecting { since: Instant }, Connected { session: Session } }
// beats: struct Connection { connected: bool, session: Option<Session>, since: Option<Instant> }
```

The second form permits `connected: true, session: None`, and somewhere in the codebase there is
an `.unwrap()` that assumes it cannot happen.

### §4 Async

- One runtime, chosen once. Do not mix `tokio` and `async-std`.
- **Never block in an async context.** `std::fs`, `std::thread::sleep`, CPU-bound loops and
  blocking DB drivers all stall the executor thread. Use `tokio::task::spawn_blocking`.
  The symptom is latency that degrades under load with no error anywhere — hard to diagnose after
  the fact, trivial to avoid up front.
- Cancellation is real: a future dropped at an await point stops there. Anything requiring cleanup
  needs a guard, not code after the await.
- `Send + Sync` bounds propagate. Fix them at the root rather than adding bounds up the stack.

### §5 Unsafe

Requirements, all of them:

1. There is no safe alternative — including a well-maintained crate that already did this
2. A `// SAFETY:` comment states the invariant being upheld and why it holds
3. The unsafe block is as small as possible
4. It is behind a safe abstraction that cannot be misused
5. `cargo miri test` covers it where applicable

Without all five, it is not ready. FFI is the common legitimate case; "the compiler was
complaining" is not.

### §6 Testing

- Unit tests in `#[cfg(test)] mod tests` beside the code.
- Integration tests in `tests/`, exercising the public API.
- `proptest` for anything with interesting input space — parsers, encoders, state machines.
- Async tests: `#[tokio::test]`.
- Database tests hit a real database. A mocked database confirms the mock matches the code, which
  was never the question.

### §7 Performance

**Measure first.** Rust is fast enough that intuition about the bottleneck is usually wrong, and
optimising the wrong thing costs clarity for nothing.

- `criterion` for benchmarks. A claim of "faster" needs a number before and after (G-2).
- Always profile in release; debug performance is meaningless.
- Allocation in a hot loop is the usual real culprit — `String` in a loop, `collect()` into an
  intermediate, `Box<dyn>` on a hot path.

---

## Failure modes

| Mode | Symptom | Fix |
|---|---|---|
| Clone-to-compile | `.clone()` scattered with no reason | Ask what ownership should be |
| `unwrap()` in a service | Panics on unexpected input | Propagate, or `expect()` with a reason |
| Blocking in async | Latency cliff under load, no errors | `spawn_blocking` |
| Mixed runtimes | Panics about no reactor running | One runtime |
| Clippy ignored | Real bugs in `-D warnings` output | Run it, fix it |
| Unsafe without SAFETY | Unreviewable, unauditable | §5, all five requirements |
| Debug benchmarks | Wrong conclusions about hot paths | `--release`, always |
</skill>
