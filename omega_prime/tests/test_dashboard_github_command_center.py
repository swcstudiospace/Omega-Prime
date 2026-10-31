from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

from omega_prime.dashboard.collectors.github import make_github_collector

ORG = "https://api.github.com/orgs/swcstudiospace/repos"
TARGETS = ("Omega-Prime", "coding-fleet")


def test_default_scope_projects_org_pr_branch_checks_and_actual_merges():
    now = datetime(2026, 10, 20, 12, tzinfo=UTC)
    calls = []

    def transport(url, headers):
        assert url.startswith("https://api.github.com/")
        assert headers["Authorization"] == "Bearer fixture-secret"
        calls.append(url)
        path = urlsplit(url).path
        query = parse_qs(urlsplit(url).query)
        if path == "/orgs/swcstudiospace/repos":
            return 200, json.dumps(
                [
                    {
                        "id": 1,
                        "full_name": "swcstudiospace/coding-fleet",
                        "name": "coding-fleet",
                        "html_url": "https://github.com/swcstudiospace/coding-fleet",
                        "visibility": "public",
                        "default_branch": "main",
                        "pushed_at": "2026-10-19T00:00:00Z",
                        "language": "Python",
                        "open_issues_count": 9,
                    },
                    {
                        "id": 2,
                        "full_name": "swcstudiospace/Omega-Prime",
                        "name": "Omega-Prime",
                        "html_url": "https://github.com/swcstudiospace/Omega-Prime",
                        "visibility": "private",
                        "default_branch": "trunk",
                        "pushed_at": None,
                        "language": "Python",
                        "open_issues_count": 3,
                    },
                    {
                        "id": 3,
                        "full_name": "swcstudiospace/other",
                        "name": "other",
                        "default_branch": "main",
                    },
                ]
            ).encode()
        if "/commits/" in path and path.endswith("/pulls"):
            return 200, b"[]"
        if path.endswith("/pulls"):
            repo = path.split("/")[2]
            if query.get("state") == ["closed"]:
                return 200, json.dumps(
                    [
                        {
                            "id": 10,
                            "number": 7,
                            "title": "merged",
                            "html_url": "https://github.com/p/7",
                            "merged_at": "2026-10-19T12:00:00Z",
                            "merge_commit_sha": "merge7",
                        },
                        {
                            "id": 11,
                            "number": 8,
                            "title": "closed only",
                            "merged_at": None,
                        },
                    ]
                ).encode()
            rows = [
                {
                    "id": 20,
                    "number": 1,
                    "title": repo,
                    "html_url": "https://github.com/p/1",
                    "draft": False,
                    "mergeable": None,
                    "head": {
                        "ref": "feature",
                        "sha": "head-" + repo.rsplit("/", 1)[-1],
                    },
                    "base": {"ref": "main"},
                    "body": "<!-- greptile_confidence_score: 0 -->",
                }
            ]
            return 200, json.dumps(rows).encode()
        if path.endswith(("/commits/main", "/commits/trunk")):
            return 200, json.dumps(
                {"sha": "branch-" + path.rsplit("/", 1)[-1]}
            ).encode()
        if path.endswith("/status"):
            return 200, b'{"state":"success","statuses":[],"total_count":0}'
        if "/commits/" in path and "/check-runs" in path:
            sha = path.split("/commits/")[1].split("/")[0]
            return 200, json.dumps(
                {
                    "check_runs": [
                        {
                            "id": 50,
                            "name": "unit",
                            "status": "completed",
                            "conclusion": "failure",
                            "started_at": "2026-10-20T10:00:00Z",
                            "completed_at": "2026-10-20T10:02:00Z",
                            "html_url": "https://github.com/check/50",
                            "head_sha": sha,
                        }
                    ]
                }
            ).encode()
        if "/commits/" in path and "/status" in path:
            return 200, json.dumps(
                {
                    "sha": path.split("/commits/")[1].split("/")[0],
                    "state": "success",
                    "statuses": [],
                }
            ).encode()
        if "/comments" in path or "/reviews" in path:
            return 200, b"[]"
        raise AssertionError(url)

    result = make_github_collector(
        env={"GITHUB_PAT_DESK_LEAD": "fixture-secret"},
        transport=transport,
        now_fn=lambda: now,
    )()
    cc = result.extra["command_center"]
    assert {r["name"] for r in cc["repositories"]} == {
        "coding-fleet",
        "Omega-Prime",
        "other",
    }
    other = next(r for r in cc["repositories"] if r["name"] == "other")
    assert other["open_pr_count"] == 1
    assert {row["repo"] for row in cc["ci_cd"] if row["kind"] == "pull_request"} == {
        f"swcstudiospace/{x}" for x in TARGETS
    }
    for row in cc["ci_cd"]:
        if row["kind"] == "pull_request":
            assert row["checks"][0]["duration_s"] == 120
            assert row["checks"][0]["details_url"] == "https://github.com/check/50"
            assert row["greptile_review"]["score"] == 0
            assert row["greptile_review"]["reviewed_sha"] is None
            assert row["ci"] == "fail"
    assert len(cc["recent_merges"]) == 2
    assert all(row["merged_at"] for row in cc["recent_merges"])
    assert {r["open_pr_count"] for r in cc["repositories"] if r["name"] in TARGETS} == {
        1
    }
    assert result.items and all(item["kind"] == "github_pr" for item in result.items)
    assert "fixture-secret" not in json.dumps(result.__dict__)
    assert all(url.startswith("https://api.github.com/") for url in calls)


def test_comment_score_has_unknown_reviewed_sha_and_old_head_review_is_not_current():
    def transport(url, headers):
        if "/commits/" in url and "/pulls?" in url:
            return 200, b"[]"
        if "/pulls?" in url:
            return (
                200,
                b'[{"id":1,"number":4,"head":{"sha":"new","ref":"x"},"body":""}]',
            )
        if "/status?" in url:
            return 200, b'{"state":"success","statuses":[],"total_count":0}'
        if "/check-runs" in url:
            return 200, b'{"check_runs":[]}'
        if "/comments" in url:
            return (
                200,
                b'[{"user":{"login":"greptile-apps[bot]"},"body":"Confidence score: 4/5","created_at":"2026-01-01T00:00:00Z","html_url":"https://github.com/c/1"}]',
            )
        if "/reviews" in url:
            return (
                200,
                b'[{"user":{"login":"greptile-apps[bot]"},"commit_id":"old","body":"Confidence score: 1/5","submitted_at":"2026-01-02T00:00:00Z"}]',
            )
        return 200, b'{"state":"pending","statuses":[]}'

    result = make_github_collector(
        env={"GITHUB_PAT_DESK_LEAD": "token"},
        transport=transport,
        repo="swcstudiospace/omes-bot",
    )()
    review = result.extra["command_center"]["ci_cd"][0]["greptile_review"]
    assert review["score"] == 4
    assert review["source"] == "comment"
    assert review["reviewed_sha"] is None
    assert result.items[0]["greptile"] == "4/5"


def test_default_env_reads_process_environment(monkeypatch):
    """env=None (the production path) must read os.environ, not degrade to 'GitHub not configured'."""
    monkeypatch.setenv("GITHUB_PAT_DESK_LEAD", "fixture-secret")
    seen = []

    def transport(url, headers):
        seen.append(headers.get("Authorization"))
        return 200, b"[]"

    result = make_github_collector(
        transport=transport, repo="swcstudiospace/Omega-Prime"
    )()
    assert result.error != "GitHub not configured"
    assert seen and all(h == "Bearer fixture-secret" for h in seen)
