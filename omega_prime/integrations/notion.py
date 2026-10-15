# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Notion client bound to the duplicated Task dashboard.

Writes go only to a database id the schema check already accepted.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

from omega_prime.integrations.binding import NOTION_ENV, resolve_secret
from omega_prime.integrations.contract import schema_gaps
from omega_prime.integrations.transport import TransportError, request_json

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
_ATTEMPTS = 3
_MAX_PAGES = 20

Transport = Callable[
    [str, str, Mapping[str, str], Mapping[str, Any] | None],
    tuple[int, dict[str, Any]],
]


class NotionError(Exception):
    """Notion call failed. ``code`` is auth, permission, transient, or schema."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class NotionClient:
    """One integration token. Rate limits retry. Auth errors do not."""

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

    def me(self) -> dict[str, str]:
        payload = self._request("GET", "/users/me", None)
        return {"id": _text(payload.get("id")), "name": _text(payload.get("name"))}

    def search_databases(self) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        cursor: str | None = None
        for _page in range(_MAX_PAGES):
            body: dict[str, Any] = {
                "filter": {"value": "database", "property": "object"},
                "page_size": 100,
            }
            if cursor:
                body["start_cursor"] = cursor
            payload = self._request("POST", "/search", body)
            rows.extend(_database_rows(payload.get("results")))
            has_more, cursor = _cursor(payload)
            if not has_more:
                return rows
        raise NotionError(
            "transient",
            "Notion returned more databases than this install can list.",
        )

    def database(self, database_id: str) -> dict[str, Any]:
        return self._request("GET", f"/databases/{database_id}", None)

    def matching_databases(self) -> tuple[list[dict[str, str]], dict[str, list[str]]]:
        """Databases that match the contract, and gaps for the ones that do not."""
        matches: list[dict[str, str]] = []
        gaps: dict[str, list[str]] = {}
        for row in self.search_databases():
            try:
                payload = self.database(row["id"])
            except NotionError as exc:
                if exc.code == "permission":
                    continue
                raise
            missing = schema_gaps(payload)
            if missing:
                gaps[row["id"]] = missing
                continue
            name = _title(payload) or row["name"]
            matches.append({"id": row["id"], "name": name})
        return matches, gaps

    def query_tasks(self, database_id: str) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        cursor: str | None = None
        wanted = database_id.replace("-", "")
        for _page in range(_MAX_PAGES):
            body: dict[str, Any] = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            payload = self._request("POST", f"/databases/{database_id}/query", body)
            rows.extend(_task_rows(payload.get("results"), wanted))
            has_more, cursor = _cursor(payload)
            if not has_more:
                return rows
        raise NotionError(
            "transient",
            "Notion returned more tasks than this install can list.",
        )

    def create_task(self, database_id: str, name: str) -> dict[str, str]:
        body = {
            "parent": {"database_id": database_id},
            "properties": {
                "Name": {"title": [{"text": {"content": name}}]},
                "Status": {"status": {"name": "Not started"}},
            },
        }
        payload = self._request("POST", "/pages", body, repeatable=False)
        return {"id": _text(payload.get("id")).replace("-", ""), "name": name}

    def update_task_status(
        self, database_id: str, page_id: str, status: str
    ) -> dict[str, str]:
        normalized = page_id.replace("-", "")
        wanted = database_id.replace("-", "")
        try:
            page = self._request("GET", f"/pages/{normalized}", None)
        except NotionError as exc:
            if exc.code == "permission":
                raise NotionError(
                    "scope",
                    "That page is not in the bound Tasks database. "
                    "The bot does not write to other Notion content.",
                ) from exc
            raise
        parent = page.get("parent")
        parent_id = ""
        if isinstance(parent, dict):
            parent_id = _text(parent.get("database_id")).replace("-", "")
        if parent_id != wanted:
            raise NotionError(
                "scope",
                "That page is not in the bound Tasks database. "
                "The bot does not write to other Notion content.",
            )
        body = {"properties": {"Status": {"status": {"name": status}}}}
        payload = self._request("PATCH", f"/pages/{normalized}", body)
        return {"id": _text(payload.get("id")).replace("-", "") or normalized}

    def _request(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None,
        *,
        repeatable: bool = True,
    ) -> dict[str, Any]:
        self._ensure_policy(NOTION_API + path)
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }
        delay = 0.2
        last = "Notion did not respond."
        for attempt in range(_ATTEMPTS):
            try:
                status, payload = self._transport(
                    method, NOTION_API + path, headers, body
                )
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
                raise NotionError("transient", last) from exc
            if status in (401, 403):
                raise NotionError(
                    "auth",
                    "Notion rejected the credential. Share the duplicated "
                    "dashboard with the integration and set NOTION_TOKEN on "
                    "the host. Do not paste it into chat.",
                )
            if status == 404:
                raise NotionError(
                    "permission",
                    "Notion cannot see that object. Share the duplicated "
                    "dashboard with the integration.",
                )
            if status == 429 or (repeatable and status >= 500):
                last = f"Notion returned HTTP {status}."
                if attempt + 1 < _ATTEMPTS:
                    self._sleep(delay)
                    delay *= 2
                    continue
                raise NotionError("transient", last)
            if status >= 500:
                raise NotionError("transient", f"Notion returned HTTP {status}.")
            if status not in (200, 201):
                raise NotionError("transient", f"Notion returned HTTP {status}.")
            return payload
        raise NotionError("transient", last)

    def _ensure_policy(self, url: str) -> None:
        if self._policy is None:
            return
        host = urlsplit(url).hostname or ""
        if not host or not self._policy.allows_host(host):
            shown = host or "that host"
            raise NotionError(
                "policy",
                f"The seat policy does not allow {shown}.",
            )


def client_from_environ(
    env: Mapping[str, str] | None = None,
    *,
    policy: Any = None,
) -> NotionClient | None:
    source = os.environ if env is None else env
    token, error = resolve_secret(source, NOTION_ENV)
    if error:
        raise NotionError("auth", error)
    if not token:
        return None
    return NotionClient(token, policy=policy)


def _default_transport(token: str) -> Transport:
    def _send(
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any] | None,
    ) -> tuple[int, dict[str, Any]]:
        return request_json(method, url, headers, body, secret=token)

    return _send


def _database_rows(results: Any) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if not isinstance(results, list):
        return rows
    for item in results:
        if not isinstance(item, dict) or item.get("object") != "database":
            continue
        ident = _text(item.get("id")).replace("-", "")
        if not ident:
            continue
        rows.append({"id": ident, "name": _title(item) or ident})
    return rows


def _task_rows(results: Any, database_id: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if not isinstance(results, list):
        return rows
    for page in results:
        if not isinstance(page, dict):
            continue
        parent = page.get("parent")
        parent_id = ""
        if isinstance(parent, dict):
            parent_id = _text(parent.get("database_id")).replace("-", "")
        if parent_id and parent_id != database_id:
            continue
        rows.append(
            {
                "id": _text(page.get("id")).replace("-", ""),
                "name": _page_name(page),
            }
        )
    return rows


def _cursor(payload: Mapping[str, Any]) -> tuple[bool, str | None]:
    if not payload.get("has_more"):
        return False, None
    cursor = payload.get("next_cursor")
    if not isinstance(cursor, str) or not cursor.strip():
        return False, None
    return True, cursor


def _title(database: Mapping[str, Any]) -> str:
    title = database.get("title")
    return _rich(title)


def _page_name(page: Mapping[str, Any]) -> str:
    props = page.get("properties")
    if not isinstance(props, dict):
        return ""
    name = props.get("Name")
    if not isinstance(name, dict):
        return ""
    return _rich(name.get("title"))


def _rich(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for block in value:
        if isinstance(block, dict) and isinstance(block.get("plain_text"), str):
            parts.append(block["plain_text"])
    return "".join(parts).strip()


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _public(message: str, token: str) -> str:
    if token and token in message:
        return message.replace(token, "[redacted]")
    return message
