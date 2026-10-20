# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Read-only GitHub pull request gate collector."""

from __future__ import annotations

import json
import os
import re
import types
import urllib.request
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from omega_prime.credentials.broker import CredentialBroker
from omega_prime.dashboard.model import SourceResult
from omega_prime.dashboard.redact import redact_value

_SCORE = re.compile(r"Confidence score:\s*([0-5])\s*/\s*5", re.IGNORECASE)
_BODY_SCORE = re.compile(
    r"<!--\s*greptile_confidence_score:\s*([0-5])\s*-->", re.IGNORECASE
)


class _GitHubEndpointPolicy:
    """Allow credentials only for GitHub's API host."""

    def allows_host(self, host: str) -> bool:
        return host == "api.github.com"


def _request(url: str, headers: Mapping[str, str]) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    with urllib.request.urlopen(request, timeout=10.0) as response:
        return response.status, response.read(1_000_000)


def make_github_collector(
    *,
    env: Mapping[str, str] | None = None,
    transport: Callable[[str, Mapping[str, str]], tuple[int, bytes]] | None = None,
    now_fn: Callable[[], datetime] | None = None,
    cache_ttl: float = 60.0,
    repo: str = "swcstudiospace/omes-bot",
) -> Callable[[], SourceResult]:
    """Create a cached, PAT-authenticated GitHub collector."""
    send = transport or _request
    clock = now_fn or (lambda: datetime.now(UTC))
    cache: list[dict[str, Any]] | None = None
    fetched: datetime | None = None

    def get(url: str, headers: Mapping[str, str]) -> Any:
        status, body = send(url, headers)
        if status != 200:
            raise RuntimeError("http")
        data = json.loads(body[:1_000_000])
        return data

    def collect() -> SourceResult:
        nonlocal cache, fetched
        try:
            provider = types.SimpleNamespace(
                name="github-dashboard",
                requires_key=True,
                env_vars=("GITHUB_PAT_DESK_LEAD",),
            )
            token = str(
                CredentialBroker(
                    _GitHubEndpointPolicy(), env=os.environ if env is None else env
                ).key_for(provider, "https://api.github.com/")
            ).strip()
        except Exception:
            token = ""
        if not token:
            return SourceResult("github", "unavailable", error="GitHub not configured")
        now = clock()
        if (
            cache is not None
            and fetched is not None
            and (now - fetched).total_seconds() < cache_ttl
        ):
            return SourceResult("github", "ok", items=cache)
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        base = f"https://api.github.com/repos/{repo}"
        try:
            prs = get(f"{base}/pulls?state=open&per_page=100", headers)
            items: list[dict[str, Any]] = []
            if not isinstance(prs, list):
                raise ValueError("payload")

            def one(pr: Any) -> dict[str, Any] | None:
                if not isinstance(pr, dict):
                    return None
                number = pr.get("number")
                head_value = pr.get("head")
                head = head_value if isinstance(head_value, dict) else {}
                checks = get(
                    f"{base}/commits/{head.get('sha', '')}/check-runs", headers
                )
                runs = checks.get("check_runs", []) if isinstance(checks, dict) else []
                conclusions = [
                    run.get("conclusion") for run in runs if isinstance(run, dict)
                ]
                if not conclusions:
                    ci = "unknown"
                elif any(
                    x in {"failure", "timed_out", "action_required", "cancelled"}
                    for x in conclusions
                ):
                    ci = "fail"
                elif all(x in {"success", "skipped", "neutral"} for x in conclusions):
                    ci = "pass"
                else:
                    ci = "pending"
                head_sha = head.get("sha")
                body = pr.get("body")
                body_match = _BODY_SCORE.search(body) if isinstance(body, str) else None
                score = body_match.group(1) if body_match else ""
                if not score:
                    # Only fetch reviews/comments when the PR body has no score.
                    comments = get(f"{base}/issues/{number}/comments", headers)
                    reviews = get(f"{base}/pulls/{number}/reviews", headers)
                    # Latest greptile-apps[bot] result for the current head:
                    # bot reviews pinned to this head sha and bot comments,
                    # ordered by timestamp so an older review cannot override
                    # a newer comment. Human comments are ignored entirely.
                    candidates: list[tuple[str, str]] = []
                    if isinstance(reviews, list):
                        for record in reviews:
                            if (
                                isinstance(record, dict)
                                and isinstance(record.get("user"), dict)
                                and record["user"].get("login") == "greptile-apps[bot]"
                                and record.get("commit_id") == head_sha
                                and isinstance(record.get("body"), str)
                            ):
                                candidates.append(
                                    (
                                        str(
                                            record.get("submitted_at")
                                            or record.get("created_at")
                                            or ""
                                        ),
                                        record["body"],
                                    )
                                )
                    if isinstance(comments, list):
                        for record in comments:
                            if (
                                isinstance(record, dict)
                                and isinstance(record.get("user"), dict)
                                and record["user"].get("login") == "greptile-apps[bot]"
                                and isinstance(record.get("body"), str)
                            ):
                                candidates.append(
                                    (
                                        str(record.get("created_at") or ""),
                                        record["body"],
                                    )
                                )
                    candidates.sort(key=lambda pair: pair[0])
                    for _, text in reversed(candidates):
                        match = _SCORE.search(text)
                        if match:
                            score = match.group(1)
                            break
                item = {
                    "kind": "github_pr",
                    "repo": repo,
                    "number": number,
                    "title": pr.get("title"),
                    "branch": head.get("ref"),
                    "draft": bool(pr.get("draft")),
                    "mergeable": pr.get("mergeable"),
                    "ci": ci,
                    "greptile": f"{score}/5" if score else "not reviewed",
                    "updated_at": pr.get("updated_at"),
                    "url": pr.get("html_url"),
                }
                return dict(redact_value(item, extra=(token,), env={}))

            # Per-PR calls run concurrently so 13+ PRs fit inside the
            # collector timeout instead of ~40 sequential round trips.
            with ThreadPoolExecutor(max_workers=8) as pool:
                rows = list(pool.map(one, prs))
            items.extend(row for row in rows if row is not None)
            cache, fetched = items, now
            return SourceResult("github", "ok", items=items)
        except Exception as exc:
            age = (
                max(0, int((now - fetched).total_seconds()))
                if fetched is not None
                else 0
            )
            error = (
                f"rate limited; cached {age}s old"
                if cache is not None
                else f"github request failed: {type(exc).__name__}"
            )
            return SourceResult("github", "error", items=cache or [], error=error)

    return collect
