# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Linear GraphQL client for the installing user's account.

The API host is Linear's. A configured token never goes through ``gh``.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

from omega_prime.integrations.binding import LINEAR_ENV, resolve_secret
from omega_prime.integrations.transport import TransportError, request_json

LINEAR_API_URL = "https://api.linear.app/graphql"
_ATTEMPTS = 3
_PAGE_SIZE = 50
_MAX_PAGES = 20

Transport = Callable[
    [str, str, Mapping[str, str], Mapping[str, Any] | None], tuple[int, dict[str, Any]]
]


class LinearError(Exception):
    """Linear call failed. ``code`` is auth, transient, empty, or scope."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class LinearClient:
    """One token, one bound account. Retries rate limits and 5xx only."""

    def __init__(
        self,
        token: str,
        transport: Transport | None = None,
        *,
        sleep: Callable[[float], None] | None = None,
        policy: Any = None,
    ) -> None:
        self._token = token
        self._transport = transport or _default_transport(token)
        self._sleep = sleep or time.sleep
        self._policy = policy

    def viewer(self) -> dict[str, str]:
        data = self._query("query { viewer { id name } }", {})
        viewer = data.get("viewer")
        if not isinstance(viewer, dict):
            raise LinearError("empty", "Linear did not return the signed-in user.")
        return {"id": _text(viewer.get("id")), "name": _text(viewer.get("name"))}

    def teams(self) -> list[dict[str, str]]:
        query = (
            "query($after: String) { teams(first: "
            f"{_PAGE_SIZE}, after: $after) "
            "{ nodes { id name } pageInfo { hasNextPage endCursor } } }"
        )
        return self._pages(query, {}, "teams")

    def projects(self, team_id: str) -> list[dict[str, str]]:
        query = (
            "query($id: String!, $after: String) { team(id: $id) { "
            f"projects(first: {_PAGE_SIZE}, after: $after) "
            "{ nodes { id name } pageInfo { hasNextPage endCursor } } } }"
        )
        rows: list[dict[str, str]] = []
        cursor: str | None = None
        for _page in range(_MAX_PAGES):
            data = self._query(query, {"id": team_id, "after": cursor})
            team = data.get("team")
            if not isinstance(team, dict):
                raise LinearError("empty", "Linear returned no team for that id.")
            container = team.get("projects")
            rows.extend(_nodes(container))
            has_next, cursor = _page_info(container)
            if not has_next:
                return rows
        raise LinearError(
            "transient",
            "Linear returned more projects than this install can list.",
        )

    def issue(self, issue_id: str) -> dict[str, str]:
        query = "query($id: String!) { issue(id: $id) { id url team { id } } }"
        data = self._query(query, {"id": issue_id})
        row = data.get("issue")
        if not isinstance(row, dict):
            raise LinearError("scope", "That issue is not visible to this connection.")
        team = row.get("team")
        team_id = _text(team.get("id")) if isinstance(team, dict) else ""
        return {
            "id": _text(row.get("id")),
            "url": _text(row.get("url")),
            "team_id": team_id,
        }

    def create_issue(
        self,
        team_id: str,
        title: str,
        description: str,
        priority: int,
        project_id: str = "",
    ) -> dict[str, str]:
        variables: dict[str, Any] = {
            "teamId": team_id,
            "title": title,
            "description": description,
            "priority": priority,
        }
        project_var = ""
        project_field = ""
        if project_id:
            variables["projectId"] = project_id
            project_var = ", $projectId: String"
            project_field = ", projectId: $projectId"
        query = (
            "mutation($teamId: String!, $title: String!, $description: String, "
            f"$priority: Int{project_var}) {{ "
            "issueCreate(input: {teamId: $teamId, title: $title, "
            f"description: $description, priority: $priority{project_field}}}) "
            "{ success issue { id url } } }"
        )
        data = self._query(query, variables, repeatable=False)
        created = data.get("issueCreate")
        if not isinstance(created, dict) or not created.get("success"):
            raise LinearError("transient", "Linear did not create the issue.")
        issue = created.get("issue")
        if not isinstance(issue, dict):
            raise LinearError("transient", "Linear did not return the issue.")
        return {"id": _text(issue.get("id")), "url": _text(issue.get("url"))}

    def update_issue(self, issue_id: str, team_id: str, title: str) -> dict[str, str]:
        current = self.issue(issue_id)
        if current["team_id"] != team_id:
            raise LinearError(
                "scope", "That issue is not on the Linear team this install selected."
            )
        query = (
            "mutation($id: String!, $title: String!) { "
            "issueUpdate(id: $id, input: {title: $title}) "
            "{ success issue { id url } } }"
        )
        data = self._query(query, {"id": issue_id, "title": title})
        updated = data.get("issueUpdate")
        if not isinstance(updated, dict) or not updated.get("success"):
            raise LinearError("transient", "Linear did not update the issue.")
        issue = updated.get("issue")
        if not isinstance(issue, dict):
            raise LinearError("transient", "Linear did not return the issue.")
        return {"id": _text(issue.get("id")), "url": _text(issue.get("url"))}

    def _pages(
        self, query: str, variables: dict[str, Any], field: str
    ) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        cursor: str | None = None
        for _page in range(_MAX_PAGES):
            data = self._query(query, {**variables, "after": cursor})
            container = data.get(field)
            rows.extend(_nodes(container))
            has_next, cursor = _page_info(container)
            if not has_next:
                return rows
        raise LinearError(
            "transient",
            "Linear returned more teams than this install can list.",
        )

    def _query(
        self,
        query: str,
        variables: Mapping[str, Any],
        *,
        repeatable: bool = True,
    ) -> dict[str, Any]:
        self._ensure_policy(LINEAR_API_URL)
        headers = {
            "Authorization": self._token,
            "Content-Type": "application/json",
        }
        body = {"query": query, "variables": dict(variables)}
        delay = 0.2
        last = "Linear did not respond."
        for attempt in range(_ATTEMPTS):
            try:
                status, payload = self._transport("POST", LINEAR_API_URL, headers, body)
            except TransportError as exc:
                last = _public(str(exc), self._token)
                if (
                    repeatable
                    and attempt + 1 < _ATTEMPTS
                    and exc.kind in ("timeout", "network")
                ):
                    self._sleep(delay)
                    delay *= 2
                    continue
                raise LinearError("transient", last) from exc
            if status in (401, 403):
                raise LinearError(
                    "auth",
                    "Linear rejected the credential. Set a new LINEAR_API_KEY "
                    "on the host. Do not paste it into chat.",
                )
            if status == 429 or (repeatable and status >= 500):
                last = f"Linear returned HTTP {status}."
                if attempt + 1 < _ATTEMPTS:
                    self._sleep(delay)
                    delay *= 2
                    continue
                raise LinearError("transient", last)
            if status >= 500:
                raise LinearError("transient", f"Linear returned HTTP {status}.")
            errors = payload.get("errors")
            if isinstance(errors, list) and errors:
                message = _graphql_message(errors)
                if _auth_message(message):
                    raise LinearError(
                        "auth",
                        "Linear rejected the credential. Set a new LINEAR_API_KEY "
                        "on the host. Do not paste it into chat.",
                    )
                raise LinearError("transient", _public(message, self._token))
            if status != 200:
                raise LinearError("transient", f"Linear returned HTTP {status}.")
            data = payload.get("data")
            if not isinstance(data, dict):
                raise LinearError("transient", "Linear returned no data.")
            return data
        raise LinearError("transient", last)

    def _ensure_policy(self, url: str) -> None:
        if self._policy is None:
            return
        host = urlsplit(url).hostname or ""
        if not host or not self._policy.allows_host(host):
            shown = host or "that host"
            raise LinearError(
                "policy",
                f"The seat policy does not allow {shown}.",
            )


def client_from_environ(
    env: Mapping[str, str] | None = None,
    *,
    policy: Any = None,
) -> LinearClient | None:
    """A client when the host has a Linear token, else ``None``.

    A configured ``LINEAR_API_KEY_FILE`` that cannot be read raises
    ``LinearError`` instead of looking like a missing credential.
    """
    source = os.environ if env is None else env
    token, error = resolve_secret(source, LINEAR_ENV)
    if error:
        raise LinearError("auth", error)
    if not token:
        return None
    return LinearClient(token, policy=policy)


def _default_transport(token: str) -> Transport:
    def _send(
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any] | None,
    ) -> tuple[int, dict[str, Any]]:
        return request_json(method, url, headers, body, secret=token)

    return _send


def _nodes(container: Any) -> list[dict[str, str]]:
    if not isinstance(container, dict):
        return []
    nodes = container.get("nodes")
    if not isinstance(nodes, list):
        return []
    rows: list[dict[str, str]] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        ident = _text(node.get("id"))
        if not ident:
            continue
        rows.append({"id": ident, "name": _text(node.get("name")) or ident})
    return rows


def _page_info(container: Any) -> tuple[bool, str | None]:
    if not isinstance(container, dict):
        return False, None
    info = container.get("pageInfo")
    if not isinstance(info, dict) or not info.get("hasNextPage"):
        return False, None
    cursor = info.get("endCursor")
    if not isinstance(cursor, str) or not cursor.strip():
        return False, None
    return True, cursor


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _graphql_message(errors: list[Any]) -> str:
    first = errors[0]
    if isinstance(first, dict) and isinstance(first.get("message"), str):
        return first["message"]
    return "Linear returned an error."


def _auth_message(message: str) -> bool:
    lowered = message.lower()
    return "auth" in lowered or "not authenticated" in lowered


def _public(message: str, token: str) -> str:
    if token and token in message:
        return message.replace(token, "[redacted]")
    return message or "Linear returned an error."
