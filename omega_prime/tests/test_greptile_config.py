"""Configured review rules use valid severities and resolve to repository scopes."""

from __future__ import annotations

import json
from pathlib import Path

OMEGA_PRIME = Path(__file__).resolve().parents[1]
ROOT = OMEGA_PRIME.parent


SEVERITIES = frozenset({"low", "medium", "high"})


def _config() -> dict:
    path = ROOT / ".greptile" / "config.json"
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def test_rules_have_shape_and_scope() -> None:
    config = _config()
    rules = config.get("rules", [])
    seen_ids = set()
    for rule in rules:
        assert isinstance(rule.get("rule"), str) and rule["rule"]
        assert rule.get("severity", "medium") in SEVERITIES
        if "id" in rule:
            assert rule["id"] not in seen_ids
            seen_ids.add(rule["id"])
        for scope in rule.get("scope", []):
            assert ".." not in scope
            assert (ROOT / scope.split("*")[0]).exists()
