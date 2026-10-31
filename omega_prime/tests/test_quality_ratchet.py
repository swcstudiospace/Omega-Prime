"""Ratchet guard: lint and type strictness only ever go up.

See docs/code-quality.md. Removing a family from pyproject.toml must be a
deliberate, reviewed change to this test, never a quiet way to turn CI green.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

RUFF_FAMILIES = frozenset(
    {
        "E4",
        "E7",
        "E9",
        "F",
        "I",
        "UP",
        "B",
        "SIM",
        "RUF",
        "C4",
        "PIE",
        "RSE",
        "LOG",
        "G",
        "PGH",
    }
)


STRICT_PACKAGES = frozenset(
    {
        "omega_prime.eventstream.*",
        "omega_prime.audit.*",
        "omega_prime.ci_runners.*",
        "omega_prime.commands.*",
        "omega_prime.contracts.*",
        "omega_prime.credentials.*",
        "omega_prime.hosting.*",
        "omega_prime.integrations.*",
        "omega_prime.quotas.*",
        "omega_prime.redteam.*",
        "omega_prime.sandbox.*",
        "omega_prime.skills.*",
        "omega_prime.skills_runtime.*",
        "omega_prime.telemetry.*",
        "omega_prime.tooling.*",
    }
)

# The flags that make a mypy override "strict" here (mypy cannot set
# ``strict`` per module). Each must stay switched on for STRICT_PACKAGES.
STRICT_FLAGS = (
    "disallow_any_generics",
    "disallow_subclassing_any",
    "disallow_untyped_calls",
    "disallow_untyped_defs",
    "disallow_incomplete_defs",
    "check_untyped_defs",
    "disallow_untyped_decorators",
    "warn_return_any",
    "warn_unused_ignores",
    "strict_equality",
    "extra_checks",
)


def _pyproject() -> dict[str, Any]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_ruff_selects_every_ratcheted_family() -> None:
    selected = set(_pyproject()["tool"]["ruff"]["lint"]["select"])
    missing = RUFF_FAMILIES - selected
    assert not missing, f"ruff families dropped from pyproject.toml: {sorted(missing)}"


def _wholesale_ignored(lint: dict[str, Any]) -> list[tuple[str, str]]:
    """(ignored code, family) pairs where an ignore disables a whole family.

    An ignore covers a family when the family's code starts with it, so a
    broader prefix ("C", or "ALL") is caught as well as the exact family.
    Narrower codes such as "C401" stay allowed: they switch off one rule.
    """
    codes: set[str] = set()
    for key in ("ignore", "extend-ignore"):
        codes.update(lint.get(key, []))
    for key in ("per-file-ignores", "extend-per-file-ignores"):
        for per_file in lint.get(key, {}).values():
            codes.update(per_file)
    return sorted(
        (code, family)
        for code in codes
        for family in RUFF_FAMILIES
        if code == "ALL" or family.startswith(code)
    )


def test_ruff_does_not_ignore_a_ratcheted_family_wholesale() -> None:
    ruff = _pyproject()["tool"]["ruff"]
    # Ruff still honours the legacy top-level keys, so check both tables.
    hits = _wholesale_ignored(ruff.get("lint", {})) + _wholesale_ignored(ruff)
    assert not hits, hits


def test_wholesale_guard_catches_broader_prefixes() -> None:
    broad = {"per-file-ignores": {"*.py": ["C"]}, "ignore": ["E501"]}
    assert ("C", "C4") in _wholesale_ignored(broad)
    assert _wholesale_ignored({"ignore": ["ALL"]})
    assert _wholesale_ignored({"ignore": ["PG"]}) == [("PG", "PGH")]
    assert not _wholesale_ignored({"ignore": ["E501", "C401", "B008"]})
    assert ("C4", "C4") in _wholesale_ignored({"extend-ignore": ["C4"]})
    extended = {"extend-per-file-ignores": {"tests/*": ["B"]}}
    assert ("B", "B") in _wholesale_ignored(extended)


def _documented_families() -> set[str]:
    """Families named in the first column of the ruff table in the doc."""
    doc = (ROOT / "docs" / "code-quality.md").read_text(encoding="utf-8")
    families: set[str] = set()
    for line in doc.splitlines():
        cells = line.split("|")
        if len(cells) > 2 and cells[1].strip().startswith("`"):
            families.update(cells[1].strip().strip("`").split())
    return families


def test_every_ratcheted_family_is_documented() -> None:
    missing = RUFF_FAMILIES - _documented_families()
    assert not missing, sorted(missing)


def _strict_override() -> dict[str, Any]:
    overrides = _pyproject()["tool"]["mypy"].get("overrides", [])
    for override in overrides:
        if "omega_prime.eventstream.*" in override.get("module", []):
            return dict(override)
    raise AssertionError("the strict mypy override is gone from pyproject.toml")


def test_mypy_strict_override_keeps_every_ratcheted_package() -> None:
    missing = STRICT_PACKAGES - set(_strict_override()["module"])
    assert not missing, f"packages dropped from the strict override: {sorted(missing)}"


def test_mypy_strict_override_keeps_every_strict_flag() -> None:
    override = _strict_override()
    off = [flag for flag in STRICT_FLAGS if override.get(flag) is not True]
    assert not off, f"strict flags switched off: {off}"
    assert override.get("ignore_missing_imports") is False


def test_every_strict_package_is_documented() -> None:
    doc = (ROOT / "docs" / "code-quality.md").read_text(encoding="utf-8")
    missing = [
        p for p in sorted(STRICT_PACKAGES) if f"`{p.removesuffix('.*')}`" not in doc
    ]
    assert not missing, missing
