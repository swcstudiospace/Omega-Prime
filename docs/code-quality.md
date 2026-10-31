# Code quality ratchet

Omega Prime raises its lint and type bar one small, reviewable step at a time.
Each step only **adds** a rule family or a strict package. Nothing is ever
removed to make a check pass. A ratchet test
(`omega_prime/tests/test_quality_ratchet.py`) fails if anyone drops a selected
family, a strict package or a strict flag from `pyproject.toml`.

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

## Strict typing

`mypy` cannot switch on `strict` for one module, so `pyproject.toml` has one
override that sets the strict flags (`disallow_untyped_defs`,
`disallow_any_generics`, `warn_return_any`, `warn_unused_ignores`,
`strict_equality`, `extra_checks` and the rest; `ignore_missing_imports =
false`) for these packages:

| Package | Since |
|---|---|
| `omega_prime.eventstream` | eventstream contracts |
| `omega_prime.audit` | 00073 N2 |
| `omega_prime.ci_runners` | 00073 N2 |
| `omega_prime.commands` | 00073 N2 |
| `omega_prime.contracts` | 00073 N2 |
| `omega_prime.credentials` | 00073 N2 |
| `omega_prime.hosting` | 00073 N2 |
| `omega_prime.integrations` | 00073 N2 |
| `omega_prime.quotas` | 00073 N2 |
| `omega_prime.redteam` | 00073 N2 |
| `omega_prime.sandbox` | 00073 N2 |
| `omega_prime.skills` | 00073 N2 |
| `omega_prime.skills_runtime` | 00073 N2 |
| `omega_prime.telemetry` | 00073 N2 |
| `omega_prime.tooling` | 00073 N2 |

The rest of the codebase runs with `check_untyped_defs`. To find the next
package to promote, run `python -m mypy --strict omega_prime/ | grep -oE
"^omega_prime/[a-z_]+" | sort | uniq -c | sort -n`. Pick one with few
errors, fix them (types, not `# type: ignore`), and add it to the override,
to `STRICT_PACKAGES` in the ratchet test and to this table.

## Adding a family

1. Measure it: `ruff check --select <FAMILY> --statistics omega_prime/`.
2. Fix every hit, or use a targeted `# noqa: CODE - reason` where the rule is
   wrong for that line. Never use a broad per-file ignore: the ratchet test
   rejects any `ignore` or `per-file-ignores` entry that is a prefix of a
   ratcheted family (`"C"` covers `C4`, `"ALL"` covers everything).
   Review autofixes in security code by hand. A `C4` fix once turned a
   deliberate all-tokens comparison into a short-circuiting `any()`; the
   callback in `omega_prime/ci_runners/app.py` now uses an explicit loop and
   a test counts the comparisons.
3. Add the family to `select` in `pyproject.toml` and to `RUFF_FAMILIES` in
   the ratchet test. Add a row here.
