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
    codes = set(lint.get("ignore", []))
    for per_file in lint.get("per-file-ignores", {}).values():
        codes.update(per_file)
    return sorted(
        (code, family)
        for code in codes
        for family in RUFF_FAMILIES
        if code == "ALL" or family.startswith(code)
    )


def test_ruff_does_not_ignore_a_ratcheted_family_wholesale() -> None:
    hits = _wholesale_ignored(_pyproject()["tool"]["ruff"]["lint"])
    assert not hits, hits


def test_wholesale_guard_catches_broader_prefixes() -> None:
    broad = {"per-file-ignores": {"*.py": ["C"]}, "ignore": ["E501"]}
    assert ("C", "C4") in _wholesale_ignored(broad)
    assert _wholesale_ignored({"ignore": ["ALL"]})
    assert _wholesale_ignored({"ignore": ["PG"]}) == [("PG", "PGH")]
    assert not _wholesale_ignored({"ignore": ["E501", "C401", "B008"]})


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
