"""State machine tests: legal map, rejections, timestamped audit rows."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from sidecar.state import (
    BLOCKED_CAUSES,
    KNOWN_CAUSES,
    LEGAL_TRANSITIONS,
    STATES,
    transition,
    transitions_path,
)


def _rows(directory) -> list[dict]:
    path = transitions_path(directory)
    assert path.is_file()
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_all_five_states_reachable_per_legal_map(tmp_path):
    seen: set[str] = set()
    for source, targets in LEGAL_TRANSITIONS.items():
        for target in sorted(targets):
            cause = "roe-missing" if target in ("BLOCKED", "ERROR") else "re-evaluated"
            if source == "RUNNING" and target == "IDLE":
                cause = "run-completed"
            if source == "IDLE" and target == "RUNNING":
                cause = "run-started"
            if target == "DEGRADED":
                cause = "drift-detected"
            row = transition(source, target, cause, directory=tmp_path)
            assert row == {
                "from": source,
                "to": target,
                "cause": cause,
                "ts": row["ts"],
            }
            seen.add(target)
    assert seen == set(STATES)


def test_illegal_transitions_raise_and_log_nothing(tmp_path):
    illegal = [
        ("RUNNING", "RUNNING", "run-started"),
        ("DEGRADED", "RUNNING", "run-started"),
        ("DEGRADED", "DEGRADED", "drift-detected"),
        ("BLOCKED", "RUNNING", "run-started"),
        ("BLOCKED", "DEGRADED", "drift-detected"),
        ("BLOCKED", "ERROR", "protocol-fault"),
        ("ERROR", "RUNNING", "run-started"),
        ("ERROR", "DEGRADED", "drift-detected"),
        ("ERROR", "BLOCKED", "kill-switch-set"),
    ]
    for source, target, cause in illegal:
        with pytest.raises(ValueError, match="illegal sidecar transition"):
            transition(source, target, cause, directory=tmp_path)
    assert not transitions_path(tmp_path).exists()


def test_unknown_states_rejected_loudly(tmp_path):
    with pytest.raises(ValueError, match="unknown sidecar state"):
        transition("BOOT", "IDLE", "re-evaluated", directory=tmp_path)
    with pytest.raises(ValueError, match="unknown sidecar state"):
        transition("IDLE", "FLYING", "re-evaluated", directory=tmp_path)


def test_unknown_causes_rejected_loudly(tmp_path):
    with pytest.raises(ValueError, match="unknown sidecar cause"):
        transition("IDLE", "RUNNING", "vibes", directory=tmp_path)


def test_blocked_and_error_require_allowlisted_causes(tmp_path):
    for target in ("BLOCKED", "ERROR"):
        with pytest.raises(ValueError, match=f"cannot enter {target}"):
            transition("IDLE", target, "run-started", directory=tmp_path)
    for cause in BLOCKED_CAUSES:
        assert cause in KNOWN_CAUSES
        transition("IDLE", "BLOCKED", cause, directory=tmp_path)


def test_every_logged_row_carries_rfc3339_timestamp(tmp_path):
    transition("IDLE", "RUNNING", "run-started", directory=tmp_path)
    transition("RUNNING", "BLOCKED", "kill-switch-set", directory=tmp_path)
    rows = _rows(tmp_path)
    assert len(rows) == 2
    for row in rows:
        parsed = datetime.fromisoformat(row["ts"])
        assert parsed.tzinfo is not None


def test_reports_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("OMEGA_PRIME_SIDECAR_REPORTS_DIR", str(tmp_path))
    transition("IDLE", "IDLE", "re-evaluated")
    assert transitions_path(tmp_path).is_file()
