# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Read-only GitHub pull request gate collector."""

from __future__ import annotations

import json
import os
import re
import urllib.request
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from omega_prime.dashboard.model import SourceResult
from omega_prime.dashboard.redact import redact_value

_SCORE = re.compile(r"Confidence score:\s*([1-5])\s*/\s*5")


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
        token = (os.environ if env is None else env).get("GITHUB_PAT_DESK_LEAD", "")
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
            for pr in prs:
                if not isinstance(pr, dict):
                    continue
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
                bodies: list[str] = []
                for endpoint in (
                    f"{base}/issues/{number}/comments",
                    f"{base}/pulls/{number}/reviews",
                ):
                    records = get(endpoint, headers)
                    if isinstance(records, list):
                        bodies.extend(
                            str(record.get("body", ""))
                            for record in records
                            if isinstance(record, dict)
                        )
                matches = _SCORE.findall("\n".join(bodies))
                item = {
                    "kind": "github_pr",
                    "repo": repo,
                    "number": number,
                    "title": pr.get("title"),
                    "branch": head.get("ref"),
                    "draft": bool(pr.get("draft")),
                    "mergeable": pr.get("mergeable"),
                    "ci": ci,
                    "greptile": f"{matches[-1]}/5" if matches else "not reviewed",
                    "updated_at": pr.get("updated_at"),
                    "url": pr.get("html_url"),
                }
                items.append(redact_value(item, extra=(token,), env={}))
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
