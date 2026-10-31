# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""GitHub collector evidence that gates merge readiness."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from omega_prime.dashboard.collectors.github import make_github_collector

REPO = "swcstudiospace/Omega-Prime"
ENV = {"GITHUB_PAT_DESK_LEAD": "fake-token"}
GREEN_RUN = b'{"check_runs":[{"id":1,"name":"ci","status":"completed","conclusion":"success","head_sha":"abc"}]}'
REVIEW_5 = {
    "user": {"login": "greptile-apps[bot]"},
    "commit_id": "abc",
    "submitted_at": "2026-10-20T00:00:00Z",
    "body": "Confidence score: 5/5",
}


def _pr(number: int = 7) -> dict:
    return {
        "id": number,
        "number": number,
        "title": "change",
        "head": {"ref": "feat/x", "sha": "abc"},
        "base": {"ref": "main"},
        "draft": False,
        "mergeable": True,
        "updated_at": "2026-10-20T00:00:00Z",
        "html_url": f"https://github.com/{REPO}/pull/{number}",
    }


def _transport(status: dict, reviews=None, mode=None):
    mode = mode if mode is not None else ["ok"]

    def send(url, headers):
        path = urlsplit(url).path
        if path.endswith("/pulls") and parse_qs(urlsplit(url).query).get("state") == [
            "open"
        ]:
            if mode[0] == "pulls_down":
                return 500, b""
            return 200, json.dumps([_pr()]).encode()
        if path.endswith("/status"):
            return 200, json.dumps(status).encode()
        if path.endswith("/check-runs"):
            return 200, GREEN_RUN
        if path.endswith("/reviews"):
            page = int(parse_qs(urlsplit(url).query)["page"][0])
            rows = reviews(page) if reviews else ([REVIEW_5] if page == 1 else [])
            return 200, json.dumps(rows).encode()
        return 200, b"[]"

    return send


def test_failing_commit_status_fails_ci_despite_green_check_runs():
    status = {
        "state": "failure",
        "total_count": 1,
        "statuses": [{"id": 9, "context": "deploy/preview", "state": "error"}],
    }
    result = make_github_collector(env=ENV, transport=_transport(status), repo=REPO)()
    assert result.items[0]["ci"] == "fail"
    row = result.extra["command_center"]["ci_cd"][0]
    assert {c["name"] for c in row["checks"]} == {"ci", "deploy/preview"}


def test_pending_commit_status_keeps_ci_pending():
    status = {
        "state": "pending",
        "total_count": 1,
        "statuses": [{"id": 9, "context": "legacy-ci", "state": "pending"}],
    }
    result = make_github_collector(env=ENV, transport=_transport(status), repo=REPO)()
    assert result.items[0]["ci"] == "pending"


def test_truncated_review_history_never_reports_an_old_score():
    """Past the page window a newer review may exist: no score is trusted."""
    status = {"state": "success", "total_count": 0, "statuses": []}
    filler = [{"user": {"login": "someone"}, "body": "lgtm"}] * 100

    def reviews(page: int):
        return [REVIEW_5, *filler[:99]] if page == 1 else filler

    result = make_github_collector(
        env=ENV, transport=_transport(status, reviews), repo=REPO
    )()
    assert result.items[0]["greptile"] == "not reviewed"
    assert result.extra["command_center"]["ci_cd"][0]["greptile_review"] is None
    assert result.extra["command_center"]["meta"]["partial"] is True


def test_failed_pr_refresh_keeps_cached_rows_in_items_and_command_center():
    status = {"state": "success", "total_count": 0, "statuses": []}
    now = [datetime(2026, 10, 20, tzinfo=UTC)]
    mode = ["ok"]
    collector = make_github_collector(
        env=ENV,
        transport=_transport(status, mode=mode),
        repo=REPO,
        now_fn=lambda: now[0],
    )
    first = collector()
    assert [item["number"] for item in first.items] == [7]
    now[0] += timedelta(seconds=91)
    mode[0] = "pulls_down"
    failed = collector()
    assert [item["number"] for item in failed.items] == [7]
    assert [row["number"] for row in failed.extra["command_center"]["ci_cd"]] == [7]
    assert failed.items[0]["greptile"] == first.items[0]["greptile"]


def test_default_branch_with_slash_and_dot_keeps_its_card():
    calls: list[str] = []

    def send(url, headers):
        calls.append(url)
        path = urlsplit(url).path
        if path == "/orgs/swcstudiospace/repos":
            return 200, json.dumps(
                [
                    {
                        "id": 1,
                        "full_name": REPO,
                        "name": "Omega-Prime",
                        "default_branch": "release/2026.10",
                    }
                ]
            ).encode()
        if path.endswith(("/commits/release%2F2026.10", "/commits/release/2026.10")):
            return 200, b'{"sha":"relsha"}'
        if path.endswith("/status"):
            return 200, b'{"state":"success","statuses":[],"total_count":0}'
        if path.endswith("/check-runs"):
            return 200, b'{"check_runs":[]}'
        return 200, b"[]"

    result = make_github_collector(env=ENV, transport=send)()
    branches = [
        row
        for row in result.extra["command_center"]["ci_cd"]
        if row["kind"] == "branch"
    ]
    assert [row["branch"] for row in branches] == ["release/2026.10"]
    assert any("release%2F2026.10" in url for url in calls)


def test_status_build_links_keep_external_https_hosts_only() -> None:
    from omega_prime.dashboard.github_data import status_rows

    rows = status_rows(
        {
            "statuses": [
                {
                    "id": 1,
                    "context": "ci/a",
                    "state": "failure",
                    "target_url": "https://ci.example.com/build/7",
                },
                {
                    "id": 2,
                    "context": "ci/b",
                    "state": "failure",
                    "target_url": "http://ci.example.com/build/8",
                },
                {
                    "id": 3,
                    "context": "ci/c",
                    "state": "failure",
                    "target_url": "https://user:pw@ci.example.com/9",
                },
                {
                    "id": 4,
                    "context": "ci/d",
                    "state": "failure",
                    "target_url": "javascript:alert(1)",
                },
            ]
        },
        "abc123",
    )
    assert [row["details_url"] for row in rows] == [
        "https://ci.example.com/build/7",
        None,
        None,
        None,
    ]
