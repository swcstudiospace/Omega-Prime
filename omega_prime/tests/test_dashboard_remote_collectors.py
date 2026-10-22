# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Fake-transport tests for remote dashboard collectors."""

from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.request
import urllib.response
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from omega_prime.dashboard.collectors import fleet as fleet_module
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
        if "/status?" in url:
            return 200, b'{"state":"success","statuses":[],"total_count":0}'
        if "/check-runs" in url:
            return 200, b'{"check_runs":[{"conclusion":"success"}]}'
        return (
            200,
            b'[{"user":{"login":"greptile-apps[bot]"},"created_at":"2026-01-01T00:00:00Z","body":"Confidence score: 4/5"}]',
        )

    collector = make_github_collector(
        env={"GITHUB_PAT_DESK_LEAD": "fake-token"},
        transport=transport,
        repo="swcstudiospace/omes-bot",
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
        if "/status?" in url:
            return 200, b'{"state":"success","statuses":[],"total_count":0}'
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
        env={"GITHUB_PAT_DESK_LEAD": "token"},
        transport=transport,
        repo="swcstudiospace/omes-bot",
    )()
    assert result.items[0]["greptile"] == "3/5"


def test_github_ignores_human_score_comment() -> None:
    def transport(url: str, headers: Any) -> tuple[int, bytes]:
        if "/pulls?" in url:
            return 200, b'[{"number":1,"head":{"sha":"head"}}]'
        if "/status?" in url:
            return 200, b'{"state":"success","statuses":[],"total_count":0}'
        if "/check-runs" in url:
            return 200, b'{"check_runs":[]}'
        if "/comments" in url:
            return 200, b'[{"user":{"login":"human"},"body":"Confidence Score: 5/5"}]'
        return 200, b"[]"

    result = make_github_collector(
        env={"GITHUB_PAT_DESK_LEAD": "token"},
        transport=transport,
        repo="swcstudiospace/omes-bot",
    )()
    assert result.items[0]["greptile"] == "not reviewed"


def test_github_latest_bot_comment_beats_matching_review() -> None:
    def transport(url: str, headers: Any) -> tuple[int, bytes]:
        if "/pulls?" in url:
            return 200, b'[{"number":1,"head":{"sha":"head"}}]'
        if "/status?" in url:
            return 200, b'{"state":"success","statuses":[],"total_count":0}'
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
        env={"GITHUB_PAT_DESK_LEAD": "token"},
        transport=transport,
        repo="swcstudiospace/omes-bot",
    )()
    assert result.items[0]["greptile"] == "2/5"


def test_github_review_score_matches_capitalized_score() -> None:
    def transport(url: str, headers: Any) -> tuple[int, bytes]:
        if "/pulls?" in url:
            return 200, b'[{"number":1,"head":{"sha":"head"}}]'
        if "/status?" in url:
            return 200, b'{"state":"success","statuses":[],"total_count":0}'
        if "/check-runs" in url:
            return 200, b'{"check_runs":[]}'
        if "/comments" in url:
            return 200, b"[]"
        return (
            200,
            b'[{"user":{"login":"greptile-apps[bot]"},"commit_id":"head","body":"Confidence Score: 4/5"}]',
        )

    result = make_github_collector(
        env={"GITHUB_PAT_DESK_LEAD": "token"},
        transport=transport,
        repo="swcstudiospace/omes-bot",
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


def test_fleet_refuses_plain_http_off_loopback(tmp_path: Path) -> None:
    config = tmp_path / "coordinator.env"
    token = "fleet" + "_" + "t" * 16
    config.write_text(
        f"FLEET_COORDINATOR_URL=http://fleet.example:8080\nFLEET_COORDINATOR_TOKEN={token}\n",
        encoding="utf-8",
    )
    calls: list[Any] = []

    def transport(url: str, headers: Any) -> tuple[int, bytes]:
        calls.append((url, headers))
        return (200, b"{}")

    result = make_fleet_collector(enabled=True, env_file=config, transport=transport)()
    assert result.status == "unavailable"
    assert "HTTPS" in (result.error or "")
    assert calls == []


def test_fleet_default_request_refuses_redirects(monkeypatch) -> None:
    """A coordinator redirect never carries the bearer token to a new URL."""
    forwarded: list[str] = []

    class Redirecting(urllib.request.BaseHandler):
        handler_order = 100  # ahead of the real HTTPHandler: no network

        def http_open(self, req):
            if req.full_url.startswith("http://127.0.0.1"):
                response = urllib.response.addinfourl(
                    io.BytesIO(b""),
                    {"Location": "http://evil.example/steal"},
                    req.full_url,
                    302,
                )
                response.msg = "Found"
                return response
            forwarded.append(req.get_header("Authorization") or "")
            return urllib.response.addinfourl(io.BytesIO(b"{}"), {}, req.full_url, 200)

    opener = urllib.request.build_opener(Redirecting, fleet_module._RefuseRedirect)
    monkeypatch.setattr(fleet_module, "_OPENER", opener)
    try:
        fleet_module._request(
            "http://127.0.0.1:1/status", {"Authorization": "Bearer x"}
        )
    except urllib.error.HTTPError as exc:
        assert exc.code == 302
    else:  # pragma: no cover - the redirect must be refused
        raise AssertionError("redirect was followed")
    assert forwarded == []


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


def _slow_pr_transport(
    calls: list[str], prs: int, delay: float
) -> Callable[[str, Any], tuple[int, bytes]]:
    def transport(url: str, headers: Any) -> tuple[int, bytes]:
        calls.append(url)
        if "/pulls?" in url:
            return 200, json.dumps(
                [
                    {
                        "number": n,
                        "head": {"sha": f"h{n}"},
                        "body": "<!-- greptile_confidence_score:0 -->",
                    }
                    for n in range(prs)
                ]
            ).encode()
        time.sleep(delay)
        if "/status?" in url:
            return 200, b'{"state":"success","statuses":[],"total_count":0}'
        if "/check-runs" in url:
            return 200, b'{"check_runs":[]}'
        return 200, b"[]"

    return transport


def test_github_zero_score_and_body_score_skips_comment_calls() -> None:
    calls: list[str] = []
    result = make_github_collector(
        env={"GITHUB_PAT_DESK_LEAD": "token"},
        transport=_slow_pr_transport(calls, 2, 0.0),
        repo="swcstudiospace/omes-bot",
    )()
    assert [row["greptile"] for row in result.items] == ["0/5", "0/5"]
    assert not any("/comments" in u or "/reviews" in u for u in calls)


def test_github_per_pr_calls_run_concurrently() -> None:
    calls: list[str] = []
    started = time.monotonic()
    result = make_github_collector(
        env={"GITHUB_PAT_DESK_LEAD": "token"},
        transport=_slow_pr_transport(calls, 16, 0.2),
        repo="swcstudiospace/omes-bot",
    )()
    elapsed = time.monotonic() - started
    assert result.status == "ok" and len(result.items) == 16
    # 16 sequential check-run calls would take 3.2s.
    assert elapsed < 1.5
