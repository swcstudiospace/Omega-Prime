---
name: python
description: Writing and reviewing Python in this codebase. Use for any change to .py files, pyproject.toml, or Python packaging.
bots: [bot-00-omega-prime]
---
<skill name="python" version="1">
# Python

## L1 — Summary

Python gives you almost no compile-time safety, so the discipline has to be deliberate: **types,
tests and explicit boundaries**. Code that "runs" proves far less here than in Rust or Swift.

**Decision tree:**

```
Writing new code?
├─▶ Type hints on every public function. mypy/pyright must pass. §1
│
Handling errors?
├─▶ Never bare `except:`. Never catch-log-continue. §2
│
Writing async?
├─▶ §4. Blocking in async stalls the loop, same as Rust, same silent symptom.
│
Adding a dependency?
├─▶ Check pyproject.toml. Then omega_prime/skills/security-supply-chain/SKILL.md.
│
Touching data or a migration?
└─▶ Migrations must be reversible, or explicitly approved.
```

**Verification:** `pytest`, `ruff check`, `mypy` (or `pyright`) on changed modules. A passing
import is not a passing test.

---

## L2 — Method

### §1 Typing

Type hints on every public function, every dataclass, every module-level constant that is not
obvious.

```python
def fetch_account(
    account_id: AccountId, *, include_closed: bool = False
) -> Account | None: ...
```

- `X | None`, not bare `Optional` imports in new code (3.10+).
- `NewType` for identifiers that should not be interchangeable:
  `AccountId = NewType("AccountId", UUID)`. The type checker then catches the argument swap.
- `Protocol` for structural interfaces rather than ABCs, unless you need the runtime check.
- `TypedDict` for dict-shaped payloads at boundaries.
- `Literal` for finite string sets.
- **Never** `Any` to silence the checker. If a type is genuinely dynamic, say so with a comment.

`mypy --strict` on new modules. Existing modules: do not weaken settings to make a change pass.

### §2 Errors

```python
# bad — swallows everything including KeyboardInterrupt and real bugs
try:
    result = risky()
except:
    logger.error("failed")

# bad — the caller believes it succeeded
try:
    result = risky()
except ValueError:
    logger.error("failed")
    result = None
```

- Catch the specific exception you can act on.
- Never catch and continue as though nothing happened — either handle it meaningfully or let it
  propagate to something that can.
- Custom exceptions for domain errors, inheriting from a package base.
- `raise ... from e` to preserve the chain. Losing the original traceback makes the incident
  harder for whoever picks it up.

### §3 Structure

- `pyproject.toml` only. No `setup.py`, no `requirements.txt` in new work.
- `uv` for dependency resolution and locking; commit `uv.lock`.
- Keep modules focused. A 2,000-line `utils.py` is where code goes to become undiscoverable.
- Dataclasses or Pydantic models for structured data — not dicts passed between layers. A dict
  has no contract and no type checking, and its keys drift.
- `from __future__ import annotations` for cleaner forward references.

### §4 Async

- `asyncio` throughout. Do not mix with threads unless you know precisely why.
- **Never block the loop:** `time.sleep`, `requests`, synchronous DB drivers, CPU-bound work.
  Use `asyncio.to_thread` or a process pool. The symptom is the same as Rust's — latency degrades
  under load with nothing in the logs.
- `asyncio.TaskGroup` (3.11+) over bare `gather` — it cancels siblings on failure rather than
  leaving orphans running.
- Always `await` or explicitly store a task. A fire-and-forget coroutine can be garbage collected
  mid-execution, which produces a warning nobody reads and work that silently did not happen.

### §5 Testing

- `pytest`, with fixtures for setup. Parametrise instead of copy-pasting cases.
- **Assert on behaviour, not implementation.** A test asserting internal call order breaks on
  every refactor and catches no bugs.
- Mock at boundaries only — the network, the clock, the filesystem. Mocking your own code means
  testing your mocks.
- Real database for anything touching persistence.
- `hypothesis` for input-space-heavy code.
- Coverage is a signal, not a target. 100% coverage with weak assertions is worse than 70% with
  strong ones, because it looks safe.

**Assertions that never fire are common.** Read the test before trusting it:

```python
def test_rejects_negative():
    with pytest.raises(ValueError):
        validate(-1)  # passes if validate raises ValueError for ANY reason,
        # including a typo in the function name
```

### §6 Performance

- Profile before optimising — `cProfile`, `py-spy` for live processes.
- The usual real culprits: N+1 queries, repeated work in a loop, unnecessary serialisation.
- Numeric work belongs in `numpy`/`polars` rather than Python loops.
- Before reaching for multiprocessing, check whether the work is actually CPU-bound. Most
  "slow Python" is I/O waiting.

---

## Failure modes

| Mode | Symptom | Fix |
|---|---|---|
| Bare `except:` | Bugs and Ctrl-C swallowed | Catch what you can act on |
| Catch-log-continue | Silent data corruption downstream | Handle or propagate |
| `Any` to pass mypy | Type checking disabled where it mattered | Model the type properly |
| Blocking in async | Latency cliff, no errors | `asyncio.to_thread` |
| Dicts between layers | Key drift, no type safety | Dataclass or Pydantic model |
| Over-mocking | Tests pass, production breaks | Mock boundaries only |
| Unawaited coroutine | Work silently not done | Await it or hold the task |
| Coverage as a target | High coverage, weak assertions | Read the tests |
</skill>
