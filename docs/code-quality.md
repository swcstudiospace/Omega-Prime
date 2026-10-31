# Code quality ratchet

Omega Prime raises its lint and type bar one small, reviewable step at a time.
Each step only **adds** a rule family or a strict package. Nothing is ever
removed to make a check pass. A ratchet test
(`omega_prime/tests/test_quality_ratchet.py`) fails if anyone drops a selected
family from `pyproject.toml`.

## Where it runs

The `lint` job (`ruff check` and `ruff format --check`) and the `types` job
(`mypy omega_prime/`) in `.github/workflows/ci.yml` enforce both lists. They
run on the self-hosted `[self-hosted, linux, x64, omega-ci]` pool, and
`omega_prime/scripts/ci_receipt.py` records a receipt for each run.

Run the same checks locally from the repo `.venv`:

```bash
python -m ruff check omega_prime/
python -m ruff format --check omega_prime/
python -m mypy omega_prime/
python -m pytest -q omega_prime/tests/test_quality_ratchet.py
```

## Ruff rule families

| Family | Why |
|---|---|
| `E4 E7 E9 F` | pyflakes and pycodestyle errors: syntax, undefined names, unused imports |
| `I` | import order (`omega_prime` is first-party) |
| `UP` | modern syntax for the minimum Python (3.11) |
| `B` | bugbear: mutable defaults, loop-variable capture, `assert False` |
| `SIM` | simpler control flow |
| `RUF` | ruff-specific correctness (mutable class defaults, ambiguous unicode) |
| `C4` | comprehensions: no `dict()`/`list()` wrappers or pointless comprehensions |
| `PIE` | `startswith`/`endswith` with a tuple, no `**{}` or reimplemented builtins |
| `RSE` | `raise Error`, not `raise Error()` with no arguments |
| `LOG` | correct `logging` API use (no direct `Logger()` construction) |
| `G` | logging calls: `logger.exception` over `error(..., exc_info=True)`, no eager formatting |
| `PGH` | blanket `# type: ignore` / `# noqa` must name a code |

Families we considered and skipped for now:

- `FLY` would turn deliberate `"".join([...])` secret-shaped test strings back into literals that secret scanners flag.
- `ICN` would rename `import datetime` to `dt` across the codebase for no correctness gain.

The next steps (timezone-aware datetimes, boundary error handling, dead code,
`pathlib` and typing-only imports) are tracked as nodes N3 to N6 of the
00073 uplift plan.

## Adding a family

1. Measure it: `ruff check --select <FAMILY> --statistics omega_prime/`.
2. Fix every hit, or use a targeted `# noqa: CODE - reason` where the rule is
   wrong for that line. Never use a broad per-file ignore.
3. Add the family to `select` in `pyproject.toml` and to `RUFF_FAMILIES` in
   the ratchet test. Add a row here.
