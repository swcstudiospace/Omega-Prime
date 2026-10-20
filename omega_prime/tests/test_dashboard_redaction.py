# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Tests for redaction boundaries, bridge persistence, and config allowlisting."""

from __future__ import annotations

import json
import os

from omega_prime.dashboard.config import DashboardConfig, load_dashboard_config
from omega_prime.dashboard.redact import redact_line, redact_value
from omega_prime.dashboard.status_file import read_tasks, write_tasks

SECRETS = (
    "sk-FAKE1234567890abcd",
    "xai-FAKEFAKEFAKE1",
    "ghp_FAKEFAKEFAKEFAKEFAKE",
    "AKIAFAKEFAKEFAKEFAKE",
    "Bearer FAKE.TOKEN.VALUE",
    "TOKEN=superfakevalue",
    "API_KEY: fakekeyvalue123",
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
    env = {"OMEGA_PRIME_OMP_ALLOW_CHATGPT": "1", "GITHUB_PAT_DESK_LEAD": "ghp_LongFakeTokenValue"}
    text = "job-5563f17a ran with ghp_LongFakeTokenValue"
    result = redact_value(text, env=env)
    assert "job-5563f17a" in result
    assert "ghp_LongFakeTokenValue" not in result


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
    assert list(path.parent.iterdir()) == [path]
    result = read_tasks(path)
    assert result.status == "ok" and result.data is not None
    assert all(secret not in json.dumps(result.data) for secret in SECRETS)
    path.write_text(json.dumps({"version": 1, "extra": "kept"}))
    assert read_tasks(path).data == {"version": 1, "extra": "kept"}


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
