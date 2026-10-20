# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Fake-transport tests for remote dashboard collectors."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from omega_prime.dashboard.collectors.fleet import make_fleet_collector
from omega_prime.dashboard.collectors.github import make_github_collector


def test_github_missing_token_makes_no_calls() -> None:
    calls: list[Any] = []

    def fake_transport(url: str, headers: Mapping[str, str]) -> tuple[int, bytes]:
        calls.append((url, headers))
        return (200, b"[]")

    result = make_github_collector(env={}, transport=fake_transport)()
    assert result.status == "unavailable"
    assert calls == []


def test_github_collects_and_caches_prs() -> None:
    now = [0]
    calls: list[Any] = []

    def transport(url: str, headers: Any) -> tuple[int, bytes]:
        calls.append((url, headers))
        if "/pulls?" in url:
            return 200, json.dumps(
                [
                    {
                        "number": 1,
                        "title": "one",
                        "head": {"ref": "fix/a", "sha": "abc"},
                        "draft": False,
                        "mergeable": None,
                        "updated_at": "now",
                        "html_url": "https://example/pr/1",
                    }
                ]
            ).encode()
        if "/check-runs" in url:
            return 200, b'{"check_runs":[{"conclusion":"success"}]}'
        return (
            200,
            b'[{"user":{"login":"greptile-apps[bot]"},"created_at":"2026-01-01T00:00:00Z","body":"Confidence score: 4/5"}]',
        )

    collector = make_github_collector(
        env={"GITHUB_PAT_DESK_LEAD": "fake-token"},
        transport=transport,
        now_fn=lambda: __import__("datetime").datetime.fromtimestamp(
            now[0], __import__("datetime").UTC
        ),
    )
    result = collector()
    assert result.items[0]["greptile"] == "4/5"
    assert result.items[0]["ci"] == "pass"
    assert "fake-token" not in json.dumps(result.__dict__)
    count = len(calls)
    collector()
    assert len(calls) == count


def test_github_body_marker_wins_over_bot_comment() -> None:
    def transport(url: str, headers: Any) -> tuple[int, bytes]:
        if "/pulls?" in url:
            return 200, json.dumps(
                [
                    {
                        "number": 1,
                        "head": {"sha": "head"},
                        "body": "<!-- greptile_confidence_score: 3 -->",
                    }
                ]
            ).encode()
        if "/check-runs" in url:
            return 200, b'{"check_runs":[]}'
        if "/comments" in url:
            return 200, json.dumps(
                [
                    {
                        "user": {"login": "greptile-apps[bot]"},
                        "body": "Confidence Score: 5/5",
                        "created_at": "2026-01-01",
                    }
                ]
            ).encode()
        return 200, b"[]"

    result = make_github_collector(
        env={"GITHUB_PAT_DESK_LEAD": "token"}, transport=transport
    )()
    assert result.items[0]["greptile"] == "3/5"


def test_github_ignores_human_score_comment() -> None:
    def transport(url: str, headers: Any) -> tuple[int, bytes]:
        if "/pulls?" in url:
            return 200, b'[{"number":1,"head":{"sha":"head"}}]'
        if "/check-runs" in url:
            return 200, b'{"check_runs":[]}'
        if "/comments" in url:
            return 200, b'[{"user":{"login":"human"},"body":"Confidence Score: 5/5"}]'
        return 200, b"[]"

    result = make_github_collector(
        env={"GITHUB_PAT_DESK_LEAD": "token"}, transport=transport
    )()
    assert result.items[0]["greptile"] == "not reviewed"


def test_github_latest_bot_comment_beats_matching_review() -> None:
    def transport(url: str, headers: Any) -> tuple[int, bytes]:
        if "/pulls?" in url:
            return 200, b'[{"number":1,"head":{"sha":"head"}}]'
        if "/check-runs" in url:
            return 200, b'{"check_runs":[]}'
        if "/comments" in url:
            return 200, json.dumps(
                [
                    {
                        "user": {"login": "greptile-apps[bot]"},
                        "body": "Confidence Score: 2/5",
                        "created_at": "2026-02-01",
                    }
                ]
            ).encode()
        return 200, json.dumps(
            [
                {
                    "user": {"login": "greptile-apps[bot]"},
                    "commit_id": "head",
                    "body": "Confidence Score: 5/5",
                    "submitted_at": "2026-01-01",
                },
                {
                    "user": {"login": "greptile-apps[bot]"},
                    "commit_id": "old",
                    "body": "Confidence Score: 1/5",
                    "submitted_at": "2026-03-01",
                },
            ]
        ).encode()

    result = make_github_collector(
        env={"GITHUB_PAT_DESK_LEAD": "token"}, transport=transport
    )()
    assert result.items[0]["greptile"] == "2/5"


def test_github_review_score_matches_capitalized_score() -> None:
    def transport(url: str, headers: Any) -> tuple[int, bytes]:
        if "/pulls?" in url:
            return 200, b'[{"number":1,"head":{"sha":"head"}}]'
        if "/check-runs" in url:
            return 200, b'{"check_runs":[]}'
        if "/comments" in url:
            return 200, b"[]"
        return (
            200,
            b'[{"user":{"login":"greptile-apps[bot]"},"commit_id":"head","body":"Confidence Score: 4/5"}]',
        )

    result = make_github_collector(
        env={"GITHUB_PAT_DESK_LEAD": "token"}, transport=transport
    )()
    assert result.items[0]["greptile"] == "4/5"


def test_fleet_disallowed_url_is_unavailable_without_request(tmp_path: Path) -> None:
    config = tmp_path / "coordinator.env"
    token = "fleet" + "_" + "t" * 16
    config.write_text(
        f"FLEET_COORDINATOR_URL=not-a-url\nFLEET_COORDINATOR_TOKEN={token}\n",
        encoding="utf-8",
    )
    calls: list[Any] = []

    def transport(url: str, headers: Any) -> tuple[int, bytes]:
        calls.append((url, headers))
        return (200, b"{}")

    result = make_fleet_collector(
        enabled=True,
        env_file=config,
        transport=transport,
    )()
    assert result.status == "unavailable"
    assert calls == []


def test_fleet_disabled_without_io() -> None:
    result = make_fleet_collector(
        env_file=Path("/does/not/exist"),
        transport=lambda url, headers: (_ for _ in ()).throw(AssertionError()),
    )()
    assert result.status == "disabled"


def test_fleet_enabled_uses_two_gets_and_redacts_token(tmp_path: Path) -> None:
    token = "fleet" + "_" + "t" * 16
    config = tmp_path / "coordinator.env"
    config.write_text(
        f"FLEET_COORDINATOR_URL=http://127.0.0.1:19999\nFLEET_COORDINATOR_TOKEN={token}\n",
        encoding="utf-8",
    )
    calls: list[Any] = []

    def transport(url: str, headers: Any) -> tuple[int, bytes]:
        calls.append((url, headers))
        return (
            200,
            b'{"hitl":2}'
            if url.endswith("/status")
            else b'{"jobs":[{"id":1,"state":"waiting_hitl","worker":"w"}]}',
        )

    result = make_fleet_collector(enabled=True, env_file=config, transport=transport)()
    assert result.status == "ok"
    assert len(calls) == 2
    assert result.extra == {"hitl_count": 3}
    assert token not in json.dumps(result.__dict__)
    datetime.fromisoformat(result.items[0]["age_measured_at"])
