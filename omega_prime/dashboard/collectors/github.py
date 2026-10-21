# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Read-only, bounded GitHub organization and command-center collector."""

from __future__ import annotations

import json
import os
import urllib.error
import time
import re
import threading
import types
import urllib.request
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote
from urllib.parse import urlencode

from omega_prime.credentials.broker import CredentialBroker
from omega_prime.dashboard.github_data import (
    CLOSED_PAGE_SIZE, MAX_CLOSED_PAGES, MAX_ERRORS, MAX_MERGES, MAX_PAGES,
    MAX_REPOSITORIES, MAX_ROWS, PAGE_SIZE, ASSOCIATION_PAGES, ASSOCIATION_PAGE_SIZE,
    check_rows, ci_state, merge_row, repository_row,
)
from omega_prime.dashboard.model import SourceResult
from omega_prime.dashboard.redact import redact_value

_SCORE = re.compile(r"Confidence score:\s*([0-5])\s*/\s*5", re.IGNORECASE)
_BODY_SCORE = re.compile(r"<!--\s*greptile_confidence_score:\s*([0-5])\s*-->", re.IGNORECASE)
API = "https://api.github.com"
ORG = "swcstudiospace"
TARGETS = (f"{ORG}/Omega-Prime", f"{ORG}/coding-fleet")  # omes-bot was renamed; the API 301s and redirects are refused

_SAFE_REPO = re.compile(r"^swcstudiospace/[A-Za-z0-9_.-]{1,100}$")
_SAFE_REF = re.compile(r"^[A-Za-z0-9_-]{1,80}$")

MAX_RESPONSE_BYTES = 1_000_000


class _ResponseTooLarge(ValueError):
    """GitHub response exceeded the bounded body size."""


class _GitHubEndpointPolicy:
    def allows_host(self, host: str) -> bool:
        return host == "api.github.com"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request: Any, fp: Any, code: int, msg: str, headers: Any, new_url: str) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())


def _request(url: str, headers: Mapping[str, str]) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    # urllib does not forward credentials across origins; require the API host here too.
    if not url.startswith(API + "/"):
        raise ValueError("untrusted GitHub API URL")
    try:
        with _OPENER.open(request, timeout=2.5) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise _ResponseTooLarge("payload")
        return response.status, body
    except urllib.error.HTTPError as exc:
        return exc.code, b""




def _stamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def make_github_collector(
    *, env: Mapping[str, str] | None = None,
    transport: Callable[[str, Mapping[str, str]], tuple[int, bytes]] | None = None,
    now_fn: Callable[[], datetime] | None = None,
    cache_ttl: float = 90.0,
    repo: str | None = None,
) -> Callable[[], SourceResult]:
    """Create a collector whose only network operation is bounded GitHub GET."""
    send = transport or _request
    clock = now_fn or (lambda: datetime.now(UTC))
    cache: SourceResult | None = None
    fetched: datetime | None = None

    def collect() -> SourceResult:
        nonlocal cache, fetched
        try:
            provider = types.SimpleNamespace(name="github-dashboard", requires_key=True, env_vars=("GITHUB_PAT_DESK_LEAD",))
            token = str(CredentialBroker(_GitHubEndpointPolicy(), env=os.environ if env is None else env).key_for(provider, API + "/")).strip()
        except Exception:
            token = ""
        if not token:
            if cache is not None:
                cc = cache.extra.get("command_center", {}) if isinstance(cache.extra, dict) else {}
                meta = cc.get("meta", {}) if isinstance(cc, dict) else {}
                if meta.get("reason") == "unauthenticated":
                    return cache
                cache = _degraded(cache, clock(), fetched, "unauthenticated", "unauthenticated")
                return cache
            return SourceResult("github", "unavailable", error="GitHub not configured", extra={"command_center": _empty_cc(clock(), "unauthenticated")})
        if repo is not None and not _SAFE_REPO.fullmatch(repo):
            return SourceResult("github", "unavailable", error="GitHub repository is not allowed", extra={"command_center": _empty_cc(clock(), "error")})
        now = clock()
        if cache is not None and fetched is not None and (now - fetched).total_seconds() < cache_ttl:
            return cache
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        previous_result = cache
        budget = [1000]
        budget_lock = threading.Lock()
        deadline = time.monotonic() + 12.0
        errors: list[str] = []
        truncated = {"repositories": False, "ci_cd": False, "recent_merges": False}
        partial = {"repositories": False, "ci_cd": False, "recent_merges": False}
        reason: dict[str, str | None] = {key: None for key in partial}
        failed_ci_repos: set[str] = set()
        failed_merge_repos: set[str] = set()
        failed_count_repos: set[str] = set()
        inventory_failed = False

        def get(url: str) -> Any:
            if not url.startswith(API + "/") or time.monotonic() >= deadline:
                raise RuntimeError("budget")
            with budget_lock:
                if budget[0] <= 0:
                    raise RuntimeError("budget")
                budget[0] -= 1
            status, body = send(url, headers)
            if time.monotonic() >= deadline:
                raise RuntimeError("budget")
            if status != 200:
                exc = RuntimeError("http_" + str(status))
                setattr(exc, "status", status)
                raise exc
            if not isinstance(body, bytes):
                raise ValueError("payload")
            if len(body) > MAX_RESPONSE_BYTES:
                raise _ResponseTooLarge("payload")
            return json.loads(body)



        def pages(path: str, params: Mapping[str, Any] | None = None) -> tuple[list[Any], bool]:
            rows: list[Any] = []
            capped = False
            for page in range(1, MAX_PAGES + 1):
                query = {"per_page": PAGE_SIZE, "page": page}
                if params:
                    query.update(params)
                url = path if path.startswith(API + "/") else API + path
                data = get(url + "?" + urlencode(query))
                if not isinstance(data, list):
                    raise ValueError("payload")
                rows.extend(data[:PAGE_SIZE])
                if len(data) < PAGE_SIZE:
                    break
                if page == MAX_PAGES:
                    capped = True
            return rows, capped
        def get_checks(base: str, sha: str, owner: str) -> list[dict[str, Any]]:
            runs: list[dict[str, Any]] = []
            for page in range(1, MAX_PAGES + 1):
                payload = get(base + "/commits/" + quote(sha, safe="") + "/check-runs?" + urlencode({"per_page": PAGE_SIZE, "page": page}))
                page_runs = payload.get("check_runs") if isinstance(payload, dict) else None
                if not isinstance(page_runs, list):
                    raise ValueError("payload")
                runs.extend(item for item in page_runs[:PAGE_SIZE] if isinstance(item, dict))
                if len(page_runs) < PAGE_SIZE:
                    break
                if page == MAX_PAGES:
                    truncated["ci_cd"] = True
                    failed_ci_repos.add(owner)
                    partial["ci_cd"], reason["ci_cd"] = True, "partial"
            return check_rows({"check_runs": runs}, sha)

        def associated_merged_pr(base: str, full_name: str, branch: str, sha: str) -> dict[str, Any] | None:
            associated: list[Any] = []
            for page in range(1, ASSOCIATION_PAGES + 1):
                data = get(base + "/commits/" + quote(sha, safe="") + "/pulls?" + urlencode({"per_page": ASSOCIATION_PAGE_SIZE, "page": page}))
                if not isinstance(data, list) or len(data) > ASSOCIATION_PAGE_SIZE:
                    raise ValueError("payload")
                if any(not isinstance(item, dict) for item in data):
                    raise ValueError("payload")
                associated.extend(data)
                if len(data) < ASSOCIATION_PAGE_SIZE:
                    break
                if page == ASSOCIATION_PAGES:
                    raise RuntimeError("budget")
            if not associated:
                return None
            matches: list[dict[str, Any]] = []
            for pr in associated:
                number = pr.get("number")
                merge_sha = pr.get("merge_commit_sha")
                merged_at = pr.get("merged_at")
                base_data = pr.get("base")
                base_repo = base_data.get("repo") if isinstance(base_data, dict) else None
                base_name = base_repo.get("full_name") if isinstance(base_repo, dict) else None
                base_ref = base_data.get("ref") if isinstance(base_data, dict) else None
                head = pr.get("head")
                head_sha = head.get("sha") if isinstance(head, dict) else None
                if not (isinstance(number, int) and not isinstance(number, bool) and 0 < number < 2**31):
                    raise ValueError("payload")
                if not isinstance(merge_sha, str) or not _SAFE_REF.fullmatch(merge_sha):
                    raise ValueError("payload")
                if not isinstance(base_name, str) or not _SAFE_REPO.fullmatch(base_name):
                    raise ValueError("payload")
                if not isinstance(base_ref, str) or not _SAFE_REF.fullmatch(base_ref):
                    raise ValueError("payload")
                if not isinstance(head_sha, str) or not _SAFE_REF.fullmatch(head_sha):
                    raise ValueError("payload")
                if merged_at is not None and not isinstance(merged_at, str):
                    raise ValueError("payload")
                if merged_at and merge_sha == sha and base_name == full_name and base_ref == branch:
                    matches.append(pr)
            if len(matches) != 1:
                raise ValueError("association")
            return matches[0]

        def mark_ci_truncated(owner: str) -> None:
            truncated["ci_cd"] = True
            failed_ci_repos.add(owner)
            partial["ci_cd"], reason["ci_cd"] = True, "partial"

        errors_lock = threading.Lock()
        def fail(section: str, exc: Exception) -> None:
            partial[section] = True
            status = getattr(exc, "status", None)
            if isinstance(exc, _ResponseTooLarge):
                why = "too_large"
            else:
                why = "rate_limited" if status == 429 else "forbidden" if status == 403 else "error" if isinstance(exc, (ValueError, json.JSONDecodeError)) else "partial" if str(exc) == "budget" else "offline"
            reason[section] = why
            with errors_lock:
                if len(errors) < MAX_ERRORS:
                    errors.append(section + ":" + why)

        def mark_ci_failure(owner: str, exc: Exception) -> None:
            failed_ci_repos.add(owner)
            fail("ci_cd", exc)
        repository_records: list[dict[str, Any]] = []
        if repo is None:
            try:
                inventory, cap = pages(f"/orgs/{ORG}/repos", {"type": "all", "sort": "full_name"})
                dedup: dict[Any, dict[str, Any]] = {}
                for item in inventory[:MAX_REPOSITORIES]:
                    if isinstance(item, dict) and isinstance(item.get("id"), (str, int)) and isinstance(item.get("full_name"), str) and _SAFE_REPO.fullmatch(item["full_name"]):
                        dedup[item["id"]] = item
                repository_records = sorted(dedup.values(), key=lambda x: (str(x.get("full_name", "")).casefold(), str(x.get("id", ""))))
                truncated["repositories"] = cap or len(inventory) > MAX_REPOSITORIES
                if truncated["repositories"]:
                    partial["repositories"], reason["repositories"] = True, "partial"
            except Exception as exc:
                inventory_failed = True
                fail("repositories", exc)
        selected = [repo] if repo else list(TARGETS)
        repository_by_name = {str(r.get("full_name")): r for r in repository_records}
        for name in selected:
            if repo is not None:
                repository_by_name.setdefault(name, {"full_name": name, "name": name.rsplit("/", 1)[-1]})
        rows: list[dict[str, Any]] = []
        legacy_items: list[dict[str, Any]] = []
        repos_out: list[dict[str, Any]] = []
        merges: list[dict[str, Any]] = []
        def closed_pages(full_name: str, path: str) -> tuple[list[dict[str, Any]], bool]:
            found: list[dict[str, Any]] = []
            capped = False
            for page in range(1, MAX_CLOSED_PAGES + 1):
                query = urlencode({"state": "closed", "sort": "updated", "direction": "desc", "per_page": CLOSED_PAGE_SIZE, "page": page})
                url = path if path.startswith(API + "/") else API + path
                data = get(url + "?" + query)
                if not isinstance(data, list):
                    raise ValueError("payload")
                for item in data[:CLOSED_PAGE_SIZE]:
                    if isinstance(item, dict):
                        merge = merge_row(full_name, item)
                        if merge:
                            found.append(merge)
                            if len(found) == MAX_MERGES:
                                return found, False
                if len(data) < CLOSED_PAGE_SIZE:
                    break
                if page == MAX_CLOSED_PAGES:
                    capped = True
            return found, capped

        for full_name in selected:
            base = API + f"/repos/{full_name}"
            open_prs: list[Any] = []
            open_complete = False
            try:
                open_prs, cap = pages(base + "/pulls", {"state": "open", "sort": "updated", "direction": "desc"})
                open_complete = not cap and len(open_prs) <= MAX_ROWS
                truncated["ci_cd"] |= not open_complete
                if not open_complete:
                    partial["ci_cd"], reason["ci_cd"] = True, "partial"
                    failed_ci_repos.add(full_name)
            except Exception as exc:
                fail("ci_cd", exc)
                failed_ci_repos.add(full_name)
            complete_count = len(open_prs) if open_complete else None
            repo_data = repository_by_name.get(full_name, {"full_name": full_name, "name": full_name.rsplit("/", 1)[-1]})
            latest: dict[str, Any] | None = None
            branch = repo_data.get("default_branch")
            if isinstance(branch, str) and _SAFE_REF.fullmatch(branch):
                try:
                    ref = get(base + "/commits/" + quote(branch, safe=""))
                    sha = ref.get("sha") if isinstance(ref, dict) else None
                    if isinstance(sha, str) and _SAFE_REF.fullmatch(sha):
                        checks = get_checks(base, sha, full_name)
                        review = {"score": None, "reviewed_sha": None, "source": "unavailable", "evidence_source": "unavailable", "pr_number": None, "submitted_at": None, "url": None, "matches_head": None}
                        try:
                            merged_pr = associated_merged_pr(base, full_name, branch, sha)
                            if merged_pr is None:
                                review["source"] = "not_applicable"
                                review["evidence_source"] = "not_applicable"
                            else:
                                number = merged_pr["number"]
                                head = merged_pr["head"]
                                reviewed_sha = head["sha"]
                                extraction_failed = [False]
                                def review_truncated() -> None:
                                    extraction_failed[0] = True
                                def review_failed(exc: Exception) -> None:
                                    extraction_failed[0] = True
                                evidence = _greptile(merged_pr, full_name, number, reviewed_sha, base, get, review_truncated, review_failed)
                                if evidence is None and not extraction_failed[0]:
                                    review = {
                                        "score": None, "reviewed_sha": None, "source": "merged_pr_unreviewed",
                                        "evidence_source": "none", "pr_number": number, "submitted_at": None,
                                        "url": _safe_url(merged_pr.get("html_url")), "matches_head": None,
                                    }
                                elif evidence is not None and not extraction_failed[0]:
                                    review = {
                                        "score": evidence["score"], "reviewed_sha": evidence["reviewed_sha"],
                                        "source": "merged_pr", "evidence_source": evidence["source"],
                                        "pr_number": number, "submitted_at": evidence["submitted_at"],
                                        "url": _safe_url(merged_pr.get("html_url")) or evidence["url"],
                                        "matches_head": evidence["matches_head"],
                                    }
                        except Exception:
                            pass
                        latest = {"branch": branch, "head_sha": sha, "ci": ci_state(checks), "checks": checks, "greptile_review": review}
                        rows.append({"id": f"{full_name}:{branch}:{sha}", "kind": "branch", "repo": full_name, "number": None, "title": None, "url": None, "branch": branch, "base_branch": None, "head_sha": sha, "draft": None, "mergeable": None, "ci": latest["ci"], "checks": checks, "greptile_review": review})
                except Exception as exc:
                    fail("ci_cd", exc)
                    failed_ci_repos.add(full_name)
            def project_pr(pr: Any) -> tuple[dict[str, Any], dict[str, Any]] | None:
                if not isinstance(pr, dict):
                    return None
                head = pr.get("head") if isinstance(pr.get("head"), dict) else {}
                sha = head.get("sha") if isinstance(head.get("sha"), str) else ""
                number = pr.get("number")
                checks: list[dict[str, Any]] = []
                try:
                    checks = get_checks(base, sha, full_name) if _SAFE_REF.fullmatch(sha) else []
                except Exception as exc:
                    fail("ci_cd", exc)
                    failed_ci_repos.add(full_name)
                state = ci_state(checks)
                number_ok = isinstance(number, int) and not isinstance(number, bool) and 0 < number < 2**31
                body = pr.get("body")
                has_body_score = isinstance(body, str) and _BODY_SCORE.search(body) is not None
                review = _greptile(pr, full_name, number, sha, base, get, lambda: mark_ci_truncated(full_name), lambda exc: mark_ci_failure(full_name, exc)) if has_body_score or (number_ok and _SAFE_REF.fullmatch(sha)) else None
                if review is not None:
                    review = {**review, "pr_number": number if number_ok else None, "evidence_source": review["source"]}
                rich = {"id": pr.get("id", f"{full_name}:{number}"), "kind": "pull_request", "repo": full_name, "number": number, "title": _safe_text(pr.get("title"), 300), "url": _safe_url(pr.get("html_url")), "branch": _safe_text(head.get("ref"), 200), "base_branch": _safe_text((pr.get("base") or {}).get("ref") if isinstance(pr.get("base"), dict) else None, 200), "head_sha": sha or None, "draft": bool(pr.get("draft")), "mergeable": pr.get("mergeable") if isinstance(pr.get("mergeable"), bool) else None, "ci": state, "checks": checks, "greptile_review": review}
                legacy = {"kind": "github_pr", "repo": full_name, "number": number, "title": _safe_text(pr.get("title"), 300), "branch": _safe_text(head.get("ref"), 200), "draft": bool(pr.get("draft")), "mergeable": pr.get("mergeable"), "ci": state, "greptile": f"{review['score']}/5" if review and review["score"] is not None else "not reviewed", "updated_at": pr.get("updated_at"), "url": _safe_url(pr.get("html_url"))}
                return rich, legacy

            pr_limit = max(0, MAX_ROWS - (1 if latest is not None else 0))
            with ThreadPoolExecutor(max_workers=8) as pool:
                projected = list(pool.map(project_pr, open_prs[:pr_limit]))
            if len(open_prs) > pr_limit:
                truncated["ci_cd"] = True
                partial["ci_cd"], reason["ci_cd"] = True, "partial"
            for pair in projected:
                if pair:
                    rows.append(pair[0])
                    legacy_items.append(pair[1])
            if full_name in repository_by_name:
                repos_out.append(repository_row(repo_data, complete_count, latest))
            if repo is None:
                try:
                    closed, cap = closed_pages(full_name, base + "/pulls")
                    merges.extend(closed)
                    truncated["recent_merges"] |= cap
                    if cap:
                        partial["recent_merges"], reason["recent_merges"] = True, "partial"
                        failed_merge_repos.add(full_name)
                except Exception as exc:
                    fail("recent_merges", exc)
                    failed_merge_repos.add(full_name)

        if repo is None:
            selected_names = set(selected)
            count_by_name = {item["full_name"]: item["open_pr_count"] for item in repos_out}

            def count_repository(record: dict[str, Any]) -> tuple[str, int | None]:
                name = record.get("full_name")
                if not isinstance(name, str):
                    return "", None
                if name in count_by_name:
                    return name, count_by_name[name]
                try:
                    open_rows, capped = pages(f"/repos/{name}/pulls", {"state": "open"})
                    if capped or len(open_rows) > MAX_ROWS:
                        truncated["repositories"] = True
                        partial["repositories"], reason["repositories"] = True, "partial"
                        failed_count_repos.add(name)
                        return name, None
                    return name, len(open_rows)
                except Exception as exc:
                    fail("repositories", exc)
                    failed_count_repos.add(name)
                    return name, None

            other_repos = [item for item in repository_records if item.get("full_name") not in selected_names]
            with ThreadPoolExecutor(max_workers=16) as pool:
                counts = dict(pool.map(count_repository, other_repos))
            for record in repository_records:
                full = record.get("full_name")
                if isinstance(full, str) and full not in selected_names:
                    repos_out.append(repository_row(record, counts.get(full), None))
            repos_out = sorted(repos_out, key=lambda x: (str(x.get("full_name", "")).casefold(), str(x.get("id", ""))))[:MAX_REPOSITORIES]
        if len(merges) > MAX_MERGES:
            truncated["recent_merges"] = True
        merges.sort(key=lambda x: (str(x.get("merged_at", "")), str(x.get("id", ""))), reverse=True)
        sections = {name: {"fetched_at": _stamp(now), "stale": False, "partial": partial[name], "reason": reason[name]} for name in partial}
        cc = {"repositories": repos_out, "ci_cd": rows[:MAX_ROWS * len(selected)], "recent_merges": merges[:MAX_MERGES], "meta": {"fetched_at": _stamp(now), "last_attempt_at": _stamp(now), "cache_ttl_s": int(cache_ttl), "stale": False, "partial": any(partial.values()), "reason": next((x for x in reason.values() if x), None), "errors": errors[:MAX_ERRORS], "truncated": truncated, "sections": sections}}
        previous_cc = previous_result.extra.get("command_center") if previous_result and isinstance(previous_result.extra, dict) else None
        if partial["repositories"] and isinstance(previous_cc, dict):
            old_repos = previous_cc.get("repositories", [])
            if inventory_failed:
                cc["repositories"] = old_repos
            elif failed_count_repos:
                old_by_name = {item.get("full_name"): item for item in old_repos if isinstance(item, dict)}
                cc["repositories"] = [old_by_name.get(item.get("full_name"), item) if item.get("full_name") in failed_count_repos else item for item in cc["repositories"]]
        if partial["ci_cd"] and isinstance(previous_cc, dict):
            old_rows = previous_cc.get("ci_cd", [])
            if failed_ci_repos:
                cc["ci_cd"] = [item for item in cc["ci_cd"] if item.get("repo") not in failed_ci_repos] + [item for item in old_rows if item.get("repo") in failed_ci_repos]
            else:
                cc["ci_cd"] = old_rows
        if partial["recent_merges"] and isinstance(previous_cc, dict):
            old_merges = previous_cc.get("recent_merges", [])
            if failed_merge_repos:
                cc["recent_merges"] = [item for item in cc["recent_merges"] if item.get("repo") not in failed_merge_repos] + [item for item in old_merges if item.get("repo") in failed_merge_repos]
            else:
                cc["recent_merges"] = old_merges
        for section in ("repositories", "ci_cd", "recent_merges"):
            if partial[section]:
                old_sections = previous_cc.get("meta", {}).get("sections", {}) if isinstance(previous_cc, dict) else {}
                old_section = old_sections.get(section, {}) if isinstance(old_sections, dict) else {}
                sections[section]["fetched_at"] = old_section.get("fetched_at")
                sections[section]["stale"] = isinstance(previous_cc, dict)
                sections[section]["partial"] = True
                sections[section]["reason"] = reason[section]
        cc["meta"]["stale"] = any(value["stale"] for value in sections.values())
        cc["meta"]["partial"] = any(partial.values())
        safe_items = [dict(redact_value(x, extra=(token,), env={})) for x in legacy_items]
        cc = redact_value(cc, extra=(token,), env={})
        cache = SourceResult("github", "ok" if not any(partial.values()) else "degraded", items=safe_items, extra={"command_center": cc})
        fetched = now
        return cache

    return collect


def _greptile(pr: dict[str, Any], full_name: str, number: Any, sha: str, base: str, get: Callable[[str], Any], on_truncate: Callable[[], None], on_error: Callable[[Exception], None]) -> dict[str, Any] | None:
    body = pr.get("body")
    marker = _BODY_SCORE.search(body) if isinstance(body, str) else None
    if marker:
        return {"score": int(marker.group(1)), "reviewed_sha": None, "source": "body", "submitted_at": None, "url": None, "matches_head": None}
    def read_records(endpoint: str) -> list[Any]:
        records: list[Any] = []
        for page in range(1, MAX_PAGES + 1):
            data = get(base + endpoint + "?" + urlencode({"per_page": PAGE_SIZE, "page": page}))
            if not isinstance(data, list):
                raise ValueError("payload")
            records.extend(data[:PAGE_SIZE])
            if len(data) < PAGE_SIZE:
                break
            if len(data) == PAGE_SIZE and page == MAX_PAGES:
                on_truncate()
        return records
    try:
        comments = read_records(f"/issues/{number}/comments")
        reviews = read_records(f"/pulls/{number}/reviews")
    except Exception as exc:
        on_error(exc)
        return None
    candidates = []
    if isinstance(reviews, list):
        for item in reviews:
            if isinstance(item, dict) and isinstance(item.get("user"), dict) and item["user"].get("login") == "greptile-apps[bot]" and item.get("commit_id") == sha and isinstance(item.get("body"), str):
                candidates.append((str(item.get("submitted_at") or item.get("created_at") or ""), item["body"], "review", item.get("commit_id"), item.get("html_url")))
    if isinstance(comments, list):
        for item in comments:
            if isinstance(item, dict) and isinstance(item.get("user"), dict) and item["user"].get("login") == "greptile-apps[bot]" and isinstance(item.get("body"), str):
                candidates.append((str(item.get("created_at") or ""), item["body"], "comment", None, item.get("html_url")))
    for stamp, text, source, reviewed_sha, url in sorted(candidates, key=lambda x: x[0], reverse=True):
        found = _SCORE.search(text)
        if found:
            return {"score": int(found.group(1)), "reviewed_sha": reviewed_sha, "source": source, "submitted_at": stamp or None, "url": _safe_url(url), "matches_head": reviewed_sha == sha if reviewed_sha else None}
    return None


def _safe_text(value: Any, limit: int) -> str | None:
    return value[:limit] if isinstance(value, str) else None


def _safe_url(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 1000:
        return None
    from urllib.parse import urlsplit
    parsed = urlsplit(value)
    return value if parsed.scheme == "https" and parsed.hostname == "github.com" else None


def _empty_cc(now: datetime, reason: str) -> dict[str, Any]:
    stamp = _stamp(now)
    sections = {name: {"fetched_at": None, "stale": False, "partial": True, "reason": reason} for name in ("repositories", "ci_cd", "recent_merges")}
    return {"repositories": [], "ci_cd": [], "recent_merges": [], "meta": {"fetched_at": None, "last_attempt_at": stamp, "cache_ttl_s": 90, "stale": False, "partial": True, "reason": reason, "errors": [], "truncated": {"repositories": False, "ci_cd": False, "recent_merges": False}, "sections": sections}}


def _degraded(previous: SourceResult, now: datetime, fetched: datetime | None, reason: str, status: str) -> SourceResult:
    import copy
    old = copy.deepcopy(previous)
    cc = old.extra["command_center"]
    cc["meta"].update({"last_attempt_at": _stamp(now), "stale": True, "partial": True, "reason": reason})
    cc["meta"]["errors"] = [reason]
    for section in cc["meta"]["sections"].values():
        section.update({"stale": True, "partial": True, "reason": reason})
    old.status = status
    return old
