# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Fake-transport tests for remote dashboard collectors."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from omega_prime.dashboard.collectors.fleet import make_fleet_collector
from omega_prime.dashboard.collectors.github import make_github_collector


def test_github_missing_token_makes_no_calls() -> None:
    calls: list[Any] = []
    result = make_github_collector(
        env={},
        transport=lambda url, headers: calls.append((url, headers)) or (200, b"[]"),
    )()
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
        return 200, b'[{"body":"Confidence score: 4/5"}]'

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


def test_fleet_disabled_without_io() -> None:
    result = make_fleet_collector(
        env_file=Path("/does/not/exist"),
        transport=lambda url, headers: (_ for _ in ()).throw(AssertionError()),
    )()
    assert result.status == "disabled"


def test_fleet_enabled_uses_two_gets_and_redacts_token(tmp_path: Path) -> None:
    config = tmp_path / "coordinator.env"
    config.write_text(
        "FLEET_COORDINATOR_URL=http://127.0.0.1:19999\nFLEET_COORDINATOR_TOKEN=fakefleettokenvalue\n",
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
    assert "fakefleettokenvalue" not in json.dumps(result.__dict__)
