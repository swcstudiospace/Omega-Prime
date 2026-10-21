from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from omega_prime.dashboard.collectors import github
from omega_prime.dashboard.collectors.github import make_github_collector


def _pr(repo: str, number: int, merged: bool = True) -> dict:
    return {
        "id": number,
        "number": number,
        "title": f"merge {number}",
        "html_url": f"https://github.com/swcstudiospace/{repo}/pull/{number}",
        "merged_at": "2026-10-20T12:00:00Z" if merged else None,
    }


def test_closed_pages_avoid_legacy_1_2mb_truncation():
    calls: list[tuple[str, int, int]] = []

    def transport(url, headers):
        parsed = urlsplit(url)
        query = parse_qs(parsed.query)
        if parsed.path.endswith("/pulls") and query.get("state") == ["closed"]:
            repo = parsed.path.split("/")[3]
            per_page, page = int(query["per_page"][0]), int(query["page"][0])
            calls.append((repo, per_page, page))
            if page == 1:
                rows = [_pr(repo, n + 1) for n in range(100)]
                # The old endpoint's full 100-row shape exceeded its fixed cap.
                for row in rows:
                    row["title"] = "x" * 12_000
                payload = json.dumps(rows).encode()
                assert len(payload) > 1_000_000
                rows = rows[:per_page]
            else:
                rows = []
            return 200, json.dumps(rows).encode()
        if "/pulls?" in url:
            return 200, b"[]"
        if "/commits/" in url:
            return 200, b'{"sha":"main"}'
        if "/check-runs?" in url:
            return 200, b'{"check_runs":[]}'
        return 200, b"[]"

    cc = make_github_collector(
        env={"GITHUB_PAT_DESK_LEAD": "fake"}, transport=transport
    )().extra["command_center"]
    assert {row["repo"] for row in cc["recent_merges"]} == {
        "swcstudiospace/Omega-Prime",
        "swcstudiospace/coding-fleet",
    }
    assert not cc["meta"]["partial"]
    assert not cc["meta"]["errors"]
    assert {per_page for _, per_page, _ in calls} == {30}


def test_oversize_response_reports_too_large_and_keeps_stale_merges(monkeypatch):
    now = [datetime(2026, 10, 20, tzinfo=UTC)]
    fail = [False]

    def transport(url, headers):
        query = parse_qs(urlsplit(url).query)
        if query.get("state") == ["closed"]:
            if fail[0]:
                return 200, b"x" * 1_000_001
            repo = urlsplit(url).path.split("/")[3]
            return 200, json.dumps([_pr(repo, 1)]).encode()
        if "/pulls?" in url:
            return 200, b"[]"
        if "/commits/" in url:
            return 200, b'{"sha":"main"}'
        if "/check-runs?" in url:
            return 200, b'{"check_runs":[]}'
        return 200, b"[]"

    collector = make_github_collector(
        env={"GITHUB_PAT_DESK_LEAD": "fake"},
        transport=transport,
        now_fn=lambda: now[0],
        cache_ttl=0,
    )
    original = collector().extra["command_center"]
    original_rows = original["recent_merges"]
    stamp = original["meta"]["sections"]["recent_merges"]["fetched_at"]
    fail[0] = True
    now[0] += timedelta(seconds=91)
    degraded = collector().extra["command_center"]
    assert "recent_merges:too_large" in degraded["meta"]["errors"]
    assert degraded["recent_merges"] == original_rows
    assert degraded["meta"]["sections"]["recent_merges"]["fetched_at"] == stamp
    assert degraded["meta"]["sections"]["recent_merges"]["stale"] is True


def test_closed_paging_stops_at_max_merged_rows_per_target():
    calls: list[tuple[str, int]] = []

    def transport(url, headers):
        query = parse_qs(urlsplit(url).query)
        if query.get("state") == ["closed"]:
            repo = urlsplit(url).path.split("/")[3]
            page = int(query["page"][0])
            calls.append((repo, page))
            return 200, json.dumps(
                [_pr(repo, page * 30 + n) for n in range(30)]
            ).encode()
        if "/pulls?" in url:
            return 200, b"[]"
        if "/commits/" in url:
            return 200, b'{"sha":"main"}'
        if "/check-runs?" in url:
            return 200, b'{"check_runs":[]}'
        return 200, b"[]"

    cc = make_github_collector(
        env={"GITHUB_PAT_DESK_LEAD": "fake"}, transport=transport
    )().extra["command_center"]
    assert len(cc["recent_merges"]) == 100
    for repo in ("Omega-Prime", "coding-fleet"):
        assert [page for name, page in calls if name == repo] == [1, 2, 3, 4]


def test_request_reads_only_limit_plus_one_bytes(monkeypatch):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, size):
            assert size == github.MAX_RESPONSE_BYTES + 1
            return b"x" * size

    class Opener:
        def open(self, request, timeout):
            assert timeout == 2.5
            return Response()

    monkeypatch.setattr(github, "_OPENER", Opener())
    try:
        github._request("https://api.github.com/repos/swcstudiospace/Omega-Prime", {})
    except github._ResponseTooLarge:
        pass
    else:
        raise AssertionError("oversize fake-opener response was accepted")
