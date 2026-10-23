# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Tests for redaction boundaries, bridge persistence, and config allowlisting."""

from __future__ import annotations

import json
import os
from secrets import token_hex

from omega_prime.dashboard.config import DashboardConfig, load_dashboard_config
from omega_prime.dashboard.redact import redact_line, redact_value
from omega_prime.dashboard.status_file import read_tasks, write_tasks

SECRETS = (
    "sk" + "-" + token_hex(10),
    "xai" + "-" + token_hex(8),
    "gh" + "p_" + "x" * 36,
    "AKIA" + token_hex(8).upper(),
    "Bearer " + token_hex(9) + ".x",
    "TOKEN=" + token_hex(8),
    "API_KEY: " + token_hex(8),
)


def document() -> dict:
    return {
        "version": 1,
        "updated_at": "now",
        "unknown": "drop",
        "tasks": [
            {
                "id": "1",
                "title": "task",
                "owner": {"kind": "agent", "ref": "r"},
                "state": "working",
                "started_at": None,
                "last_activity_at": None,
                "progress": None,
                "note": " ".join(SECRETS),
                "links": [],
            }
        ],
        "waiting_on_ove": [],
        "finished": [],
    }


def test_flag_like_env_values_are_not_secret_substrings() -> None:
    pat = "gh" + "p_" + "L" * 18
    env = {
        "OMEGA_PRIME_OMP_ALLOW_CHATGPT": "1",
        "GITHUB_PAT_DESK_LEAD": pat,
    }
    text = "job-5563f17a ran with " + pat
    result = redact_value(text, env=env)
    assert "job-5563f17a" in result
    assert pat not in result


def test_embedded_sk_keys_with_digits_or_case_are_redacted() -> None:
    embedded = "sk" + "-" + token_hex(8)
    text = "prefix" + embedded
    result = redact_value(text, env={})
    assert embedded not in result
    assert "[REDACTED]" in result

    caps = "sk" + "-" + "Abc" + token_hex(6)
    text = "prefix" + caps
    result = redact_value(text, env={})
    assert caps not in result
    assert "[REDACTED]" in result

    us = "sk" + "-" + token_hex(4) + "_" + token_hex(4)
    text = "prefix" + us
    result = redact_value(text, env={})
    assert us not in result
    assert "[REDACTED]" in result


def test_benign_word_internal_sk_names_survive() -> None:
    text = "branch feat/task-dashboard phase task-dashboard-ui"
    assert redact_value(text, env={}) == text


def test_denylist_and_benign_keys() -> None:
    result = redact_value(
        {
            "github_pat": "secret",
            "auth_token": "secret",
            "apiKey": "secret",
            "COOKIE_X": "secret",
            "path_or_url": "safe",
            "last_activity_at": "safe",
            "job_id": "safe",
            "terminal_title": "safe",
        },
        env={},
    )
    assert all(
        result[k] == "[REDACTED]"
        for k in ("github_pat", "auth_token", "apiKey", "COOKIE_X")
    )
    assert all(
        result[k] == "safe"
        for k in ("path_or_url", "last_activity_at", "job_id", "terminal_title")
    )


def test_assignment_secrets_are_redacted_in_strings_and_nested_values() -> None:
    values = (
        ("password=hunter2", "password=[REDACTED]"),
        ("PAT=ghp_x", "PAT=[REDACTED]"),
        ("COOKIE: c1", "COOKIE: [REDACTED]"),
        ("token: abc123", "token: [REDACTED]"),
        ("secret='qwerty'", "secret='[REDACTED]'"),
        ("bearer xyz", "bearer xyz"),
    )
    for value, expected in values:
        redacted = redact_value(value, env={})
        assert redacted == expected
        assert (
            redact_value({"task": {"note": value}}, env={})["task"]["note"] == expected
        )
        assert redact_value(redacted, env={}) == redacted
    assert redact_value("path=/etc/x monkey=see author: Ove", env={}) == (
        "path=/etc/x monkey=see author: Ove"
    )
    assert redact_value("task-dashboard password=hunter2", env={}) == (
        "task-dashboard password=[REDACTED]"
    )


def test_line_first_line_and_cap() -> None:
    assert redact_line("first\nsecond") == "first"
    assert redact_line("x" * 150) == "x" * 120
    assert redact_line(42) == 42


def test_writer_redacts_permissions_atomic_and_unknown_keys(tmp_path) -> None:
    path = tmp_path / "nested" / "tasks.json"
    write_tasks(document(), path=path)
    assert os.stat(path.parent).st_mode & 0o777 == 0o700
    assert os.stat(path).st_mode & 0o777 == 0o600
    raw = path.read_text()
    assert all(secret not in raw for secret in SECRETS)
    assert "unknown" not in raw
    assert sorted(entry.name for entry in path.parent.iterdir()) == [
        "tasks.json",
        "tasks.json.lock",
    ]
    result = read_tasks(path)
    assert result.status == "ok" and result.data is not None
    assert all(secret not in json.dumps(result.data) for secret in SECRETS)
    path.write_text(json.dumps({"version": 1, "extra": "kept"}))
    assert read_tasks(path).status == "unreadable"
    valid = document()
    valid["extra"] = "kept"
    path.write_text(json.dumps(valid))
    result = read_tasks(path)
    assert result.status == "ok" and "extra" not in (result.data or {})


def test_bridge_rejects_bad_sequence_and_registry_without_overwriting(tmp_path) -> None:
    path = tmp_path / "tasks.json"
    write_tasks(document(), path=path)
    original = path.read_bytes()
    for seq in (True, 0, -1, 1.5):
        invalid = document()
        invalid["tasks"][0]["seq"] = seq
        try:
            write_tasks(invalid, path=path)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid sequence accepted")
        assert path.read_bytes() == original


def test_bridge_redacts_registry_history_values(tmp_path) -> None:
    import hashlib

    path = tmp_path / "tasks.json"
    data = document()
    identity = data["tasks"][0]["id"]
    key = hashlib.sha256(f"tasks:{identity}".encode()).hexdigest()
    data["tasks"][0]["seq"] = 1
    data.update(
        next_seq=2,
        identity_registry={
            key: {
                "seq": 1,
                "title": "TOKEN=secret",
                "timeline": [
                    {"at": "2026-10-21T00:00:00+00:00", "state": "TOKEN=secret"}
                ],
                "last_state": "TOKEN=secret",
            }
        },
    )
    write_tasks(data, path=path)
    raw = path.read_text()
    assert "TOKEN=secret" not in raw
    assert key in raw


def test_reader_missing_malformed_and_writer_validation(tmp_path) -> None:
    assert read_tasks(tmp_path / "absent").status == "missing"
    bad = tmp_path / "bad"
    bad.write_text("{")
    assert read_tasks(bad).status == "unreadable"
    invalid = document()
    invalid["tasks"][0]["state"] = "surprise"
    try:
        write_tasks(invalid, path=tmp_path / "no")
    except ValueError as exc:
        assert "state" in str(exc)
    else:
        raise AssertionError("invalid state accepted")


def test_config_defaults_overrides_allowlist_and_bad_file(tmp_path) -> None:
    path = tmp_path / "settings.json"
    assert load_dashboard_config(path) == DashboardConfig()
    path.write_text(
        json.dumps(
            {
                "secret": "never expose",
                "dashboard": {
                    "window_hours": 3,
                    "quiet_minutes": 4,
                    "fleet_enabled": True,
                    "token": "no",
                },
            }
        )
    )
    assert load_dashboard_config(path) == DashboardConfig(3, 4, True)
    path.write_text("not json")
    assert load_dashboard_config(path) == DashboardConfig()


def test_standalone_fine_grained_github_tokens_are_redacted() -> None:
    token = "github_pat_" + "A1b2C3d4E5" * 4
    note = f"pushed with {token} then retried"
    assert redact_value(note, env={}) == "pushed with [REDACTED] then retried"
    nested = redact_value({"task": {"detail": note, "links": [token]}}, env={})
    assert token not in str(nested)
    # The prefix alone (no token body) is a name, not a credential.
    assert redact_value("github_pat_", env={}) == "github_pat_"
