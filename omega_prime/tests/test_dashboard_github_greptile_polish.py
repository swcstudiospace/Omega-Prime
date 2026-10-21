from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from omega_prime.dashboard.collectors.github import make_github_collector

TOKEN = "synthetic-gh-token"


def _pr(
    repo: str, number: int, branch_sha: str, head_sha: str, *, body: str = ""
) -> dict[str, Any]:
    return {
        "id": number,
        "number": number,
        "merged_at": "2026-10-20T10:00:00Z",
        "merge_commit_sha": branch_sha,
        "html_url": f"https://github.com/{repo}/pull/{number}",
        "head": {"sha": head_sha, "ref": "feature"},
        "base": {"ref": "main", "repo": {"full_name": repo}},
        "body": body,
    }


def test_branch_uses_merged_pr_score_and_actual_reviewed_sha():
    repositories = [
        {
            "id": 1,
            "full_name": "swcstudiospace/Omega-Prime",
            "name": "Omega-Prime",
            "default_branch": "main",
        },
        {
            "id": 2,
            "full_name": "swcstudiospace/coding-fleet",
            "name": "coding-fleet",
            "default_branch": "main",
        },
    ]
    omega = _pr("swcstudiospace/Omega-Prime", 41, "merge-omega", "pr-head-omega")
    fleet = _pr(
        "swcstudiospace/coding-fleet",
        42,
        "merge-fleet",
        "pr-head-fleet",
        body="<!-- greptile_confidence_score: 3 -->",
    )
    calls: list[str] = []

    def transport(url: str, headers: Mapping[str, str]) -> tuple[int, bytes]:
        assert headers["Authorization"] == f"Bearer {TOKEN}"
        calls.append(url)
        parsed = urlsplit(url)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path == "/orgs/swcstudiospace/repos":
            return 200, json.dumps(repositories).encode()
        if path.endswith("/pulls") and query.get("state") == ["open"]:
            return 200, b"[]"
        if path.endswith("/pulls") and query.get("state") == ["closed"]:
            return 200, b"[]"
        if path.endswith("/commits/main"):
            repo = path.split("/")[3]
            return 200, json.dumps(
                {"sha": "merge-omega" if repo == "Omega-Prime" else "merge-fleet"}
            ).encode()
        if "/check-runs" in path:
            return 200, b'{"check_runs":[]}'
        if path.endswith("/commits/merge-omega/pulls"):
            return 200, json.dumps([omega]).encode()
        if path.endswith("/commits/merge-fleet/pulls"):
            return 200, json.dumps([fleet]).encode()
        if path.endswith("/issues/41/comments"):
            return 200, b"[]"
        if path.endswith("/pulls/41/reviews"):
            return 200, json.dumps(
                [
                    {
                        "user": {"login": "greptile-apps[bot]"},
                        "commit_id": "pr-head-omega",
                        "body": "Confidence score: 0/5",
                        "submitted_at": "2026-10-20T11:00:00Z",
                        "html_url": "https://github.com/swcstudiospace/Omega-Prime/pull/41#review-1",
                    }
                ]
            ).encode()
        if (
            path.endswith("/pulls/41/comments")
            or path.endswith("/pulls/42/reviews")
            or path.endswith("/pulls/42/comments")
        ):
            return 200, b"[]"
        raise AssertionError(path)

    result = make_github_collector(
        env={"GITHUB_PAT_DESK_LEAD": TOKEN},
        transport=transport,
        now_fn=lambda: datetime(2026, 10, 20, tzinfo=UTC),
    )()
    rows = result.extra["command_center"]["ci_cd"]
    omega_row = next(
        row
        for row in rows
        if row["kind"] == "branch" and row["repo"].endswith("Omega-Prime")
    )
    fleet_row = next(
        row
        for row in rows
        if row["kind"] == "branch" and row["repo"].endswith("coding-fleet")
    )
    omega_review = omega_row["greptile_review"]
    assert omega_row["head_sha"] == "merge-omega"
    assert omega_review["source"] == "merged_pr"
    assert omega_review["evidence_source"] == "review"
    assert omega_review["pr_number"] == 41
    assert omega_review["score"] == 0
    assert omega_review["reviewed_sha"] == "pr-head-omega"
    assert omega_review["reviewed_sha"] != omega_row["head_sha"]
    assert (
        omega_review["url"] == "https://github.com/swcstudiospace/Omega-Prime/pull/41"
    )
    marker_review = fleet_row["greptile_review"]
    assert marker_review["source"] == "merged_pr"
    assert marker_review["evidence_source"] == "body"
    assert marker_review["pr_number"] == 42
    assert marker_review["score"] == 3
    assert marker_review["reviewed_sha"] is None
    assert all(url.startswith("https://api.github.com/") for url in calls)
    assert f"Bearer {TOKEN}" not in json.dumps(result.__dict__)


@pytest.mark.parametrize(
    "mode",
    (
        "empty",
        "forbidden",
        "offline",
        "malformed",
        "ambiguous",
        "unprovable",
        "invalid_number",
        "truncated",
        "review_failure",
    ),
)
def test_direct_commit_is_not_applicable_and_lookup_failure_is_row_local(
    mode: str,
) -> None:
    repo = "swcstudiospace/Omega-Prime"
    valid_pr = _pr(repo, 17, "branch-merge", "branch-head")
    calls: list[str] = []

    def transport(url: str, headers: Mapping[str, str]) -> tuple[int, bytes]:
        parsed = urlsplit(url)
        path = parsed.path
        calls.append(url)
        if path == "/orgs/swcstudiospace/repos":
            return 200, json.dumps(
                [
                    {
                        "id": 1,
                        "full_name": repo,
                        "name": "Omega-Prime",
                        "default_branch": "main",
                    }
                ]
            ).encode()
        if path.endswith("/commits/main"):
            return 200, b'{"sha":"branch-merge"}'
        if "/check-runs" in path:
            return 200, b'{"check_runs":[]}'
        if path.endswith("/commits/branch-merge/pulls"):
            if mode == "empty":
                return 200, b"[]"
            if mode == "forbidden":
                return 403, b"synthetic private error"
            if mode == "offline":
                raise OSError("synthetic network failure")
            if mode == "malformed":
                return 200, b"{malformed"
            if mode == "ambiguous":
                second = _pr(repo, 18, "branch-merge", "branch-head-2")
                return 200, json.dumps([valid_pr, second]).encode()
            if mode == "unprovable":
                wrong_target = _pr(repo, 18, "branch-merge", "branch-head-2")
                wrong_target["base"]["ref"] = "release"
                return 200, json.dumps([wrong_target]).encode()
            if mode == "invalid_number":
                invalid = _pr(repo, True, "branch-merge", "branch-head")
                return 200, json.dumps([invalid]).encode()
            if mode == "truncated":
                page = parse_qs(parsed.query).get("page", ["1"])[0]
                return 200, json.dumps(
                    [valid_pr] * 100 if page in {"1", "2", "3"} else []
                ).encode()
            return 200, json.dumps([valid_pr]).encode()
        if path.endswith("/issues/17/comments"):
            return 200, b"[]"
        if path.endswith("/pulls/17/reviews") and mode == "review_failure":
            return 500, b"synthetic private review failure"
        if path.endswith("/pulls/17/reviews"):
            return 200, b"[]"
        if path.endswith("/pulls") and parse_qs(parsed.query).get("state") in (
            ["open"],
            ["closed"],
        ):
            return 200, b"[]"
        raise AssertionError(path)

    result = make_github_collector(
        env={"GITHUB_PAT_DESK_LEAD": TOKEN}, transport=transport
    )()
    cc = result.extra["command_center"]
    assert cc["repositories"][0]["full_name"] == repo
    assert cc["recent_merges"] == []
    row = next(row for row in cc["ci_cd"] if row["kind"] == "branch")
    assert row["checks"] == []
    review = row["greptile_review"]
    if mode == "empty":
        assert review["source"] == "not_applicable"
        assert review["evidence_source"] == "not_applicable"
        assert review["pr_number"] is None
    else:
        assert review["source"] == "unavailable"
        assert review["evidence_source"] == "unavailable"
        assert review["score"] is None
    assert cc["meta"]["sections"]["ci_cd"]["partial"] is False
    assert cc["meta"]["sections"]["ci_cd"]["reason"] is None
    assert cc["meta"]["errors"] == []
    assert cc["meta"]["sections"]["repositories"]["partial"] is False
    assert cc["meta"]["sections"]["recent_merges"]["partial"] is False
    assert all("synthetic private" not in call for call in calls)
    assert "synthetic private error" not in json.dumps(result.__dict__)


def test_merged_pr_without_greptile_review_names_the_pr_instead_of_unavailable():
    repo = "swcstudiospace/Omega-Prime"
    dependabot = _pr(repo, 29, "branch-merge", "branch-head")

    def transport(url: str, headers: Mapping[str, str]) -> tuple[int, bytes]:
        parsed = urlsplit(url)
        path = parsed.path
        if path == "/orgs/swcstudiospace/repos":
            return 200, json.dumps(
                [
                    {
                        "id": 1,
                        "full_name": repo,
                        "name": "Omega-Prime",
                        "default_branch": "main",
                    }
                ]
            ).encode()
        if path.endswith("/commits/main"):
            return 200, b'{"sha":"branch-merge"}'
        if "/check-runs" in path:
            return 200, b'{"check_runs":[]}'
        if path.endswith("/commits/branch-merge/pulls"):
            return 200, json.dumps([dependabot]).encode()
        if path.endswith("/issues/29/comments"):
            return 200, json.dumps(
                [
                    {
                        "user": {"login": "someone"},
                        "body": "@dependabot rebase",
                        "created_at": "2026-10-20T20:48:43Z",
                    }
                ]
            ).encode()
        if path.endswith("/pulls/29/reviews"):
            return 200, b"[]"
        if path.endswith("/pulls") and parse_qs(parsed.query).get("state") in (
            ["open"],
            ["closed"],
        ):
            return 200, b"[]"
        raise AssertionError(path)

    result = make_github_collector(
        env={"GITHUB_PAT_DESK_LEAD": TOKEN}, transport=transport
    )()
    cc = result.extra["command_center"]
    review = next(row for row in cc["ci_cd"] if row["kind"] == "branch")[
        "greptile_review"
    ]
    assert review["source"] == "merged_pr_unreviewed"
    assert review["pr_number"] == 29
    assert review["score"] is None
    assert review["url"] == "https://github.com/swcstudiospace/Omega-Prime/pull/29"
    assert cc["meta"]["errors"] == []
