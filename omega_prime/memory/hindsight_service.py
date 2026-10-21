"""Episodic memory against a configured Hindsight service.

The base URL defaults to empty, which means the remote bank is disabled.
Callers pass ``HINDSIGHT_URL`` when they operate their own service. There is
no hosted default.

``HindsightService`` speaks the Hindsight bank routes (retain, recall,
reflect, knowledge-page search) on one explicitly configured bank. It raises
typed, sanitized ``HindsightError`` values for transport, auth, and malformed
response failures.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

from omega_prime.memory.hindsight import _check_bank
from omega_prime.providers.http import HttpTransport

DEFAULT_BANK = "ultrathink"
DEFAULT_BASE_URL = ""

_HINDSIGHT_PROVIDER = SimpleNamespace(
    name="hindsight",
    requires_key=True,
    env_vars=("HINDSIGHT_API_KEY", "HINDSIGHT_API_TOKEN"),
)

_RECALL_TYPES = ("world", "experience", "observation")
_BUDGETS = ("low", "mid", "high")


class HindsightError(Exception):
    """A sanitized Hindsight failure with a stable state and optional code."""

    def __init__(self, message: str, *, code: str, status: str = "unreachable"):
        super().__init__(message)
        self.code = code
        self.status = status


class HindsightService:
    """Retain/recall/reflect/search on one Hindsight bank."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        bank: str = DEFAULT_BANK,
        *,
        transport: Any = None,
        broker: Any = None,
        policy: Any = None,
        timeout: float = 30.0,
    ) -> None:
        if re.fullmatch(r"[A-Za-z0-9_-]+", bank) is None:
            raise ValueError("bank must be a safe path segment")
        _check_bank(bank)
        self._base_url = _check_base_url(base_url)
        self._bank = bank
        self._broker = broker
        self._transport = transport or HttpTransport(
            policy=policy, timeout=timeout, max_retries=0
        )

    @property
    def base_url(self) -> str:
        """The hindsight-api base URL, without a trailing slash."""
        return self._base_url

    @property
    def bank(self) -> str:
        """The bank every call is scoped to."""
        return self._bank

    def retain(
        self,
        items: list,
        *,
        context: str | None = None,
        document_id: str | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        """Keep each item in the bank. Blank strings are skipped, not errors."""
        payload_items = _retain_items(items, context, document_id, tags)
        if not payload_items:
            return {"ok": True, "count": 0}
        headers = self._headers()
        try:
            response = self._transport.post(
                self._bank_url + "/memories",
                headers,
                {"items": payload_items},
            )
        except Exception as exc:
            raise self._error("retain", exc) from None
        if not isinstance(response, dict) or not isinstance(
            response.get("success"), bool
        ):
            raise self._malformed("retain")
        if response["success"]:
            count = response.get("items_count", len(payload_items))
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise self._malformed("retain")
            return {"ok": True, "count": count}
        return {"ok": False, "reason": f"bank {self._bank} refused retain"}

    def recall(
        self,
        query: str,
        *,
        limit: int = 5,
        types: list[str] | None = None,
        max_tokens: int = 2000,
    ) -> list[dict]:
        """Recall validated result hits for ``query``, most relevant first."""
        _check_limit(limit)
        if not isinstance(query, str) or query == "":
            return []
        wanted = ["observation"] if types is None else list(types)
        for entry in wanted:
            if entry not in _RECALL_TYPES:
                raise ValueError(f"unknown recall type {entry!r}")
        body = {
            "query": query,
            "types": wanted,
            "budget": "low",
            "max_tokens": max_tokens,
        }
        headers = self._headers()
        try:
            response = self._transport.post(
                self._bank_url + "/memories/recall", headers, body
            )
        except Exception as exc:
            raise self._error("recall", exc) from None
        if not isinstance(response, dict):
            raise self._malformed("recall")
        results = response.get("results")
        if not isinstance(results, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("text"), str)
            for item in results
        ):
            raise self._malformed("recall")
        return results[:limit]

    def reflect(
        self, query: str, *, budget: str = "low", max_tokens: int = 4096
    ) -> str:
        """Deep synthesis over the bank for ``query``."""
        if budget not in _BUDGETS:
            raise ValueError(f"unknown reflect budget {budget!r}")
        if not isinstance(query, str) or query == "":
            return ""
        headers = self._headers()
        try:
            response = self._transport.post(
                self._bank_url + "/reflect",
                headers,
                {"query": query, "budget": budget, "max_tokens": max_tokens},
            )
        except Exception as exc:
            raise self._error("reflect", exc) from None
        if not isinstance(response, dict) or not isinstance(response.get("text"), str):
            raise self._malformed("reflect")
        return response["text"]

    def search_pages(self, query: str, *, limit: int = 3) -> list[dict]:
        """Knowledge-page search hits for ``query``."""
        _check_limit(limit)
        if not isinstance(query, str) or query == "":
            return []
        headers = self._headers()
        try:
            response = self._transport.get(
                self._bank_url + "/knowledge-base/search",
                headers,
                {"q": query, "limit": limit},
            )
        except Exception as exc:
            raise self._error("page search", exc) from None
        if not isinstance(response, dict):
            raise self._malformed("page search")
        results = response.get("results")
        if not isinstance(results, list) or any(
            not isinstance(item, dict) for item in results
        ):
            raise self._malformed("page search")
        return results

    @property
    def _bank_url(self) -> str:
        return f"{self._base_url}/v1/default/banks/{self._bank}"

    def _headers(self) -> dict:
        if self._broker is None:
            return {}
        try:
            token = self._broker.key_for(_HINDSIGHT_PROVIDER, self._base_url)
        except Exception:
            raise HindsightError(
                "hindsight credentials unavailable",
                code="credentials_unavailable",
                status="unauthorized",
            ) from None
        return {"Authorization": f"Bearer {token}"} if token else {}

    def _error(self, operation: str, exc: Exception) -> HindsightError:
        status_code = getattr(exc, "status_code", None)
        if (
            isinstance(status_code, int)
            and not isinstance(status_code, bool)
            and status_code in (401, 403)
        ):
            status = "unauthorized"
            code = "http_" + str(status_code)
        else:
            status = "unreachable"
            code = "transport_error"
        return HindsightError(f"hindsight {operation} failed", code=code, status=status)

    def _malformed(self, operation: str) -> HindsightError:
        return HindsightError(
            f"hindsight {operation} returned malformed data",
            code="malformed_response",
        )


def _retain_items(
    items: list,
    context: str | None,
    document_id: str | None,
    tags: list[str] | None,
) -> list[dict]:
    if not isinstance(items, list):
        raise ValueError("items must be a list of strings or dicts")
    payload: list[dict] = []
    for item in items:
        if isinstance(item, str):
            if item.strip() == "":
                continue
            entry: dict[str, Any] = {"content": item.strip()}
            if context is not None:
                entry["context"] = context
            if document_id is not None:
                entry["document_id"] = document_id
            if tags is not None:
                entry["tags"] = list(tags)
            payload.append(entry)
        elif isinstance(item, dict):
            content = item.get("content")
            if not isinstance(content, str) or content.strip() == "":
                raise ValueError(
                    "retain dict items must carry non-empty string content"
                )
            payload.append(dict(item))
        else:
            raise ValueError("items must be a list of strings or dicts")
    return payload


def _check_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")


def _check_base_url(base_url: str) -> str:
    if (
        not isinstance(base_url, str)
        or base_url == ""
        or "?" in base_url
        or "#" in base_url
    ):
        raise ValueError("base_url must be an explicitly configured HTTPS URL")
    try:
        parts = urlsplit(base_url)
        port = parts.port
    except ValueError:
        raise ValueError("base_url must be a valid HTTPS URL") from None
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or parts.path not in ("", "/")
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError("base_url must be a safe HTTPS origin")
    return f"https://{parts.netloc}".rstrip("/")


__all__ = ["DEFAULT_BANK", "DEFAULT_BASE_URL", "HindsightError", "HindsightService"]
