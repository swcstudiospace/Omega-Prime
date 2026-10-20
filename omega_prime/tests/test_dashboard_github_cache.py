from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from omega_prime.dashboard.collectors.github import make_github_collector


def test_ttl_403_and_missing_token_preserve_original_successful_section_data():
    now = [datetime(2026, 10, 20, tzinfo=UTC)]
    mode = ["ok"]
    calls = []
    def transport(url, headers):
        calls.append(url)
        if mode[0] == "forbidden":
            return 403, b"sensitive bearer detail must not escape"
        if "/orgs/" in url:
            return 200, json.dumps([{"id": 1, "full_name": "swcstudiospace/other", "name": "other", "default_branch": "main"}]).encode()
        if "/pulls?" in url:
            return 200, b"[]"
        if "/check-runs" in url:
            return 200, b'{"check_runs":[{"id":2,"name":"ci","status":"completed","conclusion":"success","head_sha":"branch-sha"}]}'
        if "/commits/main" in url:
            return 200, b'{"sha":"branch-sha"}'
        return 200, b"[]"
    env = {"GITHUB_PAT_DESK_LEAD": "secret-token"}
    collector = make_github_collector(env=env, transport=transport, now_fn=lambda: now[0])
    first = collector()
    first_cc = first.extra["command_center"]
    first_fetched = first_cc["meta"]["sections"]["repositories"]["fetched_at"]
    first_calls = len(calls)
    assert collector() is first
    assert len(calls) == first_calls
    now[0] += timedelta(seconds=91)
    mode[0] = "forbidden"
    failed = collector()
    failed_cc = failed.extra["command_center"]
    assert failed_cc["meta"]["reason"] == "forbidden"
    assert failed_cc["meta"]["stale"] is True
    assert failed_cc["repositories"] == first_cc["repositories"]
    assert failed_cc["meta"]["sections"]["repositories"]["fetched_at"] == first_fetched
    assert failed_cc["meta"]["sections"]["repositories"]["stale"] is True
    assert "secret-token" not in json.dumps(failed.__dict__)
    env.clear()
    now[0] += timedelta(seconds=91)
    missing = collector()
    assert missing.extra["command_center"]["repositories"] == first_cc["repositories"]
    assert missing.extra["command_center"]["meta"]["reason"] == "unauthenticated"


def test_cold_unauthenticated_and_rate_limited_states_are_truthful():
    calls = []
    cold = make_github_collector(env={}, transport=lambda u, h: calls.append(u) or (200, b"[]"))()
    assert cold.status == "unavailable"
    assert cold.extra["command_center"]["repositories"] == []
    assert calls == []
    def limited(url, headers):
        calls.append(url)
        return 429, b"token-shaped-private-error"
    result = make_github_collector(env={"GITHUB_PAT_DESK_LEAD": "tok"}, transport=limited)()
    assert result.extra["command_center"]["meta"]["reason"] == "rate_limited"
    assert result.extra["command_center"]["repositories"] == []
    assert "token-shaped-private-error" not in json.dumps(result.__dict__)


def test_failed_org_inventory_does_not_discard_healthy_target_pull_data():
    def transport(url, headers):
        if "/orgs/" in url:
            return 500, b"private"
        if "/pulls?" in url:
            return 200, b'[{"id":3,"number":2,"title":"visible","head":{"sha":"head"},"body":"<!-- greptile_confidence_score: 0 -->"}]'
        if "/check-runs" in url:
            return 200, b'{"check_runs":[]}'
        return 200, b'{"state":"success","statuses":[]}'
    result = make_github_collector(env={"GITHUB_PAT_DESK_LEAD":"token"}, transport=transport)()
    cc = result.extra["command_center"]
    assert cc["meta"]["sections"]["repositories"]["partial"] is True
    assert any(row["kind"] == "pull_request" for row in cc["ci_cd"])
    assert result.items and result.items[0]["greptile"] == "0/5"


def test_one_target_failure_retains_only_that_repository_while_other_refreshes():
    now = [datetime(2026, 10, 20, tzinfo=UTC)]
    mode = ["ok"]
    def transport(url, headers):
        if "/orgs/" in url:
            return 200, json.dumps([
                {"id": 1, "full_name": "swcstudiospace/Omega-Prime", "name": "Omega-Prime", "default_branch": "main"},
                {"id": 2, "full_name": "swcstudiospace/coding-fleet", "name": "coding-fleet", "default_branch": "main"},
            ]).encode()
        if "/pulls?" in url:
            if "/coding-fleet/pulls?" in url and mode[0] == "fail":
                return 403, b"private"
            name = "coding-fleet" if "/coding-fleet/" in url else "Omega-Prime"
            title = "old-fleet" if name == "coding-fleet" else ("new-omes" if mode[0] == "fail" else "old-omes")
            return 200, json.dumps([{"id": 3, "number": 5, "title": title, "head": {"sha": "head"}, "body": "<!-- greptile_confidence_score: 0 -->"}]).encode()
        if "/check-runs" in url:
            return 200, b'{"check_runs":[]}'
        if "/commits/main" in url:
            return 200, b'{"sha":"main-sha"}'
        return 200, b"[]"
    collector = make_github_collector(env={"GITHUB_PAT_DESK_LEAD": "token"}, transport=transport, now_fn=lambda: now[0])
    collector()
    now[0] += timedelta(seconds=91)
    mode[0] = "fail"
    result = collector()
    rows = {row["repo"]: row["title"] for row in result.extra["command_center"]["ci_cd"] if row["kind"] == "pull_request"}
    assert rows["swcstudiospace/coding-fleet"] == "old-fleet"
    assert rows["swcstudiospace/Omega-Prime"] == "new-omes"
    assert result.extra["command_center"]["meta"]["sections"]["ci_cd"]["stale"] is True

def test_greptile_request_failure_is_partial_not_missing_review_evidence():
    now = [datetime(2026, 10, 20, tzinfo=UTC)]
    mode = ["ok"]
    def transport(url, headers):
        if "/pulls?" in url:
            return 200, b'[{\"id\":5,\"number\":5,\"title\":\"pr\",\"head\":{\"sha\":\"head\"},\"body\":\"\"}]'
        if "/check-runs" in url:
            return 200, b'{\"check_runs\":[]}'
        if "/comments" in url and mode[0] == "fail":
            return 403, b"private response body"
        if "/comments" in url or "/reviews" in url:
            return 200, b"[]"
        return 200, b"[]"
    collector = make_github_collector(
        env={"GITHUB_PAT_DESK_LEAD": "token"},
        transport=transport,
        now_fn=lambda: now[0],
        repo="swcstudiospace/omes-bot",
    )
    first = collector()
    original_time = first.extra["command_center"]["meta"]["sections"]["ci_cd"]["fetched_at"]
    now[0] += timedelta(seconds=91)
    mode[0] = "fail"
    result = collector()
    meta = result.extra["command_center"]["meta"]
    section = meta["sections"]["ci_cd"]
    assert section["partial"] is True and section["stale"] is True
    assert section["reason"] == "forbidden"
    assert section["fetched_at"] == original_time
    row = result.extra["command_center"]["ci_cd"][0]
    assert row["greptile_review"] is None
    assert result.items[0]["greptile"] == "not reviewed"
    assert "private response body" not in json.dumps(result.__dict__)


def test_poller_publication_redacts_credentials_from_github_command_center():
    import asyncio
    from omega_prime.dashboard.poller import CollectorSpec, DashboardPoller
    secret = "test" + "_" + "token" + "_" + "with-secret-shape"
    def transport(url, headers):
        assert headers["Authorization"] == "Bearer " + secret
        if "/orgs/" in url:
            return 200, json.dumps([{"id": 1, "full_name": "swcstudiospace/omes-bot", "name": "omes-bot"}]).encode()
        if "/pulls?" in url:
            return 200, json.dumps([{"id": 9, "number": 9, "title": secret, "head": {"sha": "head"}, "body": "<!-- greptile_confidence_score: 0 -->"}]).encode()
        if "/check-runs" in url:
            return 200, b'{"check_runs":[]}'
        return 200, b"[]"
    collector = make_github_collector(env={"GITHUB_PAT_DESK_LEAD": secret}, transport=transport)
    poller = DashboardPoller([CollectorSpec("github", collector, 1.0, 1.0)])
    import asyncio
    asyncio.run(poller._collect(poller.specs[0]))
    poller._build_snapshot()
    snapshot = poller.snapshot()
    assert snapshot is not None
    assert secret not in json.dumps(snapshot)
    assert snapshot["sources"]["github"]["command_center"]["ci_cd"]
