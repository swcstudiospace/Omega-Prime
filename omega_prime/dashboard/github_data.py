"""Pure, bounded projection helpers for GitHub API data."""

from __future__ import annotations

from datetime import datetime
from typing import Any

MAX_REPOSITORIES = 300
MAX_ROWS = 300
MAX_CHECKS = 300
MAX_MERGES = 100
MAX_ERRORS = 20
MAX_PAGES = 3
PAGE_SIZE = 100
ASSOCIATION_PAGE_SIZE = 100
ASSOCIATION_PAGES = 3
CLOSED_PAGE_SIZE = 30
MAX_CLOSED_PAGES = 10


def ci_state(runs: list[dict[str, Any]]) -> str:
    conclusions = [run.get("conclusion") for run in runs]
    if not conclusions:
        return "unknown"
    if any(value in {"failure", "timed_out", "action_required", "cancelled"} for value in conclusions):
        return "fail"
    if all(value in {"success", "skipped", "neutral"} for value in conclusions):
        return "pass"
    return "pending"


def check_rows(payload: Any, expected_sha: str) -> list[dict[str, Any]]:
    runs = payload.get("check_runs", []) if isinstance(payload, dict) else []
    if not isinstance(runs, list):
        return []
    result = []
    for run in runs[:MAX_CHECKS]:
        if not isinstance(run, dict):
            continue
        sha = run.get("head_sha")
        if sha and expected_sha and sha != expected_sha:
            continue
        started, completed = run.get("started_at"), run.get("completed_at")
        duration = None
        try:
            if isinstance(started, str) and isinstance(completed, str):
                a = datetime.fromisoformat(started.replace("Z", "+00:00"))
                b = datetime.fromisoformat(completed.replace("Z", "+00:00"))
                seconds = (b - a).total_seconds()
                if seconds >= 0:
                    duration = int(seconds)
        except (TypeError, ValueError):
            pass
        result.append({
            "id": run.get("id"), "name": _text(run.get("name"), 200),
            "status": _text(run.get("status"), 40), "conclusion": _text(run.get("conclusion"), 40),
            "started_at": _text(started, 40), "completed_at": _text(completed, 40),
            "duration_s": duration, "details_url": _url(run.get("html_url")),
            "head_sha": _text(sha or expected_sha, 80),
        })
    return result


def repository_row(repo: dict[str, Any], count: int | None, latest: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "id": repo.get("id"), "full_name": _text(repo.get("full_name"), 200),
        "name": _text(repo.get("name"), 100), "url": _url(repo.get("html_url")),
        "visibility": _text(repo.get("visibility"), 30), "default_branch": _text(repo.get("default_branch"), 100),
        "pushed_at": _text(repo.get("pushed_at"), 40), "language": _text(repo.get("language"), 80),
        "open_pr_count": count, "latest_main_ci": latest,
    }


def merge_row(repo: str, pr: dict[str, Any]) -> dict[str, Any] | None:
    merged = pr.get("merged_at")
    if not isinstance(merged, str) or not merged:
        return None
    return {"id": pr.get("id"), "repo": repo, "number": pr.get("number"),
            "title": _text(pr.get("title"), 300), "url": _url(pr.get("html_url")),
            "merged_at": _text(merged, 40), "merge_commit_sha": _text(pr.get("merge_commit_sha"), 80)}


def _text(value: Any, limit: int) -> str | None:
    return value[:limit] if isinstance(value, str) else None


def _url(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 1000:
        return None
    from urllib.parse import urlsplit
    parsed = urlsplit(value)
    return value if parsed.scheme == "https" and parsed.hostname == "github.com" else None
